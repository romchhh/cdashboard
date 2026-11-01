"""Маршрути розділу «Аналіз реклами»."""

from __future__ import annotations

import asyncio
import csv
import io
import logging
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from bot.database.clients_engine import AsyncSessionLocalClients
from config import (
    GOOGLE_ADS_CLIENT_ID,
    INTEGRATION_SETTINGS_UI_ENABLED,
    google_ads_configured,
    gsc_configured,
    meta_ads_configured,
    ga4_configured,
)
from web.ad_reporting import SECTIONS, SECTION_LABELS, build_advertising_report
from web.auth import ROLE_ADMIN, get_current_user, require_admin
from web.deps import templates
from web.integrations.ad_integration_settings import update_integration_credentials
from web.integrations.seo_expenses import (
    SeoExpenseError,
    create_seo_expense,
    delete_seo_expense,
    list_seo_expenses,
    update_seo_expense,
)
from web.security import verify_csrf_dependency

logger = logging.getLogger(__name__)

router = APIRouter()

# Нейтральний URL/статика: фільтри (uBlock, старий Chrome) часто блокують шляхи з «ad» / «analytics».
AD_DASH_PREFIX = "/clients/marketing-report"
AD_DASH_LEGACY_PREFIXES = ("/clients/ad-analytics", "/clients/advertising")
MKT_API_PREFIX = "/mkt-report"


def _require_advertising_access(request: Request) -> None:
    """Доступ лише з областю «Реклама» (або admin)."""
    role = getattr(request.state, "role", None)
    if role == ROLE_ADMIN:
        return
    if not getattr(request.state, "access_advertising", False):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Доступ заборонено")


def _period_query(request: Request) -> dict[str, str]:
    qp = request.query_params
    out: dict[str, str] = {}
    for key in ("date_from", "date_to", "refresh", "compare", "chart_granularity"):
        v = (qp.get(key) or "").strip()
        if v:
            out[key] = v
    return out


async def _load_report(
    section: str,
    *,
    date_from: str | None,
    date_to: str | None,
    refresh: bool,
    compare: str,
    chart_granularity: str | None = None,
) -> dict[str, Any]:
    async with AsyncSessionLocalClients() as session:
        return await build_advertising_report(
            session,
            section=section,
            date_from=date_from,
            date_to=date_to,
            refresh=refresh,
            compare=compare,
            chart_granularity=chart_granularity,
        )


def _advertising_error_report(section: str, error: str) -> dict[str, Any]:
    """Мінімальний звіт, щоб сторінка завжди рендерилась з повідомленням про помилку."""
    sec = section if section in SECTIONS else "overview"
    today = date.today()
    start = today - timedelta(days=6)
    empty_kpi = {
        "spend": 0,
        "revenue": 0,
        "roas": None,
        "roas_pct": None,
        "orders": 0,
        "conversions": 0,
        "cpa": None,
        "avg_check": None,
        "clicks": 0,
        "impressions": 0,
        "traffic": 0,
        "conversion_rate": None,
        "conversion_rate_pct": None,
        "spend_approximate": False,
        "spend_fx_note": None,
        "delta": {},
    }
    return {
        "section": sec,
        "section_label": SECTION_LABELS.get(sec, sec),
        "period": {
            "from_iso": start.isoformat(),
            "to_iso": today.isoformat(),
            "compare": "prev",
            "compare_label": "vs попередній період",
        },
        "kpi": empty_kpi,
        "chart": [],
        "donut": {"labels": [], "values": [], "total": 0},
        "top_brands": [],
        "overview_channels": [],
        "insights": [],
        "insight_chips": [],
        "insight_feed": [],
        "api_status": {"configured": True, "error": None},
        "load_error": error,
        "chart_granularity": "day",
        "integrations_panel": None,
        "section_integrations": None,
    }


