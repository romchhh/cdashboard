"""Тести резервного копіювання SQLite."""

import sqlite3
import zipfile
from pathlib import Path

from bot.services.db_backup import (
    backup_sqlite_consistent,
    create_backup_archive,
    restore_databases_from_archive,
    split_file,
)


def _make_db(path: Path, table: str = "t") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute(f"INSERT INTO {table} (v) VALUES ('ok')")
        conn.commit()


def test_split_file_rejoins(tmp_path: Path):
    src = tmp_path / "big.bin"
    data = b"ABCDEFGH" * 10_000
    src.write_bytes(data)
    parts = split_file(src, chunk_size=50_000, out_dir=tmp_path / "parts")
    assert len(parts) > 1
    joined = b"".join(p.read_bytes() for p in parts)
    assert joined == data


def test_create_backup_archive(tmp_path: Path, monkeypatch):
    finance = tmp_path / "finance_bot.db"
    products = tmp_path / "products.db"
    clients = tmp_path / "cosmy_clients.db"
    _make_db(finance, "tx")
    _make_db(products, "p")
    _make_db(clients, "c")

    import bot.services.db_backup as mod

    monkeypatch.setattr(
        mod,
        "configured_database_sources",
        lambda: [
            ("finance_bot", finance),
            ("products", products),
            ("cosmy_clients", clients),
        ],
    )

    archive, backed_up, skipped = create_backup_archive(dest_dir=tmp_path / "out")
    assert archive.is_file()
    assert len(backed_up) == 3
    assert not skipped

    with zipfile.ZipFile(archive, "r") as zf:
        names = zf.namelist()
        assert any("finance_bot/finance_bot.db" in n for n in names)
        assert any("products/products.db" in n for n in names)
        assert any("cosmy_clients/cosmy_clients.db" in n for n in names)


def test_restore_replaces_live_databases(tmp_path: Path, monkeypatch):
    finance = tmp_path / "live" / "finance_bot.db"
    products = tmp_path / "live" / "products.db"
    clients = tmp_path / "live" / "cosmy_clients.db"
    _make_db(finance, "tx")
    _make_db(products, "p")
    _make_db(clients, "c")

    import bot.services.db_backup as mod

    monkeypatch.setattr(
        mod,
        "configured_database_sources",
        lambda: [
            ("finance_bot", finance),
            ("products", products),
            ("cosmy_clients", clients),
        ],
    )
    monkeypatch.setattr(mod, "backup_dir", lambda: tmp_path / "backups")

    archive, _, _ = create_backup_archive(dest_dir=tmp_path / "backups")
    with sqlite3.connect(finance) as conn:
        conn.execute("INSERT INTO tx (v) VALUES ('after-backup')")
        conn.commit()
    wal = Path(str(finance) + "-wal")
    wal.write_text("stale", encoding="utf-8")

    result = restore_databases_from_archive(archive)
    assert len(result.restored) == 3
    assert result.aside_dir is not None and result.aside_dir.is_dir()
    assert not wal.exists()
    with sqlite3.connect(finance) as conn:
        values = [row[0] for row in conn.execute("SELECT v FROM tx")]
    assert values == ["ok"]
    parked = result.aside_dir / "finance_bot" / "finance_bot.db"
    with sqlite3.connect(parked) as conn:
        parked_values = [row[0] for row in conn.execute("SELECT v FROM tx")]
    assert "after-backup" in parked_values


def test_backup_sqlite_consistent_while_open(tmp_path: Path):
    src = tmp_path / "live.db"
    dest = tmp_path / "copy.db"
    _make_db(src)
    with sqlite3.connect(src) as conn:
        conn.execute("INSERT INTO t (v) VALUES ('live')")
        conn.commit()
        backup_sqlite_consistent(src, dest)
    with sqlite3.connect(dest) as conn:
        rows = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    assert rows >= 1


def test_send_path_splits_large_file(tmp_path: Path):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from bot.services.db_backup import send_path_to_admins

    big = tmp_path / "cosmy_db_backup_test.zip"
    big.write_bytes(b"x" * 120)
    bot = MagicMock()
    bot.send_message = AsyncMock()
    bot.send_document = AsyncMock()

    async def _run():
        return await send_path_to_admins(
            bot,
            big,
            caption="test",
            max_bytes=50,
            admin_ids=[1, 2],
        )

    parts, delivered, failed = asyncio.run(_run())
    assert parts == 3
    assert delivered == [1, 2]
    assert not failed
    assert bot.send_message.await_count == 2
    assert bot.send_document.await_count == 6


def test_send_path_skips_failed_admin(tmp_path: Path):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from bot.services.db_backup import send_path_to_admins

    small = tmp_path / "tiny.zip"
    small.write_bytes(b"zip")
    bot = MagicMock()

    async def send_doc(admin_id, doc, caption=None):
        if admin_id == 111:
            raise Exception("chat not found")

    bot.send_document = AsyncMock(side_effect=send_doc)

    async def _run():
        return await send_path_to_admins(
            bot,
            small,
            caption="c",
            max_bytes=10_000,
            admin_ids=[111, 222],
        )

    parts, delivered, failed = asyncio.run(_run())
    assert parts == 1
    assert delivered == [222]
    assert len(failed) == 1
    assert "111" in failed[0]
