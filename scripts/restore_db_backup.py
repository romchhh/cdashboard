#!/usr/bin/env python3
"""Відновлення SQLite з zip у databases/backups.

Веб і бот мають бути зупинені: інакше вони одразу перезапишуть файли.
Поточні бази ховаються в databases/backups/pre_restore_YYYYMMDD_HHMMSS/.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _running_app_lines() -> list[str]:
    try:
        out = subprocess.check_output(["ps", "-ax", "-o", "pid=,command="], text=True)
    except (OSError, subprocess.CalledProcessError):
        return []
    found: list[str] = []
    for line in out.splitlines():
        text = line.strip()
        if "main_web.py" in text or "main_bot.py" in text:
            found.append(text)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Відновити finance_bot.db, products.db і cosmy_clients.db з zip-бекапу",
    )
    parser.add_argument(
        "archive",
        nargs="?",
        help="Шлях до cosmy_db_backup_*.zip або до папки з такими архівами",
    )
    parser.add_argument("--latest", action="store_true", help="Взяти найновіший zip у папці бекапів")
    parser.add_argument("--list", action="store_true", help="Лише показати архіви і вийти")
    parser.add_argument("--yes", action="store_true", help="Не питати підтвердження")
    args = parser.parse_args()

    from bot.services.db_backup import (
        backup_dir,
        list_backup_archives,
        restore_databases_from_archive,
    )

    folder = backup_dir()
    chosen: Path | None = None
    if args.archive:
        raw = Path(args.archive)
        if not raw.is_absolute():
            raw = (Path.cwd() / raw).resolve()
        if raw.is_dir():
            archives = list_backup_archives(raw)
            if not archives:
                print(f"У {raw} немає файлів cosmy_db_backup_*.zip")
                return 1
            chosen = archives[0]
        else:
            chosen = raw
    elif args.latest or args.list:
        archives = list_backup_archives(folder)
        if args.list or not args.latest:
            if not archives:
                print(f"У {folder} ще немає архівів cosmy_db_backup_*.zip")
                return 0
            print(f"Бекапи в {folder}:")
            for path in archives:
                print(f"  {path.name}")
            if not args.latest:
                return 0
        if not archives:
            print(f"У {folder} ще немає архівів cosmy_db_backup_*.zip")
            return 1
        chosen = archives[0]
    else:
        archives = list_backup_archives(folder)
        print(f"Папка бекапів: {folder}")
        if not archives:
            print("Архівів cosmy_db_backup_*.zip немає.")
        else:
            print("Останні архіви:")
            for path in archives[:15]:
                print(f"  {path.name}")
        print()
        print("Відновити найновіший:")
        print("  bash scripts/restore_db_backup.sh --latest --yes")
        print("Або конкретний файл:")
        print("  bash scripts/restore_db_backup.sh databases/backups/cosmy_db_backup_YYYYMMDD_HHMMSS.zip --yes")
        return 0

    assert chosen is not None
    if not chosen.is_file():
        print(f"Архів не знайдено: {chosen}")
        return 1

    running = _running_app_lines()
    if running:
        print("Веб або бот ще працюють. Зупиніть їх перед відновленням:")
        print("  bash -c 'source ./start.sh && stop_all'")
        for line in running:
            print(f"  {line}")
        return 1

    print(f"Архів: {chosen}")
    print("Поточні бази буде замінено. Копія до заміни лишиться поруч у pre_restore_*.")
    if not args.yes:
        answer = input("Напишіть YES, щоб продовжити: ").strip()
        if answer != "YES":
            print("Скасовано.")
            return 1

    result = restore_databases_from_archive(chosen)
    print("Відновлено:")
    for row in result.restored:
        print(f"  {row}")
    if result.skipped:
        print("Пропущено:")
        for row in result.skipped:
            print(f"  {row}")
    if result.aside_dir and result.aside_dir.is_dir():
        print(f"Попередні файли: {result.aside_dir}")
    print("Запустіть знову: ./start.sh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