def _template_context(
    request: Request,
    *,
    sec: str,
    report: dict[str, Any],
    sync_notice: str | None = None,
    integration_notice: str | None = None,
) -> dict[str, Any]:
    role = getattr(request.state, "role", None)
    return {
        "request": request,
        "clients_nav": "advertising",
        "ad_section": sec,
        "report": report,
        "sync_notice": sync_notice,
        "integration_notice": integration_notice,
        "is_admin": role == ROLE_ADMIN,
        "show_integration_settings": INTEGRATION_SETTINGS_UI_ENABLED,
        "period_qs": _period_query(request),
        "integrations": {
            "google_ads": google_ads_configured(),
            "meta": meta_ads_configured(),
            "gsc": gsc_configured(),
            "ga4": ga4_configured(),
        },
        "ad_dash_prefix": AD_DASH_PREFIX,
    }


@router.get("/clients/marketing-report", response_class=HTMLResponse)
@router.get("/clients/marketing-report/{section}", response_class=HTMLResponse)
@router.get("/clients/ad-analytics", response_class=HTMLResponse)
@router.get("/clients/ad-analytics/{section}", response_class=HTMLResponse)
@router.get("/clients/advertising", response_class=HTMLResponse)
@router.get("/clients/advertising/{section}", response_class=HTMLResponse)
async def advertising_page(
    request: Request,
    background_tasks: BackgroundTasks,
    section: str = "overview",
    user: str = Depends(get_current_user),
    _ad_access: None = Depends(_require_advertising_access),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    refresh: str | None = Query(None),
    sync_crm: str | None = Query(None),
    compare: str | None = Query(None),
    chart_granularity: str | None = Query(None),
    partial: str | None = Query(None),
):
    sec = (section or "overview").strip().lower()
    if sec not in SECTIONS:
        sec = "overview"

    api_refresh_requested = bool((refresh or "").strip())
    if api_refresh_requested:
        from bot.scheduler.tasks import refresh_ad_cache

        async def _refresh_ad_apis() -> None:
            try:
                await refresh_ad_cache(sync_crm=False)
            except Exception as exc:
                logger.error("Background ad API refresh failed: %s", exc, exc_info=True)

        asyncio.create_task(_refresh_ad_apis())

    if not api_refresh_requested:
        from web.integrations.crm_auto_sync import ensure_crm_fresh_for_advertising

        background_tasks.add_task(ensure_crm_fresh_for_advertising)

    sync_notice = None
    if api_refresh_requested:
        sync_notice = "⏳ Оновлення Google/Meta/GSC/GA4 у фоні. Перезавантажте сторінку через хвилину."
    if (sync_crm or "").strip() == "1":
        async def _sync_crm_feed() -> None:
            try:
                from bot.services.clients_sync import sync_clients_from_feed
                from web.integrations.crm_analytics_cache import cache_invalidate_all
                from web.ad_analytics import warm_default_crm_analytics_cache

                await sync_clients_from_feed()
                cache_invalidate_all()
                async with AsyncSessionLocalClients() as warm_session:
                    await warm_default_crm_analytics_cache(warm_session)
            except Exception as exc:
                logger.error("Ad page sync_crm error: %s", exc, exc_info=True)

        asyncio.create_task(_sync_crm_feed())
        sync_notice = "⏳ CRM синхронізується у фоні. Перезавантажте сторінку через хвилину."

    integration_notice = None
    if (request.query_params.get("integration_saved") or "").strip() == "1":
        verified = (request.query_params.get("integration_verified") or "").strip()
        if verified:
            integration_notice = f"✅ Ключі збережено і перевірено: {verified}"
        else:
            integration_notice = "✅ Ключі API збережено. Дані оновляться автоматично при наступному відкритті розділу."
    if (request.query_params.get("integration_oauth_ok") or "").strip() == "1":
        verified = (request.query_params.get("integration_verified") or "").strip()
        if verified:
            integration_notice = f"✅ Google Ads підключено. {verified}"
        else:
            integration_notice = "✅ Google Ads підключено. Новий refresh token збережено — дані оновляться автоматично."
    err = (request.query_params.get("integration_error") or "").strip()
    if err:
        integration_notice = f"❌ {err}"

    try:
        report = await _load_report(
            sec,
            date_from=date_from,
            date_to=date_to,
            refresh=api_refresh_requested,
            compare=(compare or "prev").strip() or "prev",
            chart_granularity=(chart_granularity or "").strip() or None,
        )
    except Exception as exc:
        logger.exception("Advertising report failed for section=%s", sec)
        report = _advertising_error_report(sec, str(exc))
        if not sync_notice:
            sync_notice = f"⚠️ Не вдалося завантажити дані звіту. Показано порожній шаблон."

    from web.integrations.ad_integration_settings import build_section_integration_settings
    from web.integrations.google_ads_oauth import public_base_from_request

    if report.get("integrations_panel"):
        report["section_integrations"] = build_section_integration_settings(
            sec,
            report["integrations_panel"],
            public_base=public_base_from_request(request),
        )

    ctx = _template_context(
        request, sec=sec, report=report, sync_notice=sync_notice, integration_notice=integration_notice,
    )
    template_name = "clients/advertising/_content.html" if (partial or "").strip() == "1" else "clients/advertising/dashboard.html"
    response = templates.TemplateResponse(template_name, ctx)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def _export_rows(sec: str, report: dict[str, Any]) -> tuple[list[str], list[list[Any]]]:
    if sec == "overview":
        headers = ["Канал", "Витрати", "Дохід", "Δ дохід %", "Замовлення", "Δ замовлення %", "CR %", "Δ CR %", "ROAS", "Частка доходу %"]
        rows = [
            [
                row["label"],
                row["spend"],
                row["revenue"],
                row.get("delta_revenue_pct", ""),
                row["orders"],
                row.get("delta_orders_pct", ""),
                row.get("conversion_rate", ""),
                row.get("delta_conversion_rate_pct", ""),
                row.get("roas") or "",
                row.get("revenue_share_pct", ""),
            ]
            for row in report.get("overview_channels") or []
        ]
        return headers, rows
    if sec == "google_ads":
        headers = ["Кампанія", "Тип", "Витрати", "Дохід", "Кліки", "Покази", "Конверсії", "CRM замовлення", "Δ замовлення %", "ROAS"]
        rows = [
            [
                c["name"], c["type"], c["spend"], c.get("revenue", ""),
                c["clicks"], c["impressions"], c.get("conversions", ""),
                c.get("crm_orders", ""), c.get("delta_orders_pct", ""), c.get("roas_pct", ""),
            ]
            for c in (report.get("extra") or {}).get("campaigns") or []
        ]
        return headers, rows
    if sec == "meta":
        headers = ["Кампанія", "Ціль", "Витрати", "Дохід CRM", "Кліки", "Покази", "Покупки Meta", "Δ покупки %", "ROAS CRM"]
        rows = [
            [
                c["name"], c.get("objective", ""), c["spend"], c.get("crm_revenue", c.get("revenue", "")),
                c["clicks"], c["impressions"], c.get("meta_purchases") or c.get("purchases") or 0,
                c.get("delta_conversions_pct", ""), c.get("roas_pct", ""),
            ]
            for c in (report.get("extra") or {}).get("campaigns") or []
        ]
        return headers, rows
    if sec == "seo":
        headers = ["Запит", "Кліки", "Покази", "CTR %", "Позиція"]
        rows = [
            [q["key"], q["clicks"], q["impressions"], q.get("ctr", ""), q.get("position", "")]
            for q in (report.get("extra") or {}).get("queries") or []
        ]
        return headers, rows
    if sec == "details":
        headers = ["Дата", "№ замовлення", "Канал", "Джерело", "Кампанія", "Місто", "Дохід"]
        rows = [
            [
                o["date"], o["order_id"], o["channel"], o["source"],
                o["campaign"], o["city"], o["revenue"],
            ]
            for o in (report.get("extra") or {}).get("orders") or []
        ]
        return headers, rows
    if sec == "business":
        biz = (report.get("extra") or {}).get("business") or {}
        headers = ["Тип", "Назва", "Дохід", "Частка %", "Замовлення"]
        rows = []
        for row in biz.get("top_channels") or biz.get("top_categories") or []:
            rows.append(["Канал UTM", row.get("label") or row.get("name"), row.get("revenue"), row.get("share_pct"), row.get("orders")])
        for row in biz.get("top_utm_tags") or []:
            rows.append([
                "UTM",
                f"{row.get('utm_source')} / {row.get('utm_medium')} → {row.get('channel_label')}",
                row.get("revenue"),
                row.get("share_pct"),
                row.get("orders"),
            ])
        for brand in report.get("top_brands") or []:
            rows.append(["Бренд", brand.get("brand"), brand.get("revenue"), brand.get("revenue_share_pct"), brand.get("orders")])
        for row in biz.get("top_cities") or []:
            rows.append(["Місто", row.get("name"), row.get("revenue"), row.get("share_pct"), ""])
        return headers, rows
    if sec == "direct":
        headers = ["Сторінка", "Сесії", "Δ сесії %", "Конверсії", "Δ замовлення %", "Дохід", "Δ дохід %"]
        rows = [
            [
                p.get("page"), p.get("sessions"), p.get("delta_sessions_pct"),
                p.get("conversions"), p.get("delta_orders_pct"),
                p.get("revenue"), p.get("delta_revenue_pct"),
            ]
            for p in (report.get("extra") or {}).get("landing_pages") or []
        ]
        return headers, rows
    if sec == "insights":
        headers = ["ID", "Категорія", "Заголовок", "Що сталось", "Рекомендація", "Тренд"]
        rows = [
            [
                item.get("id"), item.get("category"), item.get("title"),
                item.get("what_happened"), item.get("action"), item.get("trend"),
            ]
            for item in report.get("insight_feed") or []
        ]
        return headers, rows
    if sec == "other":
        headers = ["Джерело", "Тип", "Замовлення", "Δ замовлення %", "Дохід", "Δ дохід %", "Частка в «Інших» %"]
        rows = [
            [
                s["source"], s.get("type", ""), s["orders"], s.get("delta_orders_pct", ""),
                s["revenue"], s.get("delta_revenue_pct", ""), s.get("channel_share_pct", s.get("share_pct", "")),
            ]
            for s in (report.get("extra") or {}).get("sources") or []
        ]
        return headers, rows
    headers = ["Джерело", "Тип", "Замовлення", "Δ замовлення %", "Дохід", "Δ дохід %", "Частка %"]
    rows = [
        [
            s["source"], s.get("type", ""), s["orders"], s.get("delta_orders_pct", ""),
            s["revenue"], s.get("delta_revenue_pct", ""), s.get("share_pct", ""),
        ]
        for s in (report.get("extra") or {}).get("sources") or []
    ]
    return headers, rows


