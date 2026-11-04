"""API worker-а: лише парсинг. Стан/БД/крони — на основному сервері."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from . import bootstrap, config, jobs
from .auth import require_api_key

router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])


@router.get("/health")
async def health():
    """Легкий health — ніколи не чекає scrape (критично проти nginx 502)."""
    eng = bootstrap.engine_status()
    ok = bool(eng.get("ready"))
    body = {
        "ok": ok,
        "service": "cosmy-parser-worker",
        "role": "scrape-only",
        "engine": eng,
        "running_jobs": jobs.running_job_count(),
        "uptime_sec": round(jobs.uptime_sec(), 1),
        "rss_mb": jobs.process_rss_mb(),
        "limits": {
            "max_sku_workers": config.MAX_SKU_WORKERS,
            "max_site_workers": config.MAX_SITE_WORKERS,
            "max_running_jobs": config.MAX_RUNNING_JOBS,
            "cache_clear_every": config.CACHE_CLEAR_EVERY,
        },
    }
    if not ok:
        return JSONResponse(body, status_code=503)
    return body


@router.get("/sites")
async def sites():
    try:
        bootstrap.require_engine()
        return jobs.list_sites()
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)


@router.post("/parse/search")
async def parse_search(payload: dict[str, Any]):
    """
    Завжди фоновий job → 202 {job_id}.
    Sync scrape в HTTP вбивав worker (nginx 502 на 60–120с).
    """
    query = (payload.get("query") or "").strip()
    if not query:
        return JSONResponse({"error": "query обовʼязковий"}, status_code=400)
    try:
        bootstrap.require_engine()
        job_id = jobs.start_search_job(
            query=query,
            our_price=payload.get("our_price"),
            settings=payload.get("settings"),
        )
        return JSONResponse({"job_id": job_id, "state": "running", "kind": "search"}, status_code=202)
    except Exception as exc:
        msg = str(exc)
        busy = "вже виконується" in msg.lower() or "Зачекайте" in msg or "зайнятий" in msg
        return JSONResponse({"error": msg}, status_code=409 if busy else 500)


@router.post("/parse/catalog")
async def parse_catalog(payload: dict[str, Any]):
    """Старт фонового парсингу → 202. Основний сервер опитує /v1/jobs/{id}."""
    products = payload.get("products")
    if not isinstance(products, list) or not products:
        return JSONResponse({"error": "products: непорожній список обовʼязковий"}, status_code=400)
    try:
        bootstrap.require_engine()
        job_id = jobs.start_catalog_job(
            products=products,
            settings=payload.get("settings"),
            brand=(payload.get("brand") or None),
        )
        return JSONResponse({"job_id": job_id, "state": "running", "total": len(products)}, status_code=202)
    except Exception as exc:
        msg = str(exc)
        busy = "вже виконується" in msg or "Зачекайте" in msg or "зайнятий" in msg or "Занадто багато" in msg
        return JSONResponse({"error": msg}, status_code=409 if busy else 500)


@router.get("/jobs")
async def jobs_list():
    return {"jobs": jobs.list_jobs()}


@router.post("/jobs/{job_id}/cancel")
async def job_cancel(job_id: str):
    """Зупинити scrape. Job вийде на наступному оновленні прогресу."""
    if not jobs.cancel_job(job_id):
        return JSONResponse({"ok": False, "error": "job not running"}, status_code=404)
    return {"ok": True, "id": job_id}


@router.get("/jobs/{job_id}")
async def job_status(job_id: str, since_rows: int = 0, since_sites: int = 0):
    """Статус job. Catalog: rows[since_rows:]. Search: results/misses."""
    job = jobs.get_job(job_id)
    if not job:
        snap = jobs.load_job_snapshot(job_id)
        if snap and snap.get("state") == "running":
            # Процес перезапустився (OOM/Killed) — job у RAM зник.
            return JSONResponse(
                {
                    "id": job_id,
                    "kind": snap.get("kind") or "catalog",
                    "state": "error",
                    "phase": "error",
                    "brand": snap.get("brand"),
                    "query": snap.get("query"),
                    "current": snap.get("current"),
                    "total": snap.get("total"),
                    "retry_current": snap.get("retry_current"),
                    "retry_total": snap.get("retry_total"),
                    "product": snap.get("product"),
                    "error": (
                        "Worker перезапустився під час scrape (часто OOM «Killed»). "
                        "Запустіть моніторинг ще раз."
                    ),
                    "started_epoch": snap.get("started_epoch"),
                    "updated_epoch": snap.get("updated_epoch"),
                    "worker_restarted": True,
                },
                status_code=410,
            )
        return JSONResponse({"error": "job not found"}, status_code=404)

    kind = job.get("kind") or "catalog"
    out: dict[str, Any] = {
        "id": job["id"],
        "kind": kind,
        "state": job.get("state"),
        "phase": job.get("phase"),
        "brand": job.get("brand"),
        "query": job.get("query"),
        "current": job.get("current"),
        "total": job.get("total"),
        "retry_current": job.get("retry_current"),
        "retry_total": job.get("retry_total"),
        "product": job.get("product"),
        "error": job.get("error"),
        "started_epoch": job.get("started_epoch"),
        "updated_epoch": job.get("updated_epoch"),
    }

    if kind == "search":
        results = list(job.get("results") or [])
        misses = list(job.get("misses") or [])
        out["results"] = results
        out["misses"] = misses
        out["found"] = len(results)
        out["sites_done"] = len(results) + len(misses)
        out["since_sites"] = since_sites
        if job.get("state") == "done":
            out["report"] = job.get("report")
        return out

    rows = list(job.get("rows") or [])
    out["live_count"] = len(rows)
    out["rows"] = rows[max(0, int(since_rows)) :]
    out["rows_total"] = len(rows)
    if job.get("state") == "done":
        out["report"] = job.get("report")
    return out
