"""Веб-розділ парсера цін конкурентів (інтеграція cosmyparser)."""

from __future__ import annotations

import asyncio
from urllib.parse import quote, unquote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from web.auth import get_current_user
from web.deps import templates, verify_csrf_dependency
from web.security import verify_csrf_header_dependency
from web import parser_service as ps
from web.parser_worker_client import worker_enabled

router = APIRouter(
    prefix="/parser",
    tags=["parser"],
    dependencies=[Depends(get_current_user)],
)

def _ctx(request: Request, **extra):
    try:
        sites = ps.site_names()
        site_meta = ps.site_meta()
        settings = ps.load_settings()
        report_status = ps.report_status()
        brands = ps.list_brands()
        catalog_count = len(ps.load_catalog())
        parsed_sum = sum(int(b.get("parsed_count") or 0) for b in brands)
        log_error_count = sum(1 for item in ps.load_parse_log(120) if item.get("level") == "error")
        rotation = ps.rotation_overview()
        load_error = None
    except Exception as exc:  # noqa: BLE001
        sites, site_meta, brands = [], [], []
        settings = {}
        report_status = {"state": "error", "error": str(exc), "has_file": False}
        catalog_count = 0
        parsed_sum = 0
        log_error_count = 0
        rotation = {}
        load_error = str(exc)

    base = {
        "request": request,
        "sites": sites,
        "site_meta": site_meta,
        "settings": settings,
        "report_status": report_status,
        "brands": brands,
        "format_parsed_at": ps.format_parsed_at,
        "catalog_count": catalog_count,
        "parsed_sum": parsed_sum,
        "log_error_count": log_error_count,
        "rotation": rotation,
        "load_error": load_error,
        "parser_worker": worker_enabled(),
    }
    base.update(extra)
    return base


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def parser_home(request: Request):
    """Головна парсера = список брендів."""
    return RedirectResponse(url="/parser/brands", status_code=302)


@router.get("/brands", response_class=HTMLResponse)
async def parser_brands(request: Request):
    return templates.TemplateResponse(
        "parser/brands.html",
        _ctx(
            request,
            page="brands",
            notice=request.query_params.get("notice"),
            error=request.query_params.get("error"),
        ),
    )


@router.get("/search", response_class=HTMLResponse)
async def parser_search_page(request: Request):
    history = [ps.history_preview(i) for i in ps.load_history()]
    return templates.TemplateResponse(
        "parser/search.html",
        _ctx(request, page="search", history=history),
    )


@router.get("/logs", response_class=HTMLResponse)
async def parser_logs_page(request: Request):
    level = (request.query_params.get("level") or "all").strip()
    if level not in ("all", "error", "schedule"):
        level = "all"
    events = ps.load_parse_log(250)
    if level == "error":
        events = [item for item in events if item.get("level") == "error"]
    elif level == "schedule":
        events = [item for item in events if item.get("kind") == "schedule"]
    return templates.TemplateResponse(
        "parser/logs.html",
        _ctx(
            request,
            page="logs",
            events=events,
            level=level,
            rotation=ps.rotation_overview(),
            brand_slug=ps.brand_slug,
            notice=request.query_params.get("notice"),
        ),
    )


@router.post("/logs/clear")
async def parser_logs_clear(_csrf: None = Depends(verify_csrf_dependency)):
    ps.clear_parse_log()
    return RedirectResponse(url="/parser/logs?notice=cleared", status_code=302)


@router.get("/settings", response_class=HTMLResponse)
async def parser_settings_page(request: Request):
    return templates.TemplateResponse(
        "parser/settings.html",
        _ctx(
            request,
            page="settings",
            notice=request.query_params.get("notice"),
        ),
    )


@router.get("/brands/{brand_slug}", response_class=HTMLResponse)
async def parser_brand_detail(request: Request, brand_slug: str):
    brands = ps.list_brands()
    brand = ps.brand_from_slug(brand_slug, [b["name"] for b in brands])
    if not brand:
        return RedirectResponse(url="/parser/brands?error=not_found", status_code=302)
    report = ps.load_brand_report(brand)
    brand_info = next((b for b in brands if b["name"] == brand), None)
    rows = ps.merge_brand_rows(brand, report)
    current_sites = ps.site_names()
    # Лише актуальні магазини: старі (Kasta/Prom/…) у збереженому звіті більше не показуємо.
    shop_cols = list(current_sites)
    price_stats = ps.compute_price_stats(rows)
    rotation = ps.rotation_overview(brand=brand)
    return templates.TemplateResponse(
        "parser/brand_detail.html",
        _ctx(
            request,
            page="brand",
            brand=brand,
            brand_info=brand_info,
            report=report,
            rows=rows,
            rows_json=[ps.slim_row(r) for r in rows],
            shop_cols=shop_cols,
            price_stats=price_stats,
            notice=request.query_params.get("notice"),
            error=request.query_params.get("error"),
            report_status=ps.report_status_for_brand(brand),
            rotation=rotation,
        ),
    )


@router.post("/settings")
async def parser_save_settings(
    _csrf: None = Depends(verify_csrf_dependency),
    sku_workers: int = Form(4),
    price_low_ratio: float = Form(0.45),
    price_high_ratio: float = Form(2.2),
    at_market_pct: float = Form(3.0),
    suspicious_gap_pct: float = Form(35.0),
    min_similarity: float = Form(0.58),
    enabled_sites: list[str] = Form(default=[]),
):
    ps.save_settings(
        {
            "sku_workers": sku_workers,
            "price_low_ratio": price_low_ratio,
            "price_high_ratio": price_high_ratio,
            "at_market_pct": at_market_pct,
            "suspicious_gap_pct": suspicious_gap_pct,
            "min_similarity": min_similarity,
            "enabled_sites": list(enabled_sites or []),
        }
    )
    return RedirectResponse(url="/parser/settings?notice=settings_saved", status_code=302)


