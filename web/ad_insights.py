"""Корисні інсайти для розділу «Аналіз реклами» — структуровані правила з порогами."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from web.insights.engine import build_marketing_insights


def build_ad_insight_feed(
    *,
    overview_rows: list[dict[str, Any]],
    orders: dict[str, Any],
    gads: dict[str, Any],
    gads_prev: dict[str, Any],
    meta: dict[str, Any],
    meta_prev: dict[str, Any],
    gsc: dict[str, Any],
    gsc_prev: dict[str, Any],
    top_brands: list[dict[str, Any]],
    period_end: datetime,
    prev_period_end: datetime,
    compare_label: str = "vs попередній період",
    limit: int = 20,
    ga4: dict[str, Any] | None = None,
    ga4_prev: dict[str, Any] | None = None,
    business: dict[str, Any] | None = None,
    categories: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Збірка стрічки інсайтів (без дублікатів, з пріоритетом за бізнес-впливом)."""
    return build_marketing_insights(
        overview_rows=overview_rows,
        orders=orders,
        gads=gads,
        gads_prev=gads_prev,
        meta=meta,
        meta_prev=meta_prev,
        gsc=gsc,
        gsc_prev=gsc_prev,
        ga4=ga4,
        ga4_prev=ga4_prev,
        top_brands=top_brands,
        business=business,
        period_end=period_end,
        prev_period_end=prev_period_end,
        compare_label=compare_label,
        limit=limit,
        categories=categories,
    )


def insight_chips_from_feed(feed: list[dict[str, Any]], limit: int = 3) -> list[dict[str, str]]:
    """Короткі чипи для футера розділів (без дублікатів)."""
    chips: list[dict[str, str]] = []
    seen_text: set[str] = set()
    for item in feed:
        title = (item.get("title") or "").strip()
        body = (item.get("chip") or item.get("what_happened") or "").strip()
        if title and body and not body.startswith(title):
            text = f"{title}: {body}"
        else:
            text = body or title or (item.get("text") or "").strip()
        if len(text) > 140:
            text = text[:137].rstrip() + "…"
        if not text or text in seen_text:
            continue
        seen_text.add(text)
        trend = item.get("trend") or "neutral"
        if trend == "warn":
            trend = "down"
        elif trend not in ("up", "down"):
            trend = "down" if item.get("signal_type") == "risk" else "up"
        chips.append({"text": text, "trend": trend})
        if len(chips) >= limit:
            break
    return chips
