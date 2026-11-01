#!/usr/bin/env python3
"""Розподіл збігів по бренду в databases/parser.db (KPI coverage)."""
from __future__ import annotations

import re
import sqlite3
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "databases" / "parser.db"


def _scope_for_brand(brand: str) -> str | None:
    slug = re.sub(r"[^a-z0-9]+", "-", brand.lower()).strip("-")
    return slug or None


def distribution(brand: str) -> dict | None:
    if not DB.is_file():
        return None
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    try:
        scope_guess = _scope_for_brand(brand)
        header = con.execute(
            """
            SELECT scope FROM reports
            WHERE partial = 0
              AND (brand = ? COLLATE NOCASE OR scope = ? COLLATE NOCASE)
            ORDER BY saved_at DESC LIMIT 1
            """,
            (brand, scope_guess or brand),
        ).fetchone()
        if not header:
            return None
        scope = header["scope"]
        rows = con.execute(
            "SELECT found FROM report_rows WHERE scope = ? ORDER BY position",
            (scope,),
        ).fetchall()
    finally:
        con.close()
    if not rows:
        return None
    founds = [int(r["found"] or 0) for r in rows]
    ge5 = sum(1 for f in founds if f >= 5)
    ge7 = sum(1 for f in founds if f >= 7)
    ge8 = sum(1 for f in founds if f >= 8)
    return {
        "brand": brand,
        "sku": len(founds),
        "avg": round(sum(founds) / len(founds), 2),
        "median": statistics.median(founds),
        "ge5_pct": round(ge5 / len(founds) * 100, 1),
        "ge7_pct": round(ge7 / len(founds) * 100, 1),
        "ge8_pct": round(ge8 / len(founds) * 100, 1),
    }


def main(argv: list[str]) -> int:
    brands = argv or ["Acnemy", "Medicube", "Babor"]
    print(f"DB: {DB}")
    for brand in brands:
        stats = distribution(brand)
        if not stats:
            print(f"{brand}: немає даних (перевірте parser.db / from_report)")
            continue
        print(
            f"{stats['brand']:12} SKU={stats['sku']:3}  avg={stats['avg']}  "
            f"median={stats['median']}  ≥5={stats['ge5_pct']}%  ≥7={stats['ge7_pct']}%  ≥8={stats['ge8_pct']}%"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
