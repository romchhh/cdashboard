"""Перевірка цілісності метрик Google Ads (API ↔ звіт)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from web.integrations.google_ads import fetch_google_ads_stats


def validate_google_ads_payload(gads: dict[str, Any], *, tolerance: float = 0.05) -> list[str]:
    """Повертає список попереджень, якщо агрегати не сходяться."""
    issues: list[str] = []
    spend = float(gads.get("spend") or 0)
    conversions = float(gads.get("conversions") or 0)
    conv_value = float(gads.get("conversions_value") or 0)

    camp_spend = sum(float(c.get("spend") or 0) for c in gads.get("campaigns") or [])
    camp_conv = sum(float(c.get("conversions") or 0) for c in gads.get("campaigns") or [])
    camp_value = sum(float(c.get("conversion_value") or 0) for c in gads.get("campaigns") or [])

    daily_spend = sum(float(d.get("spend") or 0) for d in gads.get("daily") or [])
    daily_conv = sum(float(d.get("conversions") or 0) for d in gads.get("daily") or [])
    daily_value = sum(float(d.get("conversion_value") or 0) for d in gads.get("daily") or [])

    type_spend = sum(float(t.get("spend") or 0) for t in gads.get("spend_by_type") or [])

    def _check(label: str, total: float, parts: float) -> None:
        if total <= 0 and parts <= 0:
            return
        if abs(total - parts) > max(tolerance, total * tolerance):
            issues.append(f"{label}: total={total:.2f} vs parts={parts:.2f}")

    _check("spend vs campaigns", spend, camp_spend)
    _check("spend vs daily", spend, daily_spend)
    _check("spend vs type", spend, type_spend)
    _check("conversions vs campaigns", conversions, camp_conv)
    _check("conversions vs daily", conversions, daily_conv)
    _check("conversions_value vs campaigns", conv_value, camp_value)
    _check("conversions_value vs daily", conv_value, daily_value)

    if conversions > 0 and spend > 0:
        cpa = gads.get("cost_per_conversion")
        calc_cpa = spend / conversions
        if cpa and abs(float(cpa) - calc_cpa) > 0.05:
            issues.append(f"cpa mismatch: api={cpa} calc={calc_cpa:.4f}")

    return issues


def run_live_google_ads_validation(days: int = 30) -> dict[str, Any]:
    from mono.api import now_kyiv

    end = now_kyiv()
    start = end - timedelta(days=days)
    gads = fetch_google_ads_stats(start, end, use_cache=False)
    return {
        "period": {"from": start.date().isoformat(), "to": end.date().isoformat()},
        "metrics": {
            "spend": gads.get("spend"),
            "conversions": gads.get("conversions"),
            "conversions_value": gads.get("conversions_value"),
            "clicks": gads.get("clicks"),
            "impressions": gads.get("impressions"),
            "currency": gads.get("currency"),
            "campaigns": len(gads.get("campaigns") or []),
        },
        "issues": validate_google_ads_payload(gads),
        "error": gads.get("error"),
    }
