"""
Одноразова міграція: копіює таблиці clients, client_orders, client_order_lines
із основної БД (DATABASE_URL) у окрему БД клієнтів (CLIENTS_DATABASE_URL).

Запуск із кореня проєкту:
  python -m bot.database.migrate_clients_sqlite

Перед копіюванням дані в цільовій БД у цих таблицях видаляються (повна заміна).
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path
from urllib.parse import unquote


def _sqlite_path(url: str) -> Path:
    """sqlite+aiosqlite:///relative/path.db або ...:///absolute/path.db"""
    prefix = "sqlite+aiosqlite:///"
    if not url.startswith(prefix):
        raise ValueError(f"Очікується SQLite async URL, отримано: {url[:40]}…")
    rest = unquote(url[len(prefix) :].lstrip("/"))
    if url.startswith(prefix + "/") and not url.startswith(prefix + "//"):
        return Path(rest)
    if url.startswith(prefix + "//"):
        inner = url[len(prefix + "//") :]
        if inner.startswith("/"):
            return Path(unquote(inner))
        return (Path.cwd() / unquote(inner)).resolve()
    return (Path.cwd() / rest).resolve()


def main() -> int:
    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo))
    import os

    os.chdir(repo)
    from dotenv import load_dotenv

    load_dotenv()
    from config import CLIENTS_DATABASE_URL, DATABASE_URL

    src_path = _sqlite_path(DATABASE_URL)
    dst_path = _sqlite_path(CLIENTS_DATABASE_URL)

    if not src_path.is_file():
        print(f"Джерело не знайдено: {src_path}")
        return 1
    dst_path.parent.mkdir(parents=True, exist_ok=True)

    async def ensure_schema():
        from bot.database.clients_engine import init_clients_db

        await init_clients_db()

    asyncio.run(ensure_schema())

    con = sqlite3.connect(str(dst_path))
    try:
        cur = con.cursor()
        cur.execute("ATTACH DATABASE ? AS src", (str(src_path),))
        cur.execute(
            "SELECT 1 FROM src.sqlite_master WHERE type='table' AND name='clients' LIMIT 1"
        )
        if not cur.fetchone():
            print("У джерелі немає таблиці clients — нічого копіювати.")
            return 0

        print(f"Копіювання: {src_path} → {dst_path}")
        con.execute("PRAGMA foreign_keys = OFF")
        for tbl in ("client_order_lines", "client_orders", "clients"):
            con.execute(f"DELETE FROM {tbl}")
        con.execute("INSERT INTO clients SELECT * FROM src.clients")
        n_c = con.execute("SELECT changes()").fetchone()[0]
        con.execute(
            """
            INSERT INTO client_orders (
                id, order_id, client_customer_id, opencart_customer_id, order_date, order_total,
                order_status_id, order_status, referer, utm_source, utm_medium, utm_campaign, utm_content, utm_term, updated_at
            )
            SELECT
                id, order_id, client_customer_id, opencart_customer_id, order_date, order_total,
                NULL AS order_status_id, NULL AS order_status, referer, utm_source, utm_medium, utm_campaign, utm_content, utm_term, updated_at
            FROM src.client_orders
            """
        )
        n_o = con.execute("SELECT changes()").fetchone()[0]
        con.execute("INSERT INTO client_order_lines SELECT * FROM src.client_order_lines")
        n_l = con.execute("SELECT changes()").fetchone()[0]
        con.commit()
        print(f"Готово: clients={n_c}, client_orders={n_o}, client_order_lines={n_l}")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