@router.get("/clients/marketing-report/{section}/export.csv")
@router.get("/clients/ad-analytics/{section}/export.csv")
@router.get("/clients/advertising/{section}/export.csv")
async def advertising_export_csv(
    request: Request,
    section: str,
    user: str = Depends(get_current_user),
    _ad_access: None = Depends(_require_advertising_access),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    compare: str | None = Query(None),
):
    sec = (section or "overview").strip().lower()
    if sec not in SECTIONS:
        sec = "overview"

    report = await _load_report(
        sec, date_from=date_from, date_to=date_to, refresh=False, compare=(compare or "prev"),
    )
    headers, rows = _export_rows(sec, report)

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    w.writerow(headers)
    for row in rows:
        w.writerow(row)

    if report.get("top_brands") and sec not in ("details",):
        w.writerow([])
        w.writerow(["Бренд", "Дохід", "Δ дохід %", "Замовлення", "Δ замовлення %", "Витрати ≈", "ROAS", "Частка %"])
        for b in report["top_brands"]:
            w.writerow([
                b["brand"],
                b.get("revenue"),
                b.get("delta_revenue_pct"),
                b.get("orders"),
                b.get("delta_orders_pct"),
                b.get("spend"),
                b.get("roas_pct"),
                b.get("revenue_share_pct"),
            ])

    content = ("\ufeff" + buf.getvalue()).encode("utf-8")
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="advertising_{sec}.csv"'},
    )


