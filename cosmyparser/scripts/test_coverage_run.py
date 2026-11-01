#!/usr/bin/env python3
"""Порівняння свіжого прогону з останнім збереженим звітом у parser.db."""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COSMY = ROOT / "cosmyparser"
sys.path.insert(0, str(COSMY))

from catalog_report import (  # noqa: E402
    _shop_needs_retry,
    build_row,
    merge_search_payload,
)
from cosmy_parse import (  # noqa: E402
    RETRY_SEARCH_DEADLINE,
    SITE_SEARCH_WORKERS,
    clear_brand_presence_cache,
    clear_scrape_runtime_caches,
    search_all,
    set_search_feed_model,
)

DB = ROOT / "databases" / "parser.db"


def old_row_stats(scope: str, position: int) -> dict:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT found, query, model, our_price FROM report_rows WHERE scope=? AND position=?",
        (scope, position),
    ).fetchone()
    shops = con.execute(
        "SELECT site, status FROM report_shops WHERE scope=? AND row_position=?",
        (scope, position),
    ).fetchall()
    con.close()
    if not row:
        return {}
    st = {s["site"]: s["status"] for s in shops}
    return {
        "found": int(row["found"] or 0),
        "unavailable": sum(1 for v in st.values() if v == "unavailable"),
        "query": row["query"],
        "model": row["model"],
        "our_price": row["our_price"],
    }


def run_one(product: dict, *, with_retry: bool) -> dict:
    set_search_feed_model(product.get("model"))
    try:
        payload = search_all(
            product["query"],
            our_price=product.get("our_price"),
            max_workers=4,
        )
        if with_retry:
            for miss in list(payload.get("misses") or []):
                site = miss.get("site")
                if not site or not _shop_needs_retry({"status": miss.get("status"), "reason": miss.get("reason")}):
                    continue
                patch = search_all(
                    product["query"],
                    our_price=product.get("our_price"),
                    deadline=RETRY_SEARCH_DEADLINE,
                    max_workers=1,
                    site_names=(site,),
                )
                payload = merge_search_payload(payload, patch)
        row = build_row(product, payload)
    finally:
        set_search_feed_model(None)
    shops = row.get("shops") or {}
    return {
        "found": row.get("found") or 0,
        "unavailable": sum(1 for i in shops.values() if i.get("status") == "unavailable"),
        "match_sites": [s for s, i in shops.items() if i.get("status") == "match"],
    }


def main() -> int:
    scope = "acnemy"
    positions = [int(x) for x in (sys.argv[1:] or ["0", "1", "2"])]
    SITE_SEARCH_WORKERS = 4  # noqa: F841 — patched in cosmy_parse module below
    import cosmy_parse as cp

    cp.SITE_SEARCH_WORKERS = 4

    clear_brand_presence_cache()
    print(f"Тест {len(positions)} SKU (scope={scope}), worker-like cache (HTTP clear кожні 5 SKU)\n")
    print(f"{'#':>2}  {'found':^11}  {'unav':^11}  query")
    print(f"{'':>2}  {'old→new':^11}  {'old→new':^11}")
    print("-" * 88)

    totals = {"old_f": 0, "new_f": 0, "old_u": 0, "new_u": 0}
    t_start = time.time()

    for n, pos in enumerate(positions):
        old = old_row_stats(scope, pos)
        if not old:
            print(f"{pos}: немає в БД")
            continue
        product = {
            "id": pos,
            "query": old["query"],
            "model": old.get("model"),
            "our_price": old.get("our_price"),
            "name": old["query"][:60],
        }
        t0 = time.time()
        new = run_one(product, with_retry=True)
        sec = time.time() - t0

        if (n + 1) % 5 == 0:
            clear_scrape_runtime_caches()

        totals["old_f"] += old["found"]
        totals["new_f"] += new["found"]
        totals["old_u"] += old["unavailable"]
        totals["new_u"] += new["unavailable"]

        fq = f"{old['found']}→{new['found']}"
        uq = f"{old['unavailable']}→{new['unavailable']}"
        mark = " ✓" if new["found"] > old["found"] or new["unavailable"] < old["unavailable"] else ""
        if new["found"] < old["found"] and new["unavailable"] >= old["unavailable"]:
            mark = " ↓"
        print(f"{pos:2}  {fq:^11}  {uq:^11}  {old['query'][:52]}  ({sec:.0f}s){mark}")

    elapsed = time.time() - t_start
    print("-" * 88)
    print(
        f"Σ found {totals['old_f']}→{totals['new_f']} ({totals['new_f'] - totals['old_f']:+.0f}) · "
        f"unavailable {totals['old_u']}→{totals['new_u']} ({totals['new_u'] - totals['old_u']:+.0f}) · "
        f"{elapsed:.0f}s wall"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
