"""
Збірка даних для сторінок «Аналіз реклами».
Повна версія: donut-діаграми, 2 ряди KPI, атрибуція брендів, insights.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from web.ad_insights import build_ad_insight_feed, insight_chips_from_feed
from web.ad_analytics import (
    _daily_series,
    _kpi_block,
    _pct_delta,
    _safe_conversion_rate,
    _safe_cpa,
    _safe_roas,
    format_conversion_rate,
    _top_brands_table,
    aggregate_chart_series,
    build_gsc_crm_daily_chart,
    gsc_freshness_note_ua,
    normalize_ad_chart_granularity,
    build_insights,
    load_order_details,
    load_orders_analytics,
    load_overview_business_snapshot,
    merge_channel_overview,
)
from web.ad_attribution import (
    attribution_unassigned_totals,
    build_campaign_attribution,
    classify_source_type,
    resolve_campaign_attribution,
)
from web.ad_channel import (
    CHANNEL_DIRECT,
    CHANNEL_GOOGLE_ADS,
    CHANNEL_META_ADS,
    CHANNEL_OTHER,
    CHANNEL_SEO,
    channel_label_ua,
)
from web.integrations.ad_cache import cache_last_updated
from web.integrations.ad_integration_settings import (
    build_section_integration_settings,
    classify_integration_status,
)
from web.integrations.fx_rates import (
    ad_spend_to_uah,
    convert_daily_spend_series,
    format_fx_spend_note,
)
from web.integrations.google_ads import fetch_google_ads_stats, _empty_payload as empty_gads_payload
from web.integrations.google_search_console import fetch_gsc_stats, _empty_payload as empty_gsc_payload
from web.integrations.meta_ads import (
    fetch_meta_ads_stats,
    meta_currency_suffix,
    meta_roas_display,
    _empty_payload as empty_meta_payload,
)


SECTIONS = ("overview", "google_ads", "meta", "seo", "direct", "other", "business", "insights", "details")

SECTION_LABELS = {
    "overview": "Огляд",
    "google_ads": "Google Ads",
    "meta": "Meta Ads",
    "seo": "SEO (органічний)",
    "direct": "Прямий трафік",
    "other": "Інші джерела",
    "business": "Бізнес-аналітика",
    "insights": "Інсайти",
    "details": "Деталізація",
}

# Колір для кожного каналу в donut-діаграмі
CHANNEL_COLORS = {
    CHANNEL_GOOGLE_ADS: "#4285F4",
    CHANNEL_META_ADS: "#1877F2",
    CHANNEL_SEO:       "#34A853",
    CHANNEL_DIRECT:    "#F59E0B",
    CHANNEL_OTHER:     "#94A3B8",
}

CHANNEL_SLUGS = {
    CHANNEL_GOOGLE_ADS: "google_ads",
    CHANNEL_META_ADS: "meta",
    CHANNEL_SEO: "seo",
    CHANNEL_DIRECT: "direct",
    CHANNEL_OTHER: "other",
}


def _enrich_overview_channels(
    rows: list[dict[str, Any]],
    *,
    meta_api: dict[str, Any] | None = None,
    gads: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    meta_api = meta_api or {}
    gads = gads or {}
    out = []
    for row in rows:
        ch = row.get("channel")
        spend_suffix = "₴"
        roas_display = _roas_pct(row.get("roas"))
        spend_display = float(row.get("spend") or 0)
        spend_approximate = False

        if ch == CHANNEL_META_ADS:
            native = float(meta_api.get("spend") or 0)
            spend_suffix = meta_currency_suffix(meta_api.get("currency"))
            spend_display = native
            spend_approximate = (meta_api.get("currency") or "USD").upper() != "UAH" and native > 0
            roas_display = _roas_pct(row.get("roas"))
        elif ch == CHANNEL_GOOGLE_ADS:
            native = float(gads.get("spend") or spend_display)
            spend_suffix = meta_currency_suffix(gads.get("currency")) if gads.get("currency") else "₴"
            spend_display = native
            spend_approximate = (gads.get("currency") or "UAH").upper() != "UAH" and native > 0

        out.append({
            **row,
            "slug": CHANNEL_SLUGS.get(ch, "other"),
            "color": CHANNEL_COLORS.get(ch, "#94A3B8"),
            "roas_pct": roas_display,
            "spend_suffix": spend_suffix,
            "spend_display": round(spend_display, 2),
            "spend_uah": round(float(row.get("spend") or 0), 2),
            "spend_approximate": spend_approximate,
        })
    return out


def _seo_period_spend_uah(start: datetime, end: datetime) -> float:
    """Сума ручних витрат SEO за дати періоду (таблиця seo_expenses у cosmy_clients.db)."""
    from web.integrations.seo_expenses import sum_seo_expenses_uah

    return sum_seo_expenses_uah(start, end)


def _channel_spend_for_overview(
    api: dict[str, Any],
    prev_api: dict[str, Any],
    *,
    as_of: datetime,
    prev_as_of: datetime,
) -> dict[str, Any]:
    """Витрати каналу в UAH для зведення + метадані конвертації."""
    cur = ad_spend_to_uah(float(api.get("spend") or 0), api.get("currency"), as_of)
    prev = ad_spend_to_uah(
        float(prev_api.get("spend") or 0),
        prev_api.get("currency") or api.get("currency"),
        prev_as_of,
    )
    return {
        "spend": cur["amount_uah"],
        "prev_spend": prev["amount_uah"],
        "native": cur,
        "prev_native": prev,
    }


def _top_brands_crm_only(
    by_brand: dict[str, dict[str, float | int]],
    total_rev: float,
    *,
    limit: int = 15,
) -> list[dict[str, Any]]:
    """Топ брендів лише з CRM (без оціночних витрат/ROAS)."""
    rows = []
    for name, v in sorted(by_brand.items(), key=lambda x: -float(x[1]["revenue"])):
        rev = float(v["revenue"])
        orders = int(v["orders"])
        rows.append({
            "brand": name,
            "revenue": round(rev, 2),
            "orders": orders,
            "revenue_share_pct": round(100 * rev / total_rev, 1) if total_rev else 0.0,
        })
    return rows[:limit]


def _attach_brand_deltas(
    rows: list[dict[str, Any]],
    brands_previous: dict[str, dict[str, float | int]] | None,
) -> list[dict[str, Any]]:
    """Δ замовлення / Δ дохід vs попередній період (по назві бренду)."""
    prev_map = brands_previous or {}
    for row in rows:
        prev = prev_map.get(row.get("brand"), {}) or {}
        prev_rev = float(prev.get("revenue") or 0)
        prev_orders = int(prev.get("orders") or 0)
        row["prev_revenue"] = round(prev_rev, 2)
        row["prev_orders"] = prev_orders
        row["delta_revenue_pct"] = _pct_delta(row.get("revenue") or 0, prev_rev)
        row["delta_orders_pct"] = _pct_delta(row.get("orders") or 0, prev_orders)
    return rows


def _attach_metric_deltas(
    rows: list[dict[str, Any]],
    prev_rows: list[dict[str, Any]] | None,
    *,
    key: str = "name",
    key_fn=None,
    metrics: tuple[tuple[str, str], ...] = (("orders", "delta_orders_pct"),),
) -> list[dict[str, Any]]:
    """Додає відсоткову зміну метрик, зіставляючи рядки з попереднім періодом."""
    fn = key_fn or (lambda r, k=key: str(r.get(k) or ""))
    prev_map = {fn(r): r for r in (prev_rows or [])}
    for row in rows:
        prev = prev_map.get(fn(row), {}) or {}
        for field, delta_name in metrics:
            row[delta_name] = _pct_delta(row.get(field) or 0, prev.get(field) or 0)
    return rows


def _crm_brands_with_compare(
    brands_current: dict[str, dict[str, float | int]],
    brands_previous: dict[str, dict[str, float | int]],
    total_rev: float,
    *,
    limit: int = 15,
) -> list[dict[str, Any]]:
    rows = _top_brands_crm_only(brands_current, total_rev, limit=limit)
    return _attach_brand_deltas(rows, brands_previous)


def _build_channel_top_brands(
    brands_current: dict[str, dict[str, float | int]],
    brands_previous: dict[str, dict[str, float | int]],
    channel_rev: float,
    prev_channel_rev: float,
    channel_spend_uah: float,
    *,
    limit: int = 15,
) -> list[dict[str, Any]]:
    """Топ брендів каналу: CRM + оціночні витрати + порівняння з попереднім періодом."""
    rows = _enrich_brands(
        _top_brands_table(brands_current, spend=channel_spend_uah, limit=limit),
        channel_spend_uah,
        channel_rev,
    )
    _attach_brand_deltas(rows, brands_previous)
    for row in rows:
        prev_rev = float(row.get("prev_revenue") or 0)
        prev_spend = (
            round(channel_spend_uah * (prev_rev / prev_channel_rev), 2)
            if prev_channel_rev and channel_spend_uah
            else 0.0
        )
        row["prev_spend"] = prev_spend
        row["delta_spend_pct"] = _pct_delta(row.get("spend") or 0, prev_spend)
    return rows


def _tip(text: str, *, trend: str | None = None) -> dict[str, str]:
    if trend is None:
        if "↓" in text:
            trend = "down"
        elif "↑" in text:
            trend = "up"
        else:
            trend = "up"
    return {"text": text, "trend": trend}


def build_integrations_panel(
    start: datetime,
    end: datetime,
    *,
    gads: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
    gsc: dict[str, Any] | None = None,
    ga4: dict[str, Any] | None = None,
    crm_updated: str | None = None,
) -> list[dict[str, Any]]:
    """Статус підключених джерел для панелі інтеграцій."""
    gads = gads or {}
    meta = meta or {}
    gsc = gsc or {}
    ga4 = ga4 or {}

    def _row(
        key: str,
        label: str,
        configured: bool,
        cache_key: str,
        api_error: str | None = None,
    ) -> dict[str, Any]:
        updated = cache_last_updated(cache_key)
        status = classify_integration_status(configured=configured, error=api_error)
        return {
            "key": key,
            "label": label,
            "ok": status == "ok",
            "configured": configured,
            "status": status,
            "updated": updated,
            "error": api_error,
        }

    from config import ga4_configured, google_ads_configured, gsc_configured, meta_ads_configured

    return [
        _row("google_ads", "Google Ads", google_ads_configured(), f"gads_v4_{start.date()}_{end.date()}", gads.get("error")),
        _row("meta", "Meta Ads", meta_ads_configured(), f"meta_v3_{start.date()}_{end.date()}", meta.get("error")),
        _row("gsc", "Search Console", gsc_configured(), f"gsc2_{start.date()}_{end.date()}", gsc.get("error")),
        _row("ga4", "Google Analytics 4", ga4_configured(), f"ga4_{start.date()}_{end.date()}", ga4.get("error")),
        {
            "key": "crm",
            "label": "CRM Cosmy",
            "ok": True,
            "configured": True,
            "status": "ok",
            "updated": crm_updated,
            "error": None,
        },
    ]


def _parse_period_dates(orders_data: dict[str, Any]) -> tuple[datetime, datetime, datetime, datetime]:
    meta = orders_data["period"]
    start = datetime.strptime(meta["from_iso"], "%Y-%m-%d")
    end = datetime.strptime(meta["to_iso"][:10], "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    prev_start = datetime.strptime(meta["prev_from_iso"], "%Y-%m-%d")
    prev_end = datetime.strptime(meta["prev_to_iso"][:10], "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    return start, end, prev_start, prev_end


def _donut_from_spend_list(
    items: list[dict[str, Any]],
    key_name: str = "name",
    value_key: str = "spend",
    palette: list[str] | None = None,
) -> dict[str, Any]:
    """Дані для Chart.js donut з довільного списку {name, spend}."""
    _colors = palette or [
        "#4285F4", "#34A853", "#FBBC05", "#EA4335", "#9C27B0",
        "#FF9800", "#00BCD4", "#795548", "#607D8B", "#E91E63",
    ]
    total = sum(float(r.get(value_key) or 0) for r in items) or 1.0
    labels, values, colors = [], [], []
    for i, row in enumerate(items):
        val = float(row.get(value_key) or 0)
        if val <= 0:
            continue
        labels.append(str(row.get(key_name) or "—"))
        values.append(round(val, 2))
        colors.append(_colors[i % len(_colors)])
    return {
        "labels": labels,
        "values": values,
        "colors": colors,
        "total": round(total, 2),
        "unit": "uah",
        "legend": [
            {"label": l, "value": round(v, 2), "pct": round(100 * v / total, 1), "color": c}
            for l, v, c in zip(labels, values, colors)
        ],
    }


def _channel_donut(overview_rows: list[dict[str, Any]]) -> dict[str, Any]:
    items = []
    for row in overview_rows:
        items.append({
            "name": row["label"],
            "spend": row["revenue"],  # donut по доходу
            "color": CHANNEL_COLORS.get(row["channel"], "#94A3B8"),
        })
    return _donut_from_spend_list(
        items,
        value_key="spend",
        palette=[CHANNEL_COLORS.get(r["channel"], "#94A3B8") for r in overview_rows],
    )


def _roas_pct(roas: float | None) -> str:
    if roas is None:
        return "—"
    return f"{round(roas * 100)}%"


def _campaign_attribution_from_orders(
    orders: dict[str, Any],
    channel_key: str,
    *,
    previous: bool = False,
) -> dict[str, dict[str, float | int]]:
    """Кешована атрибуція з load_orders_analytics (без повторного читання БД)."""
    bucket = "prev_campaign_attribution" if previous else "campaign_attribution"
    store = orders.get(bucket) or {}
    attr = store.get(channel_key)
    if attr is not None:
        return attr
    aliases = {"meta_ads": "meta", "meta": "meta_ads"}
    alt = aliases.get(channel_key)
    if alt:
        attr = store.get(alt)
        if attr is not None:
            return attr
    return {}


def _enrich_gads_campaigns(
    campaigns: list[dict],
    *,
    total_crm_revenue: float,
    total_gads_spend: float,
    attribution: dict[str, dict[str, float | int]] | None = None,
    orders_raw: list | None = None,
) -> tuple[list[dict], bool]:
    """Google Ads API метрики + CRM атрибуція (окремо)."""
    if attribution is None:
        attribution = build_campaign_attribution(orders_raw or [], channel=CHANNEL_GOOGLE_ADS)
    enriched: list[dict[str, Any]] = []
    any_match = False
    total_spend = sum(float(c.get("spend") or 0) for c in campaigns) or 1.0
    resolved = resolve_campaign_attribution(
        [str(c.get("name") or "") for c in campaigns],
        attribution,
    )

    for c in campaigns:
        sp = float(c.get("spend") or 0)
        g_conv = float(c.get("conversions") or 0)
        g_rev = float(c.get("conversion_value") or 0)
        camp_name = str(c.get("name") or "")
        crm_rev, crm_ord, matched, _utm = resolved.get(camp_name, (0.0, 0, False, None))
        if matched:
            any_match = True
        else:
            share = sp / total_spend
            crm_rev = round(total_crm_revenue * share, 2)
            crm_ord = 0

        g_roas = g_rev / sp if sp > 0 and g_rev > 0 else None
        enriched.append({
            **c,
            "gads_conversions": round(g_conv, 1),
            "gads_revenue": round(g_rev, 2),
            "gads_roas_pct": meta_roas_display(g_roas),
            "crm_revenue": round(crm_rev, 2),
            "crm_orders": crm_ord,
            "revenue": round(g_rev, 2),
            "orders": crm_ord,
            "conversions": round(g_conv, 1),
            "roas_pct": meta_roas_display(g_roas) if g_rev > 0 else _roas_pct(_safe_roas(crm_rev, sp)),
            "cpa": round(sp / g_conv, 2) if g_conv > 0 else _safe_cpa(sp, crm_ord),
            "attribution": "utm" if matched else ("gads" if g_rev > 0 else "estimate"),
        })

    return enriched, any_match


def _enrich_meta_campaigns(
    campaigns: list[dict],
    *,
    total_crm_revenue: float,
    total_meta_spend: float,
    attribution: dict[str, dict[str, float | int]] | None = None,
    orders_raw: list | None = None,
) -> tuple[list[dict], bool]:
    """Meta Ads API метрики + CRM атрибуція (окремо), як у Google Ads."""
    if attribution is None:
        attribution = build_campaign_attribution(orders_raw or [], channel=CHANNEL_META_ADS)
    enriched: list[dict[str, Any]] = []
    any_match = False
    total_spend = sum(float(c.get("spend") or 0) for c in campaigns) or 1.0
    spend_uah = float(total_meta_spend or 0)
    resolved = resolve_campaign_attribution(
        [str(c.get("name") or "") for c in campaigns],
        attribution,
    )

    for c in campaigns:
        sp = float(c.get("spend") or 0)
        sp_uah = round(spend_uah * (sp / total_spend), 2) if spend_uah else sp
        m_purch = int(c.get("purchases") or 0)
        m_rev = float(c.get("purchase_value") or c.get("revenue") or 0)
        camp_name = str(c.get("name") or "")
        crm_rev, crm_ord, matched, _utm = resolved.get(camp_name, (0.0, 0, False, None))
        if matched:
            any_match = True
        else:
            share = sp / total_spend
            crm_rev = round(total_crm_revenue * share, 2)
            crm_ord = 0

        m_roas = m_rev / sp if sp > 0 and m_rev > 0 else None
        enriched.append({
            **c,
            "meta_purchases": m_purch,
            "meta_revenue": round(m_rev, 2),
            "meta_roas_pct": meta_roas_display(m_roas),
            "crm_revenue": round(crm_rev, 2),
            "crm_orders": crm_ord,
            "revenue": round(crm_rev, 2),
            "orders": crm_ord,
            "purchases": m_purch,
            "roas_pct": _roas_pct(_safe_roas(crm_rev, sp_uah)),
            "cpa": round(sp / m_purch, 2) if m_purch > 0 else _safe_cpa(sp, crm_ord),
            "attribution": "utm" if matched else ("meta" if m_rev > 0 else "estimate"),
        })

    return enriched, any_match


def _build_overview_extra(
    orders: dict[str, Any],
    gads: dict[str, Any],
    gsc: dict[str, Any],
    business: dict[str, Any],
    gads_prev: dict[str, Any] | None = None,
    meta_api: dict[str, Any] | None = None,
    meta_prev: dict[str, Any] | None = None,
    meta_spend_uah: float = 0.0,
    meta_prev_spend_uah: float = 0.0,
    gads_spend_uah: float = 0.0,
    seo_spend_uah: float = 0.0,
) -> dict[str, Any]:
    """Превʼю-блоки Google Ads / Meta / SEO та бізнес-аналітика для сторінки «Огляд»."""
    cur_ch = orders["current"]["by_channel"]
    prev_ch = orders["previous"]["by_channel"]
    g_rev = float(cur_ch.get(CHANNEL_GOOGLE_ADS, {}).get("revenue", 0))
    g_ord = int(cur_ch.get(CHANNEL_GOOGLE_ADS, {}).get("orders", 0))
    s_rev = float(cur_ch.get(CHANNEL_SEO, {}).get("revenue", 0))
    s_ord = int(cur_ch.get(CHANNEL_SEO, {}).get("orders", 0))
    g_spend_native = float(gads.get("spend") or 0)
    gads_currency = gads.get("currency") or "UAH"
    gads_value = float(gads.get("conversions_value") or 0)
    g_spend_uah = float(gads_spend_uah or g_spend_native)
    camps, gads_utm_matched = _enrich_gads_campaigns(
        gads.get("campaigns") or [],
        total_crm_revenue=g_rev,
        total_gads_spend=g_spend_native,
        attribution=_campaign_attribution_from_orders(orders, "google_ads"),
    )
    gads_prev = gads_prev or {}
    prev_g_rev = float(prev_ch.get(CHANNEL_GOOGLE_ADS, {}).get("revenue", 0))
    prev_camps, _ = _enrich_gads_campaigns(
        gads_prev.get("campaigns") or [],
        total_crm_revenue=prev_g_rev,
        total_gads_spend=float(gads_prev.get("spend") or 0),
        attribution=_campaign_attribution_from_orders(orders, "google_ads", previous=True),
    )
    _attach_metric_deltas(
        camps,
        prev_camps,
        key="name",
        metrics=(
            ("crm_orders", "delta_orders_pct"),
            ("gads_conversions", "delta_conversions_pct"),
            ("gads_revenue", "delta_revenue_pct"),
            ("spend", "delta_spend_pct"),
        ),
    )
    camps = camps[:5]
    gads_daily_map = {
        d["date"]: {
            "spend": float(d.get("spend") or 0),
            "revenue": float(d.get("conversion_value") or 0),
        }
        for d in (gads.get("daily") or [])
        if d.get("date")
    }
    google_chart = []
    for day in sorted(gads_daily_map.keys()):
        row = gads_daily_map[day]
        google_chart.append({
            "date": day,
            "revenue": round(row["revenue"], 2),
            "orders": 0,
            "spend": round(row["spend"], 2),
        })
    seo_chart = [
        {"date": d["date"], "clicks": d.get("clicks", 0)}
        for d in (gsc.get("daily") or [])
    ]
    total_rev = float(orders["current"]["total_revenue"] or 0)
    share = lambda rev: round(100.0 * float(rev) / total_rev, 1) if total_rev else 0.0
    g_crm_cpa = _safe_cpa(g_spend_uah, g_ord)
    g_gads_cpa = gads.get("cost_per_conversion") or _safe_cpa(
        g_spend_native, float(gads.get("conversions") or 0),
    )
    total_native = g_spend_native or 1.0
    preview_camps = []
    for c in camps:
        sp_nat = float(c.get("spend") or 0)
        sp_uah = round(g_spend_uah * (sp_nat / total_native), 2) if g_spend_uah else sp_nat
        crm_rev = float(c.get("crm_revenue") or 0)
        preview_camps.append({
            **c,
            "spend_uah": sp_uah,
            "revenue": round(crm_rev, 2),
            "roas_pct": _roas_pct(_safe_roas(crm_rev, sp_uah)),
        })
    meta_preview = _build_meta_overview_preview(
        orders,
        meta_api or {},
        meta_prev or {},
        meta_spend_uah=meta_spend_uah,
        meta_prev_spend_uah=meta_prev_spend_uah,
    )
    return {
        "campaign_attribution_utm": bool(gads_utm_matched or meta_preview.get("campaign_attribution_utm")),
        "google_preview": {
            "spend": g_spend_native,
            "spend_uah": round(g_spend_uah, 2),
            "currency_suffix": meta_currency_suffix(gads_currency),
            "revenue": round(g_rev, 2),
            "revenue_google": round(gads_value, 2) if gads_value > 0 else None,
            "orders": g_ord,
            "revenue_share_pct": share(g_rev),
            "cpa": g_crm_cpa,
            "crm_cpa": g_crm_cpa,
            "gads_cpa": g_gads_cpa,
            "roas_pct": _roas_pct(_safe_roas(g_rev, g_spend_uah)),
            "conversion_rate_pct": format_conversion_rate(
                _safe_conversion_rate(g_ord, int(gads.get("clicks") or 0)),
            ),
            "campaigns": preview_camps,
            "chart": google_chart,
        },
        "seo_preview": {
            "clicks": gsc.get("clicks"),
            "revenue": s_rev,
            "orders": s_ord,
            "spend": round(float(seo_spend_uah or 0), 2),
            "roas_pct": _roas_pct(_safe_roas(s_rev, float(seo_spend_uah or 0))) if seo_spend_uah else None,
            "revenue_share_pct": share(s_rev),
            "ctr": gsc.get("ctr"),
            "conversion_rate_pct": format_conversion_rate(
                _safe_conversion_rate(s_ord, int(gsc.get("clicks") or 0)),
            ),
            "chart": seo_chart,
        },
        "business": business,
        "meta_preview": meta_preview,
    }


def _build_meta_overview_preview(
    orders: dict[str, Any],
    meta_api: dict[str, Any],
    meta_prev: dict[str, Any],
    *,
    meta_spend_uah: float,
    meta_prev_spend_uah: float = 0.0,
) -> dict[str, Any]:
    """Превʼю Meta на огляді: дохід/замовлення з CRM, витрати з Ads Manager."""
    cur_ch = orders["current"]["by_channel"]
    prev_ch = orders["previous"]["by_channel"]
    m_rev = float(cur_ch.get(CHANNEL_META_ADS, {}).get("revenue", 0))
    m_ord = int(cur_ch.get(CHANNEL_META_ADS, {}).get("orders", 0))
    prev_m_rev = float(prev_ch.get(CHANNEL_META_ADS, {}).get("revenue", 0))
    m_spend = float(meta_api.get("spend") or 0)
    meta_currency = meta_api.get("currency") or "USD"
    camps, meta_utm_matched = _enrich_meta_campaigns(
        meta_api.get("campaigns") or [],
        total_crm_revenue=m_rev,
        total_meta_spend=float(meta_spend_uah or 0),
        attribution=_campaign_attribution_from_orders(orders, CHANNEL_META_ADS),
    )
    prev_camps, _ = _enrich_meta_campaigns(
        (meta_prev or {}).get("campaigns") or [],
        total_crm_revenue=prev_m_rev,
        total_meta_spend=float(meta_prev_spend_uah or meta_spend_uah or 0),
        attribution=_campaign_attribution_from_orders(orders, CHANNEL_META_ADS, previous=True),
    )
    _attach_metric_deltas(
        camps,
        prev_camps,
        key="name",
        metrics=(
            ("crm_orders", "delta_orders_pct"),
            ("crm_revenue", "delta_revenue_pct"),
            ("meta_purchases", "delta_conversions_pct"),
            ("spend", "delta_spend_pct"),
        ),
    )
    total_rev = float(orders["current"]["total_revenue"] or 0)
    meta_purchases = int(meta_api.get("purchases") or 0)
    m_crm_cpa = _safe_cpa(float(meta_spend_uah or 0), m_ord) if m_ord else None
    m_meta_cpa = meta_api.get("cost_per_purchase") or _safe_cpa(m_spend, meta_purchases)
    attr = _campaign_attribution_from_orders(orders, CHANNEL_META_ADS)
    resolved = resolve_campaign_attribution(
        [str(c.get("name") or "") for c in camps],
        attr,
    )
    unassigned_orders, unassigned_revenue = attribution_unassigned_totals(attr, resolved)
    return {
        "spend": m_spend,
        "spend_uah": round(float(meta_spend_uah or 0), 2),
        "currency_suffix": meta_currency_suffix(meta_currency),
        "campaign_attribution_utm": meta_utm_matched,
        "revenue": round(m_rev, 2),
        "orders": m_ord,
        "purchases": meta_purchases,
        "revenue_share_pct": round(100.0 * m_rev / total_rev, 1) if total_rev else 0.0,
        "cpa": m_crm_cpa,
        "crm_cpa": m_crm_cpa,
        "meta_cpa": m_meta_cpa,
        "roas_pct": _roas_pct(_safe_roas(m_rev, meta_spend_uah or m_spend)),
        "conversion_rate_pct": format_conversion_rate(
            _safe_conversion_rate(m_ord, int(meta_api.get("clicks") or 0)),
        ),
        "campaigns": camps[:5],
        "unassigned_crm_orders": unassigned_orders,
        "unassigned_crm_revenue": unassigned_revenue,
    }


def _build_insight_payload(
    *,
    overview_rows: list[dict[str, Any]],
    orders: dict[str, Any],
    gads: dict[str, Any],
    gads_prev: dict[str, Any],
    meta_api: dict[str, Any],
    meta_prev: dict[str, Any],
    gsc: dict[str, Any],
    gsc_prev: dict[str, Any],
    top_brands: list[dict[str, Any]],
    period_end: datetime,
    prev_period_end: datetime,
    chip_limit: int = 3,
    feed_limit: int = 20,
    ga4: dict[str, Any] | None = None,
    ga4_prev: dict[str, Any] | None = None,
    business: dict[str, Any] | None = None,
    categories: set[str] | None = None,
) -> dict[str, Any]:
    compare_label = (orders.get("period") or {}).get("compare_label") or "vs попередній період"
    feed = build_ad_insight_feed(
        overview_rows=overview_rows,
        orders=orders,
        gads=gads,
        gads_prev=gads_prev,
        meta=meta_api,
        meta_prev=meta_prev,
        gsc=gsc,
        gsc_prev=gsc_prev,
        top_brands=top_brands,
        period_end=period_end,
        prev_period_end=prev_period_end,
        compare_label=compare_label,
        limit=feed_limit,
        ga4=ga4,
        ga4_prev=ga4_prev,
        business=business,
        categories=categories,
    )
    chips = insight_chips_from_feed(feed, limit=chip_limit)
    return {
        "insight_feed": feed,
        "insight_chips": chips,
        "insights": [c["text"] for c in chips],
    }


def _section_insight_payload(
    section: str,
    *,
    overview_rows: list[dict[str, Any]],
    orders: dict[str, Any],
    gads: dict[str, Any],
    gads_prev: dict[str, Any],
    meta_api: dict[str, Any],
    meta_prev: dict[str, Any],
    gsc: dict[str, Any],
    gsc_prev: dict[str, Any],
    top_brands: list[dict[str, Any]],
    period_end: datetime,
    prev_period_end: datetime,
    ga4: dict[str, Any] | None = None,
    ga4_prev: dict[str, Any] | None = None,
    business: dict[str, Any] | None = None,
    chip_limit: int = 3,
    feed_limit: int = 12,
) -> dict[str, Any]:
    from web.insights.engine import SECTION_INSIGHT_CATEGORIES

    return _build_insight_payload(
        overview_rows=overview_rows,
        orders=orders,
        gads=gads,
        gads_prev=gads_prev,
        meta_api=meta_api,
        meta_prev=meta_prev,
        gsc=gsc,
        gsc_prev=gsc_prev,
        top_brands=top_brands,
        period_end=period_end,
        prev_period_end=prev_period_end,
        ga4=ga4,
        ga4_prev=ga4_prev,
        business=business,
        categories=SECTION_INSIGHT_CATEGORIES.get(section),
        chip_limit=chip_limit,
        feed_limit=feed_limit,
    )


def _enrich_brands(brands: list[dict], total_spend: float, total_rev: float) -> list[dict]:
    """Додає ROAS%, CPA, частку доходу до таблиці брендів."""
    result = []
    for b in brands:
        rev = float(b.get("revenue") or 0)
        orders = int(b.get("orders") or 0)
        share = round(100 * rev / total_rev, 1) if total_rev else 0.0
        sp = round(total_spend * (rev / total_rev), 2) if total_rev and total_spend else 0.0
        result.append({
            **b,
            "spend": sp,
            "roas_pct": _roas_pct(_safe_roas(rev, sp)),
            "cpa": round(sp / orders, 2) if orders and sp else None,
            "revenue_share_pct": share,
        })
    return result


def _seo_queries_with_position_change(queries: list[dict], prev_queries: list[dict]) -> list[dict]:
    """Додає зміну позиції (△) та кліків до топ-запитів GSC."""
    prev_map = {q["key"]: q for q in prev_queries}
    result = []
    for q in queries:
        prev = prev_map.get(q["key"]) or {}
        prev_pos = prev.get("position")
        cur_pos = q.get("position")
        if prev_pos and cur_pos:
            change = round(prev_pos - cur_pos, 1)  # позитивне = покращення
        else:
            change = None
        result.append({
            **q,
            "position_change": change,
            "delta_clicks_pct": _pct_delta(q.get("clicks") or 0, prev.get("clicks") or 0),
        })
    return result


def _source_distribution_donut(sources: list[dict]) -> dict[str, Any]:
    """Donut розподілу джерел для «Інші джерела»."""
    # Групуємо джерела по типу
    groups: dict[str, float] = {}
    type_colors: dict[str, str] = {
        "email": "#F59E0B",
        "referral": "#4285F4",
        "social": "#E91E63",
        "affiliate": "#34A853",
        "other": "#94A3B8",
    }
    for s in sources:
        src = (s.get("source") or "").lower()
        if "email" in src or "newsletter" in src:
            t = "email"
        elif "facebook" in src or "instagram" in src or "telegram" in src:
            t = "social"
        elif "affiliate" in src or "partner" in src:
            t = "affiliate"
        else:
            t = "referral"
        groups[t] = groups.get(t, 0) + float(s.get("revenue") or 0)

    ua_labels = {
        "email": "Email",
        "referral": "Реферальний",
        "social": "Органічні соцмережі",
        "affiliate": "Партнерські",
        "other": "Інше",
    }
    items = [{"name": ua_labels.get(k, k), "spend": v, "color": type_colors.get(k, "#94A3B8")} for k, v in groups.items() if v > 0]
    if not items:
        return {"labels": [], "values": [], "colors": [], "total": 0, "legend": []}
    return _donut_from_spend_list(items, value_key="spend", palette=[i["color"] for i in items])


def _channel_traffic_metrics(
    gads: dict[str, Any],
    meta: dict[str, Any],
    gsc: dict[str, Any],
    ga4: dict[str, Any],
    gads_prev: dict[str, Any] | None = None,
    meta_prev: dict[str, Any] | None = None,
    gsc_prev: dict[str, Any] | None = None,
    ga4_prev: dict[str, Any] | None = None,
) -> tuple[dict[str, int], dict[str, int]]:
    """Трафік по каналах для коефіцієнта конверсії (кліки або сесії GA4)."""
    ga4 = ga4 or {}
    ga4_prev = ga4_prev or {}
    ga4_org = ga4.get("organic") or {}
    ga4_org_prev = ga4_prev.get("organic") or {}
    ga4_dir = ga4.get("direct") or {}
    ga4_dir_prev = ga4_prev.get("direct") or {}
    other_sessions = sum(int(g.get("sessions") or 0) for g in (ga4.get("other_groups") or []))
    other_sessions_prev = sum(int(g.get("sessions") or 0) for g in (ga4_prev.get("other_groups") or []))

    def _seo_traffic(gsc_data: dict, ga4_organic: dict) -> int:
        clicks = int(gsc_data.get("clicks") or 0)
        if clicks:
            return clicks
        return int(ga4_organic.get("sessions") or 0)

    current = {
        CHANNEL_GOOGLE_ADS: int(gads.get("clicks") or 0),
        CHANNEL_META_ADS: int(meta.get("clicks") or 0),
        CHANNEL_SEO: _seo_traffic(gsc, ga4_org),
        CHANNEL_DIRECT: int(ga4_dir.get("sessions") or 0),
        CHANNEL_OTHER: other_sessions,
    }
    previous = {
        CHANNEL_GOOGLE_ADS: int((gads_prev or {}).get("clicks") or 0),
        CHANNEL_META_ADS: int((meta_prev or {}).get("clicks") or 0),
        CHANNEL_SEO: _seo_traffic(gsc_prev or {}, ga4_org_prev),
        CHANNEL_DIRECT: int(ga4_dir_prev.get("sessions") or 0),
        CHANNEL_OTHER: other_sessions_prev,
    }
    return current, previous


def _attach_conversion_rate_kpi(
    kpi: dict[str, Any],
    orders: int,
    traffic: int,
    prev_orders: int,
    prev_traffic: int,
) -> None:
    conv_rate = _safe_conversion_rate(orders, traffic)
    prev_conv_rate = _safe_conversion_rate(prev_orders, prev_traffic)
    kpi["conversion_rate"] = conv_rate
    kpi["conversion_rate_pct"] = format_conversion_rate(conv_rate)
    kpi.setdefault("delta", {})
    kpi["delta"]["conversion_rate_pct"] = _pct_delta(conv_rate or 0, prev_conv_rate or 0)


def _compute_overview_kpis(
    orders: dict,
    gads: dict,
    meta: dict,
    gads_prev: dict,
    meta_prev: dict,
    *,
    period_end: datetime,
    prev_period_end: datetime,
    gsc: dict[str, Any] | None = None,
    ga4: dict[str, Any] | None = None,
    gsc_prev: dict[str, Any] | None = None,
    ga4_prev: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rev = float(orders["current"]["total_revenue"] or 0)
    prev_rev = float(orders["previous"]["total_revenue"] or 0)
    ord_count = int(orders["current"]["total_orders"] or 0)
    prev_ord = int(orders["previous"]["total_orders"] or 0)

    g_spend = _channel_spend_for_overview(gads, gads_prev, as_of=period_end, prev_as_of=prev_period_end)
    m_spend = _channel_spend_for_overview(meta, meta_prev, as_of=period_end, prev_as_of=prev_period_end)
    spend = g_spend["spend"] + m_spend["spend"]
    prev_spend = g_spend["prev_spend"] + m_spend["prev_spend"]
    spend_approximate = bool(
        m_spend["native"].get("converted") or g_spend["native"].get("converted")
    )
    traffic_cur, traffic_prev = _channel_traffic_metrics(
        gads, meta, gsc or {}, ga4 or {},
        gads_prev=gads_prev, meta_prev=meta_prev,
        gsc_prev=gsc_prev or {}, ga4_prev=ga4_prev or {},
    )
    total_traffic = sum(traffic_cur.values())
    prev_total_traffic = sum(traffic_prev.values())
    conv_rate = _safe_conversion_rate(ord_count, total_traffic)
    prev_conv_rate = _safe_conversion_rate(prev_ord, prev_total_traffic)

    return {
        "spend": round(spend, 2),
        "revenue": round(rev, 2),
        "roas": _safe_roas(rev, spend),
        "roas_pct": _roas_pct(_safe_roas(rev, spend)),
        "orders": ord_count,
        "conversions": ord_count,
        "cpa": _safe_cpa(spend, ord_count),
        "avg_check": round(rev / ord_count, 2) if ord_count else None,
        "clicks": gads["clicks"] + meta["clicks"],
        "impressions": gads["impressions"] + meta["impressions"],
        "traffic": total_traffic,
        "conversion_rate": conv_rate,
        "conversion_rate_pct": format_conversion_rate(conv_rate),
        "spend_approximate": spend_approximate,
        "spend_fx_note": format_fx_spend_note(
            meta_native=m_spend["native"],
            gads_native=g_spend["native"],
        ),
        "spend_breakdown": {
            "google_ads_uah": g_spend["spend"],
            "meta_uah": m_spend["spend"],
            "meta_usd": m_spend["native"].get("amount_raw"),
            "fx_rate": m_spend["native"].get("fx_rate"),
            "fx_date": m_spend["native"].get("fx_date"),
        },
        "delta": {
            "spend_pct": _pct_delta(spend, prev_spend),
            "revenue_pct": _pct_delta(rev, prev_rev),
            "orders_pct": _pct_delta(ord_count, prev_ord),
            "roas_pct": _pct_delta(_safe_roas(rev, spend) or 0, _safe_roas(prev_rev, prev_spend) or 0),
            "cpa_pct": _pct_delta(
                _safe_cpa(spend, ord_count) or 0,
                _safe_cpa(prev_spend, prev_ord) or 0,
            ),
            "avg_check_pct": _pct_delta(
                rev / ord_count if ord_count else 0,
                prev_rev / prev_ord if prev_ord else 0,
            ),
            "conversion_rate_pct": _pct_delta(conv_rate or 0, prev_conv_rate or 0),
        },
    }


def _merge_direct_chart(
    ga4_daily: list[dict],
    crm_by_day: dict[str, dict],
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[dict]:
    by_date = {d["date"]: d for d in ga4_daily if d.get("date")}
    dates = set(by_date.keys()) | set(crm_by_day.keys())
    if start and end:
        cur = start.date()
        last = end.date()
        while cur <= last:
            dates.add(cur.isoformat())
            cur += timedelta(days=1)
    out = []
    for day in sorted(dates):
        g = by_date.get(day, {})
        crm = crm_by_day.get(day, {})
        out.append({
            "date": day,
            "sessions": int(g.get("sessions") or 0),
            "orders": int(crm.get("orders") or 0),
            "revenue": round(float(crm.get("revenue") or 0), 2),
        })
    return out


def _other_donut_from_ga4(groups: list[dict]) -> dict[str, Any]:
    label_map = {
        "Referral": "Referral",
        "Email": "Email",
        "Organic Social": "Органічні соцмережі",
        "Organic Video": "Відео",
        "Affiliates": "Партнерські",
        "Unassigned": "Інше",
    }
    items = [{"name": label_map.get(g["name"], g["name"]), "spend": g["sessions"]} for g in groups if g.get("sessions")]
    donut = _donut_from_spend_list(
        items,
        value_key="spend",
        palette=["#4285F4", "#F59E0B", "#E91E63", "#34A853", "#9C27B0", "#94A3B8"],
    )
    donut["unit"] = "sessions"
    return donut


def _finalize_report(
    report: dict[str, Any],
    *,
    section: str,
    orders: dict[str, Any],
    start: datetime,
    end: datetime,
    gads: dict[str, Any],
    meta_api: dict[str, Any],
    gsc: dict[str, Any],
    ga4: dict[str, Any] | None = None,
    chart_granularity: str = "day",
) -> dict[str, Any]:
    ch_rows = report.get("overview_channels")
    if ch_rows and not (ch_rows[0].get("slug") if ch_rows else False):
        report["overview_channels"] = _enrich_overview_channels(
            ch_rows, meta_api=meta_api, gads=gads,
        )
    report["integrations_panel"] = build_integrations_panel(
        start, end, gads=gads, meta=meta_api, gsc=gsc, ga4=ga4 or {}, crm_updated=orders.get("generated_at"),
    )
    report["section_integrations"] = build_section_integration_settings(
        section, report["integrations_panel"],
    )
    if not report.get("insight_chips") and report.get("insights"):
        report["insight_chips"] = [_tip(t) for t in (report.get("insights") or [])[:3]]
    gran = normalize_ad_chart_granularity(chart_granularity)
    chart = report.get("chart")
    if isinstance(chart, list) and chart:
        report["chart"] = aggregate_chart_series(chart, gran)
    report["chart_granularity"] = gran
    generated_at = report.get("generated_at")
    if generated_at:
        for item in report.get("insight_feed") or []:
            if isinstance(item, dict) and not item.get("generated_at"):
                item["generated_at"] = generated_at
    return report


async def build_advertising_report(
    session: AsyncSession,
    *,
    section: str = "overview",
    date_from: str | None = None,
    date_to: str | None = None,
    refresh: bool = False,
    compare: str = "prev",
    chart_granularity: str | None = None,
) -> dict[str, Any]:
    from config import refresh_marketing_config

    refresh_marketing_config()

    section = section if section in SECTIONS else "overview"
    chart_gran = normalize_ad_chart_granularity(chart_granularity)
    channel_map = {
        "google_ads": CHANNEL_GOOGLE_ADS,
        "meta": CHANNEL_META_ADS,
        "seo": CHANNEL_SEO,
        "direct": CHANNEL_DIRECT,
        "other": CHANNEL_OTHER,
    }
    channel = channel_map.get(section)

    orders = await load_orders_analytics(
        session, date_from=date_from, date_to=date_to, channel=channel, refresh=refresh, compare=compare,
    )
    start, end, prev_start, prev_end = _parse_period_dates(orders)
    use_cache = not refresh

    needs_gads = section in ("overview", "google_ads", "insights", "business")
    needs_meta = section in ("overview", "meta", "insights", "business")
    needs_gsc = section in ("overview", "seo", "insights")
    needs_ga4 = section in ("overview", "seo", "direct", "other", "insights", "business", "details")
    from web.integrations.google_analytics import fetch_ga4_stats, _empty_payload as empty_ga4_payload

    async def _api_pair(enabled: bool, fetch_fn, empty_fn):
        if not enabled:
            return empty_fn(), empty_fn()
        return await asyncio.gather(
            asyncio.to_thread(fetch_fn, start, end, use_cache=use_cache),
            asyncio.to_thread(fetch_fn, prev_start, prev_end, use_cache=use_cache),
        )

    cur_orders_pairs = orders.pop("_cur_orders_pairs", None)

    (gads, gads_prev), (meta_api, meta_prev), (gsc, gsc_prev), (ga4, ga4_prev) = await asyncio.gather(
        _api_pair(needs_gads, fetch_google_ads_stats, empty_gads_payload),
        _api_pair(needs_meta, fetch_meta_ads_stats, empty_meta_payload),
        _api_pair(needs_gsc, fetch_gsc_stats, empty_gsc_payload),
        _api_pair(needs_ga4, fetch_ga4_stats, empty_ga4_payload),
    )

    gads_spend = _channel_spend_for_overview(gads, gads_prev, as_of=end, prev_as_of=prev_end)
    meta_spend = _channel_spend_for_overview(meta_api, meta_prev, as_of=end, prev_as_of=prev_end)

    seo_spend = _seo_period_spend_uah(start, end)
    seo_prev_spend = _seo_period_spend_uah(prev_start, prev_end)
    spend_by_channel = {
        CHANNEL_GOOGLE_ADS: {"spend": gads_spend["spend"], "prev_spend": gads_spend["prev_spend"]},
        CHANNEL_META_ADS: {"spend": meta_spend["spend"], "prev_spend": meta_spend["prev_spend"]},
        CHANNEL_SEO: {"spend": seo_spend, "prev_spend": seo_prev_spend},
        CHANNEL_DIRECT: {"spend": 0.0, "prev_spend": 0.0},
        CHANNEL_OTHER: {"spend": 0.0, "prev_spend": 0.0},
    }

    traffic_cur, traffic_prev = _channel_traffic_metrics(
        gads, meta_api, gsc, ga4,
        gads_prev=gads_prev, meta_prev=meta_prev,
        gsc_prev=gsc_prev, ga4_prev=ga4_prev,
    )
    overview_rows = merge_channel_overview(
        orders, spend_by_channel, traffic_cur, traffic_prev,
    )
    total_revenue = float(orders["current"]["total_revenue"] or 0)
    cur_ch = orders["current"]
    prev_ch = orders["previous"]
    cur_rev = float(cur_ch["total_revenue"] or 0)
    cur_ord = int(cur_ch["total_orders"] or 0)
    prev_rev = float(prev_ch["total_revenue"] or 0)
    prev_ord = int(prev_ch["total_orders"] or 0)

    # ───────────── OVERVIEW ─────────────
    if section == "overview":
        ov_kpi = _compute_overview_kpis(
            orders, gads, meta_api, gads_prev, meta_prev,
            period_end=end, prev_period_end=prev_end,
            gsc=gsc, ga4=ga4, gsc_prev=gsc_prev, ga4_prev=ga4_prev,
        )
        daily_spend: dict[str, float] = {}
        meta_daily_uah = convert_daily_spend_series(
            meta_api.get("daily") or [], meta_api.get("currency"),
        )
        for d in (gads.get("daily") or []) + meta_daily_uah:
            k = d.get("date")
            if k:
                daily_spend[k] = daily_spend.get(k, 0.0) + float(d.get("spend", 0))
        chart = _daily_series(orders["current"]["by_day"], daily_spend)
        channel_donut = _channel_donut(overview_rows)
        top_brands = _attach_brand_deltas(
            _enrich_brands(_top_brands_table(orders["brands_current"]), 0, total_revenue)[:5],
            orders["brands_previous"],
        )
        business = await load_overview_business_snapshot(
            session, start, end, refresh=refresh, orders_pairs=cur_orders_pairs,
        )
        extra = _build_overview_extra(
            orders, gads, gsc, business,
            gads_prev=gads_prev,
            meta_api=meta_api,
            meta_prev=meta_prev,
            meta_spend_uah=float(meta_spend["spend"] or 0),
            meta_prev_spend_uah=float(meta_spend["prev_spend"] or 0),
            gads_spend_uah=float(gads_spend["spend"] or 0),
            seo_spend_uah=seo_spend,
        )
        insight_payload = _build_insight_payload(
            overview_rows=overview_rows,
            orders=orders,
            gads=gads,
            gads_prev=gads_prev,
            meta_api=meta_api,
            meta_prev=meta_prev,
            gsc=gsc,
            gsc_prev=gsc_prev,
            top_brands=top_brands,
            period_end=end,
            prev_period_end=prev_end,
            ga4=ga4,
            ga4_prev=ga4_prev,
            business=business,
        )
        insight_chips = insight_payload["insight_chips"]
        insights = insight_payload["insights"]
        return _finalize_report({
            "section": section,
            "section_label": SECTION_LABELS[section],
            "period": orders["period"],
            "kpi": ov_kpi,
            "chart": chart,
            "donut": channel_donut,
            "top_brands": top_brands,
            "overview_channels": _enrich_overview_channels(
                overview_rows, meta_api=meta_api, gads=gads,
            ),
            "insights": insights,
            "insight_chips": insight_chips,
            "insight_feed": insight_payload["insight_feed"],
            "api_status": {"configured": True, "error": None},
            "extra": extra,
            "campaign_attribution_utm": extra.get("campaign_attribution_utm"),
            "generated_at": orders.get("generated_at"),
            "cache_updated": cache_last_updated(f"gads_v4_{start.date()}_{end.date()}"),
            "orders_attribution_note": "Дохід і конверсії — успішні замовлення з CRM Cosmy за UTM/джерелом.",
        }, orders=orders, start=start, end=end, gads=gads, meta_api=meta_api, gsc=gsc, ga4=ga4, section=section, chart_granularity=chart_gran)

    # ───────────── GOOGLE ADS ─────────────
    elif section == "google_ads":
        gads_value = float(gads.get("conversions_value") or 0)
        gads_conv = float(gads.get("conversions") or 0)
        prev_gads_value = float(gads_prev.get("conversions_value") or 0)
        prev_gads_conv = float(gads_prev.get("conversions") or 0)
        gads_currency = gads.get("currency") or "UAH"
        crm_rev = float(cur_ch.get("total_revenue") or 0)
        crm_ord = int(cur_ch.get("total_orders") or 0)
        prev_crm_rev = float(prev_ch.get("total_revenue") or 0)
        prev_crm_ord = int(prev_ch.get("total_orders") or 0)

        revenue_source = "crm" if crm_rev > 0 else ("google" if gads_value > 0 else "none")

        kpi = _kpi_block(
            crm_rev if crm_rev > 0 else gads_value,
            crm_ord if crm_ord > 0 else int(round(gads_conv)),
            gads["spend"],
            impressions=gads["impressions"],
            clicks=gads["clicks"],
            prev_revenue=prev_crm_rev if prev_crm_rev > 0 else prev_gads_value,
            prev_orders=prev_crm_ord if prev_crm_ord > 0 else int(round(prev_gads_conv)),
            prev_spend=gads_prev["spend"],
            prev_clicks=gads_prev["clicks"],
            prev_impressions=gads_prev["impressions"],
        )
        if gads.get("ctr") is not None:
            kpi["ctr"] = gads["ctr"]
        if gads.get("cpc") is not None:
            kpi["cpc"] = gads["cpc"]
        kpi["currency"] = gads_currency
        kpi["currency_suffix"] = meta_currency_suffix(gads_currency)
        kpi["gads_conversions"] = round(gads_conv, 1)
        kpi["gads_revenue"] = round(gads_value, 2)
        kpi["revenue_source"] = revenue_source
        kpi["crm_revenue"] = round(crm_rev, 2)
        kpi["crm_orders"] = crm_ord
        kpi["prev_crm_revenue"] = round(prev_crm_rev, 2)
        kpi["prev_crm_orders"] = prev_crm_ord
        spend_uah = float(gads_spend["spend"] or 0)
        prev_spend_uah = float(gads_spend["prev_spend"] or 0)
        crm_roas = _safe_roas(crm_rev, spend_uah)
        prev_crm_roas = _safe_roas(prev_crm_rev, prev_spend_uah)
        gads_roas = _safe_roas(gads_value, gads["spend"]) if gads_value > 0 else None
        prev_gads_roas = _safe_roas(prev_gads_value, gads_prev["spend"]) if prev_gads_value > 0 else None
        kpi["crm_roas_pct"] = _roas_pct(crm_roas)
        kpi["roas_pct"] = _roas_pct(crm_roas) if crm_rev > 0 else meta_roas_display(gads_roas)
        kpi["roas_label"] = "ROAS CRM" if crm_rev > 0 else "ROAS Google"
        kpi["gads_roas_pct"] = meta_roas_display(gads_roas) if gads_roas else None
        kpi["gads_cpa"] = gads.get("cost_per_conversion") or _safe_cpa(gads["spend"], gads_conv or 0)
        kpi["crm_cpa"] = _safe_cpa(spend_uah, crm_ord) if crm_ord else None
        kpi["cpa"] = kpi["crm_cpa"]
        kpi["avg_quality_score"] = gads.get("avg_quality_score")
        kpi["search_impression_share"] = gads.get("search_impression_share")
        prev_gads_cpa = gads_prev.get("cost_per_conversion") or _safe_cpa(
            gads_prev["spend"], prev_gads_conv or 0,
        )
        prev_crm_cpa = _safe_cpa(prev_spend_uah, prev_crm_ord) if prev_crm_ord else None
        kpi["delta"]["roas_pct"] = _pct_delta(float(crm_roas or 0), float(prev_crm_roas or 0))
        kpi["delta"]["gads_roas_pct"] = _pct_delta(float(gads_roas or 0), float(prev_gads_roas or 0))
        kpi["delta"]["cpa_pct"] = _pct_delta(kpi.get("crm_cpa") or 0, prev_crm_cpa or 0)
        kpi["delta"]["gads_cpa_pct"] = _pct_delta(kpi.get("gads_cpa") or 0, prev_gads_cpa or 0)
        kpi["delta"]["gads_conversions_pct"] = _pct_delta(gads_conv, prev_gads_conv)
        kpi["delta"]["crm_orders_pct"] = _pct_delta(crm_ord, prev_crm_ord)
        kpi["delta"]["crm_revenue_pct"] = _pct_delta(crm_rev, prev_crm_rev)
        _attach_conversion_rate_kpi(
            kpi, crm_ord, int(gads.get("clicks") or 0),
            prev_crm_ord, int(gads_prev.get("clicks") or 0),
        )

        if gads_value > 0:
            gads_by_day = {
                d["date"]: {
                    "revenue": float(d.get("conversion_value") or 0),
                    "orders": round(float(d.get("conversions") or 0), 2),
                }
                for d in (gads.get("daily") or [])
                if d.get("date")
            }
        else:
            gads_by_day = {
                day: {
                    "revenue": float(vals.get("revenue") or 0),
                    "orders": int(vals.get("orders") or 0),
                }
                for day, vals in (cur_ch.get("by_day") or {}).items()
            }
        daily_spend_uah = {
            d["date"]: float(d.get("spend") or 0)
            for d in convert_daily_spend_series(gads.get("daily") or [], gads_currency)
            if d.get("date")
        }
        chart = _daily_series(gads_by_day, daily_spend_uah)
        spend_by_type = gads.get("spend_by_type") or []
        donut = _donut_from_spend_list(spend_by_type, palette=["#4285F4","#34A853","#FBBC05","#EA4335","#9C27B0"])
        enriched_camps, utm_matched = _enrich_gads_campaigns(
            gads.get("campaigns") or [],
            total_crm_revenue=crm_rev,
            total_gads_spend=gads["spend"],
            attribution=_campaign_attribution_from_orders(orders, "google_ads"),
        )
        prev_camps, _ = _enrich_gads_campaigns(
            gads_prev.get("campaigns") or [],
            total_crm_revenue=prev_crm_rev,
            total_gads_spend=gads_prev["spend"],
            attribution=_campaign_attribution_from_orders(orders, "google_ads", previous=True),
        )
        _attach_metric_deltas(
            enriched_camps,
            prev_camps,
            key="name",
            metrics=(
                ("crm_orders", "delta_orders_pct"),
                ("gads_conversions", "delta_conversions_pct"),
                ("gads_revenue", "delta_revenue_pct"),
                ("spend", "delta_spend_pct"),
            ),
        )
        keywords = [{**k} for k in (gads.get("keywords") or [])]
        _attach_metric_deltas(
            keywords,
            gads_prev.get("keywords") or [],
            key_fn=lambda k: (k.get("keyword"), k.get("match_type")),
            metrics=(
                ("conversions", "delta_conversions_pct"),
                ("clicks", "delta_clicks_pct"),
            ),
        )
        top_brands = _build_channel_top_brands(
            orders["brands_current"],
            orders["brands_previous"],
            crm_rev,
            prev_crm_rev,
            gads_spend["spend"],
        )
        insight_payload = _section_insight_payload(
            "google_ads",
            overview_rows=overview_rows,
            orders=orders,
            gads=gads,
            gads_prev=gads_prev,
            meta_api=meta_api,
            meta_prev=meta_prev,
            gsc=gsc,
            gsc_prev=gsc_prev,
            top_brands=top_brands,
            period_end=end,
            prev_period_end=prev_end,
            ga4=ga4,
            ga4_prev=ga4_prev,
        )
        insights = insight_payload["insights"]
        revenue_note = (
            f"Дохід Google (conversions_value): {round(gads_value, 2):,.0f} {gads_currency}."
            if gads_value > 0
            else "Дохід Google: не налаштовано в Ads (conversions_value = 0) — у KPI показано дохід CRM."
        )
        return _finalize_report({
            "section": section,
            "section_label": SECTION_LABELS[section],
            "period": orders["period"],
            "kpi": kpi,
            "chart": chart,
            "donut": donut,
            "donut_label": "Витрати за типом кампанії",
            "top_brands": top_brands,
            "overview_channels": overview_rows,
            "insights": insights,
            "insight_chips": insight_payload["insight_chips"],
            "insight_feed": insight_payload["insight_feed"],
            "api_status": gads,
            "extra": {
                "campaigns": enriched_camps,
                "keywords": keywords,
                "spend_by_type": spend_by_type,
            },
            "generated_at": orders.get("generated_at"),
            "cache_updated": cache_last_updated(f"gads_v4_{start.date()}_{end.date()}"),
            "orders_attribution_note": (
                f"Витрати, кліки, покази — Google Ads API ({gads_currency}); "
                f"конверсії — за часом конверсії (як «Conversions by conv. time» у кабінеті). "
                f"{revenue_note} "
                f"CRM: {crm_ord} замовлень, {round(crm_rev, 2):,.0f} ₴."
                .replace(",", " ")
            ),
            "campaign_attribution_utm": utm_matched,
        }, orders=orders, start=start, end=end, gads=gads, meta_api=meta_api, gsc=gsc, ga4=ga4, section=section, chart_granularity=chart_gran)

    # ───────────── META ADS ─────────────
    elif section == "meta":
        meta_revenue = float(meta_api.get("purchase_value") or 0)
        meta_prev_revenue = float(meta_prev.get("purchase_value") or 0)
        meta_purchases = int(meta_api.get("purchases") or 0)
        meta_prev_purchases = int(meta_prev.get("purchases") or 0)
        meta_roas = meta_api.get("purchase_roas")
        meta_prev_roas = meta_prev.get("purchase_roas")
        meta_currency = meta_api.get("currency") or "USD"
        crm_rev = cur_rev
        crm_ord = cur_ord
        prev_crm_rev = prev_rev
        prev_crm_ord = prev_ord
        spend_uah = float(meta_spend["spend"] or 0)
        prev_spend_uah = float(meta_spend["prev_spend"] or 0)

        kpi = _kpi_block(
            crm_rev,
            crm_ord,
            meta_api["spend"],
            impressions=meta_api["impressions"],
            clicks=meta_api["clicks"],
            reach=meta_api.get("reach", 0),
            prev_revenue=prev_crm_rev,
            prev_orders=prev_crm_ord,
            prev_spend=meta_prev["spend"],
            prev_clicks=meta_prev["clicks"],
            prev_impressions=meta_prev["impressions"],
        )
        kpi["frequency"] = meta_api.get("frequency")
        kpi["cpm"] = meta_api.get("cpm")
        kpi["purchases"] = meta_purchases
        kpi["meta_revenue"] = round(meta_revenue, 2)
        kpi["currency"] = meta_currency
        kpi["currency_suffix"] = meta_currency_suffix(meta_currency)
        kpi["crm_revenue"] = round(crm_rev, 2)
        kpi["crm_orders"] = crm_ord
        kpi["prev_crm_revenue"] = round(prev_crm_rev, 2)
        kpi["prev_crm_orders"] = prev_crm_ord
        kpi["revenue_source"] = "crm"
        kpi["roas"] = _safe_roas(crm_rev, spend_uah)
        kpi["roas_pct"] = _roas_pct(_safe_roas(crm_rev, spend_uah))
        kpi["meta_roas_pct"] = meta_roas_display(
            meta_roas if isinstance(meta_roas, (int, float)) else None
        )
        kpi["cpa"] = meta_api.get("cost_per_purchase") or _safe_cpa(meta_api["spend"], meta_purchases)
        kpi["crm_cpa"] = _safe_cpa(spend_uah, crm_ord) if crm_ord else None
        prev_cpa = meta_prev.get("cost_per_purchase") or _safe_cpa(
            meta_prev["spend"], meta_prev_purchases
        )
        kpi["delta"]["roas_pct"] = _pct_delta(
            float(_safe_roas(crm_rev, spend_uah) or 0),
            float(_safe_roas(prev_crm_rev, prev_spend_uah) or 0),
        )
        kpi["delta"]["meta_roas_pct"] = _pct_delta(
            float(meta_roas or 0),
            float(meta_prev_roas or 0),
        )
        kpi["delta"]["cpa_pct"] = _pct_delta(kpi.get("cpa") or 0, prev_cpa or 0)
        kpi["delta"]["crm_cpa_pct"] = _pct_delta(kpi.get("crm_cpa") or 0, _safe_cpa(prev_spend_uah, prev_crm_ord) or 0)
        kpi["delta"]["purchases_pct"] = _pct_delta(meta_purchases, meta_prev_purchases)
        kpi["delta"]["crm_orders_pct"] = _pct_delta(crm_ord, prev_crm_ord)
        kpi["delta"]["crm_revenue_pct"] = _pct_delta(crm_rev, prev_crm_rev)
        kpi["delta"]["meta_revenue_pct"] = _pct_delta(meta_revenue, meta_prev_revenue)
        kpi["delta"]["reach_pct"] = _pct_delta(
            int(meta_api.get("reach") or 0),
            int(meta_prev.get("reach") or 0),
        )
        _attach_conversion_rate_kpi(
            kpi, crm_ord, int(meta_api.get("clicks") or 0),
            prev_crm_ord, int(meta_prev.get("clicks") or 0),
        )

        crm_by_day = {
            day: {
                "revenue": float(vals.get("revenue") or 0),
                "orders": int(vals.get("orders") or 0),
            }
            for day, vals in (cur_ch.get("by_day") or {}).items()
        }
        daily_spend_uah = {
            d["date"]: float(d.get("spend") or 0)
            for d in convert_daily_spend_series(meta_api.get("daily") or [], meta_currency)
            if d.get("date")
        }
        chart = _daily_series(crm_by_day, daily_spend_uah)
        placements = meta_api.get("placements") or []
        donut = _donut_from_spend_list(placements, palette=["#1877F2","#E91E63","#FBBC05","#34A853","#9C27B0"])
        enriched_meta_camps, meta_utm_matched = _enrich_meta_campaigns(
            meta_api.get("campaigns") or [],
            total_crm_revenue=crm_rev,
            total_meta_spend=spend_uah,
            attribution=_campaign_attribution_from_orders(orders, CHANNEL_META_ADS),
        )
        prev_meta_camps, _ = _enrich_meta_campaigns(
            meta_prev.get("campaigns") or [],
            total_crm_revenue=prev_crm_rev,
            total_meta_spend=prev_spend_uah,
            attribution=_campaign_attribution_from_orders(orders, CHANNEL_META_ADS, previous=True),
        )
        _attach_metric_deltas(
            enriched_meta_camps,
            prev_meta_camps,
            key="name",
            metrics=(
                ("crm_orders", "delta_orders_pct"),
                ("crm_revenue", "delta_revenue_pct"),
                ("meta_purchases", "delta_conversions_pct"),
                ("spend", "delta_spend_pct"),
            ),
        )
        top_brands = _build_channel_top_brands(
            orders["brands_current"],
            orders["brands_previous"],
            crm_rev,
            prev_crm_rev,
            spend_uah,
        )
        insight_payload = _section_insight_payload(
            "meta",
            overview_rows=overview_rows,
            orders=orders,
            gads=gads,
            gads_prev=gads_prev,
            meta_api=meta_api,
            meta_prev=meta_prev,
            gsc=gsc,
            gsc_prev=gsc_prev,
            top_brands=top_brands,
            period_end=end,
            prev_period_end=prev_end,
            ga4=ga4,
            ga4_prev=ga4_prev,
        )
        insights = insight_payload["insights"]
        revenue_note = (
            f"Дохід Meta (purchase_value): {round(meta_revenue, 2):,.0f} {meta_currency}."
            if meta_revenue > 0
            else "Дохід Meta з Ads Manager = 0 — у KPI і на графіку показано дохід CRM з адмінки."
        )
        return _finalize_report({
            "section": section,
            "section_label": SECTION_LABELS[section],
            "period": orders["period"],
            "kpi": kpi,
            "chart": chart,
            "donut": donut,
            "donut_label": "Витрати за плейсментами",
            "top_brands": top_brands,
            "overview_channels": overview_rows,
            "insights": insights,
            "insight_chips": insight_payload["insight_chips"],
            "insight_feed": insight_payload["insight_feed"],
            "api_status": meta_api,
            "extra": {
                "campaigns": enriched_meta_camps,
                "placements": placements,
                "audience": meta_api.get("audience") or [],
            },
            "generated_at": orders.get("generated_at"),
            "cache_updated": cache_last_updated(f"meta_v3_{start.date()}_{end.date()}"),
            "orders_attribution_note": (
                f"Витрати/кліки — за датою показу; покупки Meta — за часом конверсії ({meta_currency}). "
                f"{revenue_note} "
                f"CRM: {crm_ord} замовлень, {round(crm_rev, 2):,.0f} ₴."
                .replace(",", " ")
            ),
            "campaign_attribution_utm": meta_utm_matched,
        }, orders=orders, start=start, end=end, gads=gads, meta_api=meta_api, gsc=gsc, ga4=ga4, section=section, chart_granularity=chart_gran)

    # ───────────── SEO ─────────────
    elif section == "seo":
        ga4_org = ga4.get("organic") or {}
        ga4_org_prev = ga4_prev.get("organic") or {}
        gsc_clicks = int(gsc.get("clicks") or 0)
        gsc_clicks_prev = int(gsc_prev.get("clicks") or 0)

        seo_spend_cur = _seo_period_spend_uah(start, end)
        seo_spend_prev = _seo_period_spend_uah(prev_start, prev_end)
        kpi = _kpi_block(
            cur_rev, cur_ord,
            spend=seo_spend_cur,
            prev_revenue=prev_rev,
            prev_orders=prev_ord,
            prev_spend=seo_spend_prev,
        )
        kpi["roas_pct"] = _roas_pct(_safe_roas(cur_rev, seo_spend_cur)) if seo_spend_cur else None
        kpi["delta"]["roas_pct"] = _pct_delta(
            float(_safe_roas(cur_rev, seo_spend_cur) or 0),
            float(_safe_roas(prev_rev, seo_spend_prev) or 0),
        ) if seo_spend_cur or seo_spend_prev else None
        kpi["gsc_clicks"] = gsc_clicks
        kpi["organic_clicks"] = gsc_clicks
        kpi["ga4_sessions"] = ga4_org.get("sessions")
        kpi["organic_impressions"] = gsc["impressions"]
        kpi["organic_ctr"] = gsc["ctr"]
        kpi["new_users"] = ga4_org.get("new_users")
        kpi["bounce_rate"] = ga4_org.get("bounce_rate")
        kpi["pages_per_session"] = ga4_org.get("pages_per_session")
        kpi["kw_top10"] = gsc.get("kw_top10")
        kpi["kw_top3"] = gsc.get("kw_top3")
        kpi["indexed_pages"] = gsc.get("indexed_pages")
        kpi["referring_domains"] = ga4.get("referring_domains")
        kpi["delta"]["organic_clicks_pct"] = _pct_delta(gsc_clicks, gsc_clicks_prev)
        kpi["delta"]["ga4_sessions_pct"] = _pct_delta(
            ga4_org.get("sessions") or 0,
            ga4_org_prev.get("sessions") or 0,
        )
        kpi["delta"]["bounce_rate_pct"] = _pct_delta(
            ga4_org_prev.get("bounce_rate") or 0,
            ga4_org.get("bounce_rate") or 0,
        )
        seo_traffic = int(gsc_clicks or 0) or int(ga4_org.get("sessions") or 0)
        seo_traffic_prev = int(gsc_clicks_prev or 0) or int(ga4_org_prev.get("sessions") or 0)
        _attach_conversion_rate_kpi(kpi, cur_ord, seo_traffic, prev_ord, seo_traffic_prev)
        chart = build_gsc_crm_daily_chart(
            start,
            end,
            gsc.get("daily") or [],
            cur_ch.get("by_day") or {},
        )
        gsc_note = gsc_freshness_note_ua(gsc, end)
        # Page type donut — з GSC pages
        pages = gsc.get("pages") or []
        page_types: dict[str, float] = {"Категорії": 0, "Товари": 0, "Головна": 0, "Інше": 0}
        for p in pages:
            url = (p.get("key") or "").lower()
            cl = float(p.get("clicks") or 0)
            if url in ("/", ""):
                page_types["Головна"] += cl
            elif "/product" in url or "product_id" in url:
                page_types["Товари"] += cl
            elif "/category" in url or "/katalog" in url:
                page_types["Категорії"] += cl
            else:
                page_types["Інше"] += cl
        page_type_items = [{"name": k, "spend": v} for k, v in page_types.items() if v > 0]
        donut = _donut_from_spend_list(page_type_items, palette=["#4285F4","#34A853","#FBBC05","#94A3B8"])
        donut["unit"] = "clicks"
        queries_enriched = _seo_queries_with_position_change(gsc.get("queries") or [], gsc_prev.get("queries") or [])
        top_brands = _crm_brands_with_compare(orders["brands_current"], orders["brands_previous"], cur_rev)
        landing_pages_seo = [
            {
                "url": p.get("key") or "—",
                "clicks": int(p.get("clicks") or 0),
                "orders": 0,
                "revenue": 0.0,
            }
            for p in sorted(pages, key=lambda x: -(x.get("clicks") or 0))[:20]
        ]
        from web.integrations.seo_expenses import list_seo_expenses

        seo_expense_rows = list_seo_expenses(date_from=start.date(), date_to=end.date())
        insight_payload = _section_insight_payload(
            "seo",
            overview_rows=overview_rows,
            orders=orders,
            gads=gads,
            gads_prev=gads_prev,
            meta_api=meta_api,
            meta_prev=meta_prev,
            gsc=gsc,
            gsc_prev=gsc_prev,
            top_brands=top_brands,
            period_end=end,
            prev_period_end=prev_end,
            ga4=ga4,
            ga4_prev=ga4_prev,
        )
        insights = insight_payload["insights"]
        return _finalize_report({
            "section": section,
            "section_label": SECTION_LABELS[section],
            "period": orders["period"],
            "kpi": kpi,
            "chart": chart,
            "donut": donut,
            "donut_label": "Трафік за типом сторінок",
            "top_brands": top_brands,
            "overview_channels": overview_rows,
            "insights": insights,
            "insight_chips": insight_payload["insight_chips"],
            "insight_feed": insight_payload["insight_feed"],
            "api_status": gsc,
            "extra": {
                "queries": queries_enriched,
                "pages": pages,
                "landing_pages": landing_pages_seo,
                "seo_expenses": seo_expense_rows,
                "seo_expenses_total": round(seo_spend_cur, 2),
            },
            "generated_at": orders.get("generated_at"),
            "cache_updated": cache_last_updated(f"gsc2_{start.date()}_{end.date()}"),
            "gsc_data_note": gsc_note,
            "orders_attribution_note": (
                "Кліки, покази, CTR і позиції — Google Search Console. "
                "Дохід і конверсії — успішні замовлення CRM Cosmy з атрибуцією SEO. "
                "Сесії GA4 — окремий показник (не змішується з GSC)."
            ),
        }, orders=orders, start=start, end=end, gads=gads, meta_api=meta_api, gsc=gsc, ga4=ga4, section=section, chart_granularity=chart_gran)

    # ───────────── DIRECT ─────────────
    elif section == "direct":
        ga4_dir = ga4.get("direct") or {}
        ga4_dir_prev = ga4_prev.get("direct") or {}
        avg_check = round(cur_rev / cur_ord, 2) if cur_ord else None
        kpi = _kpi_block(cur_rev, cur_ord, prev_revenue=prev_rev, prev_orders=prev_ord)
        kpi["sessions"] = ga4_dir.get("sessions") or cur_ord
        kpi["conversions"] = ga4_dir.get("conversions") or cur_ord
        kpi["avg_check"] = avg_check
        kpi["bounce_rate"] = ga4_dir.get("bounce_rate")
        kpi["pages_per_session"] = ga4_dir.get("pages_per_session")
        kpi["delta"]["sessions_pct"] = _pct_delta(
            ga4_dir.get("sessions") or cur_ord,
            ga4_dir_prev.get("sessions") or prev_ord,
        )
        kpi["delta"]["avg_check_pct"] = _pct_delta(
            cur_rev / cur_ord if cur_ord else 0,
            prev_rev / prev_ord if prev_ord else 0,
        )
        kpi["delta"]["bounce_rate_pct"] = _pct_delta(
            ga4_dir_prev.get("bounce_rate") or 0,
            ga4_dir.get("bounce_rate") or 0,
        )
        dir_sessions = int(ga4_dir.get("sessions") or 0)
        prev_dir_sessions = int(ga4_dir_prev.get("sessions") or 0)
        _attach_conversion_rate_kpi(kpi, cur_ord, dir_sessions, prev_ord, prev_dir_sessions)
        chart = _merge_direct_chart(ga4_dir.get("daily") or [], cur_ch["by_day"], start, end)
        landing_pages = [{**p} for p in (ga4_dir.get("landing_pages") or [])]
        _attach_metric_deltas(
            landing_pages,
            ga4_dir_prev.get("landing_pages") or [],
            key="page",
            metrics=(
                ("sessions", "delta_sessions_pct"),
                ("conversions", "delta_orders_pct"),
                ("revenue", "delta_revenue_pct"),
            ),
        )
        devices = ga4_dir.get("devices") or []
        top_brands = _crm_brands_with_compare(orders["brands_current"], orders["brands_previous"], cur_rev)
        best_device = max(devices, key=lambda x: x.get("sessions", 0), default=None)
        best_page = landing_pages[0] if landing_pages else None
        insights = []
        if best_device:
            total_sess = sum(d.get("sessions", 0) for d in devices) or 1
            share = round(100 * best_device["sessions"] / total_sess, 1)
            insights.append(f"Найбільша частка сесій за пристроєм: {best_device['device']} {share}% ↑")
        if best_page:
            insights.append(f"Найкраща сторінка за сесіями: {best_page['page']} ↑")
        if total_revenue:
            share = round(100 * cur_rev / total_revenue, 1)
            insights.append(f"Частка прямого трафіку у загальному доході: {share}% ↑")
        return _finalize_report({
            "section": section,
            "section_label": SECTION_LABELS[section],
            "period": orders["period"],
            "kpi": kpi,
            "chart": chart,
            "donut": None,
            "top_brands": top_brands,
            "overview_channels": overview_rows,
            "insights": insights,
            "api_status": ga4,
            "extra": {"landing_pages": landing_pages, "devices": devices},
            "generated_at": orders.get("generated_at"),
            "cache_updated": cache_last_updated(f"ga4_{start.date()}_{end.date()}"),
            "orders_attribution_note": "Дохід і конверсії — CRM Cosmy; сесії, відмови, пристрої — Google Analytics 4.",
        }, orders=orders, start=start, end=end, gads=gads, meta_api=meta_api, gsc=gsc, ga4=ga4, section=section, chart_granularity=chart_gran)

    # ───────────── OTHER ─────────────
    elif section == "other":
        ga4_other_groups = ga4.get("other_groups") or []
        avg_check = round(cur_rev / cur_ord, 2) if cur_ord else None
        kpi = _kpi_block(cur_rev, cur_ord, prev_revenue=prev_rev, prev_orders=prev_ord)
        other_sessions = sum(int(g.get("sessions") or 0) for g in ga4_other_groups)
        ga4_other_prev = ga4_prev.get("other_groups") or []
        other_sessions_prev = sum(int(g.get("sessions") or 0) for g in ga4_other_prev)
        kpi["sessions"] = other_sessions or cur_ord
        kpi["avg_check"] = avg_check
        kpi["delta"]["sessions_pct"] = _pct_delta(other_sessions, other_sessions_prev)
        _attach_conversion_rate_kpi(kpi, cur_ord, other_sessions, prev_ord, other_sessions_prev)
        sources = _source_table(
            cur_ch.get("by_source_detail") or {},
            prev_ch.get("by_source_detail") or {},
        )
        n_sources = len(sources) or len(ga4.get("other_sources") or [])
        kpi["n_sources"] = n_sources
        share = round(100 * cur_rev / total_revenue, 1) if total_revenue else None
        kpi["revenue_share_pct"] = share
        donut = _other_donut_from_ga4(ga4_other_groups) if ga4_other_groups else _source_distribution_donut(sources)
        chart = _daily_series(cur_ch["by_day"])
        top_brands = _crm_brands_with_compare(orders["brands_current"], orders["brands_previous"], cur_rev)
        best_source = sources[0] if sources else None
        insights = []
        if best_source:
            insights.append(f"Найбільше джерело за доходом: {best_source['source']} ↑")
        if n_sources > 1:
            insights.append(f"Активних джерел за період: {n_sources}")
        if total_revenue:
            share = round(100 * cur_rev / total_revenue, 1)
            insights.append(f"Частка «Інших джерел» у загальному доході: {share}%")
        return _finalize_report({
            "section": section,
            "section_label": SECTION_LABELS[section],
            "period": orders["period"],
            "kpi": kpi,
            "chart": chart,
            "donut": donut,
            "donut_label": "Розподіл за джерелами",
            "top_brands": top_brands,
            "overview_channels": overview_rows,
            "insights": insights,
            "api_status": ga4,
            "extra": {"sources": sources, "ga4_sources": ga4.get("other_sources") or []},
            "generated_at": orders.get("generated_at"),
            "cache_updated": cache_last_updated(f"ga4_{start.date()}_{end.date()}"),
            "orders_attribution_note": "Дохід — CRM Cosmy; сесії та розподіл каналів — Google Analytics 4.",
        }, orders=orders, start=start, end=end, gads=gads, meta_api=meta_api, gsc=gsc, ga4=ga4, section=section, chart_granularity=chart_gran)

    # ───────────── BUSINESS ─────────────
    elif section == "business":
        business = await load_overview_business_snapshot(session, start, end, refresh=refresh)
        ga4_all_devices = (ga4.get("direct") or {}).get("devices") or []
        if not ga4_all_devices:
            ga4_all_devices = (ga4.get("organic") or {}).get("devices") or []
        top_brands = _crm_brands_with_compare(orders["brands_current"], orders["brands_previous"], total_revenue)
        ov_kpi = _compute_overview_kpis(
            orders, gads, meta_api, gads_prev, meta_prev,
            period_end=end, prev_period_end=prev_end,
            gsc=gsc, ga4=ga4, gsc_prev=gsc_prev, ga4_prev=ga4_prev,
        )
        insights = []
        top_ch = business.get("top_channels") or business.get("top_categories") or []
        if top_ch:
            insights.append(f"Топ канал (UTM): {top_ch[0].get('label') or top_ch[0].get('name')} ↑")
        if business.get("top_cities"):
            insights.append(f"Топ місто: {business['top_cities'][0]['name']} ↑")
        if top_brands:
            insights.append(f"Топ бренд: {top_brands[0]['brand']} ↑")
        return _finalize_report({
            "section": section,
            "section_label": SECTION_LABELS[section],
            "period": orders["period"],
            "kpi": ov_kpi,
            "chart": None,
            "donut": None,
            "top_brands": top_brands,
            "overview_channels": overview_rows,
            "insights": insights,
            "api_status": {"configured": True, "error": None},
            "extra": {
                "business": business,
                "devices": ga4_all_devices,
            },
            "generated_at": orders.get("generated_at"),
            "cache_updated": orders.get("generated_at"),
            "orders_attribution_note": "Бізнес-аналітика — успішні замовлення CRM Cosmy за обраний період.",
        }, orders=orders, start=start, end=end, gads=gads, meta_api=meta_api, gsc=gsc, ga4=ga4, section=section, chart_granularity=chart_gran)

    # ───────────── INSIGHTS ─────────────
    elif section == "insights":
        top_brands = _attach_brand_deltas(
            _enrich_brands(_top_brands_table(orders["brands_current"]), 0, total_revenue)[:5],
            orders["brands_previous"],
        )
        business = await load_overview_business_snapshot(
            session, start, end, refresh=refresh, orders_pairs=cur_orders_pairs,
        )
        insight_payload = _build_insight_payload(
            overview_rows=overview_rows,
            orders=orders,
            gads=gads,
            gads_prev=gads_prev,
            meta_api=meta_api,
            meta_prev=meta_prev,
            gsc=gsc,
            gsc_prev=gsc_prev,
            top_brands=top_brands,
            period_end=end,
            prev_period_end=prev_end,
            chip_limit=0,
            feed_limit=24,
            ga4=ga4,
            ga4_prev=ga4_prev,
            business=business,
        )
        insight_feed = insight_payload["insight_feed"]
        return _finalize_report({
            "section": section,
            "section_label": SECTION_LABELS[section],
            "period": orders["period"],
            "kpi": _compute_overview_kpis(
                orders, gads, meta_api, gads_prev, meta_prev,
                period_end=end, prev_period_end=prev_end,
                gsc=gsc, ga4=ga4, gsc_prev=gsc_prev, ga4_prev=ga4_prev,
            ),
            "chart": None,
            "donut": None,
            "top_brands": top_brands,
            "overview_channels": overview_rows,
            "insights": [],
            "insight_chips": [],
            "insight_feed": insight_feed,
            "api_status": {"configured": True, "error": None},
            "extra": {},
            "generated_at": orders.get("generated_at"),
            "cache_updated": orders.get("generated_at"),
            "orders_attribution_note": "Автоматичні висновки на основі CRM, Google Ads, Meta та Search Console.",
        }, orders=orders, start=start, end=end, gads=gads, meta_api=meta_api, gsc=gsc, ga4=ga4, section=section, chart_granularity=chart_gran)

    # ───────────── DETAILS ─────────────
    elif section == "details":
        order_rows = await load_order_details(session, start, end)
        kpi = _kpi_block(cur_rev, cur_ord, prev_revenue=prev_rev, prev_orders=prev_ord)
        shown = len(order_rows)
        kpi["transactions"] = shown
        kpi["transactions_limited"] = shown < cur_ord
        return _finalize_report({
            "section": section,
            "section_label": SECTION_LABELS[section],
            "period": orders["period"],
            "kpi": kpi,
            "chart": None,
            "donut": None,
            "top_brands": [],
            "overview_channels": overview_rows,
            "insights": [f"Замовлень за період: {len(order_rows)}"],
            "api_status": {"configured": True, "error": None},
            "extra": {"orders": order_rows},
            "generated_at": orders.get("generated_at"),
            "cache_updated": orders.get("generated_at"),
            "orders_attribution_note": "Деталізація — успішні замовлення CRM Cosmy (до 500 останніх).",
        }, orders=orders, start=start, end=end, gads=gads, meta_api=meta_api, gsc=gsc, ga4=ga4, section=section, chart_granularity=chart_gran)

    else:
        return _finalize_report({
            "section": "overview",
            "section_label": SECTION_LABELS["overview"],
            "period": orders["period"],
            "kpi": _compute_overview_kpis(
                orders, gads, meta_api, gads_prev, meta_prev,
                period_end=end, prev_period_end=prev_end,
                gsc=gsc, ga4=ga4, gsc_prev=gsc_prev, ga4_prev=ga4_prev,
            ),
            "chart": [],
            "donut": _channel_donut(overview_rows),
            "top_brands": [],
            "overview_channels": overview_rows,
            "insights": [],
            "api_status": {"configured": True, "error": None},
            "extra": {},
            "generated_at": orders.get("generated_at"),
        }, orders=orders, start=start, end=end, gads=gads, meta_api=meta_api, gsc=gsc, ga4=ga4, section=section, chart_granularity=chart_gran)


def _source_table(
    by_source: dict[str, dict],
    prev_by_source: dict[str, dict] | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    total = sum(float(v["revenue"]) for v in by_source.values()) or 1.0
    prev_by_source = prev_by_source or {}
    rows = []
    for name, v in sorted(by_source.items(), key=lambda x: -float(x[1]["revenue"])):
        rev = float(v["revenue"])
        orders = int(v["orders"])
        prev = prev_by_source.get(name) or {}
        channel_share = round(100.0 * rev / total, 1)
        rows.append({
            "source": name,
            "type": classify_source_type(name),
            "orders": orders,
            "revenue": round(rev, 2),
            "share_pct": channel_share,
            "channel_share_pct": channel_share,
            "delta_orders_pct": _pct_delta(orders, int(prev.get("orders") or 0)),
            "delta_revenue_pct": _pct_delta(rev, float(prev.get("revenue") or 0)),
        })
    return rows[:limit]
