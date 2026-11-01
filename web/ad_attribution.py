"""Атрибуція доходу за UTM campaign з CRM для ROAS кампаній."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from typing import Any

from bot.database.clients_models import Client, ClientOrder
from web.ad_channel import classify_order_channel

_SKIP_UTM_KEYS = frozenset({"(без utm campaign)", "(без utm campaign)"})


def _norm_campaign(name: str) -> str:
    text = unicodedata.normalize("NFKC", (name or "").strip().lower())
    text = re.sub(r"\s+", " ", text)
    return text


def _campaign_tokens(name: str) -> set[str]:
    text = _norm_campaign(name)
    parts = re.split(r"[/|+&,]+", text)
    tokens: set[str] = set()
    for part in parts:
        token = part.strip()
        if len(token) >= 3 and not token.isdigit():
            tokens.add(token)
        elif re.fullmatch(r"\d{1,2}\.\d{1,2}", token):
            tokens.add(token)
    return tokens


def campaign_match_score(ads_name: str, utm_key: str) -> float:
    """Оцінка збігу назви кампанії Ads і utm_campaign CRM (0–100)."""
    norm_a = _norm_campaign(ads_name)
    norm_u = _norm_campaign(utm_key)
    if not norm_a or not norm_u or norm_u in _SKIP_UTM_KEYS:
        return 0.0
    if norm_a == norm_u:
        return 100.0
    if norm_a in norm_u or norm_u in norm_a:
        shorter = min(len(norm_a), len(norm_u))
        longer = max(len(norm_a), len(norm_u))
        if shorter >= 10 and shorter / longer >= 0.5:
            return 75.0 + 20.0 * (shorter / longer)
        return 0.0
    tokens_a = _campaign_tokens(ads_name)
    tokens_u = _campaign_tokens(utm_key)
    if not tokens_a or not tokens_u:
        return 0.0
    overlap = tokens_a & tokens_u
    if len(overlap) < 2:
        return 0.0
    return min(95.0, 100.0 * len(overlap) / max(len(tokens_a), len(tokens_u)))


def build_campaign_attribution(
    orders: list[tuple[ClientOrder, Client | None]],
    *,
    channel: str,
) -> dict[str, dict[str, float | int]]:
    """Дохід і замовлення по нормалізованій назві utm_campaign."""
    by_campaign: dict[str, dict[str, float | int]] = defaultdict(lambda: {"revenue": 0.0, "orders": 0})
    for order, client in orders:
        if classify_order_channel(order, client) != channel:
            continue
        camp = (order.utm_campaign or getattr(client, "source_utm_campaign", None) or "").strip()
        if not camp:
            camp = "(без UTM campaign)"
        key = _norm_campaign(camp)
        rev = float(order.order_total or 0.0)
        by_campaign[key]["revenue"] = float(by_campaign[key]["revenue"]) + rev
        by_campaign[key]["orders"] = int(by_campaign[key]["orders"]) + 1
    return dict(by_campaign)


def resolve_campaign_attribution(
    campaign_names: list[str],
    attribution: dict[str, dict[str, float | int]],
    *,
    min_score: float = 50.0,
) -> dict[str, tuple[float, int, bool, str | None]]:
    """
    Ексклюзивне зіставлення: кожен utm_campaign — максимум одній кампанії Ads.
    Повертає ads_name → (revenue, orders, matched, utm_key).
    """
    results: dict[str, tuple[float, int, bool, str | None]] = {
        name: (0.0, 0, False, None) for name in campaign_names
    }
    if not attribution or not campaign_names:
        return results

    candidates: list[tuple[float, str, str]] = []
    for ads_name in campaign_names:
        for utm_key, row in attribution.items():
            if utm_key in _SKIP_UTM_KEYS:
                continue
            score = campaign_match_score(ads_name, utm_key)
            if score >= min_score:
                candidates.append((score, ads_name, utm_key))

    candidates.sort(key=lambda item: (-item[0], -int(attribution[item[2]]["orders"])))
    used_utm: set[str] = set()
    used_ads: set[str] = set()
    for _score, ads_name, utm_key in candidates:
        if ads_name in used_ads or utm_key in used_utm:
            continue
        row = attribution[utm_key]
        results[ads_name] = (float(row["revenue"]), int(row["orders"]), True, utm_key)
        used_utm.add(utm_key)
        used_ads.add(ads_name)

    return results


def lookup_campaign_attribution(
    campaign_name: str,
    attribution: dict[str, dict[str, float | int]],
) -> tuple[float, int, bool]:
    """Повертає (revenue, orders, matched) для однієї кампанії Ads."""
    resolved = resolve_campaign_attribution([campaign_name], attribution)
    rev, orders, matched, _ = resolved[campaign_name]
    return rev, orders, matched


def attribution_unassigned_totals(
    attribution: dict[str, dict[str, float | int]],
    resolved: dict[str, tuple[float, int, bool, str | None]],
) -> tuple[int, float]:
    """CRM замовлення/дохід, не прив'язані до жодної кампанії Ads."""
    used_keys = {utm for _rev, _ord, matched, utm in resolved.values() if matched and utm}
    orders = 0
    revenue = 0.0
    for key, row in attribution.items():
        if key in _SKIP_UTM_KEYS or key in used_keys:
            continue
        orders += int(row.get("orders") or 0)
        revenue += float(row.get("revenue") or 0)
    return orders, round(revenue, 2)


