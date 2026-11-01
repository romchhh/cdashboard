"""Перевірка цілісності метрик Google Search Console."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from web.integrations.google_search_console import fetch_gsc_stats


def validate_gsc_payload(gsc: dict[str, Any], *, tolerance: float = 0.01) -> list[str]:
    issues: list[str] = []
    clicks = int(gsc.get("clicks") or 0)
    impressions = int(gsc.get("impressions") or 0)

    daily_clicks = sum(int(d.get("clicks") or 0) for d in gsc.get("daily") or [])
    daily_impr = sum(int(d.get("impressions") or 0) for d in gsc.get("daily") or [])

    def _check(label: str, total: float, parts: float) -> None:
        if total <= 0 and parts <= 0:
            return
        if abs(total - parts) > max(tolerance, total * tolerance):
            issues.append(f"{label}: total={total} vs parts={parts}")

    _check("clicks vs daily", clicks, daily_clicks)
    _check("impressions vs daily", impressions, daily_impr)

    if impressions > 0 and clicks > 0 and gsc.get("ctr") is not None:
        calc_ctr = round(100.0 * clicks / impressions, 2)
        api_ctr = float(gsc["ctr"])
        if abs(calc_ctr - api_ctr) > 0.15:
            issues.append(f"ctr mismatch: api={api_ctr}% calc={calc_ctr}%")

    return issues


def run_live_gsc_validation(days: int = 30) -> dict[str, Any]:
    from mono.api import now_kyiv

    end = now_kyiv()
    start = end - timedelta(days=days)
    gsc = fetch_gsc_stats(start, end, use_cache=False)
    return {
        "period": {"from": start.date().isoformat(), "to": end.date().isoformat()},
        "metrics": {
            "clicks": gsc.get("clicks"),
            "impressions": gsc.get("impressions"),
            "ctr": gsc.get("ctr"),
            "position": gsc.get("position"),
            "queries": len(gsc.get("queries") or []),
            "pages": len(gsc.get("pages") or []),
            "daily_rows": len(gsc.get("daily") or []),
        },
        "issues": validate_gsc_payload(gsc),
        "error": gsc.get("error"),
    }
