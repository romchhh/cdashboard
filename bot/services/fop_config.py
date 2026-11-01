"""Конфігурація ФОП у БД: міграція з .env, runtime-кеш для Mono/NovaPay."""

from __future__ import annotations

import logging
import asyncio
from os import getenv

from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import FOP

logger = logging.getLogger(__name__)

_FOP_ENV_COLUMN_SPECS: tuple[tuple[str, str], ...] = (
    ("mono_token", "TEXT"),
    ("mono_iban", "VARCHAR(50)"),
    ("novapay_refresh_token", "TEXT"),
    ("novapay_certificate", "TEXT"),
    ("novapay_login", "VARCHAR(200)"),
    ("novapay_session_suffix", "VARCHAR(20)"),
)


def sources_from_fop_row(fop: FOP) -> list[str]:
    """Джерела транзакцій за заповненими полями ФОП у БД."""
    sources: list[str] = []
    if (fop.mono_token or "").strip():
        sources.append("monobank")
    refresh = (fop.novapay_refresh_token or "").strip()
    cert = (fop.novapay_certificate or "").strip()
    if refresh or cert:
        sources.append("novapay")
    return sources


def _env_has_fop_keys() -> bool:
    for i in range(1, 20):
        if getenv(f"MONO_TOKEN_{i}"):
            return True
        if getenv(f"FOP_IBAN_{i}"):
            return True
        if getenv(f"NOVAPAY_{i}_NAME"):
            return True
    return False


def _parse_env_mono_tokens() -> dict[str, str]:
    out: dict[str, str] = {}
    for i in range(1, 20):
        val = getenv(f"MONO_TOKEN_{i}")
        if not val or "|" not in val:
            continue
        name, token = val.split("|", 1)
        out[name.strip()] = token.strip()
    return out


def _parse_env_fop_ibans() -> dict[str, str]:
    """iban → name"""
    out: dict[str, str] = {}
    for i in range(1, 20):
        val = getenv(f"FOP_IBAN_{i}")
        if not val or "|" not in val:
            continue
        iban, name = val.split("|", 1)
        out[iban.strip()] = name.strip()
    return out


def _parse_env_novapay_tokens() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(1, 20):
        name = getenv(f"NOVAPAY_{i}_NAME")
        if not name:
            continue
        refresh = getenv(f"NOVAPAY_{i}_REFRESH")
        cert = getenv(f"NOVAPAY_{i}_CERT") or getenv(f"NOVAPAY_{i}_CERT_B64")
        suffix = getenv(f"NOVAPAY_{i}_SUFFIX")
        out[name.strip()] = {
            "refresh_token": (refresh or "session-managed").strip(),
            "certificate": (cert or "").strip().replace("\\n", "\n"),
            "suffix": (suffix or "").strip(),
        }
    return out


def _strip_fops_from_env_file() -> None:
    from config import _env_unset

    for i in range(1, 20):
        for key in (
            f"MONO_TOKEN_{i}",
            f"FOP_IBAN_{i}",
            f"NOVAPAY_{i}_NAME",
            f"NOVAPAY_{i}_REFRESH",
            f"NOVAPAY_{i}_CERT",
            f"NOVAPAY_{i}_CERT_B64",
            f"NOVAPAY_{i}_SUFFIX",
        ):
            if getenv(key):
                _env_unset(key)


def apply_fops_to_runtime(fops: list[FOP]) -> None:
    """Заповнити MONO_TOKENS / NOVAPAY_TOKENS / FOP_IBANS / SESSION_SUFFIX з рядків БД."""
    from config import (
        FOP_IBANS,
        INACTIVE_FOP_NAMES,
        MONO_TOKENS,
        NOVAPAY_TOKENS,
        SESSION_SUFFIX,
        ensure_session_suffix,
    )

    MONO_TOKENS.clear()
    FOP_IBANS.clear()
    NOVAPAY_TOKENS.clear()

    for fop in fops:
        if not fop.is_active or fop.name in INACTIVE_FOP_NAMES:
            continue
        name = (fop.name or "").strip()
        if not name:
            continue

        mono_token = (fop.mono_token or "").strip()
        if mono_token:
            MONO_TOKENS[name] = mono_token
            iban = (fop.mono_iban or "").strip()
            if iban:
                FOP_IBANS[iban] = name

        refresh = (fop.novapay_refresh_token or "").strip()
        cert = (fop.novapay_certificate or "").strip()
        login = (getattr(fop, "novapay_login", None) or "").strip()
        if refresh or cert:
            suffix = (fop.novapay_session_suffix or "").strip()
            if not suffix:
                suffix = ensure_session_suffix(name)
            else:
                SESSION_SUFFIX[name] = suffix
            NOVAPAY_TOKENS[name] = {
                "refresh_token": refresh or "session-managed",
                "certificate": cert,
                "login": login,
                "slot": 0,
            }


