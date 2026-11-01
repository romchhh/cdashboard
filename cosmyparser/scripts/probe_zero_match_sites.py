#!/usr/bin/env python3
"""Магазини з 0 match у parser.db + живий probe (чи парсер, чи асортимент/таймаути)."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "databases" / "parser.db"
COSMY = ROOT / "cosmyparser"
sys.path.insert(0, str(COSMY))


def db_zero_match_sites() -> list[dict]:
    if not DB.is_file():
        return []
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT s.site,
               SUM(s.status = 'match') AS matches,
               SUM(s.status = 'no_brand') AS no_brand,
               SUM(s.status = 'unavailable') AS unav,
               SUM(s.status = 'not_found') AS nf,
               COUNT(*) AS n
        FROM report_shops s
        JOIN reports rep ON rep.scope = s.scope AND rep.partial = 0
        GROUP BY s.site
        HAVING matches = 0
        ORDER BY (1.0 * no_brand / COUNT(*)) ASC, unav DESC
        """
    ).fetchall()
    con.close()
    out = []
    for r in rows:
        n = int(r["n"])
        out.append({
            "site": r["site"],
            "no_brand_pct": round(100 * int(r["no_brand"]) / n, 1) if n else 0,
            "unav_pct": round(100 * int(r["unav"]) / n, 1) if n else 0,
            "nf_pct": round(100 * int(r["nf"]) / n, 1) if n else 0,
        })
    return out


def live_probe(sites: list[str], tests: list[tuple[str, str | None]]) -> dict[str, list[str]]:
    from cosmy_parse import SITES, clear_brand_presence_cache, search_one_site, set_search_feed_model

    by = {s.name: s for s in SITES}
    clear_brand_presence_cache()
    result: dict[str, list[str]] = {}
    for site_name in sites:
        site = by.get(site_name)
        if not site:
            result[site_name] = ["NO_CONFIG"]
            continue
        cols = []
        for _label, query, model in tests:
            set_search_feed_model(model)
            try:
                r = search_one_site(site, query, our_price=1200)
            except Exception as exc:
                r = {"status": "error", "reason": str(exc)}
            finally:
                set_search_feed_model(None)
            st = r.get("status") or "?"
            if st == "ok":
                cols.append("OK")
            elif st == "no_brand":
                cols.append("no_brand")
            elif st == "unavailable":
                cols.append("DEAD")
            elif st == "not_found":
                cols.append("nf")
            elif st == "low_match":
                m = r.get("match")
                cols.append(f"low{int(float(m)*100)}%" if m else "low")
            else:
                cols.append(st[:10])
        result[site_name] = cols
    return result


def main() -> int:
    zeros = db_zero_match_sites()
    if not zeros:
        print("Немає даних у", DB)
        return 1
    site_names = [z["site"] for z in zeros]
    tests = [
        ("Acnemy", "Acnemy Zitback", "105518"),
        ("Academie", "Academie 12h Radiance Serum 104240", "104240"),
        ("Babor", "Babor Skinovage Moisturizing Serum 08763", "08763"),
    ]
    print(f"parser.db: {len(zeros)} магазинів з 0 match (усі бренди, ~32 звіти)\n")
    print(f"{'Магазин':16} {'no_brand%':>9} {'unav%':>7} {'nf%':>6}  Probe: Acnemy / Academie / Babor")
    print("-" * 72)
    live = live_probe(site_names, tests) if "--live" in sys.argv else {}
    for z in zeros:
        probe = "  (додай --live)" if not live else " / ".join(live.get(z["site"], []))
        print(
            f"{z['site']:16} {z['no_brand_pct']:>8}% {z['unav_pct']:>6}% {z['nf_pct']:>5}%  {probe}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