@router.get("/clients/marketing-report/{section}/export.xlsx")
@router.get("/clients/ad-analytics/{section}/export.xlsx")
@router.get("/clients/advertising/{section}/export.xlsx")
async def advertising_export_xlsx(
    request: Request,
    section: str,
    user: str = Depends(get_current_user),
    _ad_access: None = Depends(_require_advertising_access),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    compare: str | None = Query(None),
):
    from openpyxl import Workbook

    sec = (section or "overview").strip().lower()
    if sec not in SECTIONS:
        sec = "overview"

    report = await _load_report(
        sec, date_from=date_from, date_to=date_to, refresh=False, compare=(compare or "prev"),
    )
    headers, rows = _export_rows(sec, report)

    wb = Workbook()
    ws = wb.active
    ws.title = (SECTION_LABELS.get(sec, sec) or sec)[:31]
    ws.append(headers)
    for row in rows:
        ws.append(row)

    if report.get("top_brands") and sec not in ("details",):
        ws.append([])
        ws.append(["Бренд", "Дохід", "Δ дохід %", "Замовлення", "Δ замовлення %", "Витрати ≈", "ROAS", "Частка %"])
        for b in report["top_brands"]:
            ws.append([
                b["brand"],
                b.get("revenue"),
                b.get("delta_revenue_pct"),
                b.get("orders"),
                b.get("delta_orders_pct"),
                b.get("spend"),
                b.get("roas_pct"),
                b.get("revenue_share_pct"),
            ])

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return Response(
        content=out.read(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="advertising_{sec}.xlsx"'},
    )


@router.post("/mkt-report/integrations/{integration_key}")
@router.post("/ad-analytics/integrations/{integration_key}")
@router.post("/advertising/integrations/{integration_key}")
async def advertising_integration_update(
    request: Request,
    integration_key: str,
    user: str = Depends(require_admin),
    _csrf: None = Depends(verify_csrf_dependency),
):
    """Оновлення API-ключів інтеграції в marketing_config.json (лише admin)."""
    if not INTEGRATION_SETTINGS_UI_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    key = (integration_key or "").strip().lower()
    form = await request.form()
    values = {k: str(v) for k, v in form.items() if k not in ("csrf_token",)}
    referer = (request.headers.get("referer") or f"{AD_DASH_PREFIX}/overview").split("?")[0]

    wants_json = "application/json" in (request.headers.get("accept") or "").lower()

    try:
        updated, verified = update_integration_credentials(key, values)
        if wants_json:
            from fastapi.responses import JSONResponse
            return JSONResponse({
                "ok": True,
                "updated": updated,
                "verified": verified,
                "message": (
                    ("Збережено: " + ", ".join(updated) + (f". {verified}" if verified else ""))
                    if updated
                    else "Немає змін"
                ),
            })
        sep = "&" if "?" in referer else "?"
        url = f"{referer}{sep}integration_saved=1"
        if verified:
            url += f"&integration_verified={quote(verified)}"
        return RedirectResponse(url=url, status_code=302)
    except ValueError as exc:
        if wants_json:
            from fastapi.responses import JSONResponse
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        sep = "&" if "?" in referer else "?"
        return RedirectResponse(url=f"{referer}{sep}integration_error={quote(str(exc))}", status_code=302)
    except Exception as exc:
        logger.exception("Integration credentials update failed")
        if wants_json:
            from fastapi.responses import JSONResponse
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _normalize_ad_dash_path(path: str) -> str:
    for legacy in AD_DASH_LEGACY_PREFIXES:
        if path == legacy or path.startswith(legacy + "/"):
            return AD_DASH_PREFIX + path[len(legacy):]
    return path


def _safe_return_path(raw: str | None) -> str:
    fallback = f"{AD_DASH_PREFIX}/google_ads"
    path = (raw or fallback).strip()
    if not path.startswith("/") or path.startswith("//"):
        return fallback
    path = _normalize_ad_dash_path(path.split("?")[0])
    if path == AD_DASH_PREFIX or path.startswith(AD_DASH_PREFIX + "/"):
        return path
    return fallback


@router.get("/mkt-report/oauth/google-ads/start")
@router.get("/ad-analytics/oauth/google-ads/start")
@router.get("/advertising/oauth/google-ads/start")
async def google_ads_oauth_start(
    request: Request,
    user: str = Depends(require_admin),
    return_to: str | None = Query(None),
):
    """Редірект на Google OAuth для отримання нового refresh token (лише admin)."""
    from web.integrations.google_ads_oauth import (
        build_authorize_url,
        create_oauth_state,
        oauth_prerequisites_ok,
        resolve_oauth_redirect_uri,
    )

    if not oauth_prerequisites_ok():
        raise HTTPException(
            status_code=400,
            detail="Спочатку збережіть GOOGLE_ADS_CLIENT_ID і GOOGLE_ADS_CLIENT_SECRET у налаштуваннях.",
        )

    redirect_uri = resolve_oauth_redirect_uri(request, str(request.base_url))
    logger.info("Google Ads OAuth start: redirect_uri=%s client_id=%s…", redirect_uri, GOOGLE_ADS_CLIENT_ID[:20])
    return_path = _safe_return_path(return_to)
    state = create_oauth_state(
        return_to=return_path,
        redirect_uri=redirect_uri,
        username=user,
    )
    url = build_authorize_url(redirect_uri=redirect_uri, state=state)
    return RedirectResponse(url=url, status_code=302)


@router.get("/advertising/oauth/google-ads/callback")
async def google_ads_oauth_callback(
    request: Request,
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
):
    """Callback Google OAuth — зберігає refresh token у .env."""
    from urllib.parse import quote

    from web.integrations.google_ads_oauth import (
        exchange_code_for_tokens,
        load_oauth_state,
        save_refresh_token,
    )

    fallback = f"{AD_DASH_PREFIX}/google_ads"

    if error:
        msg = quote(error)
        return RedirectResponse(url=f"{fallback}?integration_error={msg}", status_code=302)

    if not code or not state:
        return RedirectResponse(
            url=f"{fallback}?integration_error={quote('Відсутній code або state від Google')}",
            status_code=302,
        )

    payload = load_oauth_state(state)
    if not payload:
        return RedirectResponse(
            url=f"{fallback}?integration_error={quote('Недійсний або прострочений OAuth state')}",
            status_code=302,
        )

    return_to = _safe_return_path(payload.get("return_to"))
    redirect_uri = str(payload.get("redirect_uri") or "").strip()

    try:
        tokens = exchange_code_for_tokens(code, redirect_uri)
        refresh = tokens.get("refresh_token")
        if not refresh:
            raise ValueError(
                "Google не повернув refresh_token. Відкликайте доступ у "
                "myaccount.google.com/permissions і підключіть знову."
            )
        verified = save_refresh_token(refresh)
    except ValueError as exc:
        return RedirectResponse(
            url=f"{return_to}?integration_error={quote(str(exc))}",
            status_code=302,
        )
    except Exception as exc:
        logger.exception("Google Ads OAuth callback failed")
        return RedirectResponse(
            url=f"{return_to}?integration_error={quote(str(exc))}",
            status_code=302,
        )

    sep = "&" if "?" in return_to else "?"
    url = f"{return_to}{sep}integration_oauth_ok=1"
    if verified:
        url += f"&integration_verified={quote(verified)}"
    return RedirectResponse(url=url, status_code=302)


@router.post("/mkt-report/sync")
@router.post("/ad-analytics/sync")
@router.post("/advertising/sync")
async def advertising_sync_post(
    request: Request,
    user: str = Depends(require_admin),
    _csrf: None = Depends(verify_csrf_dependency),
):
    """Ручне оновлення кешу рекламної аналітики (Google Ads, Meta, GSC, GA4) + CRM."""
    from bot.scheduler.tasks import refresh_ad_cache

    referer = (request.headers.get("referer") or "/sync").split("?")[0]

    async def run_ad_cache_refresh() -> None:
        try:
            await refresh_ad_cache()
        except Exception as exc:
            logger.error("❌ Помилка синхронізації рекламної аналітики: %s", exc, exc_info=True)

    asyncio.create_task(run_ad_cache_refresh())
    return RedirectResponse(url=f"{referer}?ad_sync_started=1", status_code=302)


def _seo_expense_json_error(exc: Exception, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=status_code)


@router.get("/mkt-report/seo-expenses")
@router.get("/ad-analytics/seo-expenses")
@router.get("/advertising/seo-expenses")
async def seo_expenses_list(
    request: Request,
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    user: str = Depends(get_current_user),
):
    _require_advertising_access(request)
    rows = list_seo_expenses(date_from=date_from, date_to=date_to)
    total = round(sum(float(r.get("amount_uah") or 0) for r in rows), 2)
    return JSONResponse({"ok": True, "items": rows, "total": total})


@router.post("/mkt-report/seo-expenses")
@router.post("/ad-analytics/seo-expenses")
@router.post("/advertising/seo-expenses")
async def seo_expenses_create(
    request: Request,
    expense_date: str = Form(...),
    amount_uah: str = Form(...),
    description: str = Form(...),
    csrf_token: str = Form(None),
    user: str = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_dependency),
):
    _require_advertising_access(request)
    try:
        row = create_seo_expense(
            expense_date=expense_date,
            amount_uah=amount_uah,
            description=description,
        )
        return JSONResponse({"ok": True, "item": row})
    except SeoExpenseError as exc:
        return _seo_expense_json_error(exc)