async def refresh_fop_runtime(session: AsyncSession) -> None:
    """Перечитати runtime-кеш з активних ФОП у БД. Чекає, якщо файл SQLite зайнятий."""
    last: Exception | None = None
    for attempt in range(8):
        try:
            result = await session.execute(select(FOP).where(FOP.is_active.is_(True)))
            apply_fops_to_runtime(list(result.scalars().all()))
            return
        except OperationalError as exc:
            last = exc
            if "locked" not in str(exc).lower() or attempt >= 7:
                raise
            await session.rollback()
            await asyncio.sleep(0.5 * (attempt + 1))
    if last:
        raise last


def _migrate_env_fops_to_db_sync(sync_conn) -> bool:
    """
    Одноразово перенести MONO/NOVAPAY/IBAN з .env у таблицю fops.
    Повертає True, якщо були ключі в .env і виконано міграцію.
    """
    from sqlalchemy import inspect

    from config import INACTIVE_FOP_NAMES, SESSION_SUFFIX, ensure_session_suffix

    if not _env_has_fop_keys():
        return False

    try:
        insp = inspect(sync_conn)
        if not insp.has_table("fops"):
            return False
    except Exception:
        return False

    mono = _parse_env_mono_tokens()
    ibans = _parse_env_fop_ibans()
    nova = _parse_env_novapay_tokens()

    ibans_by_name: dict[str, str] = {}
    for iban, name in ibans.items():
        ibans_by_name[(name or "").strip()] = iban

    all_names: set[str] = set(mono) | set(nova) | set(ibans_by_name)
    if not all_names:
        return False

    existing_rows = sync_conn.execute(text("SELECT id, name, is_active FROM fops")).fetchall()
    by_name = {str(row[1]).strip(): row for row in existing_rows}

    migrated = 0
    for name in sorted(all_names, key=lambda s: s.casefold()):
        if name in INACTIVE_FOP_NAMES:
            continue
        row = by_name.get(name)
        mono_token = mono.get(name)
        iban = ibans_by_name.get(name)
        nova_cfg = nova.get(name)

        if row is None:
            from datetime import datetime

            sync_conn.execute(
                text(
                    """
                    INSERT INTO fops (
                        name, is_active, mono_token, mono_iban,
                        novapay_refresh_token, novapay_certificate, novapay_session_suffix,
                        created_at
                    ) VALUES (
                        :name, 1, :mono_token, :mono_iban,
                        :novapay_refresh, :novapay_cert, :novapay_suffix,
                        :created_at
                    )
                    """
                ),
                {
                    "name": name,
                    "mono_token": mono_token,
                    "mono_iban": iban,
                    "novapay_refresh": (nova_cfg or {}).get("refresh_token"),
                    "novapay_cert": (nova_cfg or {}).get("certificate") or None,
                    "novapay_suffix": _resolve_suffix(name, nova_cfg, SESSION_SUFFIX, ensure_session_suffix),
                    "created_at": datetime.utcnow(),
                },
            )
            migrated += 1
            continue

        fop_id, _, is_active = row
        updates: dict[str, str | None] = {}
        if mono_token:
            updates["mono_token"] = mono_token
        if iban:
            updates["mono_iban"] = iban
        if nova_cfg:
            if nova_cfg.get("refresh_token"):
                updates["novapay_refresh_token"] = nova_cfg["refresh_token"]
            if nova_cfg.get("certificate"):
                updates["novapay_certificate"] = nova_cfg["certificate"]
            suffix = _resolve_suffix(name, nova_cfg, SESSION_SUFFIX, ensure_session_suffix)
            if suffix:
                updates["novapay_session_suffix"] = suffix

        if not updates:
            continue

        set_clause = ", ".join(f"{col} = :{col}" for col in updates)
        sync_conn.execute(
            text(f"UPDATE fops SET {set_clause} WHERE id = :id"),
            {"id": fop_id, **updates},
        )
        migrated += 1

    _strip_fops_from_env_file()
    logger.info(
        "Міграція ФОП з .env у БД: оброблено %d імен, ключі MONO/NOVAPAY/IBAN прибрано з .env",
        len(all_names),
    )
    return migrated > 0 or bool(all_names)


