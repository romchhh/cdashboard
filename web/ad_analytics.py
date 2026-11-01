"""
Аналітика замовлень для розділу «Аналіз реклами»:
дохід, конверсії, бренди, динаміка — з БД клієнтів Cosmy.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.clients_models import Client, ClientOrder, ClientOrderLine
from config import CLIENTS_EXPORT_EXCLUDED_CUSTOMER_IDS
from mono.api import now_kyiv
from web.ad_channel import (
    ALL_CHANNELS,
    CHANNEL_DIRECT,
    CHANNEL_EMAIL,
    CHANNEL_GOOGLE_ADS,
    CHANNEL_META_ADS,
    CHANNEL_OTHER,
    CHANNEL_REFERRAL,
    CHANNEL_SEO,
    OVERVIEW_CHANNELS,
    channel_label_ua,
    classify_order_channel,
    looks_like_google_ads_campaign,
    other_channel_group,
    utm_classification_rules_ua,
)
from web.client_marketing import (
    _clients_orders_exclude_clause,
    _parse_date_param,
    _sql_outcome_is_success,
    merge_city_revenue_rows,
    resolve_monthly_chart_order_range,
)
from web.ad_attribution import build_campaign_attribution
from web.integrations.crm_analytics_cache import (
    business_cache_key,
    cache_get as crm_cache_get,
    cache_set as crm_cache_set,
    orders_cache_key,
)

_BUSINESS_CATEGORY_LABELS = {
    "ads": "Реклама (Cosmy feed)",
    "organic": "Органіка (Cosmy feed)",
    "unknown": "Невідомо (Cosmy feed)",
    "direct": "Прямий (Cosmy feed)",
    "email": "Email (Cosmy feed)",
    "referral": "Реферальний (Cosmy feed)",
    "інше": "Інше",
    "other": "Інше",
}


def business_category_label(raw: str | None) -> str:
    """Людський підпис для source_category з Cosmy (не маркетинговий канал UTM)."""
    key = (raw or "").strip().lower()
    if not key:
        return "Інше"
    return _BUSINESS_CATEGORY_LABELS.get(key, (raw or "").strip())


def _order_utm_fields(order: ClientOrder, client: Client | None) -> tuple[str, str, str]:
    """UTM замовлення з fallback на перше джерело клієнта (як у classify_order_channel)."""
    us = (order.utm_source or getattr(client, "source_utm_source", None) or "").strip()
    um = (order.utm_medium or getattr(client, "source_utm_medium", None) or "").strip()
    uc = (order.utm_campaign or getattr(client, "source_utm_campaign", None) or "").strip()
    return us or "—", um or "—", uc or "—"


def _rollup_revenue_rows(
    data: dict[str, dict[str, float | int]],
    *,
    label_fn,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    total = sum(float(v.get("revenue") or 0) for v in data.values()) or 1.0
    rows = sorted(data.items(), key=lambda x: float(x[1].get("revenue") or 0), reverse=True)
    if limit:
        rows = rows[:limit]
    out: list[dict[str, Any]] = []
    for key, stats in rows:
        rev = float(stats.get("revenue") or 0)
        out.append({
            "name": key,
            "label": label_fn(key),
            "channel": key,
            "revenue": round(rev, 2),
            "orders": int(stats.get("orders") or 0),
            "share_pct": round(100 * rev / total, 1),
        })
    return out


def _utm_audit_warning(us: str, um: str, uc: str, channel: str) -> str | None:
    """Попередження для UTM, які варто перевірити вручну."""
    um_l = um.casefold() if um and um != "—" else ""
    us_l = us.casefold() if us and us != "—" else ""
    if channel == CHANNEL_OTHER and um_l in ("cpc", "paid", "ppc"):
        return "Платний medium (cpc/paid), але source не google/meta — перевірте кампанію"
    if channel == CHANNEL_SEO and looks_like_google_ads_campaign(uc):
        return "Назва кампанії схожа на Google Ads, але канал SEO"
    if channel == CHANNEL_REFERRAL and us_l in ("sms", "mailings", "mailing"):
        return "Розсилка (sms/mailings) — очікується Email, не реферал"
    if channel == CHANNEL_DIRECT and um_l in ("cpc", "cpm", "paid", "ppc"):
        return "Платний medium при source=direct — перевірте UTM"
    if channel == CHANNEL_META_ADS and us_l == "direct":
        return "Meta medium (cpm) при source=direct — типова особливість Cosmy"
    return None


def build_utm_channel_audit(
    orders_pairs: list[tuple[ClientOrder, Client | None]],
    *,
    min_revenue: float = 500.0,
    limit: int = 15,
) -> list[dict[str, Any]]:
    """Комбінації UTM з попередженнями для ручної перевірки."""
    groups: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}

    for order, client in orders_pairs:
        ch = classify_order_channel(order, client)
        us, um, uc = _order_utm_fields(order, client)
        note = _utm_audit_warning(us, um, uc, ch)
        if not note:
            continue
        key = (us, um, uc if uc != "—" else "", ch, note)
        if key not in groups:
            groups[key] = {
                "utm_source": us,
                "utm_medium": um,
                "utm_campaign": uc if uc != "—" else "",
                "channel": ch,
                "channel_label": channel_label_ua(ch),
                "warning": note,
                "revenue": 0.0,
                "orders": 0,
            }
        groups[key]["revenue"] += float(order.order_total or 0.0)
        groups[key]["orders"] += 1

    rows = [
        {
            **g,
            "revenue": round(g["revenue"], 2),
        }
        for g in groups.values()
        if g["revenue"] >= min_revenue
    ]
    rows.sort(key=lambda r: r["revenue"], reverse=True)
    return rows[:limit]


def build_business_utm_attribution(
    orders_pairs: list[tuple[ClientOrder, Client | None]],
) -> dict[str, Any]:
    """Атрибуція доходу за UTM-каналами та топ UTM-мітки за період."""
    by_channel: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {"revenue": 0.0, "orders": 0},
    )
    by_utm: dict[tuple[str, str, str, str], dict[str, float | int]] = defaultdict(
        lambda: {"revenue": 0.0, "orders": 0},
    )
    by_cosmy_category: dict[str, float] = defaultdict(float)

    for order, client in orders_pairs:
        rev = float(order.order_total or 0.0)
        ch = classify_order_channel(order, client)
        by_channel[ch]["revenue"] += rev
        by_channel[ch]["orders"] += 1

        us, um, uc = _order_utm_fields(order, client)
        by_utm[(us, um, uc, ch)]["revenue"] += rev
        by_utm[(us, um, uc, ch)]["orders"] += 1

        cat = (getattr(client, "source_category", None) or "Інше").strip() or "Інше"
        by_cosmy_category[cat] += rev

    channel_order = list(ALL_CHANNELS)
    channel_data = {ch: by_channel[ch] for ch in channel_order if ch in by_channel}
    top_channels = _rollup_revenue_rows(
        channel_data,
        label_fn=channel_label_ua,
    )

    utm_rows_raw = sorted(
        by_utm.items(),
        key=lambda x: float(x[1].get("revenue") or 0),
        reverse=True,
    )[:20]
    utm_total = sum(float(v.get("revenue") or 0) for _, v in by_utm.items()) or 1.0
    top_utm_tags: list[dict[str, Any]] = []
    for (us, um, uc, ch), stats in utm_rows_raw:
        rev = float(stats.get("revenue") or 0)
        top_utm_tags.append({
            "utm_source": us,
            "utm_medium": um,
            "utm_campaign": uc if uc != "—" else "",
            "channel": ch,
            "channel_label": channel_label_ua(ch),
            "revenue": round(rev, 2),
            "orders": int(stats.get("orders") or 0),
            "share_pct": round(100 * rev / utm_total, 1),
        })

    cosmy_total = sum(by_cosmy_category.values()) or 1.0
    top_cosmy_categories = [
        {
            "name": name,
            "label": business_category_label(name),
            "revenue": round(val, 2),
            "share_pct": round(100 * val / cosmy_total, 1),
        }
        for name, val in sorted(by_cosmy_category.items(), key=lambda x: -x[1])[:6]
    ]

    return {
        "top_channels": top_channels,
        "top_categories": top_channels,
        "top_utm_tags": top_utm_tags,
        "top_cosmy_categories": top_cosmy_categories,
        "utm_rules": utm_classification_rules_ua(),
        "utm_audit": build_utm_channel_audit(orders_pairs),
    }


def _pct_delta(current: float | int, previous: float | int) -> float | None:
    prev = float(previous or 0)
    cur = float(current or 0)
    if prev == 0:
        return 100.0 if cur > 0 else None
    return round(100.0 * (cur - prev) / prev, 1)


def _safe_roas(revenue: float, spend: float) -> float | None:
    if not spend or spend <= 0:
        return None
    return round(revenue / spend, 2)


def _safe_cpa(spend: float, conversions: int | float) -> float | None:
    conv = float(conversions or 0)
    if conv <= 0:
        return None
    return round(spend / conv, 2)


def _safe_conversion_rate(orders: int, traffic: int | float) -> float | None:
    """Коефіцієнт конверсії, %: замовлення CRM ÷ трафік (кліки або сесії)."""
    t = float(traffic or 0)
    if t <= 0:
        return None
    return round(100.0 * int(orders or 0) / t, 2)


def format_conversion_rate(rate: float | None) -> str | None:
    if rate is None:
        return None
    return f"{rate}%"


def _shift_year(dt: datetime, years: int = -1) -> datetime:
    """Зсув дати на рік назад з корекцією 29 лютого."""
    try:
        return dt.replace(year=dt.year + years)
    except ValueError:
        return dt.replace(month=2, day=28, year=dt.year + years)


def format_compare_range_display(prev_start: datetime, prev_end: datetime) -> str:
    """Людський діапазон базового періоду для Δ (дати без часу)."""
    d0 = prev_start.date()
    d1 = prev_end.date()
    return f"{d0.strftime('%d.%m.%Y')} — {d1.strftime('%d.%m.%Y')}"


def resolve_ad_period(
    raw_from: str | None,
    raw_to: str | None,
    data_order_min: datetime | None = None,
    *,
    compare: str = "prev",
) -> tuple[datetime, datetime, datetime, datetime, dict[str, Any]]:
    """
    Повертає (start, end, prev_start, prev_end, meta) для поточного та порівняльного періоду.
    compare: prev — попередній період тієї ж довжини; yoy — аналогічний період минулого року.
    """
    now = now_kyiv()
    pf = _parse_date_param(raw_from)
    pt = _parse_date_param(raw_to)

    if not pf and not pt:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=23, minute=59, second=59, microsecond=0)
        clamped_to_feed = False
        if data_order_min:
            dm = data_order_min.replace(hour=0, minute=0, second=0, microsecond=0)
            if dm > start:
                start = dm
                clamped_to_feed = True
        meta = {
            "from_iso": start.strftime("%Y-%m-%d"),
            "to_iso": end.strftime("%Y-%m-%d"),
            "from_display": start.strftime("%d.%m.%Y"),
            "to_display": end.strftime("%d.%m.%Y"),
            "max_date_iso": now.strftime("%Y-%m-%d"),
            "is_custom": False,
            "clipped_span": False,
            "clamped_start_to_feed": clamped_to_feed,
            "notes_ua": "Період за замовчуванням: поточний місяць." if not clamped_to_feed else None,
        }
    else:
        start, end, meta = resolve_monthly_chart_order_range(now, raw_from, raw_to, data_order_min)

    span = (end.date() - start.date()).days + 1
    compare_mode = (compare or "prev").strip().lower()
    if compare_mode == "yoy":
        prev_start = _shift_year(start).replace(hour=0, minute=0, second=0, microsecond=0)
        prev_end = _shift_year(end).replace(hour=23, minute=59, second=59, microsecond=0)
        compare_kind = "vs аналогічний період минулого року"
    else:
        prev_end = start - timedelta(seconds=1)
        prev_start = (prev_end - timedelta(days=span - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
        compare_mode = "prev"
        compare_kind = "vs попередній період"
    compare_range_display = format_compare_range_display(prev_start, prev_end)
    compare_label = f"{compare_kind} ({compare_range_display})"
    meta = {
        **meta,
        "span_days": span,
        "compare": compare_mode,
        "compare_kind": compare_kind,
        "compare_label": compare_label,
        "compare_range_display": compare_range_display,
        "prev_from_iso": prev_start.strftime("%Y-%m-%d"),
        "prev_to_iso": prev_end.strftime("%Y-%m-%d"),
        "prev_from_display": prev_start.strftime("%d.%m.%Y"),
        "prev_to_display": prev_end.strftime("%d.%m.%Y"),
    }
    return start, end, prev_start, prev_end, meta


async def _load_success_orders_in_range(
    session: AsyncSession,
    start: datetime,
    end: datetime,
) -> list[tuple[ClientOrder, Client | None]]:
    deny = _clients_orders_exclude_clause()
    succ = _sql_outcome_is_success()
    stmt = (
        select(ClientOrder, Client)
        .outerjoin(Client, Client.customer_id == ClientOrder.client_customer_id)
        .where(
            ClientOrder.order_date.isnot(None),
            ClientOrder.order_date >= start,
            ClientOrder.order_date <= end,
            succ,
        )
    )
    if deny is not None:
        stmt = stmt.where(deny)
    if CLIENTS_EXPORT_EXCLUDED_CUSTOMER_IDS:
        stmt = stmt.where(ClientOrder.client_customer_id.notin_(CLIENTS_EXPORT_EXCLUDED_CUSTOMER_IDS))
    rows = (await session.execute(stmt)).all()
    return [(o, c) for o, c in rows]


def _aggregate_orders(
    orders: list[tuple[ClientOrder, Client | None]],
    *,
    channel_filter: str | None = None,
) -> dict[str, Any]:
    by_channel: dict[str, dict[str, float | int]] = defaultdict(lambda: {"revenue": 0.0, "orders": 0})
    by_brand: dict[str, dict[str, float | int]] = defaultdict(lambda: {"revenue": 0.0, "orders": 0, "qty": 0})
    by_day: dict[str, dict[str, float | int]] = defaultdict(lambda: {"revenue": 0.0, "orders": 0})
    by_source_detail: dict[str, dict[str, float | int]] = defaultdict(lambda: {"revenue": 0.0, "orders": 0, "sessions": 0})

    for order, client in orders:
        ch = classify_order_channel(order, client)
        if channel_filter:
            if channel_filter == CHANNEL_OTHER:
                if not other_channel_group(ch) and ch != CHANNEL_OTHER:
                    continue
            elif ch != channel_filter:
                continue

        rev = float(order.order_total or 0.0)
        by_channel[ch]["revenue"] += rev
        by_channel[ch]["orders"] += 1

        day_key = order.order_date.strftime("%Y-%m-%d") if order.order_date else ""
        if day_key:
            by_day[day_key]["revenue"] += rev
            by_day[day_key]["orders"] += 1

        src_label = (order.utm_source or getattr(client, "source_raw", None) or getattr(client, "source_utm_source", None) or "—").strip()
        um = (order.utm_medium or getattr(client, "source_utm_medium", None) or "").strip()
        detail_key = f"{src_label} / {um}" if um else src_label
        by_source_detail[detail_key]["revenue"] += rev
        by_source_detail[detail_key]["orders"] += 1

    return {
        "by_channel": dict(by_channel),
        "by_brand": dict(by_brand),
        "by_day": dict(by_day),
        "by_source_detail": dict(by_source_detail),
        "total_revenue": sum(v["revenue"] for v in by_channel.values()),
        "total_orders": sum(int(v["orders"]) for v in by_channel.values()),
    }


async def _load_brand_breakdown(
    session: AsyncSession,
    start: datetime,
    end: datetime,
    channel_filter: str | None = None,
) -> dict[str, dict[str, float | int]]:
    deny = _clients_orders_exclude_clause()
    succ = _sql_outcome_is_success()
    stmt = (
        select(
            ClientOrder,
            Client,
            ClientOrderLine.brand,
            ClientOrderLine.quantity,
            ClientOrderLine.price,
        )
        .join(ClientOrderLine, ClientOrderLine.order_row_id == ClientOrder.id)
        .outerjoin(Client, Client.customer_id == ClientOrder.client_customer_id)
        .where(
            ClientOrder.order_date.isnot(None),
            ClientOrder.order_date >= start,
            ClientOrder.order_date <= end,
            succ,
        )
    )
    if deny is not None:
        stmt = stmt.where(deny)
    if CLIENTS_EXPORT_EXCLUDED_CUSTOMER_IDS:
        stmt = stmt.where(ClientOrder.client_customer_id.notin_(CLIENTS_EXPORT_EXCLUDED_CUSTOMER_IDS))

    by_brand: dict[str, dict[str, float | int]] = defaultdict(lambda: {"revenue": 0.0, "orders": 0, "qty": 0})
    seen_orders: dict[int, set[str]] = defaultdict(set)

    for order, client, brand, qty, price in (await session.execute(stmt)).all():
        ch = classify_order_channel(order, client)
        if channel_filter:
            if channel_filter == CHANNEL_OTHER:
                if not other_channel_group(ch) and ch != CHANNEL_OTHER:
                    continue
            elif ch != channel_filter:
                continue
        b = (brand or "").strip() or "Без бренду"
        line_rev = float(price or 0) * int(qty or 1)
        by_brand[b]["revenue"] += line_rev
        by_brand[b]["qty"] += int(qty or 1)
        if order.id not in seen_orders[b]:
            seen_orders[b].add(order.id)
            by_brand[b]["orders"] += 1

    return dict(by_brand)


def _kpi_block(
    revenue: float,
    orders: int,
    spend: float = 0.0,
    *,
    impressions: int = 0,
    clicks: int = 0,
    reach: int = 0,
    prev_revenue: float = 0.0,
    prev_orders: int = 0,
    prev_spend: float = 0.0,
    prev_clicks: int = 0,
    prev_impressions: int = 0,
) -> dict[str, Any]:
    ctr = round(100.0 * clicks / impressions, 2) if impressions else None
    cpc = round(spend / clicks, 2) if clicks and spend else None
    cpm = round(1000.0 * spend / impressions, 2) if impressions and spend else None
    return {
        "spend": round(spend, 2),
        "revenue": round(revenue, 2),
        "orders": orders,
        "conversions": orders,
        "roas": _safe_roas(revenue, spend),
        "cpa": _safe_cpa(spend, orders),
        "clicks": clicks,
        "impressions": impressions,
        "reach": reach,
        "ctr": ctr,
        "cpc": cpc,
        "cpm": cpm,
        "avg_check": round(revenue / orders, 2) if orders else None,
        "delta": {
            "revenue_pct": _pct_delta(revenue, prev_revenue),
            "orders_pct": _pct_delta(orders, prev_orders),
            "spend_pct": _pct_delta(spend, prev_spend),
            "clicks_pct": _pct_delta(clicks, prev_clicks),
            "impressions_pct": _pct_delta(impressions, prev_impressions),
        },
    }


def _top_brands_table(by_brand: dict[str, dict[str, float | int]], spend: float = 0.0, limit: int = 15) -> list[dict[str, Any]]:
    rows = []
    for name, v in sorted(by_brand.items(), key=lambda x: -float(x[1]["revenue"])):
        rev = float(v["revenue"])
        orders = int(v["orders"])
        rows.append(
            {
                "brand": name,
                "revenue": round(rev, 2),
                "orders": orders,
                "qty": int(v.get("qty", 0)),
                "roas": _safe_roas(rev, spend * (rev / max(sum(float(x["revenue"]) for x in by_brand.values()), 1))),
                "cpa": _safe_cpa(spend * (rev / max(sum(float(x["revenue"]) for x in by_brand.values()), 1)), orders),
            }
        )
    return rows[:limit]


def gsc_daily_through_iso(gsc_daily: list[dict[str, Any]] | None) -> str | None:
    dates = [str(d.get("date"))[:10] for d in (gsc_daily or []) if d.get("date")]
    return max(dates) if dates else None


def build_gsc_crm_daily_chart(
    start: datetime,
    end: datetime,
    gsc_daily: list[dict[str, Any]] | None,
    crm_by_day: dict[str, dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Усі календарні дні періоду: кліки GSC (0 якщо немає) + дохід/замовлення CRM."""
    gsc_by_date = {
        str(d["date"])[:10]: d for d in (gsc_daily or []) if d.get("date")
    }
    by_day = crm_by_day or {}
    chart: list[dict[str, Any]] = []
    cur = start.date()
    end_d = end.date()
    while cur <= end_d:
        iso = cur.isoformat()
        gsc_row = gsc_by_date.get(iso) or {}
        crm_row = by_day.get(iso) or {}
        chart.append(
            {
                "date": iso,
                "clicks": int(gsc_row.get("clicks") or 0),
                "revenue": float(crm_row.get("revenue") or 0),
                "orders": int(crm_row.get("orders") or 0),
            }
        )
        cur += timedelta(days=1)
    return chart


