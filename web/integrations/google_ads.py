"""Google Ads API — витрати, кампанії, ключові слова.

Конверсії — як стандартна колонка «Conversions» / «Конверсії» у кабінеті:
metrics.conversions + metrics.conversions_value (дата кліку / взаємодії).

Це той самий показник, що в Google Ads API за замовчуванням і в основній
колонці UI. «Conversions (by conv. time)» — окрема колонка
(conversions_by_conversion_date), її тут не використовуємо.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from config import google_ads_configured
from marketing_config import load_marketing_config
from web.integrations.ad_cache import (
    CACHE_NOT_WARMED,
    cache_set,
    get_stale_ad_payload,
    load_cached_period_payload,
)

logger = logging.getLogger(__name__)

# v4 = metrics.conversions (click/interaction date), як колонка Conversions у UI.
_CACHE_PREFIX = "gads_v4"

_CAMPAIGN_TYPE_UA = {
    "SEARCH": "Search",
    "PERFORMANCE_MAX": "PMax",
    "SHOPPING": "Shopping",
    "DISPLAY": "Display",
    "VIDEO": "Video",
    "SMART": "Smart",
}

_MATCH_TYPE_UA = {
    "EXACT": "Точний",
    "PHRASE": "Фразовий",
    "BROAD": "Широкий",
}


def _marketing() -> dict[str, str]:
    return load_marketing_config()


def _customer_id() -> str:
    return (_marketing().get("GOOGLE_ADS_CUSTOMER_ID") or "").replace("-", "")


def _client_from_dict(creds: dict[str, str]):
    from google.ads.googleads.client import GoogleAdsClient

    return GoogleAdsClient.load_from_dict(
        {
            "developer_token": creds["developer_token"],
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "refresh_token": creds["refresh_token"],
            "use_proto_plus": True,
        }
    )


def _client():
    m = _marketing()
    return _client_from_dict(
        {
            "developer_token": m.get("GOOGLE_ADS_DEVELOPER_TOKEN", ""),
            "client_id": m.get("GOOGLE_ADS_CLIENT_ID", ""),
            "client_secret": m.get("GOOGLE_ADS_CLIENT_SECRET", ""),
            "refresh_token": m.get("GOOGLE_ADS_REFRESH_TOKEN", ""),
        }
    )


def _map_known_google_ads_error(text: str) -> str:
    msg = (text or "").strip()
    low = msg.lower()

    if "developer_token_invalid" in low or "developer token is not valid" in low:
        return (
            "Developer Token недійсний. Перевірте GOOGLE_ADS_DEVELOPER_TOKEN у "
            "Google Ads API Center (ads.google.com → Tools → API Center)."
        )
    if "invalid_grant" in low:
        return (
            "Refresh token недійсний (invalid_grant). "
            "Отримайте новий через «Підключити через Google» у налаштуваннях."
        )
    if "unauthorized_client" in low:
        return "Невірний Client ID або Client Secret."
    if "customer_not_found" in low or "user_permission_denied" in low:
        return "Немає доступу до GOOGLE_ADS_CUSTOMER_ID або невірний ID акаунта."
    if "unauthenticated" in low or "missing required authentication credential" in low:
        if "developer_token" in low:
            return (
                "Developer Token недійсний або не схвалений. "
                "Оновіть GOOGLE_ADS_DEVELOPER_TOKEN у налаштуваннях."
            )
        return (
            "Помилка авторизації Google Ads. Перевірте refresh token, Client ID/Secret і Developer Token."
        )
    if len(msg) > 280 or "_singlethreadedrendezvous" in low or "statuscode." in low:
        if "developer_token" in low:
            return (
                "Developer Token недійсний. Перевірте GOOGLE_ADS_DEVELOPER_TOKEN у Google Ads API Center."
            )
        return "Помилка Google Ads API. Перевірте ключі в налаштуваннях інтеграції."

    return f"Google Ads API: {msg}" if msg else "Google Ads API: невідома помилка"


def _format_google_ads_error(exc: Exception) -> str:
    try:
        from google.ads.googleads.errors import GoogleAdsException

        if isinstance(exc, GoogleAdsException):
            messages: list[str] = []
            failure = getattr(exc, "failure", None)
            for err in getattr(failure, "errors", []) or []:
                part = (getattr(err, "message", None) or "").strip()
                if part:
                    messages.append(part)
            if messages:
                return _map_known_google_ads_error(" ".join(messages))
            details = ""
            if getattr(exc, "error", None) is not None:
                details = (exc.error.details() or "").strip()
            if details:
                return _map_known_google_ads_error(details)
    except ImportError:
        pass

    return _map_known_google_ads_error(str(exc))


def verify_google_ads_credentials(
    *,
    developer_token: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    refresh_token: str | None = None,
    customer_id: str | None = None,
) -> str:
    """
    Перевіряє refresh token запитом до Google Ads API.
    Повертає коротке повідомлення про успіх або ValueError з причиною.
    """
    m = _marketing()
    creds = {
        "developer_token": (developer_token or m.get("GOOGLE_ADS_DEVELOPER_TOKEN") or "").strip(),
        "client_id": (client_id or m.get("GOOGLE_ADS_CLIENT_ID") or "").strip(),
        "client_secret": (client_secret or m.get("GOOGLE_ADS_CLIENT_SECRET") or "").strip(),
        "refresh_token": (refresh_token or m.get("GOOGLE_ADS_REFRESH_TOKEN") or "").strip(),
        "customer_id": (customer_id or m.get("GOOGLE_ADS_CUSTOMER_ID") or "").strip(),
    }
    missing = [k for k, v in creds.items() if not v]
    if missing:
        labels = {
            "developer_token": "GOOGLE_ADS_DEVELOPER_TOKEN",
            "client_id": "GOOGLE_ADS_CLIENT_ID",
            "client_secret": "GOOGLE_ADS_CLIENT_SECRET",
            "refresh_token": "GOOGLE_ADS_REFRESH_TOKEN",
            "customer_id": "GOOGLE_ADS_CUSTOMER_ID",
        }
        need = ", ".join(labels.get(k, k) for k in missing)
        raise ValueError(f"Для перевірки Google Ads заповніть: {need}")

    cid = creds["customer_id"].replace("-", "")
    try:
        client = _client_from_dict(creds)
        ga = client.get_service("GoogleAdsService")
        currency: str | None = None
        for row in _search_rows(ga, cid, "SELECT customer.id, customer.currency_code FROM customer LIMIT 1"):
            currency = str(row.customer.currency_code or "").upper() or None
            break
        if currency:
            return f"Підключення OK (акаунт {creds['customer_id']}, {currency})"
        return f"Підключення OK (акаунт {creds['customer_id']})"
    except Exception as exc:
        raise ValueError(_format_google_ads_error(exc)) from exc


def _date_clause(start: datetime, end: datetime) -> str:
    return f"segments.date BETWEEN '{start.strftime('%Y-%m-%d')}' AND '{end.strftime('%Y-%m-%d')}'"


def _search_rows(ga, customer_id: str, query: str) -> list[Any]:
    """Усі рядки GAQL (stream, без обмеження LIMIT)."""
    rows: list[Any] = []
    for batch in ga.search_stream(customer_id=customer_id, query=query):
        rows.extend(batch.results)
    return rows


def _metric_float(metrics: Any, *names: str) -> float:
    for name in names:
        if hasattr(metrics, name):
            val = getattr(metrics, name)
            if val is not None:
                return float(val or 0)
    return 0.0


def _empty_payload(error: str | None = None) -> dict[str, Any]:
    return {
        "configured": google_ads_configured(),
        "error": error,
        "currency": None,
        "spend": 0.0,
        "clicks": 0,
        "impressions": 0,
        "conversions": 0.0,
        "conversions_value": 0.0,
        "conversions_time_basis": "click_date",
        "cost_per_conversion": None,
        "ctr": None,
        "cpc": None,
        "campaigns": [],
        "keywords": [],
        "spend_by_type": [],
        "daily": [],
        "avg_quality_score": None,
        "search_impression_share": None,
    }


def _normalize_gads_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Доповнює старий кеш полями (conversions_value, conversion_value по днях/кампаніях)."""
    if not payload:
        return payload

    payload.setdefault("conversions_time_basis", "click_date")

    for camp in payload.get("campaigns") or []:
        camp.setdefault("conversion_value", 0.0)

    for day in payload.get("daily") or []:
        day.setdefault("conversions", 0.0)
        day.setdefault("conversion_value", 0.0)

    conv_value = payload.get("conversions_value")
    if conv_value is None:
        camp_val = sum(float(c.get("conversion_value") or 0) for c in payload.get("campaigns") or [])
        daily_val = sum(float(d.get("conversion_value") or 0) for d in payload.get("daily") or [])
        payload["conversions_value"] = round(max(camp_val, daily_val), 2)
    else:
        payload["conversions_value"] = float(conv_value or 0)

    spend = float(payload.get("spend") or 0)
    conv = float(payload.get("conversions") or 0)
    if payload.get("cost_per_conversion") is None and conv > 0 and spend > 0:
        payload["cost_per_conversion"] = round(spend / conv, 2)

    clicks = int(payload.get("clicks") or 0)
    impr = int(payload.get("impressions") or 0)
    if payload.get("ctr") is None and impr > 0:
        payload["ctr"] = round(100.0 * clicks / impr, 2)
    if payload.get("cpc") is None and clicks > 0 and spend > 0:
        payload["cpc"] = round(spend / clicks, 2)

    return payload


