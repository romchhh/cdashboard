#!/usr/bin/env python3
"""Діагностика пошуку по всіх магазинах SITES (один SKU).

Приклад:
  .venv/bin/python cosmyparser/scripts/probe_search.py "Acnemy Zitback"
  .venv/bin/python cosmyparser/scripts/probe_search.py --brand-catalog
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
COSMY = ROOT / "cosmyparser"
if str(COSMY) not in sys.path:
    sys.path.insert(0, str(COSMY))

from cosmy_parse import (  # noqa: E402
    SITES,
    clear_brand_presence_cache,
    search_one_site,
    set_search_feed_model,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe competitor search for one query")
    parser.add_argument("query", nargs="?", default="Acnemy Zitback")
    parser.add_argument("--model", default="", help="product.model from feed")
    parser.add_argument("--our-price", type=float, default=None)
    parser.add_argument("--only", nargs="*", help="site names subset")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    clear_brand_presence_cache()
    set_search_feed_model(args.model or None)
    sites = [s for s in SITES if not args.only or s.name in args.only]
    ok = nf = dead = nob = 0
    print(f"Query: «{args.query}» · sites: {len(sites)}\n" + "-" * 72)
    try:
        for site in sites:
            item = search_one_site(
                site,
                args.query,
                debug=args.debug,
                our_price=args.our_price,
            )
            st = item.get("status") or "?"
            if st == "ok":
                ok += 1
                price = item.get("price")
                print(f"  OK   {site.name:16} {item.get('match', 0):.0%}  {price} ₴  {item.get('title', '')[:50]}")
            elif st == "no_brand":
                nob += 1
                print(f"  NOBR {site.name:16} {item.get('reason', '')[:60]}")
            elif st in ("unavailable", "error"):
                dead += 1
                print(f"  DEAD {site.name:16} {item.get('reason', st)[:60]}")
            else:
                nf += 1
                extra = item.get("best_title") or item.get("reason") or ""
                print(f"  {st:4} {site.name:16} {str(extra)[:60]}")
    finally:
        set_search_feed_model(None)

    print("-" * 72)
    print(f"match={ok}  not_found/low={nf}  unavailable={dead}  no_brand={nob}")
    return 0 if dead < len(sites) else 1


if __name__ == "__main__":
    raise SystemExit(main())