def gsc_freshness_note_ua(gsc: dict[str, Any], period_end: datetime) -> str | None:
    """Підказка, якщо GSC не покриває кінець обраного періоду (затримка API / кеш)."""
    through = (gsc.get("daily_through") or gsc_daily_through_iso(gsc.get("daily"))) if gsc else None
    if not through:
        return None
    end_s = period_end.strftime("%Y-%m-%d")
    if through >= end_s:
        return None
    sites = gsc.get("gsc_site_urls") or []
    site_hint = f" Джерела GSC: {', '.join(sites)}." if sites else ""
    return (
        f"Кліки GSC у звіті є до {through} (обрано період по {end_s}). "
        "У Google Search Console дані зазвичай з’являються з затримкою 2–3 дні."
        f"{site_hint} Після переїзду на cosmy.ua переконайтесь, що властивість cosmy.ua "
        "підключена в GSC і сервісний акаунт має доступ; натисніть «Оновити з API»."
    )


def normalize_ad_chart_granularity(raw: str | None) -> str:
    """day | week | month | quarter. За замовчуванням — день."""
    v = (raw or "day").strip().lower()
    if v in ("day", "week", "month", "quarter"):
        return v
    return "day"


def aggregate_chart_series(
    daily: list[dict[str, Any]],
    granularity: str,
) -> list[dict[str, Any]]:
    """Агрегація денного ряду: day | week | month | quarter."""
    gran = normalize_ad_chart_granularity(granularity)
    if not daily or gran == "day":
        return daily

    sum_fields = ("revenue", "orders", "spend", "sessions", "clicks", "conversions", "conversion_value")
    buckets: dict[str, dict[str, Any]] = {}

    def _bucket_key(date_str: str) -> str:
        raw = str(date_str)
        dt = datetime.strptime(raw[:10], "%Y-%m-%d")
        if gran == "month":
            return dt.strftime("%Y-%m")
        if gran == "quarter":
            return f"{dt.year}-Q{(dt.month - 1) // 3 + 1}"
        iso = dt.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"

    for row in daily:
        key = _bucket_key(row["date"])
        if key not in buckets:
            buckets[key] = {
                "date": key,
                "_pos_w": 0.0,
                "_pos_n": 0.0,
                **{f: 0.0 if f != "orders" else 0 for f in sum_fields},
            }
        bucket = buckets[key]
        for field in sum_fields:
            if field not in row:
                continue
            val = row.get(field) or 0
            if field == "orders":
                bucket[field] += int(val)
            else:
                bucket[field] += float(val)
        pos = row.get("position")
        if pos is not None:
            weight = float(row.get("clicks") or 0) or 1.0
            bucket["_pos_w"] += float(pos) * weight
            bucket["_pos_n"] += weight

    sample = daily[0]
    out: list[dict[str, Any]] = []
    for key in sorted(buckets.keys()):
        bucket = buckets[key]
        spend = float(bucket["spend"] or 0)
        revenue = float(bucket["revenue"] or 0)
        point: dict[str, Any] = {
            "date": bucket["date"],
            "revenue": round(revenue, 2),
            "orders": int(bucket["orders"] or 0),
            "spend": round(spend, 2),
            "roas": _safe_roas(revenue, spend),
        }
        if "sessions" in sample:
            point["sessions"] = int(bucket["sessions"] or 0)
        if "clicks" in sample:
            point["clicks"] = int(bucket["clicks"] or 0)
        if "conversions" in sample:
            point["conversions"] = round(float(bucket["conversions"] or 0), 2)
        if "position" in sample:
            point["position"] = (
                round(bucket["_pos_w"] / bucket["_pos_n"], 1) if bucket["_pos_n"] else None
            )
        out.append(point)
    return out


