#!/usr/bin/env python3
"""Фоновий прогін парсера. Живе окремо від веб-процесу, пише статус у databases/parser.db."""

from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _emergency_fail(brand: str | None, message: str) -> None:
    """Якщо імпорт parser_service впав — все одно зафіксувати error у SQLite."""
    try:
        from web import parser_store as store

        prev = store.load_job() or {}
        store.save_job(
            {
                **prev,
                "state": "error",
                "phase": "error",
                "brand": brand or prev.get("brand"),
                "error": message,
                "pid": os.getpid(),
                "updated_epoch": time.time(),
            }
        )
    except Exception:
        try:
            crash = ROOT / "databases" / "parser" / "jobs" / "crash.log"
            crash.parent.mkdir(parents=True, exist_ok=True)
            crash.write_text(
                f"{time.time()}\t{brand}\t{message}\n",
                encoding="utf-8",
            )
        except OSError:
            pass


def main() -> None:
    brand = (sys.argv[1] if len(sys.argv) > 1 else "").strip() or None
    try:
        from web.parser_service import _publish_status, _run_report_job
    except Exception as exc:
        traceback.print_exc()
        _emergency_fail(brand, f"Не вдалося імпортувати парсер: {exc}")
        raise SystemExit(1) from exc

    # Одразу фіксуємо живий pid, щоб веб не думав що воркер «зупинився» під час імпорту.
    try:
        _publish_status(
            {
                "state": "running",
                "phase": "starting",
                "brand": brand,
                "product": "воркер стартував…",
                "current": 0,
            },
            force=True,
        )
    except Exception as exc:
        traceback.print_exc()
        _emergency_fail(brand, f"Не вдалося записати статус старту: {exc}")
        raise SystemExit(1) from exc

    try:
        _run_report_job(brand=brand)
    except Exception as exc:
        traceback.print_exc()
        try:
            _publish_status(
                {"state": "error", "error": str(exc), "brand": brand, "phase": "error"},
                force=True,
            )
        except Exception:
            _emergency_fail(brand, str(exc))
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
