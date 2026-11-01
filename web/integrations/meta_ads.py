"""Meta Marketing API — витрати, кампанії, плейсменти."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

import requests

from config import meta_ads_configured
from marketing_config import load_marketing_config
from web.integrations.ad_cache import CACHE_NOT_WARMED, cache_set, get_stale_ad_payload, load_cached_period_payload

logger = logging.getLogger(__name__)

_OBJECTIVE_UA = {
    "OUTCOME_SALES": "Продажі",
    "OUTCOME_TRAFFIC": "Трафік",
    "OUTCOME_LEADS": "Ліди",
    "OUTCOME_ENGAGEMENT": "Залучення",
    "CONVERSIONS": "Конверсії",
    "LINK_CLICKS": "Кліки",
}

# Пріоритет типів дій Meta (як у Ads Manager — omni_purchase дедуплікований).
_PURCHASE_COUNT_TYPES = (
    "omni_purchase",
    "purchase",
    "offsite_conversion.fb_pixel_purchase",
)
_PURCHASE_VALUE_TYPES = _PURCHASE_COUNT_TYPES
_PURCHASE_ROAS_TYPES = (
    "omni_purchase",
    "offsite_conversion.fb_pixel_purchase",
)

_ACCOUNT_INSIGHT_FIELDS = (
    "impressions,clicks,spend,ctr,cpc,cpm,reach,frequency,"
    "actions,action_values,purchase_roas,website_purchase_roas,account_currency"
)
_CAMPAIGN_INSIGHT_FIELDS = (
    "campaign_name,objective,spend,impressions,clicks,"
    "actions,action_values,purchase_roas"
)
_DAILY_INSIGHT_FIELDS = "spend,impressions,clicks,date_start,actions,action_values"


def _marketing() -> dict[str, str]:
    return load_marketing_config()


def _base_url() -> str:
    return f"https://graph.facebook.com/{_marketing().get('META_ADS_API_VERSION', 'v21.0')}"


def _get(endpoint: str, params: dict | None = None) -> dict:
    p = {"access_token": _marketing().get("META_ADS_ACCESS_TOKEN", "")}
    if params:
        p.update(params)
    r = requests.get(f"{_base_url()}/{endpoint}", params=p, timeout=60)
    return r.json()


def _pick_action_metric(
    rows: list[dict[str, Any]] | None,
    preferred_types: tuple[str, ...],
) -> float:
    """Повертає value першого знайденого action_type (без сумування дублікатів)."""
    if not rows:
        return 0.0
    by_type = {str(r.get("action_type") or ""): r for r in rows}
    for action_type in preferred_types:
        row = by_type.get(action_type)
        if row is None:
            continue
        try:
            return float(row.get("value") or 0)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def parse_meta_purchase_metrics(row: dict[str, Any]) -> dict[str, float | int | None]:
    """
    Розбір покупок / доходу / ROAS з рядка insights Meta.
    Не сумуємо різні action_type — беремо omni_purchase (як Ads Manager).
    """
    purchases = int(_pick_action_metric(row.get("actions"), _PURCHASE_COUNT_TYPES))
    purchase_value = round(
        _pick_action_metric(row.get("action_values"), _PURCHASE_VALUE_TYPES), 2
    )
    purchase_roas = _pick_action_metric(row.get("purchase_roas"), _PURCHASE_ROAS_TYPES)
    if not purchase_roas:
        purchase_roas = _pick_action_metric(
            row.get("website_purchase_roas"), _PURCHASE_ROAS_TYPES
        )
    spend = float(row.get("spend") or 0)
    if not purchase_roas and spend > 0 and purchase_value > 0:
        purchase_roas = round(purchase_value / spend, 4)
    return {
        "purchases": purchases,
        "purchase_value": purchase_value,
        "purchase_roas": round(purchase_roas, 4) if purchase_roas else None,
    }


def meta_currency_suffix(currency: str | None) -> str:
    code = (currency or "USD").upper()
    return {"USD": "$", "UAH": "₴", "EUR": "€", "GBP": "£"}.get(code, code)


def meta_roas_display(roas: float | None) -> str:
    """ROAS у форматі Meta Ads Manager: 12.63x."""
    if roas is None:
        return "—"
    return f"{roas:.2f}x"


def _insight_window(time_range: dict[str, str], fields: str, **extra: Any) -> dict[str, Any]:
    """Вікно як у Ads Manager: дата конверсії + атрибуція акаунта.

    Без action_report_time=conversion Meta кладе покупку на день показу/кліку,
    і короткі періоди не сходяться з кабінетом (повний місяць — майже сходиться).
    """
    params: dict[str, Any] = {
        "time_range": json.dumps(time_range),
        "fields": fields,
        "action_report_time": "conversion",
        "use_account_attribution_setting": "true",
    }
    params.update(extra)
    return params


def _paginate_insights(
    endpoint: str,
    params: dict[str, Any],
    *,
    limit: int = 100,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Завантажує всі сторінки insights (Meta paging)."""
    rows: list[dict[str, Any]] = []
    after: str | None = None
    while True:
        page_params = {**params, "limit": limit}
        if after:
            page_params["after"] = after
        data = _get(endpoint, page_params)
        if "error" in data:
            return str(data["error"].get("message", "Meta insights error")), rows
        rows.extend(data.get("data") or [])
        paging = data.get("paging") or {}
        cursors = paging.get("cursors") or {}
        after = cursors.get("after")
        if not after or not paging.get("next"):
            break
    return None, rows


