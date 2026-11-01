#!/usr/bin/env python3
"""Аудит UTM → канал по БД CRM Cosmy. Запуск: PYTHONPATH=. python scripts/audit_utm_channels.py"""

from __future__ import annotations

import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from web.ad_analytics import build_utm_channel_audit, _order_utm_fields
from web.ad_channel import CHANNEL_LABELS_UA, classify_order_channel

DB = ROOT / "databases" / "cosmy_clients.db"


def main() -> None:
    if not DB.is_file():
        print(f"DB not found: {DB}")
        sys.exit(1)

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT o.*, c.source_category, c.source_raw, c.source_utm_source, c.source_utm_medium,
               c.source_utm_campaign, c.source_referrer
        FROM client_orders o
        LEFT JOIN clients c ON c.customer_id = o.client_customer_id
        WHERE o.order_status_id IN (24, 26)
        """
    ).fetchall()
    pairs = []
    for r in rows:
        order = SimpleNamespace(**{k: r[k] for k in r.keys() if k in (
            "utm_source", "utm_medium", "utm_campaign", "referer", "order_total",
        )})
        client = SimpleNamespace(
            source_category=r["source_category"],
            source_raw=r["source_raw"],
            source_utm_source=r["source_utm_source"],
            source_utm_medium=r["source_utm_medium"],
            source_utm_campaign=r["source_utm_campaign"],
            source_referrer=r["source_referrer"],
        )
        pairs.append((order, client))

    print(f"Успішних замовлень: {len(pairs)}\n")

    by_ch: dict[str, float] = defaultdict(float)
    for order, client in pairs:
        ch = classify_order_channel(order, client)
        by_ch[ch] += float(order.order_total or 0)

    print("=== Дохід за каналами (після правил) ===")
    for ch, rev in sorted(by_ch.items(), key=lambda x: -x[1]):
        print(f"  {CHANNEL_LABELS_UA.get(ch, ch):24} {rev:>12,.0f} ₴")

    print("\n=== Топ UTM (дохід) ===")
    utm_groups: dict[tuple[str, str, str], dict] = defaultdict(lambda: {"rev": 0.0, "n": 0, "ch": ""})
    for order, client in pairs:
        ch = classify_order_channel(order, client)
        us, um, uc = _order_utm_fields(order, client)
        key = (us, um, uc)
        utm_groups[key]["rev"] += float(order.order_total or 0)
        utm_groups[key]["n"] += 1
        utm_groups[key]["ch"] = ch
    for (us, um, uc), g in sorted(utm_groups.items(), key=lambda x: -x[1]["rev"])[:25]:
        print(
            f"  {g['rev']:>10,.0f} ₴ | {g['n']:>4} | {us} / {um}"
            f" | {uc[:40] if uc != '—' else '—'} → {CHANNEL_LABELS_UA.get(g['ch'], g['ch'])}"
        )

    audit = build_utm_channel_audit(pairs, min_revenue=500, limit=20)
    print(f"\n=== Аудит (≥500 ₴, {len(audit)} рядків) ===")
    for row in audit:
        print(
            f"  {row['revenue']:>10,.0f} ₴ | {row['orders']:>3} | {row['utm_source']}/{row['utm_medium']}"
            f" → {row['channel_label']} | {row['warning']}"
        )


if __name__ == "__main__":
    main()
