# Database engine та сесія

import asyncio
from functools import lru_cache

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from config import DATABASE_URL

from bot.database.models import Base

SQLITE_TIMEOUT_SEC = 120.0
SQLITE_BUSY_MS = 120_000


def database_url_for_sync(async_url: str) -> str:
    """URL для синхронного Engine (не через greenlet async-пул)."""
    url = async_url or ""
    if "sqlite+aiosqlite" in url:
        return url.replace("sqlite+aiosqlite", "sqlite", 1)
    if "postgresql+asyncpg" in url:
        return url.replace("postgresql+asyncpg", "postgresql+psycopg", 1)
    if "postgresql+psycopg_async" in url:
        return url.replace("postgresql+psycopg_async", "postgresql+psycopg", 1)
    return url


@lru_cache(maxsize=1)
def get_sync_engine() -> Engine:
    """Окремий sync engine для коротких записів з sync-коду (NovaPay JWT тощо)."""
    sync_url = database_url_for_sync(DATABASE_URL)
    kwargs: dict = {"echo": False}
    if sync_url.startswith("sqlite"):
        kwargs["poolclass"] = NullPool
        kwargs["connect_args"] = {"timeout": SQLITE_TIMEOUT_SEC, "check_same_thread": False}
    eng = create_engine(sync_url, **kwargs)
    if sync_url.startswith("sqlite"):
        event.listen(eng, "connect", _sqlite_pragmas)
    return eng


def _sqlite_pragmas(dbapi_conn, _connection_record) -> None:
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_MS}")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def sqlite_engine_kwargs(url: str) -> dict:
    """Один конект за раз + довге очікування, щоб нічний синк і бекап не били database is locked."""
    if "sqlite" not in (url or ""):
        return {"echo": False, "pool_pre_ping": True}
    return {
        "echo": False,
        "pool_pre_ping": True,
        "poolclass": NullPool,
        "connect_args": {"timeout": SQLITE_TIMEOUT_SEC},
    }


class RetryAsyncSession(AsyncSession):
    """Повторює запит, якщо SQLite ще зайнята після busy_timeout."""

    async def execute(self, *args, **kwargs):
        last: Exception | None = None
        for attempt in range(8):
            try:
                return await super().execute(*args, **kwargs)
            except OperationalError as exc:
                last = exc
                if "locked" not in str(exc).lower() or attempt >= 7:
                    raise
                try:
                    await self.rollback()
                except Exception:
                    pass
                await asyncio.sleep(min(0.25 * (2 ** attempt), 4.0))
        if last:
            raise last
        raise OperationalError("database is locked", None, None)


# Async engine
_sqlite = "sqlite" in (DATABASE_URL or "")
engine = create_async_engine(DATABASE_URL, **sqlite_engine_kwargs(DATABASE_URL))
if _sqlite:
    event.listen(engine.sync_engine, "connect", _sqlite_pragmas)

# Session maker
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=RetryAsyncSession,
    expire_on_commit=False,
)


def _add_comment_column_if_missing(sync_conn):
    """Додати колонку comment до transactions, якщо її ще немає (міграція)."""
    from sqlalchemy import text
    try:
        r = sync_conn.execute(text("PRAGMA table_info(transactions)"))
        rows = r.fetchall()
    except Exception:
        return
    if not rows:
        return
    names = [row[1] for row in rows]
    if "comment" not in names:
        sync_conn.execute(text("ALTER TABLE transactions ADD COLUMN comment TEXT"))