def _parse_campaign_row(c: dict[str, Any]) -> dict[str, Any]:
    obj = c.get("objective") or ""
    purchase = parse_meta_purchase_metrics(c)
    spend = round(float(c.get("spend", 0)), 2)
    purchases = int(purchase["purchases"] or 0)
    purchase_value = float(purchase["purchase_value"] or 0)
    purchase_roas = purchase.get("purchase_roas")
    return {
        "name": c.get("campaign_name", "—"),
        "objective": _OBJECTIVE_UA.get(obj, obj),
        "spend": spend,
        "clicks": int(c.get("clicks", 0)),
        "impressions": int(c.get("impressions", 0)),
        "purchases": purchases,
        "purchase_value": purchase_value,
        "purchase_roas": purchase_roas,
        "revenue": purchase_value,
        "orders": purchases,
        "roas_pct": meta_roas_display(purchase_roas if isinstance(purchase_roas, float) else None),
        "cpa": round(spend / purchases, 2) if purchases and spend else None,
        "attribution": "meta",
    }


def _empty_payload(error: str | None = None) -> dict[str, Any]:
    return {
        "configured": meta_ads_configured(),
        "error": error,
        "currency": None,
        "spend": 0.0,
        "clicks": 0,
        "impressions": 0,
        "reach": 0,
        "frequency": None,
        "ctr": None,
        "cpc": None,
        "cpm": None,
        "purchases": 0,
        "purchase_value": 0.0,
        "purchase_roas": None,
        "cost_per_purchase": None,
        "campaigns": [],
        "placements": [],
        "daily": [],
        "audience": [],
    }


