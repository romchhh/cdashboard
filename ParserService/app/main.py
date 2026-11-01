"""Cosmy Parser Worker — лише скрейпінг, без бізнес-БД і кронів."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from . import config
from .bootstrap import setup
from .routes_v1 import router as v1_router

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("parser_worker")

# Інтернет-сканери шукають витоки секретів — не помилка сервісу.
_PROBE_PREFIXES = (
    "/.env",
    "/env",
    "/config",
    "/.git",
    "/wp-",
    "/phpmyadmin",
    "/actuator",
    "/vendor/",
    "/storage/",
)


class _ProbeFilterMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path or "/"
        low = path.lower()
        if any(low.startswith(p) or p in low for p in (".env", ".git", "phpmyadmin", "wp-login")):
            return Response(status_code=404, content="Not Found")
        if any(low.startswith(p) for p in _PROBE_PREFIXES):
            return Response(status_code=404, content="Not Found")
        return await call_next(request)


class _SkipProbeAccessLog(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        low = msg.lower()
        if ".env" in low or ".git" in low or "wp-" in low or "phpmyadmin" in low:
            return False
        return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    from . import bootstrap

    root = setup()
    os.environ.setdefault("PARSER_CATALOG_SQLITE_ONLY", "1")
    try:
        import web.parser_store as store

        store.DB_PATH = config.WORK_DIR / "ephemeral.db"
        store.JSON_DIR = config.WORK_DIR
        store._READY_DB_PATHS.clear()
    except Exception:
        logger.debug("web.parser_store недоступний — ок для чистого worker runtime")
    eng = bootstrap.engine_status()
    logger.info(
        "Parser worker root=%s work=%s engine_ready=%s",
        root,
        config.WORK_DIR,
        eng.get("ready"),
    )
    if not eng.get("ready"):
        logger.error("ENGINE NOT READY: %s", eng.get("error"))
    if not config.REQUIRE_API_KEY or not config.API_KEY:
        logger.warning("PARSER_API_KEY / REQUIRE_API_KEY слабкі — увімкніть ключ у проді")
    yield


_docs = None if config.REQUIRE_API_KEY else "/docs"
_redoc = None if config.REQUIRE_API_KEY else "/redoc"

app = FastAPI(
    title="Cosmy Parser Worker",
    description="Тільки парсинг. БД, UI і крони — на основному CosmyFinancesBot.",
    version="2.0.0",
    lifespan=lifespan,
    docs_url=_docs,
    redoc_url=_redoc,
    openapi_url=None if config.REQUIRE_API_KEY else "/openapi.json",
)
app.add_middleware(_ProbeFilterMiddleware)
app.include_router(v1_router)

logging.getLogger("uvicorn.access").addFilter(_SkipProbeAccessLog())


@app.get("/")
async def root():
    from . import bootstrap, config, jobs

    eng = bootstrap.engine_status()
    return {
        "service": "cosmy-parser-worker",
        "role": "scrape-only",
        "engine_ready": eng.get("ready"),
        "running_jobs": jobs.running_job_count(),
        "uptime_sec": round(jobs.uptime_sec(), 1),
        "rss_mb": jobs.process_rss_mb(),
        "limits": {
            "max_sku_workers": config.MAX_SKU_WORKERS,
            "max_site_workers": config.MAX_SITE_WORKERS,
            "max_running_jobs": config.MAX_RUNNING_JOBS,
        },
        "endpoints": [
            "GET /v1/health",
            "GET /v1/sites",
            "POST /v1/parse/search",
            "POST /v1/parse/catalog",
            "GET /v1/jobs/{job_id}",
        ],
    }
