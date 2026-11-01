#!/usr/bin/env python3
"""Ручний запуск резервної копії БД (без підняття бота)."""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Резервна копія SQLite → zip → Telegram адмінам")
    parser.add_argument(
        "--no-telegram",
        action="store_true",
        help="Лише створити локальний архів у databases/backups",
    )
    args = parser.parse_args()

    from bot.services.db_backup import run_database_backup

    result = await run_database_backup(notify_telegram=not args.no_telegram)
    if result.archive_path:
        print(f"Архів: {result.archive_path}")
    if result.backed_up:
        print("БД:", ", ".join(result.backed_up))
    if result.telegram_delivered_to:
        print("Telegram OK:", result.telegram_delivered_to)
    if result.telegram_failed:
        print("Telegram пропущено:", "; ".join(result.telegram_failed))
    hard_errors = [e for e in result.errors if not str(e).startswith("Telegram:")]
    if hard_errors:
        print("Помилки:", "; ".join(hard_errors))
        return 1
    if result.errors:
        print("Увага:", "; ".join(result.errors))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