def _merge_daily(
    traffic_daily: list[dict[str, Any]],
    conv_daily: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_date: dict[str, dict[str, Any]] = {}
    for row in traffic_daily:
        day = str(row.get("date") or "")
        if not day:
            continue
        by_date[day] = {
            "date": day,
            "spend": float(row.get("spend") or 0),
            "clicks": int(row.get("clicks") or 0),
            "impressions": int(row.get("impressions") or 0),
            "conversions": 0.0,
            "conversion_value": 0.0,
        }
    for row in conv_daily:
        day = str(row.get("date") or "")
        if not day:
            continue
        slot = by_date.setdefault(
            day,
            {
                "date": day,
                "spend": 0.0,
                "clicks": 0,
                "impressions": 0,
                "conversions": 0.0,
                "conversion_value": 0.0,
            },
        )
        slot["conversions"] = float(row.get("conversions") or 0)
        slot["conversion_value"] = float(row.get("conversion_value") or 0)
    out = []
    for day in sorted(by_date):
        row = by_date[day]
        out.append(
            {
                "date": day,
                "spend": round(float(row["spend"]), 2),
                "clicks": int(row["clicks"]),
                "impressions": int(row["impressions"]),
                "conversions": round(float(row["conversions"]), 2),
                "conversion_value": round(float(row["conversion_value"]), 2),
            }
        )
    return out


def fetch_google_ads_stats(start: datetime, end: datetime, *, use_cache: bool = True) -> dict[str, Any]:
    cache_key = f"{_CACHE_PREFIX}_{start.date()}_{end.date()}"

    def _from_cache(data: dict[str, Any] | None) -> dict[str, Any] | None:
        return _normalize_gads_payload(data) if data is not None else None

    if use_cache:
        # Не підставляємо gads_v2: там конверсії за датою кліку — інша семантика.
        cached = _from_cache(load_cached_period_payload(_CACHE_PREFIX, start, end))
        if cached is not None:
            return cached
        return _empty_payload(CACHE_NOT_WARMED)

    stale_payload = _from_cache(get_stale_ad_payload(cache_key))

    if not google_ads_configured():
        if stale_payload is not None:
            logger.info("Google Ads: API не налаштовано — stale cache %s – %s", start.date(), end.date())
            return stale_payload
        return _empty_payload("Google Ads API не налаштовано (marketing_config.json)")

    cid = _customer_id()
    if not cid:
        if stale_payload is not None:
            return stale_payload
        return _empty_payload("GOOGLE_ADS_CUSTOMER_ID не задано")

    try:
        client = _client()
        ga = client.get_service("GoogleAdsService")
        where = _date_clause(start, end)

        currency: str | None = None
        currency_q = "SELECT customer.currency_code FROM customer LIMIT 1"
        for row in _search_rows(ga, cid, currency_q):
            currency = str(row.customer.currency_code or "").upper() or None
            break

        # ── Traffic: дата кліку (Cost / Clicks / Impr. у UI) ──────────────
        traffic_q = f"""
            SELECT
                metrics.clicks,
                metrics.impressions,
                metrics.cost_micros,
                metrics.ctr,
                metrics.average_cpc
            FROM customer
            WHERE {where}
        """
        total_clicks = total_impr = 0
        total_cost = 0.0
        for row in _search_rows(ga, cid, traffic_q):
            total_clicks += int(row.metrics.clicks or 0)
            total_impr += int(row.metrics.impressions or 0)
            total_cost += float(row.metrics.cost_micros or 0) / 1_000_000

        # ── Conversions: дата кліку (стандартна колонка Conversions у UI) ──
        conv_q = f"""
            SELECT
                metrics.conversions,
                metrics.conversions_value
            FROM customer
            WHERE {where}
        """
        total_conv = 0.0
        total_conv_value = 0.0
        for row in _search_rows(ga, cid, conv_q):
            total_conv += _metric_float(row.metrics, "conversions")
            total_conv_value += _metric_float(row.metrics, "conversions_value")

        camp_traffic_q = f"""
            SELECT
                campaign.name,
                campaign.advertising_channel_type,
                metrics.clicks,
                metrics.impressions,
                metrics.cost_micros
            FROM campaign
            WHERE {where}
                AND campaign.status != 'REMOVED'
            ORDER BY metrics.cost_micros DESC
        """
        campaigns_by_name: dict[str, dict[str, Any]] = {}
        type_spend: dict[str, float] = {}
        for row in _search_rows(ga, cid, camp_traffic_q):
            ctype = row.campaign.advertising_channel_type.name if row.campaign.advertising_channel_type else "OTHER"
            cost = float(row.metrics.cost_micros or 0) / 1_000_000
            label = _CAMPAIGN_TYPE_UA.get(ctype, ctype)
            type_spend[label] = type_spend.get(label, 0.0) + cost
            name = str(row.campaign.name or "")
            campaigns_by_name[name] = {
                "name": name,
                "type": label,
                "clicks": int(row.metrics.clicks or 0),
                "impressions": int(row.metrics.impressions or 0),
                "spend": round(cost, 2),
                "conversions": 0.0,
                "conversion_value": 0.0,
                "roas": None,
                "cpa": None,
            }

        camp_conv_q = f"""
            SELECT
                campaign.name,
                metrics.conversions,
                metrics.conversions_value
            FROM campaign
            WHERE {where}
                AND campaign.status != 'REMOVED'
        """
        for row in _search_rows(ga, cid, camp_conv_q):
            name = str(row.campaign.name or "")
            conv = _metric_float(row.metrics, "conversions")
            conv_val = _metric_float(row.metrics, "conversions_value")
            slot = campaigns_by_name.get(name)
            if slot is None:
                slot = {
                    "name": name,
                    "type": "OTHER",
                    "clicks": 0,
                    "impressions": 0,
                    "spend": 0.0,
                    "conversions": 0.0,
                    "conversion_value": 0.0,
                    "roas": None,
                    "cpa": None,
                }
                campaigns_by_name[name] = slot
            slot["conversions"] = round(conv, 2)
            slot["conversion_value"] = round(conv_val, 2)

        campaigns = []
        for slot in campaigns_by_name.values():
            cost = float(slot["spend"] or 0)
            conv = float(slot["conversions"] or 0)
            conv_val = float(slot["conversion_value"] or 0)
            slot["roas"] = round(conv_val / cost, 4) if cost > 0 and conv_val > 0 else None
            slot["cpa"] = round(cost / conv, 2) if conv > 0 and cost > 0 else None
            campaigns.append(slot)
        campaigns.sort(key=lambda c: float(c.get("spend") or 0), reverse=True)

        kw_traffic_q = f"""
            SELECT
                ad_group_criterion.keyword.text,
                ad_group_criterion.keyword.match_type,
                ad_group_criterion.quality_info.quality_score,
                metrics.clicks,
                metrics.impressions,
                metrics.ctr,
                metrics.average_cpc
            FROM keyword_view
            WHERE {where}
                AND ad_group_criterion.status != 'REMOVED'
            ORDER BY metrics.clicks DESC
            LIMIT 50
        """
        keywords_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        qs_weighted_sum = 0.0
        qs_weight = 0
        for row in _search_rows(ga, cid, kw_traffic_q):
            qs = int(row.ad_group_criterion.quality_info.quality_score or 0)
            impr = int(row.metrics.impressions or 0)
            if qs > 0 and impr > 0:
                qs_weighted_sum += qs * impr
                qs_weight += impr
            match = row.ad_group_criterion.keyword.match_type.name if row.ad_group_criterion.keyword.match_type else ""
            text = str(row.ad_group_criterion.keyword.text or "")
            match_ua = _MATCH_TYPE_UA.get(match, match)
            keywords_by_key[(text, match_ua)] = {
                "keyword": text,
                "match_type": match_ua,
                "quality_score": qs if qs > 0 else None,
                "clicks": int(row.metrics.clicks or 0),
                "impressions": impr,
                "ctr": round(float(row.metrics.ctr or 0) * 100, 2),
                "cpc": round(float(row.metrics.average_cpc or 0) / 1_000_000, 2),
                "conversions": 0.0,
                "conversion_value": 0.0,
            }

        kw_conv_q = f"""
            SELECT
                ad_group_criterion.keyword.text,
                ad_group_criterion.keyword.match_type,
                metrics.conversions,
                metrics.conversions_value
            FROM keyword_view
            WHERE {where}
                AND ad_group_criterion.status != 'REMOVED'
            ORDER BY metrics.conversions DESC
            LIMIT 50
        """
        for row in _search_rows(ga, cid, kw_conv_q):
            match = row.ad_group_criterion.keyword.match_type.name if row.ad_group_criterion.keyword.match_type else ""
            text = str(row.ad_group_criterion.keyword.text or "")
            match_ua = _MATCH_TYPE_UA.get(match, match)
            key = (text, match_ua)
            conv = _metric_float(row.metrics, "conversions")
            conv_val = _metric_float(row.metrics, "conversions_value")
            slot = keywords_by_key.get(key)
            if slot is None:
                slot = {
                    "keyword": text,
                    "match_type": match_ua,
                    "quality_score": None,
                    "clicks": 0,
                    "impressions": 0,
                    "ctr": 0.0,
                    "cpc": 0.0,
                    "conversions": 0.0,
                    "conversion_value": 0.0,
                }
                keywords_by_key[key] = slot
            slot["conversions"] = round(conv, 2)
            slot["conversion_value"] = round(conv_val, 2)

        keywords = sorted(
            keywords_by_key.values(),
            key=lambda k: (int(k.get("clicks") or 0), float(k.get("conversions") or 0)),
            reverse=True,
        )[:50]

        sis_q = f"""
            SELECT
                metrics.search_impression_share,
                metrics.impressions
            FROM campaign
            WHERE {where}
                AND campaign.status != 'REMOVED'
                AND campaign.advertising_channel_type = 'SEARCH'
        """
        sis_weighted = 0.0
        sis_impr = 0
        for row in _search_rows(ga, cid, sis_q):
            sis = float(row.metrics.search_impression_share or 0)
            impr = int(row.metrics.impressions or 0)
            if impr > 0 and sis > 0:
                sis_weighted += sis * impr
                sis_impr += impr
        avg_quality_score = round(qs_weighted_sum / qs_weight, 1) if qs_weight else None
        search_impression_share = round(100.0 * sis_weighted / sis_impr, 1) if sis_impr else None

        traffic_daily_q = f"""
            SELECT
                segments.date,
                metrics.cost_micros,
                metrics.clicks,
                metrics.impressions
            FROM customer
            WHERE {where}
            ORDER BY segments.date
        """
        traffic_daily = []
        for row in _search_rows(ga, cid, traffic_daily_q):
            traffic_daily.append(
                {
                    "date": str(row.segments.date),
                    "spend": round(float(row.metrics.cost_micros or 0) / 1_000_000, 2),
                    "clicks": int(row.metrics.clicks or 0),
                    "impressions": int(row.metrics.impressions or 0),
                }
            )

        conv_daily_q = f"""
            SELECT
                segments.date,
                metrics.conversions,
                metrics.conversions_value
            FROM customer
            WHERE {where}
            ORDER BY segments.date
        """
        conv_daily = []
        for row in _search_rows(ga, cid, conv_daily_q):
            conv_daily.append(
                {
                    "date": str(row.segments.date),
                    "conversions": round(_metric_float(row.metrics, "conversions"), 2),
                    "conversion_value": round(
                        _metric_float(row.metrics, "conversions_value"),
                        2,
                    ),
                }
            )

        daily = _merge_daily(traffic_daily, conv_daily)
        spend_by_type = [{"name": k, "spend": round(v, 2)} for k, v in sorted(type_spend.items(), key=lambda x: -x[1])]

        payload = {
            "configured": True,
            "error": None,
            "currency": currency,
            "spend": round(total_cost, 2),
            "clicks": total_clicks,
            "impressions": total_impr,
            "conversions": round(total_conv, 2),
            "conversions_value": round(total_conv_value, 2),
            "conversions_time_basis": "click_date",
            "cost_per_conversion": round(total_cost / total_conv, 2) if total_conv > 0 else None,
            "ctr": round(100.0 * total_clicks / total_impr, 2) if total_impr else None,
            "cpc": round(total_cost / total_clicks, 2) if total_clicks else None,
            "campaigns": campaigns,
            "keywords": keywords,
            "spend_by_type": spend_by_type,
            "daily": daily,
            "avg_quality_score": avg_quality_score,
            "search_impression_share": search_impression_share,
        }
        cache_set(cache_key, payload)
        return _normalize_gads_payload(payload)
    except Exception as e:
        logger.exception("Google Ads API error")
        friendly = _format_google_ads_error(e)
        if stale_payload is not None:
            logger.info("Google Ads: API помилка — stale cache %s – %s", start.date(), end.date())
            stale_payload = dict(stale_payload)
            stale_payload["error"] = friendly
            return stale_payload
        return _normalize_gads_payload(_empty_payload(friendly))