@router.post("/mkt-report/seo-expenses/{expense_id}/update")
@router.post("/ad-analytics/seo-expenses/{expense_id}/update")
@router.post("/advertising/seo-expenses/{expense_id}/update")
async def seo_expenses_update(
    request: Request,
    expense_id: str,
    expense_date: str = Form(...),
    amount_uah: str = Form(...),
    description: str = Form(...),
    csrf_token: str = Form(None),
    user: str = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_dependency),
):
    _require_advertising_access(request)
    try:
        row = update_seo_expense(
            expense_id,
            expense_date=expense_date,
            amount_uah=amount_uah,
            description=description,
        )
        return JSONResponse({"ok": True, "item": row})
    except SeoExpenseError as exc:
        return _seo_expense_json_error(exc)


@router.post("/mkt-report/seo-expenses/{expense_id}/delete")
@router.post("/ad-analytics/seo-expenses/{expense_id}/delete")
@router.post("/advertising/seo-expenses/{expense_id}/delete")
async def seo_expenses_delete(
    request: Request,
    expense_id: str,
    csrf_token: str = Form(None),
    user: str = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_dependency),
):
    _require_advertising_access(request)
    try:
        delete_seo_expense(expense_id)
        return JSONResponse({"ok": True})
    except SeoExpenseError as exc:
        return _seo_expense_json_error(exc)