def _daily_series(by_day: dict[str, dict[str, float | int]], spend_by_day: dict[str, float] | None = None) -> list[dict[str, Any]]:
    spend_by_day = spend_by_day or {}
    all_days = sorted(set(by_day.keys()) | set(spend_by_day.keys()))
    out = []
    for day in all_days:
        rev = float(by_day.get(day, {}).get("revenue", 0))
        raw_orders = float(by_day.get(day, {}).get("orders", 0) or 0)
        orders: int | float = int(raw_orders) if abs(raw_orders - round(raw_orders)) < 1e-9 else round(raw_orders, 2)
        sp = float(spend_by_day.get(day, 0))
        out.append(
            {
                "date": day,
                "revenue": round(rev, 2),
                "orders": orders,
                "spend": round(sp, 2),
                "roas": _safe_roas(rev, sp),
            }
        )
    return out


async def warm_default_crm_analytics_cache(session: AsyncSession) -> None:
    """Прогрів CRM-кешу після синхронізації фіду (фонове завдання)."""
    import logging

    _logger = logging.getLogger(__name__)
    try:
        orders = await load_orders_analytics(session, refresh=True)
        period = orders.get("period") or {}
        start_s = period.get("from_iso")
        end_s = period.get("to_iso")
        if start_s and end_s:
            start = datetime.strptime(start_s[:10], "%Y-%m-%d")
            end = datetime.strptime(end_s[:10], "%Y-%m-%d").replace(hour=23, minute=59, second=59)
            pairs = orders.pop("_cur_orders_pairs", None)
            await load_overview_business_snapshot(
                session, start, end, refresh=True, orders_pairs=pairs,
            )
        _logger.info("CRM analytics cache warmed for default advertising period")
    except Exception as exc:
        _logger.warning("CRM analytics cache warm failed: %s", exc)


