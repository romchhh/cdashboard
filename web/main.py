# FastAPI веб-апка — той самий функціонал, що й Telegram-бот

from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler as default_http_exception_handler
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

from config import WEB_ALLOWED_HOSTS, check_web_secret_key
from web.auth import (
    COOKIE_NAME,
    ROLE_ADMIN,
    SCOPE_ADVERTISING,
    SCOPE_FINANCE,
    SCOPE_MARKETING,
    SCOPE_WAREHOUSE,
    get_session_data,
    normalize_access,
)
from web.deps import BASE_DIR
from web.security import RateLimitMiddleware, SecurityHeadersMiddleware

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ініціалізація БД та запуск планувальника cron-задач разом з веб-апкою."""
    from bot.database.engine import init_db
    from bot.database.clients_engine import init_clients_db
    from bot.database.products_engine import init_products_db

    logger.info("=" * 60)
    logger.info("Ініціалізація бази даних...")
    await init_db()
    logger.info("✅ Структура БД створена/перевірена")
    try:
        await init_clients_db()
        logger.info("✅ Структура БД клієнтів Cosmy (окремий файл) створена/перевірена")
    except Exception as e:
        logger.error(f"❌ Помилка ініціалізації БД клієнтів Cosmy: {e}", exc_info=True)
    try:
        await init_products_db()
        logger.info("✅ Структура БД продуктів Cosmy створена/перевірена")
    except Exception as e:
        logger.error(f"❌ Помилка ініціалізації БД продуктів Cosmy: {e}", exc_info=True)
    logger.info("=" * 60)
    
    check_web_secret_key()

    from web.security import configure_multipart_upload_limits

    configure_multipart_upload_limits()

    from config import INTEGRATION_SETTINGS_UI_ENABLED, MARKETING_CONFIG_PATH, refresh_marketing_config

    marketing = refresh_marketing_config()
    if marketing:
        logger.info(
            "Маркетингові ключі з %s (%d полів, UI налаштувань: %s)",
            MARKETING_CONFIG_PATH,
            len(marketing),
            "увімкнено" if INTEGRATION_SETTINGS_UI_ENABLED else "приховано",
        )
    else:
        logger.warning("marketing.json порожній або відсутній: %s", MARKETING_CONFIG_PATH)

    from os import getenv

    # Планувальник лише тут і лише в одному процесі.
    # Другий uvicorn-воркер має стартувати з ENABLE_SCHEDULER=0, інакше нічні задачі підуть двічі.
    from bot.scheduler.scheduler import setup_scheduler

    scheduler = None
    if getenv("ENABLE_SCHEDULER", "1").strip().lower() not in ("0", "false", "no", "off"):
        scheduler = setup_scheduler()
        app.state.scheduler = scheduler
        logger.info("Планувальник увімкнено (ENABLE_SCHEDULER=1). Один процес веб-апки; бот планувальник не піднімає.")
    else:
        app.state.scheduler = None
        logger.warning(
            "Планувальник вимкнено (ENABLE_SCHEDULER). Автосинк Mono/NovaPay не працює."
        )

    yield

    if scheduler is not None:
        scheduler.shutdown()


# Локальна розробка завжди дозволена, навіть якщо WEB_ALLOWED_HOSTS = прод-домен.
_LOCAL_DEV_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})


class AllowedHostsMiddleware(BaseHTTPMiddleware):
    """Якщо задано WEB_ALLOWED_HOSTS — приймаємо запити тільки з цих Host (+ localhost)."""

    async def dispatch(self, request: StarletteRequest, call_next):
        if not WEB_ALLOWED_HOSTS:
            return await call_next(request)
        host_header = request.headers.get("host") or ""
        host = host_header.split(":")[0].strip().lower()
        if host in _LOCAL_DEV_HOSTS or host in WEB_ALLOWED_HOSTS:
            return await call_next(request)
        return Response(status_code=404, content="Not Found")


def _scopes_required_for_path(path: str) -> list[str] | None:
    if path.startswith("/managers"):
        return None
    if path.startswith("/fops"):
        return None
    if path.startswith("/sync"):
        return None
    if path.startswith("/reports"):
        return None
    if path.startswith("/procurement") or path.startswith("/products") or path.startswith("/parser"):
        return [SCOPE_WAREHOUSE]
    # Аналіз реклами — окремий доступ «Реклама» (не через маркетинг)
    if (
        path.startswith("/clients/marketing-report")
        or path.startswith("/clients/ad-analytics")
        or path.startswith("/clients/advertising")
    ):
        return [SCOPE_ADVERTISING]
    if path.startswith("/mkt-report/") or path.startswith("/ad-analytics/") or path.startswith("/advertising/"):
        return [SCOPE_ADVERTISING]
    if path.startswith("/clients"):
        return [SCOPE_MARKETING]
    if path in ("/", ""):
        return [SCOPE_FINANCE, SCOPE_MARKETING, SCOPE_WAREHOUSE, SCOPE_ADVERTISING]
    if (
        path.startswith("/dashboard")
        or path.startswith("/transactions")
        or path.startswith("/cash")
        or path.startswith("/manual-expense")
        or path.startswith("/categories")
        or path.startswith("/pending")
        or path.startswith("/statistics")
        or path.startswith("/reports")
    ):
        return [SCOPE_FINANCE]
    return None


PUBLIC_PATHS = frozenset({
    "/login",
    "/logout",
    "/legal",
    "/privacy",
    "/terms",
    "/unsubscribe",
    "/index.php",
    "/advertising/oauth/google-ads/callback",
})


class ScopeAccessMiddleware(BaseHTTPMiddleware):
    """Обмеження URL за областями доступу (токен сесії); admin проходить завжди."""

    async def dispatch(self, request: StarletteRequest, call_next):
        path = request.url.path
        if path.startswith("/static") or path in PUBLIC_PATHS:
            return await call_next(request)
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            return await call_next(request)
        data = get_session_data(token)
        if not data:
            return await call_next(request)
        role = data.get("role", ROLE_ADMIN)
        if role == ROLE_ADMIN:
            return await call_next(request)
        access = normalize_access(data.get("access"), role)
        needed = _scopes_required_for_path(path)
        if needed is None:
            return await call_next(request)
        if any(access.get(s) for s in needed):
            return await call_next(request)
        return HTMLResponse(
            status_code=403,
            content=(
                '<!DOCTYPE html><html lang="uk"><head><meta charset="UTF-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
                '<title>Немає доступу</title><link rel="stylesheet" href="/static/style.css"></head>'
                '<body class="login-page"><div class="login-box"><h1>Доступ заборонено</h1>'
                "<p class=\"login-sub\">Цей розділ не входить до ваших прав доступу. "
                "Зверніться до адміністратора або оберіть інший розділ у меню (якщо доступний).</p>"
                '<p><a href="/logout">Вийти</a></p></div></body></html>'
            ),
            media_type="text/html; charset=utf-8",
        )


app = FastAPI(title="Cosmy Agent — Web", lifespan=lifespan)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(AllowedHostsMiddleware)
app.add_middleware(ScopeAccessMiddleware)
app.add_middleware(RateLimitMiddleware)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 401:
        return RedirectResponse(url="/login", status_code=302)
    return await default_http_exception_handler(request, exc)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error(
        "Unhandled error on %s %s: %s",
        request.method,
        request.url.path,
        exc,
        exc_info=True,
    )
    if request.url.path == "/login":
        from web.routes.auth import _login_page_response

        return _login_page_response(
            request,
            error="Тимчасова помилка сервера. Спробуйте оновити сторінку.",
        )
    accept = request.headers.get("accept", "")
    if "text/html" in accept and not request.url.path.startswith("/api"):
        return HTMLResponse(
            status_code=500,
            content=(
                '<!DOCTYPE html><html lang="uk"><head><meta charset="UTF-8">'
                '<title>Помилка</title><link rel="stylesheet" href="/static/style.css"></head>'
                '<body class="login-page"><div class="login-box"><h1>Помилка сервера</h1>'
                '<p class="login-sub">Спробуйте оновити сторінку або увійти знову.</p>'
                '<p><a href="/login">На сторінку входу</a></p></div></body></html>'
            ),
            media_type="text/html; charset=utf-8",
        )
    return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})


from web.routes.auth import router as auth_router
from web.routes.managers import router as managers_router
from web.routes.fops import router as fops_router
from web.routes.finance import router as finance_router
from web.routes.clients import router as clients_router
from web.routes.emails import router as emails_router
from web.routes.products import router as products_router
from web.routes.parser import router as parser_router
from web.routes.advertising import router as advertising_router
from web.routes.legal import router as legal_router

app.include_router(auth_router)
app.include_router(legal_router)
app.include_router(managers_router)
app.include_router(fops_router)
app.include_router(finance_router)
app.include_router(emails_router)
app.include_router(advertising_router)
app.include_router(clients_router)
app.include_router(products_router)
app.include_router(parser_router)
