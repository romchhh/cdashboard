"""Google Search Console API — органічні запити та сторінки."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from config import gsc_configured
from marketing_config import load_marketing_config
from settings.paths import DEFAULT_GSC_SERVICE_ACCOUNT_REL, resolve_project_path
from web.cosmy_storefront import gsc_site_urls_from_config, normalize_gsc_site_url
from web.integrations.ad_cache import CACHE_NOT_WARMED, cache_set, get_stale_ad_payload, load_cached_period_payload

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]


def _service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    m = load_marketing_config()
    sa_file = str(
        resolve_project_path(m.get("GSC_SERVICE_ACCOUNT_FILE", DEFAULT_GSC_SERVICE_ACCOUNT_REL))
    )
    creds = service_account.Credentials.from_service_account_file(sa_file, scopes=SCOPES)
    return build("searchconsole", "v1", credentials=creds, cache_discovery=False)


def _gsc_merge_storefront_domains() -> bool:
    m = load_marketing_config()
    raw = m.get("GSC_MERGE_STOREFRONT_DOMAINS")
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().lower() in ("1", "true", "yes", "on")


def _gsc_site_urls() -> list[str]:
    m = load_marketing_config()
    return gsc_site_urls_from_config(
        m.get("GSC_SITE_URL", "https://cosmy.ua"),
        m.get("GSC_SITE_URL_EXTRA"),
        include_storefront_pair=_gsc_merge_storefront_domains(),
    )


_GSC_PERMISSION_OK = frozenset({"siteowner", "sitefulluser"})


def _list_accessible_gsc_site_urls(service) -> set[str]:
    """URL properties, до яких service account має доступ (sites().list)."""
    try:
        result = service.sites().list().execute()
    except Exception as exc:
        logger.warning("GSC sites().list: %s", exc)
        return set()
    out: set[str] = set()
    for entry in result.get("siteEntry") or []:
        perm = str(entry.get("permissionLevel") or "").replace("_", "").lower()
        if perm not in _GSC_PERMISSION_OK:
            continue
        url = normalize_gsc_site_url(str(entry.get("siteUrl") or ""))
        if url:
            out.add(url)
    return out


def _filter_gsc_site_urls_for_api(service, configured: list[str]) -> tuple[list[str], list[str]]:
    """Лишає лише properties з доступом; skipped — без прав (не помилка API)."""
    accessible = _list_accessible_gsc_site_urls(service)
    if not accessible:
        return configured, []
    picked: list[str] = []
    skipped: list[str] = []
    for url in configured:
        norm = normalize_gsc_site_url(url)
        if norm in accessible:
            if norm not in picked:
                picked.append(norm)
        else:
            skipped.append(norm)
    if not picked:
        # Конфіг застарів — беремо всі доступні cosmy-* properties.
        for url in sorted(accessible):
            if "cosmy" in url:
                picked.append(url)
    return picked, skipped


def _empty_payload(error: str | None = None) -> dict[str, Any]:
    return {
        "configured": gsc_configured(),
        "error": error,
        "clicks": 0,
        "impressions": 0,
        "ctr": None,
        "position": None,
        "queries": [],
        "pages": [],
        "daily": [],
        "kw_top10": None,
        "kw_top3": None,
        "indexed_pages": None,
        "gsc_site_urls": _gsc_site_urls(),
    }


def _query_analytics(
    service,
    site_url: str,
    start: datetime,
    end: datetime,
    dimensions: list[str],
    limit: int = 25,
) -> list[dict]:
    body = {
        "startDate": start.strftime("%Y-%m-%d"),
        "endDate": end.strftime("%Y-%m-%d"),
        "dimensions": dimensions,
        "rowLimit": min(limit, 25000),
    }
    result = service.searchanalytics().query(siteUrl=site_url, body=body).execute()
    rows = []
    for row in result.get("rows") or []:
        keys = row.get("keys") or []
        item = {
            "clicks": int(row.get("clicks", 0)),
            "impressions": int(row.get("impressions", 0)),
            "ctr": round(float(row.get("ctr", 0)) * 100, 2),
            "position": round(float(row.get("position", 0)), 1),
        }
        if dimensions == ["query"]:
            item["key"] = keys[0] if keys else "—"
        elif dimensions == ["page"]:
            item["key"] = keys[0] if keys else "—"
        elif dimensions == ["query", "page"]:
            item["key"] = keys[0] if keys else "—"
            item["page"] = keys[1] if len(keys) > 1 else "—"
        else:
            item["key"] = keys[0] if keys else "—"
        rows.append(item)
    return rows


def _merge_keyed_rows(rows: list[dict], *, key_field: str = "key") -> list[dict]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = row.get(key_field) or "—"
        if key not in merged:
            merged[key] = {
                key_field: key,
                "clicks": 0,
                "impressions": 0,
                "_pos_weight": 0.0,
            }
            if "page" in row:
                merged[key]["page"] = row.get("page")
        bucket = merged[key]
        clicks = int(row.get("clicks") or 0)
        impr = int(row.get("impressions") or 0)
        pos = float(row.get("position") or 0)
        bucket["clicks"] += clicks
        bucket["impressions"] += impr
        bucket["_pos_weight"] += pos * impr
        if row.get("page") and (not bucket.get("page") or bucket.get("page") == "—"):
            bucket["page"] = row.get("page")
    out: list[dict] = []
    for bucket in merged.values():
        impr = bucket["impressions"]
        clicks = bucket["clicks"]
        ctr = round(100.0 * clicks / impr, 2) if impr else 0.0
        pos = round(bucket["_pos_weight"] / impr, 1) if impr else None
        item = {
            "key": bucket["key"],
            "clicks": clicks,
            "impressions": impr,
            "ctr": ctr,
            "position": pos,
        }
        if "page" in bucket:
            item["page"] = bucket.get("page") or "—"
        out.append(item)
    return out


def _merge_query_page_rows(rows: list[dict]) -> list[dict]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        q = row.get("key") or "—"
        p = row.get("page") or "—"
        k = (q, p)
        if k not in merged:
            merged[k] = {"key": q, "page": p, "clicks": 0, "impressions": 0, "_pos_weight": 0.0}
        bucket = merged[k]
        clicks = int(row.get("clicks") or 0)
        impr = int(row.get("impressions") or 0)
        pos = float(row.get("position") or 0)
        bucket["clicks"] += clicks
        bucket["impressions"] += impr
        bucket["_pos_weight"] += pos * impr
    out: list[dict] = []
    for bucket in merged.values():
        impr = bucket["impressions"]
        clicks = bucket["clicks"]
        out.append(
            {
                "key": bucket["key"],
                "page": bucket["page"],
                "clicks": clicks,
                "impressions": impr,
                "ctr": round(100.0 * clicks / impr, 2) if impr else 0.0,
                "position": round(bucket["_pos_weight"] / impr, 1) if impr else None,
            }
        )
    return out


def _merge_daily_rows(rows: list[dict]) -> list[dict]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        d = row.get("key") or row.get("date")
        if not d:
            continue
        if d not in merged:
            merged[d] = {"date": d, "key": d, "clicks": 0, "impressions": 0, "_pos_weight": 0.0}
        bucket = merged[d]
        clicks = int(row.get("clicks") or 0)
        impr = int(row.get("impressions") or 0)
        pos = float(row.get("position") or 0) if row.get("position") is not None else 0.0
        bucket["clicks"] += clicks
        bucket["impressions"] += impr
        bucket["_pos_weight"] += pos * impr
    out: list[dict] = []
    for d in sorted(merged.keys()):
        bucket = merged[d]
        impr = bucket["impressions"]
        out.append(
            {
                "date": d,
                "clicks": bucket["clicks"],
                "impressions": impr,
                "position": round(bucket["_pos_weight"] / impr, 1) if impr else None,
            }
        )
    return out


def _count_keywords_by_position(queries: list[dict]) -> tuple[int, int]:
    top10 = sum(1 for q in queries if (q.get("position") or 99) <= 10)
    top3 = sum(1 for q in queries if (q.get("position") or 99) <= 3)
    return top10, top3


def _fetch_indexed_pages(service, site_url: str) -> int | None:
    try:
        result = service.sitemaps().list(siteUrl=site_url).execute()
        indexed = 0
        for sm in result.get("sitemap") or []:
            for block in sm.get("contents") or []:
                indexed += int(block.get("indexed", 0))
        return indexed or None
    except Exception as e:
        logger.warning("GSC sitemaps %s: %s", site_url, e)
        return None


def _fetch_site_payload(service, site_url: str, start: datetime, end: datetime) -> dict[str, Any]:
    summary_body = {
        "startDate": start.strftime("%Y-%m-%d"),
        "endDate": end.strftime("%Y-%m-%d"),
    }
    summary = service.searchanalytics().query(siteUrl=site_url, body=summary_body).execute()
    srow = (summary.get("rows") or [{}])[0] if summary.get("rows") else {}
    total_clicks = int(srow.get("clicks", 0))
    total_impr = int(srow.get("impressions", 0))
    avg_ctr = round(float(srow.get("ctr", 0)) * 100, 2) if srow.get("ctr") is not None else None
    avg_pos = round(float(srow.get("position", 0)), 1) if srow.get("position") is not None else None

    queries = _query_analytics(service, site_url, start, end, ["query"], 500)
    query_pages = _query_analytics(service, site_url, start, end, ["query", "page"], 100)
    pages = _query_analytics(service, site_url, start, end, ["page"], 50)
    daily_rows = _query_analytics(service, site_url, start, end, ["date"], 500)
    indexed_pages = _fetch_indexed_pages(service, site_url)

    return {
        "clicks": total_clicks,
        "impressions": total_impr,
        "ctr": avg_ctr,
        "position": avg_pos,
        "queries": queries,
        "query_pages": query_pages,
        "pages": pages,
        "daily": daily_rows,
        "indexed_pages": indexed_pages,
    }


def _merge_site_payloads(parts: list[dict[str, Any]]) -> dict[str, Any]:
    if not parts:
        return {}
    if len(parts) == 1:
        p = parts[0]
        queries = list(p.get("queries") or [])
        query_pages = list(p.get("query_pages") or [])
        page_by_query: dict[str, str] = {}
        for qp in query_pages:
            qk = qp.get("key")
            if qk and qk not in page_by_query:
                page_by_query[qk] = qp.get("page") or "—"
        for q in queries:
            q["page"] = page_by_query.get(q.get("key"), "—")
        daily = _merge_daily_rows(list(p.get("daily") or []))
        indexed = p.get("indexed_pages")
        if indexed is None and p.get("pages"):
            indexed = len(p.get("pages") or [])
        return {
            "clicks": int(p.get("clicks") or 0),
            "impressions": int(p.get("impressions") or 0),
            "ctr": p.get("ctr"),
            "position": p.get("position"),
            "queries": queries,
            "pages": list(p.get("pages") or []),
            "daily": daily,
            "indexed_pages": indexed,
        }

    total_clicks = sum(int(p.get("clicks") or 0) for p in parts)
    total_impr = sum(int(p.get("impressions") or 0) for p in parts)
    pos_weight = 0.0
    for p in parts:
        impr = int(p.get("impressions") or 0)
        pos = p.get("position")
        if impr and pos is not None:
            pos_weight += float(pos) * impr
    avg_pos = round(pos_weight / total_impr, 1) if total_impr else None
    avg_ctr = round(100.0 * total_clicks / total_impr, 2) if total_impr else None

    all_queries = _merge_keyed_rows([q for p in parts for q in (p.get("queries") or [])])
    all_query_pages = _merge_query_page_rows([q for p in parts for q in (p.get("query_pages") or [])])
    page_by_query: dict[str, str] = {}
    for qp in sorted(all_query_pages, key=lambda x: -(x.get("clicks") or 0)):
        qk = qp.get("key")
        if qk and qk not in page_by_query:
            page_by_query[qk] = qp.get("page") or "—"
    for q in all_queries:
        q["page"] = page_by_query.get(q.get("key"), "—")
    all_queries.sort(key=lambda x: -(x.get("clicks") or 0))
    all_pages = _merge_keyed_rows([pg for p in parts for pg in (p.get("pages") or [])])
    all_pages.sort(key=lambda x: -(x.get("clicks") or 0))
    daily = _merge_daily_rows([d for p in parts for d in (p.get("daily") or [])])

    indexed_sum = 0
    indexed_any = False
    for p in parts:
        ip = p.get("indexed_pages")
        if ip is not None:
            indexed_any = True
            indexed_sum += int(ip)
    indexed_pages = indexed_sum if indexed_any else (len(all_pages) if all_pages else None)

    return {
        "clicks": total_clicks,
        "impressions": total_impr,
        "ctr": avg_ctr,
        "position": avg_pos,
        "queries": all_queries[:500],
        "pages": all_pages[:50],
        "daily": daily,
        "indexed_pages": indexed_pages,
    }


def fetch_gsc_stats(start: datetime, end: datetime, *, use_cache: bool = True) -> dict[str, Any]:
    cache_key = f"gsc2_{start.date()}_{end.date()}"

    if use_cache:
        cached = load_cached_period_payload("gsc2", start, end, legacy_prefix="gsc")
        if cached is not None:
            return cached
        return _empty_payload(CACHE_NOT_WARMED)

    stale_payload = get_stale_ad_payload(cache_key)

    if not gsc_configured():
        if stale_payload is not None:
            logger.info("GSC: API не налаштовано — stale cache %s – %s", start.date(), end.date())
            return stale_payload
        return _empty_payload("Google Search Console не налаштовано (marketing_config.json)")

    configured_urls = _gsc_site_urls()
    try:
        service = _service()
        site_urls, skipped_urls = _filter_gsc_site_urls_for_api(service, configured_urls)
        if skipped_urls:
            logger.info(
                "GSC: пропущено properties без доступу SA: %s",
                ", ".join(skipped_urls),
            )
        parts: list[dict[str, Any]] = []
        errors: list[str] = []
        for site_url in site_urls:
            try:
                parts.append(_fetch_site_payload(service, site_url, start, end))
            except Exception as site_err:
                logger.warning("GSC API error for %s: %s", site_url, site_err)
                errors.append(f"{site_url}: {site_err}")

        if not parts:
            raise RuntimeError("; ".join(errors) if errors else "GSC: немає даних")

        merged = _merge_site_payloads(parts)
        kw_top10, kw_top3 = _count_keywords_by_position(merged.get("queries") or [])
        _daily = merged.get("daily") or []
        _dates = [str(d.get("date"))[:10] for d in _daily if d.get("date")]
        daily_through = max(_dates) if _dates else None

        payload = {
            "configured": True,
            "error": "; ".join(errors) if errors else None,
            "gsc_site_urls": site_urls,
            "gsc_site_urls_configured": configured_urls,
            "gsc_site_urls_skipped": skipped_urls,
            **merged,
            "daily_through": daily_through,
            "kw_top10": kw_top10,
            "kw_top3": kw_top3,
        }
        cache_set(cache_key, payload)
        return payload
    except Exception as e:
        logger.exception("GSC API error")
        if stale_payload is not None:
            logger.info("GSC: API помилка — stale cache %s – %s", start.date(), end.date())
            stale_payload = dict(stale_payload)
            stale_payload["error"] = str(e)
            return stale_payload
        return _empty_payload(str(e))
