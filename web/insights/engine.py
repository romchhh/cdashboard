"""Оркестратор генерації маркетингових інсайтів."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from web.insights.config import load_insight_thresholds
from web.insights.postprocess import postprocess_insights
from web.insights.rules import (
    apply_cross_rules,
    apply_general_rules,
    apply_google_ads_rules,
    apply_meta_rules,
    apply_seo_rules,
)
from web.insights.types import InsightContext

SECTION_INSIGHT_CATEGORIES: dict[str, set[str] | None] = {
    "overview": None,
    "insights": None,
    "google_ads": {"google_ads", "cross"},
    "meta": {"meta", "cross"},
    "seo": {"seo", "cross"},
    "business": {"general", "cross"},
    "direct": {"cross"},
    "other": {"cross"},
}

_SIGNAL_SORT = {
    "risk": 0,
    "anomaly": 1,
    "opportunity": 2,
    "positive": 3,
    "neutral": 4,
}


def _sort_key(item: dict[str, Any]) -> tuple:
    st = item.get("signal_type") or "neutral"
    trend = item.get("trend") or "neutral"
    neg_boost = 0 if trend in ("down", "warn") or st == "risk" else 1
    return (
        neg_boost,
        _SIGNAL_SORT.get(st, 5),
        -float(item.get("impact_score") or 0),
        -int(item.get("priority") or 0),
        item.get("id") or "",
    )


def build_marketing_insights(
    *,
    overview_rows: list[dict[str, Any]],
    orders: dict[str, Any],
    gads: dict[str, Any],
    gads_prev: dict[str, Any],
    meta: dict[str, Any],
    meta_prev: dict[str, Any],
    gsc: dict[str, Any],
    gsc_prev: dict[str, Any],
    ga4: dict[str, Any] | None = None,
    ga4_prev: dict[str, Any] | None = None,
    top_brands: list[dict[str, Any]] | None = None,
    business: dict[str, Any] | None = None,
    period_end: datetime,
    prev_period_end: datetime,
    compare_label: str = "vs попередній період",
    limit: int = 24,
    categories: set[str] | None = None,
) -> list[dict[str, Any]]:
    ctx = InsightContext(
        overview_rows=overview_rows,
        orders=orders,
        gads=gads,
        gads_prev=gads_prev,
        meta=meta,
        meta_prev=meta_prev,
        gsc=gsc,
        gsc_prev=gsc_prev,
        ga4=ga4 or {},
        ga4_prev=ga4_prev or {},
        top_brands=top_brands or [],
        business=business or {},
        period_end=period_end,
        prev_period_end=prev_period_end,
        compare_label=compare_label,
        thresholds=load_insight_thresholds(),
    )

    apply_general_rules(ctx)
    apply_google_ads_rules(ctx)
    apply_meta_rules(ctx)
    apply_seo_rules(ctx)
    apply_cross_rules(ctx)

    items = postprocess_insights(ctx.items)
    if categories:
        items = [i for i in items if i.get("category") in categories]

    items.sort(key=_sort_key)
    return items[:limit]