def _resolve_suffix(name: str, nova_cfg: dict | None, session_suffix: dict, ensure_fn) -> str | None:
    if nova_cfg and nova_cfg.get("suffix"):
        return nova_cfg["suffix"]
    if name in session_suffix:
        return session_suffix[name]
    if nova_cfg:
        return ensure_fn(name)
    return None


def add_fop_credential_columns_if_missing(sync_conn) -> None:
    from sqlalchemy import inspect

    try:
        insp = inspect(sync_conn)
        if not insp.has_table("fops"):
            return
        cols = {c["name"] for c in insp.get_columns("fops")}
    except Exception:
        return

    dialect = sync_conn.dialect.name
    for col_name, col_type in _FOP_ENV_COLUMN_SPECS:
        if col_name in cols:
            continue
        if dialect == "sqlite":
            sync_conn.execute(text(f"ALTER TABLE fops ADD COLUMN {col_name} {col_type}"))
        else:
            sync_conn.execute(text(f"ALTER TABLE fops ADD COLUMN {col_name} {col_type}"))


async def set_mono_credentials(
    session: AsyncSession,
    *,
    name: str,
    token: str,
    iban: str | None = None,
) -> FOP:
    name = (name or "").strip()
    token = (token or "").strip()
    iban_clean = (iban or "").strip().replace(" ", "") or None
    if not name or not token:
        raise ValueError("Потрібні ім'я ФОП і токен Monobank")

    fop = (
        await session.execute(select(FOP).where(FOP.name == name))
    ).scalar_one_or_none()
    if fop is None:
        fop = FOP(name=name, is_active=True)
        session.add(fop)
    elif not fop.is_active:
        fop.is_active = True

    if (fop.mono_token or "").strip() and (fop.mono_token or "").strip() != token:
        raise ValueError(f"ФОП «{name}» уже має інший токен Monobank у БД")

    fop.mono_token = token
    if iban_clean:
        fop.mono_iban = iban_clean
    await session.commit()
    await session.refresh(fop)
    await refresh_fop_runtime(session)
    return fop


async def set_novapay_credentials(
    session: AsyncSession,
    *,
    name: str,
    refresh: str | None = None,
    cert: str | None = None,
    login: str | None = None,
) -> FOP:
    from config import ensure_session_suffix

    name = (name or "").strip()
    if not name:
        raise ValueError("Потрібне ім'я ФОП NovaPay")

    fop = (
        await session.execute(select(FOP).where(FOP.name == name))
    ).scalar_one_or_none()
    if fop is None:
        fop = FOP(name=name, is_active=True)
        session.add(fop)
    elif not fop.is_active:
        fop.is_active = True

    refresh_val = (refresh or "").strip() or "session-managed"
    cert_val = (cert or "").strip()
    suffix = (fop.novapay_session_suffix or "").strip() or ensure_session_suffix(name)

    fop.novapay_refresh_token = refresh_val
    if cert_val:
        fop.novapay_certificate = cert_val
    if login is not None:
        login_val = (login or "").strip()
        if login_val:
            fop.novapay_login = login_val
    fop.novapay_session_suffix = suffix
    await session.commit()
    await session.refresh(fop)
    await refresh_fop_runtime(session)
    from novapay.token_store import clear_rotated_stash

    clear_rotated_stash(name)
    return fop


async def clear_fop_runtime_for_name(session: AsyncSession, name: str) -> None:
    """Після архівації — оновити runtime (credentials у БД лишаються для відновлення)."""
    await refresh_fop_runtime(session)