@router.post("/search")
async def parser_search_api(
    request: Request,
    _csrf: None = Depends(verify_csrf_header_dependency),
):
    payload = {}
    ctype = (request.headers.get("content-type") or "").lower()
    if "application/json" in ctype:
        payload = await request.json()
    else:
        form = await request.form()
        payload = {"query": form.get("query"), "our_price": form.get("our_price")}
    query = (payload.get("query") or "").strip()
    if not query:
        return JSONResponse({"error": "Введіть назву товару"}, status_code=400)
    status, code = ps.start_search(query, payload.get("our_price"))
    return JSONResponse(status, status_code=code)


@router.get("/api/search/status")
async def parser_search_status():
    return JSONResponse(ps.search_status())


@router.get("/api/history")
async def parser_history_api():
    items = [ps.history_preview(i) for i in ps.load_history()]
    return JSONResponse({"items": items})


@router.get("/api/history/{item_id}")
async def parser_history_item(item_id: str):
    item = ps.get_history_item(item_id)
    if not item:
        return JSONResponse({"error": "Запис не знайдено"}, status_code=404)
    return JSONResponse(item)


@router.post("/api/history/clear")
async def parser_history_clear(_csrf: None = Depends(verify_csrf_dependency)):
    ps.save_history([])
    return RedirectResponse(url="/parser/search", status_code=302)


@router.get("/api/report/status")
async def parser_report_status():
    return JSONResponse(ps.report_status())


@router.get("/api/brands/{brand_slug}/live")
async def parser_brand_live(brand_slug: str, known: str = ""):
    brand = ps.brand_from_slug(unquote(brand_slug))
    if not brand:
        return JSONResponse({"error": "not_found"}, status_code=404)
    known_ids = {x for x in (known or "").split(",") if x}
    return JSONResponse(ps.live_brand_snapshot(brand, known_ids))


@router.post("/api/report/run")
async def parser_report_run(
    _csrf: None = Depends(verify_csrf_dependency),
    brand: str | None = Form(None),
):
    brand_name = (brand or "").strip() or None
    status, code = await asyncio.to_thread(ps.start_report, brand=brand_name)
    return JSONResponse(status, status_code=code)


@router.post("/brands/{brand_slug}/run")
async def parser_brand_run(
    brand_slug: str,
    _csrf: None = Depends(verify_csrf_dependency),
):
    brand = ps.brand_from_slug(unquote(brand_slug))
    if not brand:
        return RedirectResponse(url="/parser/brands?error=not_found", status_code=302)
    status, code = await asyncio.to_thread(ps.start_report, brand=brand)
    if code == 409 and (status.get("brand") or "").strip().lower() == (brand or "").strip().lower():
        return RedirectResponse(
            url=f"/parser/brands/{ps.brand_slug(brand)}?notice=started",
            status_code=302,
        )
    if code >= 400:
        raw = status.get("error") or ""
        err = "disabled" if "вимкнено" in raw else quote(raw or "Не вдалося запустити парсинг", safe="")
        return RedirectResponse(
            url=f"/parser/brands/{ps.brand_slug(brand)}?error={err}",
            status_code=302,
        )
    return RedirectResponse(
        url=f"/parser/brands/{ps.brand_slug(brand)}?notice=started",
        status_code=302,
    )


@router.post("/brands/{brand_slug}/stop")
async def parser_brand_stop(
    brand_slug: str,
    _csrf: None = Depends(verify_csrf_dependency),
):
    brand = ps.brand_from_slug(unquote(brand_slug))
    if not brand:
        return RedirectResponse(url="/parser/brands?error=not_found", status_code=302)
    status, code = await asyncio.to_thread(ps.stop_report, brand=brand)
    if code >= 400:
        err = quote(status.get("error") or "Не вдалося зупинити парсер", safe="")
        return RedirectResponse(
            url=f"/parser/brands/{ps.brand_slug(brand)}?error={err}",
            status_code=302,
        )
    return RedirectResponse(
        url=f"/parser/brands/{ps.brand_slug(brand)}?notice=stopped",
        status_code=302,
    )


@router.post("/brands/{brand_slug}/toggle")
async def parser_brand_toggle(
    brand_slug: str,
    request: Request,
    _csrf: None = Depends(verify_csrf_dependency),
    enabled: str | None = Form(None),
):
    brand = ps.brand_from_slug(unquote(brand_slug))
    if not brand:
        accept = request.headers.get("accept", "")
        if "application/json" in accept:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return RedirectResponse(url="/parser/brands?error=not_found", status_code=302)
    want_enabled = str(enabled or "").strip().lower() in ("1", "true", "on", "yes")
    result = ps.set_brand_enabled(brand, want_enabled)
    accept = request.headers.get("accept", "")
    if "application/json" in accept or request.headers.get("x-requested-with") == "fetch":
        return JSONResponse(result)
    # з картки бренду — назад на ту ж сторінку
    referer = request.headers.get("referer") or ""
    if f"/parser/brands/{ps.brand_slug(brand)}" in referer:
        return RedirectResponse(
            url=f"/parser/brands/{ps.brand_slug(brand)}",
            status_code=302,
        )
    return RedirectResponse(url="/parser/brands", status_code=302)


@router.get("/api/report/download")
async def parser_report_download(brand: str | None = None):
    from fastapi.responses import Response

    data, name = await asyncio.to_thread(ps.get_report_excel, brand)
    if data is None:
        return JSONResponse({"error": name or "Звіт ще не згенеровано"}, status_code=404)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
