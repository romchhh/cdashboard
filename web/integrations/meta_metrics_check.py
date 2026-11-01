"""Перевірка цілісності метрик Meta Ads (API ↔ звіт)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from web.integrations.meta_ads import fetch_meta_ads_stats, parse_meta_purchase_metrics


def validate_meta_payload(meta: dict[str, Any], *, tolerance: float = 0.05) -> list[str]:
    """Повертає список попереджень, якщо агрегати не сходяться."""
    issues: list[str] = []
    spend = float(meta.get("spend") or 0)
    purchases = int(meta.get("purchases") or 0)
    purchase_value = float(meta.get("purchase_value") or 0)

    camp_spend = sum(float(c.get("spend") or 0) for c in meta.get("campaigns") or [])
    camp_purchases = sum(int(c.get("purchases") or 0) for c in meta.get("campaigns") or [])
    camp_value = sum(float(c.get("purchase_value") or 0) for c in meta.get("campaigns") or [])

    daily_spend = sum(float(d.get("spend") or 0) for d in meta.get("daily") or [])
    daily_purchases = sum(int(d.get("purchases") or 0) for d in meta.get("daily") or [])
    daily_value = sum(float(d.get("purchase_value") or 0) for d in meta.get("daily") or [])

    place_spend = sum(float(p.get("spend") or 0) for p in meta.get("placements") or [])

    def _check(label: str, total: float, parts: float) -> None:
        if total <= 0 and parts <= 0:
            return
        if abs(total - parts) > max(tolerance, total * tolerance):
            issues.append(f"{label}: total={total:.2f} vs parts={parts:.2f}")

    _check("spend vs campaigns", spend, camp_spend)
    _check("spend vs daily", spend, daily_spend)
    _check("spend vs placements", spend, place_spend)
    _check("purchases vs campaigns", purchases, camp_purchases)
    _check("purchases vs daily", purchases, daily_purchases)
    _check("purchase_value vs campaigns", purchase_value, camp_value)
    _check("purchase_value vs daily", purchase_value, daily_value)

    roas = meta.get("purchase_roas")
    if spend > 0 and purchase_value > 0 and roas:
        calc = purchase_value / spend
        if abs(calc - float(roas)) > 0.05:
            issues.append(f"purchase_roas mismatch: api={roas} calc={calc:.4f}")

    if purchases > 0 and spend > 0:
        cpa = meta.get("cost_per_purchase")
        calc_cpa = spend / purchases
        if cpa and abs(float(cpa) - calc_cpa) > 0.05:
            issues.append(f"cpa mismatch: api={cpa} calc={calc_cpa:.4f}")

    return issues


def run_live_meta_validation(days: int = 30) -> dict[str, Any]:
    from mono.api import now_kyiv

    end = now_kyiv()
    start = end - timedelta(days=days)
    meta = fetch_meta_ads_stats(start, end, use_cache=False)
    return {
        "period": {"from": start.date().isoformat(), "to": end.date().isoformat()},
        "metrics": {
            "spend": meta.get("spend"),
            "purchase_value": meta.get("purchase_value"),
            "purchases": meta.get("purchases"),
            "purchase_roas": meta.get("purchase_roas"),
            "impressions": meta.get("impressions"),
            "reach": meta.get("reach"),
            "currency": meta.get("currency"),
            "campaigns": len(meta.get("campaigns") or []),
            "placements": len(meta.get("placements") or []),
        },
        "issues": validate_meta_payload(meta),
        "error": meta.get("error"),
    }