def fetch_meta_ads_stats(start: datetime, end: datetime, *, use_cache: bool = True) -> dict[str, Any]:
    cache_key = f"meta_v3_{start.date()}_{end.date()}"

    if use_cache:
        # v2 рахував покупки за часом показу — не підставляємо.
        cached = load_cached_period_payload("meta_v3", start, end)
        if cached is not None:
            return cached
        return _empty_payload(CACHE_NOT_WARMED)

    stale_payload = get_stale_ad_payload(cache_key)

    if not meta_ads_configured():
        if stale_payload is not None:
            logger.info("Meta Ads: API не налаштовано — stale cache %s – %s", start.date(), end.date())
            return stale_payload
        return _empty_payload("Meta Ads API не налаштовано (marketing_config.json)")

    acct = _marketing().get("META_AD_ACCOUNT_ID", "")
    if not acct.startswith("act_"):
        acct = f"act_{acct}"

    time_range = {"since": start.strftime("%Y-%m-%d"), "until": end.strftime("%Y-%m-%d")}

    try:
        token_check = _get("me", {"fields": "id"})
        if "error" in token_check:
            return _empty_payload(token_check["error"].get("message", "Meta token error"))

        ins = _get(
            f"{acct}/insights",
            _insight_window(time_range, _ACCOUNT_INSIGHT_FIELDS, level="account"),
        )
        if "error" in ins:
            return _empty_payload(ins["error"].get("message"))

        row = (ins.get("data") or [{}])[0]
        spend = float(row.get("spend", 0))
        clicks = int(row.get("clicks", 0))
        impressions = int(row.get("impressions", 0))
        reach = int(row.get("reach", 0))
        frequency = float(row.get("frequency", 0)) if row.get("frequency") else None
        currency = (row.get("account_currency") or "").strip().upper() or None

        if not currency:
            acct_info = _get(acct, {"fields": "currency"})
            if "error" not in acct_info:
                currency = (acct_info.get("currency") or "").strip().upper() or None

        purchase = parse_meta_purchase_metrics(row)
        purchases = int(purchase["purchases"] or 0)
        purchase_value = float(purchase["purchase_value"] or 0)
        purchase_roas = purchase.get("purchase_roas")

        camp_err, camp_rows = _paginate_insights(
            f"{acct}/insights",
            _insight_window(time_range, _CAMPAIGN_INSIGHT_FIELDS, level="campaign"),
            limit=100,
        )
        campaigns = [_parse_campaign_row(c) for c in camp_rows]
        campaigns.sort(key=lambda x: -x["spend"])
        if camp_err:
            logger.warning("Meta campaign insights: %s", camp_err)

        place_err, place_rows = _paginate_insights(
            f"{acct}/insights",
            _insight_window(
                time_range,
                "spend,impressions,clicks",
                breakdowns="publisher_platform,platform_position",
                level="account",
            ),
            limit=100,
        )
        placements: dict[str, float] = {}
        if not place_err:
            for p in place_rows:
                plat = p.get("publisher_platform") or "unknown"
                pos = p.get("platform_position") or ""
                label = f"{plat}: {pos}" if pos else plat
                placements[label] = placements.get(label, 0.0) + float(p.get("spend", 0))
        placement_rows = [
            {"name": k, "spend": round(v, 2)}
            for k, v in sorted(placements.items(), key=lambda x: -x[1])
        ]

        aud_err, aud_rows = _paginate_insights(
            f"{acct}/insights",
            _insight_window(
                time_range,
                "spend,impressions,clicks,reach",
                breakdowns="age,gender",
                level="account",
            ),
            limit=100,
        )
        audience_rows: list[dict[str, Any]] = []
        if not aud_err:
            for a in aud_rows:
                gender = a.get("gender") or "unknown"
                age = a.get("age") or "unknown"
                audience_rows.append({
                    "segment": f"{gender}, {age}",
                    "gender": gender,
                    "age": age,
                    "spend": round(float(a.get("spend", 0)), 2),
                    "impressions": int(a.get("impressions", 0)),
                    "clicks": int(a.get("clicks", 0)),
                    "reach": int(a.get("reach", 0)),
                })
            audience_rows.sort(key=lambda x: -x["spend"])

        daily_err, daily_rows = _paginate_insights(
            f"{acct}/insights",
            _insight_window(
                time_range,
                _DAILY_INSIGHT_FIELDS,
                time_increment=1,
                level="account",
            ),
            limit=100,
        )
        daily: list[dict[str, Any]] = []
        if not daily_err:
            for d in daily_rows:
                day_purchase = parse_meta_purchase_metrics(d)
                daily.append(
                    {
                        "date": d.get("date_start"),
                        "spend": round(float(d.get("spend", 0)), 2),
                        "clicks": int(d.get("clicks", 0)),
                        "impressions": int(d.get("impressions", 0)),
                        "purchases": int(day_purchase["purchases"] or 0),
                        "purchase_value": float(day_purchase["purchase_value"] or 0),
                    }
                )
            daily.sort(key=lambda x: x.get("date") or "")

        payload = {
            "configured": True,
            "error": None,
            "currency": currency,
            "spend": round(spend, 2),
            "clicks": clicks,
            "impressions": impressions,
            "reach": reach,
            "frequency": round(frequency, 2) if frequency else None,
            "ctr": round(float(row.get("ctr", 0)), 2) if row.get("ctr") else None,
            "cpc": round(float(row.get("cpc", 0)), 2) if row.get("cpc") else None,
            "cpm": round(float(row.get("cpm", 0)), 2) if row.get("cpm") else None,
            "purchases": purchases,
            "purchase_value": purchase_value,
            "purchase_roas": purchase_roas,
            "cost_per_purchase": round(spend / purchases, 2) if purchases and spend else None,
            "campaigns": campaigns,
            "placements": placement_rows,
            "daily": daily,
            "audience": audience_rows,
        }
        cache_set(cache_key, payload)
        return payload
    except Exception as e:
        logger.exception("Meta Ads API error")
        if stale_payload is not None:
            logger.info("Meta Ads: API помилка — stale cache %s – %s", start.date(), end.date())
            stale_payload = dict(stale_payload)
            stale_payload["error"] = str(e)
            return stale_payload
        return _empty_payload(str(e))
