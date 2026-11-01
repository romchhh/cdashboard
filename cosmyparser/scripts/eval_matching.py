"""Стенд для оцінки якості пошуку/мапінгу: кілька брендів × N товарів з реального фіда.

Запуск (з кореня проєкту, інтерпретатор з bs4+sqlalchemy):
    python3 cosmyparser/scripts/eval_matching.py "SesDerma,Moroccanoil,BABOR" 8 [out.json]
Друкує по кожному товару: у скількох конкурентів знайдено, і зведення по сайтах/брендах.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "cosmyparser"))

from web import parser_service as ps  # noqa: E402
import catalog_report as cr  # noqa: E402


def pick(catalog: list, brand: str, n: int) -> list:
    rows = [r for r in catalog if (r.get("brand") or "").lower() == brand.lower()]
    seen, uniq = set(), []
    for r in rows:
        pid = r.get("product_id") or r["id"]
        if pid in seen:
            continue
        seen.add(pid)
        uniq.append(r)
    if len(uniq) <= n:
        return uniq
    step = len(uniq) / n
    return [uniq[int(i * step)] for i in range(n)]


def main() -> None:
    brands = [b.strip() for b in sys.argv[1].split(",") if b.strip()]
    per = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    out = Path(sys.argv[3]) if len(sys.argv) > 3 else ROOT / "databases" / "parser" / "eval" / "report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    catalog = ps.load_catalog(force=True)
    subset = []
    for b in brands:
        subset += pick(catalog, b, per)
    print(f"товарів: {len(subset)}")
    report = cr.run_catalog(
        catalog=subset, output_json=str(out), write_excel_file=False, workers=6,
        progress=lambda p: print(f"  {p.get('current')}/{p.get('total')} {p.get('product')}", flush=True)
        if p.get("phase") == "products" and p.get("row") else None,
    )
    summarize(report)


def summarize(report: dict) -> None:
    by_brand = defaultdict(list)
    site_status = defaultdict(Counter)
    for row in report["rows"]:
        by_brand[row["brand"]].append(row)
        for site, s in (row.get("shops") or {}).items():
            site_status[site][s.get("status")] += 1
    print("\n=== по брендах (знайдено / з бренду) ===")
    for brand, rows in by_brand.items():
        dist = Counter(r["found"] for r in rows)
        print(f"{brand:22} товарів {len(rows):2}  розподіл знайдено: {dict(sorted(dist.items()))}"
              f"  ≥3: {sum(v for k, v in dist.items() if k >= 3)}/{len(rows)}")
    print("\n=== по сайтах ===")
    for site, c in site_status.items():
        print(f"{site:14}", dict(c))


if __name__ == "__main__":
    main()