def enrich_campaigns_with_attribution(
    campaigns: list[dict[str, Any]],
    attribution: dict[str, dict[str, float | int]],
    *,
    channel_total_revenue: float,
    channel_total_spend: float,
    roas_pct_fn,
    safe_roas_fn,
    safe_cpa_fn,
) -> tuple[list[dict[str, Any]], bool]:
    """
    Додає revenue/ROAS до кампаній.
    Повертає (enriched_campaigns, any_utm_match).
    """
    total_spend = sum(float(c.get("spend") or 0) for c in campaigns) or 1.0
    names = [str(c.get("name") or "") for c in campaigns]
    resolved = resolve_campaign_attribution(names, attribution)
    any_match = False
    result: list[dict[str, Any]] = []

    for c in campaigns:
        sp = float(c.get("spend") or 0)
        name = str(c.get("name") or "")
        rev, orders, matched, _utm = resolved.get(name, (0.0, 0, False, None))
        if matched:
            any_match = True
        else:
            share = sp / total_spend
            rev = round(channel_total_revenue * share, 2)
            orders = int(c.get("conversions") or 0)
        result.append({
            **c,
            "revenue": round(rev, 2),
            "orders": orders,
            "roas_pct": roas_pct_fn(safe_roas_fn(rev, sp)),
            "cpa": safe_cpa_fn(sp, orders) or "—",
            "attribution": "utm" if matched else "estimate",
        })

    return result, any_match


def classify_source_type(source_name: str) -> str:
    """Тип джерела для розділу «Інші джерела» (ТЗ §7)."""
    s = (source_name or "").lower()
    if any(x in s for x in ("email", "newsletter", "розсил", "mailchimp", "esputnik")):
        return "Email"
    if any(x in s for x in ("facebook", "instagram", "telegram", "tiktok", "youtube", "social")):
        return "Органічні соцмережі"
    if any(x in s for x in ("affiliate", "partner", "партнер")):
        return "Партнерські"
    if s in ("direct", "(direct)", "(none)", "—"):
        return "Direct"
    return "Реферальний"


def seo_brand_avg_positions(
    brands: list[dict[str, Any]],
    queries: list[dict[str, Any]],
) -> dict[str, float | None]:
    """Середня позиція GSC-запитів, що містять назву бренду."""
    out: dict[str, float | None] = {}
    for b in brands:
        brand = (b.get("brand") or "").strip().lower()
        if not brand or brand == "без бренду":
            out[b["brand"]] = None
            continue
        positions = [
            float(q["position"])
            for q in queries
            if brand in (q.get("key") or "").lower() and q.get("position")
        ]
        out[b["brand"]] = round(sum(positions) / len(positions), 1) if positions else None
    return out