async def load_order_details(
    session: AsyncSession,
    start: datetime,
    end: datetime,
    *,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Таблиця замовлень для розділу «Деталізація»."""
    orders = await _load_success_orders_in_range(session, start, end)
    orders.sort(key=lambda x: x[0].order_date or datetime.min, reverse=True)
    rows: list[dict[str, Any]] = []
    for order, client in orders[:limit]:
        ch = classify_order_channel(order, client)
        rows.append({
            "date": order.order_date.strftime("%d.%m.%Y %H:%M") if order.order_date else "—",
            "order_id": str(order.order_id) if order.order_id else str(order.id),
            "channel": channel_label_ua(ch),
            "channel_key": ch,
            "source": (order.utm_source or getattr(client, "source_utm_source", None) or "—").strip() or "—",
            "medium": (order.utm_medium or getattr(client, "source_utm_medium", None) or "—").strip() or "—",
            "campaign": (order.utm_campaign or getattr(client, "source_utm_campaign", None) or "—").strip() or "—",
            "city": (getattr(client, "geo_city", None) or "—").strip() or "—",
            "revenue": round(float(order.order_total or 0.0), 2),
        })
    return rows


async def load_orders_analytics(
    session: AsyncSession,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    channel: str | None = None,
    refresh: bool = False,
    compare: str = "prev",
) -> dict[str, Any]:
    """Завантажити агрегати замовлень для періоду (+ попередній період для △)."""
    deny = _clients_orders_exclude_clause()
    min_stmt = select(func.min(ClientOrder.order_date)).select_from(ClientOrder)
    if deny is not None:
        min_stmt = min_stmt.where(deny)
    data_min = (await session.execute(min_stmt)).scalar()

    start, end, prev_start, prev_end, period_meta = resolve_ad_period(
        date_from, date_to, data_min, compare=compare,
    )
    cache_key = orders_cache_key(
        channel, period_meta["from_iso"], period_meta["to_iso"], period_meta.get("compare", "prev"),
    )
    if not refresh:
        cached = crm_cache_get(cache_key)
        if cached is not None:
            return cached

    cur_orders, prev_orders, cur_brands, prev_brands = await asyncio.gather(
        _load_success_orders_in_range(session, start, end),
        _load_success_orders_in_range(session, prev_start, prev_end),
        _load_brand_breakdown(session, start, end, channel),
        _load_brand_breakdown(session, prev_start, prev_end, channel),
    )

    cur = _aggregate_orders(cur_orders, channel_filter=channel)
    prev = _aggregate_orders(prev_orders, channel_filter=channel)

    cur_gads_attr = build_campaign_attribution(cur_orders, channel=CHANNEL_GOOGLE_ADS)
    cur_meta_attr = build_campaign_attribution(cur_orders, channel=CHANNEL_META_ADS)
    prev_gads_attr = build_campaign_attribution(prev_orders, channel=CHANNEL_GOOGLE_ADS)
    prev_meta_attr = build_campaign_attribution(prev_orders, channel=CHANNEL_META_ADS)

    result = {
        "period": period_meta,
        "current": cur,
        "previous": prev,
        "brands_current": cur_brands,
        "brands_previous": prev_brands,
        "campaign_attribution": {
            CHANNEL_GOOGLE_ADS: cur_gads_attr,
            CHANNEL_META_ADS: cur_meta_attr,
            "meta": cur_meta_attr,
        },
        "prev_campaign_attribution": {
            CHANNEL_GOOGLE_ADS: prev_gads_attr,
            CHANNEL_META_ADS: prev_meta_attr,
            "meta": prev_meta_attr,
        },
        "generated_at": now_kyiv().strftime("%d.%m.%Y %H:%M"),
    }
    crm_cache_set(cache_key, result)
    return {**result, "_cur_orders_pairs": cur_orders}


async def load_overview_business_snapshot(
    session: AsyncSession,
    start: datetime,
    end: datetime,
    *,
    refresh: bool = False,
    orders_pairs: list[tuple[ClientOrder, Client | None]] | None = None,
) -> dict[str, Any]:
    """Бізнес-блоки для сторінки «Огляд» (категорії, міста, сегменти покупців)."""
    cache_key = business_cache_key(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    if not refresh:
        cached = crm_cache_get(cache_key)
        if cached is not None:
            return cached

    deny = _clients_orders_exclude_clause()
    succ = _sql_outcome_is_success()
    if orders_pairs is None:
        stmt = (
            select(ClientOrder, Client)
            .outerjoin(Client, Client.customer_id == ClientOrder.client_customer_id)
            .where(
                ClientOrder.order_date.isnot(None),
                ClientOrder.order_date >= start,
                ClientOrder.order_date <= end,
                succ,
            )
        )
        if deny is not None:
            stmt = stmt.where(deny)
        if CLIENTS_EXPORT_EXCLUDED_CUSTOMER_IDS:
            stmt = stmt.where(ClientOrder.client_customer_id.notin_(CLIENTS_EXPORT_EXCLUDED_CUSTOMER_IDS))
        orders_pairs = [(o, c) for o, c in (await session.execute(stmt)).all()]

    prior_stmt = (
        select(ClientOrder.client_customer_id)
        .where(
            ClientOrder.order_date.isnot(None),
            ClientOrder.order_date < start,
            ClientOrder.client_customer_id.isnot(None),
            succ,
        )
        .distinct()
    )
    if deny is not None:
        prior_stmt = prior_stmt.where(deny)
    if CLIENTS_EXPORT_EXCLUDED_CUSTOMER_IDS:
        prior_stmt = prior_stmt.where(ClientOrder.client_customer_id.notin_(CLIENTS_EXPORT_EXCLUDED_CUSTOMER_IDS))
    prior_customers = {
        str(row[0]) for row in (await session.execute(prior_stmt)).all() if row[0] is not None
    }

    by_city: dict[str, float] = defaultdict(float)
    new_rev = returning_rev = 0.0
    new_orders = returning_orders = 0
    seen_customers: set[str] = set()
    utm_stats = build_business_utm_attribution(orders_pairs)

    for order, client in orders_pairs:
        rev = float(order.order_total or 0.0)
        city = (getattr(client, "geo_city", None) or "Невідомо").strip() or "Невідомо"
        by_city[city] += rev

        cid = str(order.client_customer_id or "")
        if not cid or cid in seen_customers:
            continue
        seen_customers.add(cid)

        if cid in prior_customers:
            returning_rev += rev
            returning_orders += 1
        else:
            new_rev += rev
            new_orders += 1

    mix_total = new_rev + returning_rev or 1.0
    result = {
        **utm_stats,
        "top_cities": merge_city_revenue_rows(by_city),
        "customer_mix": {
            "new_revenue": round(new_rev, 2),
            "returning_revenue": round(returning_rev, 2),
            "new_orders": new_orders,
            "returning_orders": returning_orders,
            "new_pct": round(100 * new_rev / mix_total, 1),
            "returning_pct": round(100 * returning_rev / mix_total, 1),
        },
    }
    crm_cache_set(cache_key, result)
    return result


def merge_channel_overview(
    orders_data: dict[str, Any],
    spend_by_channel: dict[str, dict[str, Any]],
    traffic_current: dict[str, int] | None = None,
    traffic_previous: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Зведення по каналах для сторінки «Огляд»."""
    cur_ch = orders_data["current"]["by_channel"]
    prev_ch = orders_data["previous"]["by_channel"]
    total_rev = float(orders_data["current"]["total_revenue"] or 0)

    rows = []
    for ch in OVERVIEW_CHANNELS:
        if ch == CHANNEL_OTHER:
            cur_rev = sum(float(cur_ch.get(c, {}).get("revenue", 0)) for c in cur_ch if c not in (
                CHANNEL_GOOGLE_ADS, CHANNEL_META_ADS, CHANNEL_SEO, CHANNEL_DIRECT
            ))
            cur_ord = sum(int(cur_ch.get(c, {}).get("orders", 0)) for c in cur_ch if c not in (
                CHANNEL_GOOGLE_ADS, CHANNEL_META_ADS, CHANNEL_SEO, CHANNEL_DIRECT
            ))
            prev_rev = sum(float(prev_ch.get(c, {}).get("revenue", 0)) for c in prev_ch if c not in (
                CHANNEL_GOOGLE_ADS, CHANNEL_META_ADS, CHANNEL_SEO, CHANNEL_DIRECT
            ))
            prev_ord = sum(int(prev_ch.get(c, {}).get("orders", 0)) for c in prev_ch if c not in (
                CHANNEL_GOOGLE_ADS, CHANNEL_META_ADS, CHANNEL_SEO, CHANNEL_DIRECT
            ))
        else:
            cur_rev = float(cur_ch.get(ch, {}).get("revenue", 0))
            cur_ord = int(cur_ch.get(ch, {}).get("orders", 0))
            prev_rev = float(prev_ch.get(ch, {}).get("revenue", 0))
            prev_ord = int(prev_ch.get(ch, {}).get("orders", 0))

        sp = spend_by_channel.get(ch, {})
        spend = float(sp.get("spend", 0))
        prev_spend = float(sp.get("prev_spend", 0))
        traffic = int((traffic_current or {}).get(ch, 0) or 0)
        prev_traffic = int((traffic_previous or {}).get(ch, 0) or 0)
        conv_rate = _safe_conversion_rate(cur_ord, traffic)
        prev_conv_rate = _safe_conversion_rate(prev_ord, prev_traffic)
        rows.append(
            {
                "channel": ch,
                "label": channel_label_ua(ch),
                "revenue": round(cur_rev, 2),
                "orders": cur_ord,
                "spend": round(spend, 2),
                "roas": _safe_roas(cur_rev, spend),
                "traffic": traffic,
                "conversion_rate": conv_rate,
                "conversion_rate_pct": format_conversion_rate(conv_rate),
                "revenue_share_pct": round(100.0 * cur_rev / total_rev, 1) if total_rev else 0.0,
                "delta_revenue_pct": _pct_delta(cur_rev, prev_rev),
                "delta_orders_pct": _pct_delta(cur_ord, prev_ord),
                "delta_spend_pct": _pct_delta(spend, prev_spend),
                "delta_conversion_rate_pct": _pct_delta(conv_rate or 0, prev_conv_rate or 0),
            }
        )
    return rows


def build_insights(
    channel: str,
    kpi: dict[str, Any],
    *,
    top_campaign: str | None = None,
    top_brand: str | None = None,
    top_placement: str | None = None,
    total_revenue: float = 0.0,
) -> list[str]:
    tips: list[str] = []
    roas = kpi.get("roas")
    if roas is not None:
        tips.append(f"ROAS за період: {roas:.2f}")
    if top_campaign:
        tips.append(f"Найкраща кампанія за витратами: {top_campaign}")
    if top_brand:
        tips.append(f"Найкращий бренд за доходом: {top_brand}")
    if top_placement:
        tips.append(f"Найкращий плейсмент: {top_placement}")
    rev = float(kpi.get("revenue") or 0)
    if total_revenue and rev:
        share = round(100.0 * rev / total_revenue, 1)
        tips.append(f"Частка {channel_label_ua(channel)} у загальному доході: {share}%")
    if kpi.get("delta", {}).get("revenue_pct") is not None:
        d = kpi["delta"]["revenue_pct"]
        arrow = "↑" if d >= 0 else "↓"
        tips.append(f"Дохід vs попередній період: {arrow} {abs(d):.1f}%")
    return tips[:4]
