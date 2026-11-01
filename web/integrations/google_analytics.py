"""Google Analytics 4 Data API — Direct, Organic, інші канали."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from config import ga4_configured
from marketing_config import load_marketing_config
from settings.paths import DEFAULT_GSC_SERVICE_ACCOUNT_REL, resolve_project_path
from web.integrations.ad_cache import CACHE_NOT_WARMED, cache_set, get_stale_ad_payload, load_cached_period_payload

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]

_CHANNEL_DIRECT = "Direct"
_CHANNEL_ORGANIC = "Organic Search"


def _service_account_file() -> str:
    m = load_marketing_config()
    rel = m.get("GA4_SERVICE_ACCOUNT_FILE") or m.get(
        "GSC_SERVICE_ACCOUNT_FILE", DEFAULT_GSC_SERVICE_ACCOUNT_REL
    )
    return str(resolve_project_path(rel))


def _client():
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(_service_account_file(), scopes=SCOPES)
    return BetaAnalyticsDataClient(credentials=creds)


def _empty_channel() -> dict[str, Any]:
    return {
        "sessions": 0,
        "users": 0,
        "new_users": 0,
        "conversions": 0,
        "revenue": 0.0,
        "bounce_rate": None,
        "pages_per_session": None,
        "devices": [],
        "landing_pages": [],
        "daily": [],
    }


def _empty_payload(error: str | None = None) -> dict[str, Any]:
    return {
        "configured": ga4_configured(),
        "error": error,
        "direct": _empty_channel(),
        "organic": _empty_channel(),
        "referring_domains": 0,
        "other_groups": [],
        "other_sources": [],
    }


def _channel_filter(channel_group: str):
    from google.analytics.data_v1beta.types import Filter, FilterExpression

    return FilterExpression(
        filter=Filter(
            field_name="sessionDefaultChannelGroup",
            string_filter=Filter.StringFilter(value=channel_group),
        )
    )


def _ga4_property_id() -> str:
    return load_marketing_config().get("GA4_PROPERTY_ID", "")


def _run_report(
    client,
    *,
    dimensions: list[str],
    metrics: list[str],
    start: datetime,
    end: datetime,
    dimension_filter=None,
    limit: int = 10000,
) -> list[dict[str, Any]]:
    from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest

    request = RunReportRequest(
        property=f"properties/{_ga4_property_id()}",
        dimensions=[Dimension(name=d) for d in dimensions],
        metrics=[Metric(name=m) for m in metrics],
        date_ranges=[DateRange(
            start_date=start.strftime("%Y-%m-%d"),
            end_date=end.strftime("%Y-%m-%d"),
        )],
        dimension_filter=dimension_filter,
        limit=limit,
    )
    response = client.run_report(request)
    dim_headers = [h.name for h in response.dimension_headers]
    met_headers = [h.name for h in response.metric_headers]
    rows: list[dict[str, Any]] = []
    for row in response.rows:
        item: dict[str, Any] = {}
        for i, val in enumerate(row.dimension_values):
            item[dim_headers[i]] = val.value
        for i, val in enumerate(row.metric_values):
            item[met_headers[i]] = val.value
        rows.append(item)
    return rows


def _num(val: str | None, *, as_float: bool = False) -> float | int:
    if val in (None, "", "(not set)"):
        return 0.0 if as_float else 0
    try:
        return float(val) if as_float else int(float(val))
    except (TypeError, ValueError):
        return 0.0 if as_float else 0


def _channel_summary(client, start: datetime, end: datetime, channel_group: str) -> dict[str, Any]:
    rows = _run_report(
        client,
        dimensions=[],
        metrics=[
            "sessions",
            "totalUsers",
            "newUsers",
            "conversions",
            "totalRevenue",
            "bounceRate",
            "screenPageViewsPerSession",
        ],
        start=start,
        end=end,
        dimension_filter=_channel_filter(channel_group),
        limit=1,
    )
    row = rows[0] if rows else {}
    bounce = _num(row.get("bounceRate"), as_float=True)
    pps = _num(row.get("screenPageViewsPerSession"), as_float=True)
    return {
        "sessions": _num(row.get("sessions")),
        "users": _num(row.get("totalUsers")),
        "new_users": _num(row.get("newUsers")),
        "conversions": _num(row.get("conversions")),
        "revenue": round(_num(row.get("totalRevenue"), as_float=True), 2),
        "bounce_rate": round(bounce * 100, 1) if bounce else None,
        "pages_per_session": round(pps, 2) if pps else None,
    }


def _channel_devices(client, start: datetime, end: datetime, channel_group: str) -> list[dict[str, Any]]:
    rows = _run_report(
        client,
        dimensions=["deviceCategory"],
        metrics=["sessions"],
        start=start,
        end=end,
        dimension_filter=_channel_filter(channel_group),
        limit=10,
    )
    out = []
    for row in rows:
        device = (row.get("deviceCategory") or "unknown").lower()
        label = {"desktop": "Desktop", "mobile": "Mobile", "tablet": "Tablet"}.get(device, device.title())
        out.append({"device": label, "sessions": _num(row.get("sessions"))})
    return sorted(out, key=lambda x: -x["sessions"])


def _channel_landing_pages(client, start: datetime, end: datetime, channel_group: str, *, limit: int = 15) -> list[dict]:
    rows = _run_report(
        client,
        dimensions=["landingPage"],
        metrics=["sessions", "conversions", "totalRevenue", "bounceRate"],
        start=start,
        end=end,
        dimension_filter=_channel_filter(channel_group),
        limit=limit,
    )
    pages = []
    for row in rows:
        bounce = _num(row.get("bounceRate"), as_float=True)
        pages.append({
            "page": row.get("landingPage") or "—",
            "sessions": _num(row.get("sessions")),
            "conversions": _num(row.get("conversions")),
            "revenue": round(_num(row.get("totalRevenue"), as_float=True), 2),
            "bounce_rate": round(bounce * 100, 1) if bounce else None,
        })
    return sorted(pages, key=lambda x: -x["sessions"])


def _channel_daily(client, start: datetime, end: datetime, channel_group: str) -> list[dict]:
    rows = _run_report(
        client,
        dimensions=["date"],
        metrics=["sessions", "totalRevenue"],
        start=start,
        end=end,
        dimension_filter=_channel_filter(channel_group),
        limit=400,
    )
    daily = []
    for row in rows:
        raw = row.get("date") or ""
        iso = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if len(raw) == 8 else raw
        daily.append({
            "date": iso,
            "sessions": _num(row.get("sessions")),
            "revenue": round(_num(row.get("totalRevenue"), as_float=True), 2),
        })
    return sorted(daily, key=lambda x: x["date"])


def _other_channel_groups(client, start: datetime, end: datetime) -> list[dict[str, Any]]:
    excluded = {_CHANNEL_DIRECT, _CHANNEL_ORGANIC, "Paid Search", "Paid Social", "Paid Other", "Display", "Cross-network"}
    rows = _run_report(
        client,
        dimensions=["sessionDefaultChannelGroup"],
        metrics=["sessions", "totalRevenue"],
        start=start,
        end=end,
        limit=50,
    )
    groups = []
    for row in rows:
        name = row.get("sessionDefaultChannelGroup") or "Інше"
        if name in excluded:
            continue
        groups.append({
            "name": name,
            "sessions": _num(row.get("sessions")),
            "revenue": round(_num(row.get("totalRevenue"), as_float=True), 2),
        })
    return sorted(groups, key=lambda x: -x["sessions"])


def _referral_sources(client, start: datetime, end: datetime) -> tuple[int, list[dict]]:
    rows = _run_report(
        client,
        dimensions=["sessionSource"],
        metrics=["sessions", "totalRevenue"],
        start=start,
        end=end,
        dimension_filter=_channel_filter("Referral"),
        limit=100,
    )
    sources = []
    for row in rows:
        src = row.get("sessionSource") or "—"
        if src in ("(not set)", "(direct)"):
            continue
        sources.append({
            "source": src,
            "sessions": _num(row.get("sessions")),
            "revenue": round(_num(row.get("totalRevenue"), as_float=True), 2),
        })
    return len(sources), sorted(sources, key=lambda x: -x["sessions"])


def fetch_ga4_stats(start: datetime, end: datetime, *, use_cache: bool = True) -> dict[str, Any]:
    cache_key = f"ga4_{start.date()}_{end.date()}"

    if use_cache:
        cached = load_cached_period_payload("ga4", start, end)
        if cached is not None:
            return cached
        return _empty_payload(CACHE_NOT_WARMED)

    stale_payload = get_stale_ad_payload(cache_key)

    if not ga4_configured():
        if stale_payload is not None:
            logger.info("GA4: API не налаштовано — stale cache %s – %s", start.date(), end.date())
            return stale_payload
        return _empty_payload("Google Analytics 4 не налаштовано (GA4_PROPERTY_ID + service account)")

    try:
        client = _client()
        direct_summary = _channel_summary(client, start, end, _CHANNEL_DIRECT)
        organic_summary = _channel_summary(client, start, end, _CHANNEL_ORGANIC)

        direct = {
            **direct_summary,
            "devices": _channel_devices(client, start, end, _CHANNEL_DIRECT),
            "landing_pages": _channel_landing_pages(client, start, end, _CHANNEL_DIRECT),
            "daily": _channel_daily(client, start, end, _CHANNEL_DIRECT),
        }
        organic = {
            **organic_summary,
            "devices": _channel_devices(client, start, end, _CHANNEL_ORGANIC),
            "landing_pages": _channel_landing_pages(client, start, end, _CHANNEL_ORGANIC, limit=10),
            "daily": _channel_daily(client, start, end, _CHANNEL_ORGANIC),
        }

        ref_domains, ref_sources = _referral_sources(client, start, end)
        other_groups = _other_channel_groups(client, start, end)

        payload = {
            "configured": True,
            "error": None,
            "direct": direct,
            "organic": organic,
            "referring_domains": ref_domains,
            "other_groups": other_groups,
            "other_sources": ref_sources,
        }
        cache_set(cache_key, payload)
        return payload
    except Exception as e:
        logger.exception("GA4 API error")
        if stale_payload is not None:
            logger.info("GA4: API помилка — stale cache %s – %s", start.date(), end.date())
            stale_payload = dict(stale_payload)
            stale_payload["error"] = str(e)
            return stale_payload
        return _empty_payload(str(e))
