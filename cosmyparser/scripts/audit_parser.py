#!/usr/bin/env python3
"""Швидкий аудит парсера: матчинг + (опційно) живий пошук.

  .venv/bin/python cosmyparser/scripts/audit_parser.py
  .venv/bin/python cosmyparser/scripts/audit_parser.py --live
  .venv/bin/python cosmyparser/scripts/audit_parser.py --live --workers 8
"""
from __future__ import annotations

import argparse
import collections
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COSMY = ROOT / "cosmyparser"
sys.path.insert(0, str(COSMY))

LIVE_SKUS = [
    ("Acnemy Zitback Спрей для тіла з акне", "105518"),
    ("Medik8 Total Moisture Daily Facial Cream", None),
    ("Rejuran Essential Hydration Cream 50 ml", None),
    ("Valmont Hydra3 Charging Cream", None),
]


def run_match_cases() -> int:
    proc = subprocess.run(
        [sys.executable, str(COSMY / "scripts" / "match_cases.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    print(proc.stdout.strip() or proc.stderr.strip())
    return proc.returncode


def run_live(*, workers: int) -> int:
    from cosmy_parse import (  # noqa: WPS433
        SITES,
        clear_brand_presence_cache,
        clear_scrape_runtime_caches,
        search_all,
        set_search_feed_model,
    )

    totals: collections.Counter[str] = collections.Counter()
    for query, model in LIVE_SKUS:
        clear_scrape_runtime_caches()
        clear_brand_presence_cache()
        set_search_feed_model(model)
        try:
            payload = search_all(query, max_workers=workers)
        finally:
            set_search_feed_model(None)
        by_st: collections.Counter[str] = collections.Counter()
        for row in payload.get("results") or []:
            by_st["ok"] += 1
        for row in payload.get("misses") or []:
            by_st[row.get("status") or "?"] += 1
        totals.update(by_st)
        print(f"\n«{query[:56]}»")
        print(f"  found={payload.get('found')} / {len(SITES)}  {dict(by_st)}")
        low = [
            (m.get("site"), round(float(m.get("match") or 0), 2), (m.get("best_title") or "")[:42])
            for m in payload.get("misses") or []
            if m.get("status") == "low_match"
        ]
        if low:
            print("  low_match:", low[:6])

    print("\n--- Сумарно по 4 SKU ---")
    print(dict(totals))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="живий search_all (мережа)")
    parser.add_argument("--workers", type=int, default=8, help="паралельність магазинів")
    args = parser.parse_args()

    code = run_match_cases()
    if code != 0:
        return code
    if args.live:
        return run_live(workers=max(1, min(args.workers, 12)))
    print("\nДля живого аудиту: audit_parser.py --live --workers 8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