def _add_web_user_access_columns_if_missing(sync_conn):
    """Додати колонки доступу до web_users (міграція для існуючих SQLite/інших БД)."""
    from sqlalchemy import text

    try:
        insp = inspect(sync_conn)
        if not insp.has_table("web_users"):
            return
        cols = {c["name"] for c in insp.get_columns("web_users")}
    except Exception:
        return
    dialect = sync_conn.dialect.name
    if "access_finance" not in cols:
        if dialect == "sqlite":
            sync_conn.execute(
                text("ALTER TABLE web_users ADD COLUMN access_finance INTEGER NOT NULL DEFAULT 1")
            )
        else:
            sync_conn.execute(
                text("ALTER TABLE web_users ADD COLUMN access_finance BOOLEAN NOT NULL DEFAULT TRUE")
            )
    if "access_marketing" not in cols:
        if dialect == "sqlite":
            sync_conn.execute(
                text("ALTER TABLE web_users ADD COLUMN access_marketing INTEGER NOT NULL DEFAULT 0")
            )
        else:
            sync_conn.execute(
                text("ALTER TABLE web_users ADD COLUMN access_marketing BOOLEAN NOT NULL DEFAULT FALSE")
            )
    if "access_warehouse" not in cols:
        if dialect == "sqlite":
            sync_conn.execute(
                text("ALTER TABLE web_users ADD COLUMN access_warehouse INTEGER NOT NULL DEFAULT 0")
            )
        else:
            sync_conn.execute(
                text("ALTER TABLE web_users ADD COLUMN access_warehouse BOOLEAN NOT NULL DEFAULT FALSE")
            )
    if "access_clients_export" not in cols:
        if dialect == "sqlite":
            sync_conn.execute(
                text("ALTER TABLE web_users ADD COLUMN access_clients_export INTEGER NOT NULL DEFAULT 0")
            )
        else:
            sync_conn.execute(
                text("ALTER TABLE web_users ADD COLUMN access_clients_export BOOLEAN NOT NULL DEFAULT FALSE")
            )
    if "access_advertising" not in cols:
        if dialect == "sqlite":
            sync_conn.execute(
                text("ALTER TABLE web_users ADD COLUMN access_advertising INTEGER NOT NULL DEFAULT 0")
            )
        else:
            sync_conn.execute(
                text("ALTER TABLE web_users ADD COLUMN access_advertising BOOLEAN NOT NULL DEFAULT FALSE")
            )


def _add_operator_username_if_missing(sync_conn):
    """Додати колонку operator_username до transactions, якщо її ще немає."""
    from sqlalchemy import text
    try:
        r = sync_conn.execute(text("PRAGMA table_info(transactions)"))
        rows = r.fetchall()
    except Exception:
        return
    if not rows:
        return
    names = [row[1] for row in rows]
    if "operator_username" not in names:
        sync_conn.execute(text("ALTER TABLE transactions ADD COLUMN operator_username VARCHAR(150)"))


def _deactivate_inactive_fops(sync_conn):
    """Деактивувати ФОПів з INACTIVE_FOP_NAMES (міграція при старті)."""
    from sqlalchemy import text
    from config import INACTIVE_FOP_NAMES

    if not INACTIVE_FOP_NAMES:
        return
    try:
        insp = inspect(sync_conn)
        if not insp.has_table("fops"):
            return
    except Exception:
        return
    for name in INACTIVE_FOP_NAMES:
        sync_conn.execute(
            text("UPDATE fops SET is_active = 0 WHERE name = :name"),
            {"name": name},
        )


async def init_db():
    """Ініціалізація бази даних (створення таблиць та міграції)."""
    from bot.services.fop_config import (
        add_fop_credential_columns_if_missing,
        _migrate_env_fops_to_db_sync,
        refresh_fop_runtime,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_comment_column_if_missing)
        await conn.run_sync(_add_operator_username_if_missing)
        await conn.run_sync(_add_web_user_access_columns_if_missing)
        await conn.run_sync(add_fop_credential_columns_if_missing)
        await conn.run_sync(_migrate_env_fops_to_db_sync)
        await conn.run_sync(_deactivate_inactive_fops)

    from novapay.token_store import import_rotated_stash_into_db

    import_rotated_stash_into_db()

    async with AsyncSessionLocal() as session:
        await refresh_fop_runtime(session)
