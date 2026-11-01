"""Типи та контекст для генерації інсайтів."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from web.ad_channel import CHANNEL_GOOGLE_ADS, CHANNEL_META_ADS, CHANNEL_SEO
from web.insights.utils import channel_row, spend_uah


_CATEGORY_LABELS = {
    "overview": "Загальний маркетинг",
    "general": "Загальний маркетинг",
    "google_ads": "Google Ads",
    "meta": "Meta Ads",
    "seo": "SEO",
    "cross": "Кросканальний аналіз",
    "business": "Бізнес",
    "alert": "Увага",
}

_SIGNAL_PRIORITY = {
    "risk": 4,
    "anomaly": 3,
    "opportunity": 2,
    "positive": 1,
    "neutral": 0,
}


@dataclass
class InsightContext:
    overview_rows: list[dict[str, Any]]
    orders: dict[str, Any]
    gads: dict[str, Any]
    gads_prev: dict[str, Any]
    meta: dict[str, Any]
    meta_prev: dict[str, Any]
    gsc: dict[str, Any]
    gsc_prev: dict[str, Any]
    ga4: dict[str, Any]
    ga4_prev: dict[str, Any]
    top_brands: list[dict[str, Any]]
    business: dict[str, Any]
    period_end: datetime
    prev_period_end: datetime
    compare_label: str
    thresholds: dict[str, float | int]
    items: list[dict[str, Any]] = field(default_factory=list)
    seen_ids: set[str] = field(default_factory=set)
    suppressed_entities: set[str] = field(default_factory=set)

    @property
    def cur(self) -> dict[str, Any]:
        return self.orders.get("current") or {}

    @property
    def prev(self) -> dict[str, Any]:
        return self.orders.get("previous") or {}

    @property
    def revenue(self) -> float:
        return float(self.cur.get("total_revenue") or 0)

    @property
    def prev_revenue(self) -> float:
        return float(self.prev.get("total_revenue") or 0)

    @property
    def orders_count(self) -> int:
        return int(self.cur.get("total_orders") or 0)

    @property
    def prev_orders_count(self) -> int:
        return int(self.prev.get("total_orders") or 0)

    @property
    def g_spend(self) -> float:
        return spend_uah(self.gads, self.period_end)

    @property
    def g_prev_spend(self) -> float:
        return spend_uah(self.gads_prev, self.prev_period_end)

    @property
    def m_spend(self) -> float:
        return spend_uah(self.meta, self.period_end)

    @property
    def m_prev_spend(self) -> float:
        return spend_uah(self.meta_prev, self.prev_period_end)

    @property
    def paid_spend(self) -> float:
        return self.g_spend + self.m_spend

    @property
    def prev_paid_spend(self) -> float:
        return self.g_prev_spend + self.m_prev_spend

    def channel(self, channel_id: str) -> dict[str, Any]:
        return channel_row(self.overview_rows, channel_id)

    @property
    def google_row(self) -> dict[str, Any]:
        return self.channel(CHANNEL_GOOGLE_ADS)

    @property
    def meta_row(self) -> dict[str, Any]:
        return self.channel(CHANNEL_META_ADS)

    @property
    def seo_row(self) -> dict[str, Any]:
        return self.channel(CHANNEL_SEO)

    @property
    def ga4_organic(self) -> dict[str, Any]:
        return self.ga4.get("organic") or {}

    @property
    def ga4_organic_prev(self) -> dict[str, Any]:
        return self.ga4_prev.get("organic") or {}

    def threshold(self, key: str) -> float | int:
        return self.thresholds.get(key, 0)

    def add(self, item: dict[str, Any]) -> None:
        iid = str(item.get("id") or "")
        if not iid or iid in self.seen_ids:
            return
        self.seen_ids.add(iid)
        self.items.append(item)


def make_insight(
    *,
    id: str,
    category: str,
    title: str,
    what_happened: str,
    why: list[str] | str | None = None,
    impact: str | None = None,
    action: str | None = None,
    trend: str = "neutral",
    impact_score: float = 50,
    signal_type: str | None = None,
) -> dict[str, Any]:
    why_list = [why] if isinstance(why, str) else [w for w in (why or []) if w]
    text_parts = [what_happened]
    if why_list:
        text_parts.append("Причини: " + "; ".join(why_list[:3]))
    if impact:
        text_parts.append(impact)
    if action:
        text_parts.append(f"Перевірити: {action}")
    text = ". ".join(text_parts)
    st = signal_type or (
        "risk" if trend in ("down", "warn") else "positive" if trend == "up" else "opportunity"
    )
    priority = int(impact_score) + _SIGNAL_PRIORITY.get(st, 0) * 25
    chip_text = what_happened.strip()
    if len(chip_text) > 96:
        chip_text = chip_text[:93].rstrip() + "…"
    return {
        "id": id,
        "category": category,
        "category_label": _CATEGORY_LABELS.get(category, category),
        "title": title,
        "what_happened": what_happened,
        "why": why_list,
        "impact": impact or "",
        "action": action or "",
        "text": text,
        "trend": trend,
        "signal_type": st,
        "impact_score": round(impact_score, 1),
        "priority": priority,
        "chip": chip_text,
    }
