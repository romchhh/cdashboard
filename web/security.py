# Захист веб-апки: CSRF, обмеження запитів, заголовки безпеки

import hmac
import logging
import secrets
import time
from collections import defaultdict
from typing import Callable

from fastapi import Depends, Request, HTTPException, status, Form
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import JSONResponse, Response

from config import WEB_SECURE_COOKIE
from web.auth import get_current_user

logger = logging.getLogger(__name__)


# --- CSRF (constant-time compare) ---

def _constant_time_compare(a: str | None, b: str | None) -> bool:
    """Порівняння рядків у константному часі (захист від timing attacks)."""
    if a is None or b is None:
        return False
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def generate_csrf_token() -> str:
    """Згенерувати випадковий токен для CSRF."""
    return secrets.token_urlsafe(32)


def verify_csrf_token(expected: str | None, received: str | None) -> bool:
    """Перевірити CSRF токен (constant-time)."""
    return bool(expected and received and _constant_time_compare(expected, received))


# --- Обмеження спроб логіну (rate limit) ---

_LOGIN_FAILURES: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT_WINDOW = 60  # секунд
RATE_LIMIT_MAX_ATTEMPTS = 5


def _get_client_ip(request: Request | StarletteRequest) -> str:
    """Отримати IP клієнта (враховує X-Forwarded-For за проксі)."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "0.0.0.0"


def record_login_failure(ip: str) -> None:
    """Зафіксувати невдалу спробу логіну."""
    now = time.time()
    _LOGIN_FAILURES[ip].append(now)
    cutoff = now - RATE_LIMIT_WINDOW
    _LOGIN_FAILURES[ip] = [t for t in _LOGIN_FAILURES[ip] if t > cutoff]


def is_login_rate_limited(ip: str) -> bool:
    """Чи перевищено ліміт спроб логіну для цього IP."""
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW
    times = [t for t in _LOGIN_FAILURES[ip] if t > cutoff]
    _LOGIN_FAILURES[ip] = times
    return len(times) >= RATE_LIMIT_MAX_ATTEMPTS


def clear_login_failures(ip: str) -> None:
    """Очистити лічильник після успішного логіну."""
    _LOGIN_FAILURES.pop(ip, None)


# --- Загальний rate limit для публічних endpoint ---

_REQUEST_HITS: dict[str, list[float]] = defaultdict(list)

# (path_prefix, methods or None, max_hits, window_sec)
_RATE_LIMIT_RULES: tuple[tuple[str, frozenset[str] | None, int, int], ...] = (
    ("/login", frozenset({"POST"}), 15, 60),
    ("/api/mailings", frozenset({"POST"}), 120, 60),
    ("/unsubscribe", None, 40, 60),
    ("/index.php", None, 40, 60),
)


def is_request_rate_limited(request: StarletteRequest) -> bool:
    path = request.url.path
    method = request.method.upper()
    matched = None
    for prefix, methods, max_hits, window in _RATE_LIMIT_RULES:
        if not path.startswith(prefix):
            continue
        if methods is not None and method not in methods:
            continue
        matched = (prefix, max_hits, window)
        break
    if not matched:
        return False

    prefix, max_hits, window = matched
    ip = _get_client_ip(request)
    bucket = f"{ip}:{prefix}:{method}"
    now = time.time()
    cutoff = now - window
    hits = [t for t in _REQUEST_HITS[bucket] if t > cutoff]
    if len(hits) >= max_hits:
        _REQUEST_HITS[bucket] = hits
        return True
    hits.append(now)
    _REQUEST_HITS[bucket] = hits
    return False


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Обмеження частоти запитів на чутливі публічні endpoint."""

    async def dispatch(self, request: StarletteRequest, call_next):
        if is_request_rate_limited(request):
            logger.warning("Rate limit: %s %s from %s", request.method, request.url.path, _get_client_ip(request))
            if request.url.path.startswith("/api/"):
                return JSONResponse(
                    status_code=429,
                    content={"success": False, "error": "Too many requests. Try again later."},
                )
            return Response(status_code=429, content="Too many requests")
        return await call_next(request)


# --- Заголовки безпеки (middleware) ---

def _default_csp() -> str:
    return (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "style-src-elem 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "img-src 'self' data: https:; "
        "font-src 'self' data: https://fonts.gstatic.com; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )


def _mailings_csp() -> str:
    """Превʼю HTML-листів: шрифти/стилі з CDN (iframe srcdoc успадковує CSP сторінки)."""
    return (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https:; "
        "img-src 'self' data: https:; "
        "font-src 'self' data: https:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )


def content_security_policy_for_path(path: str) -> str:
    if (path or "").startswith("/clients/mailings"):
        return _mailings_csp()
    return _default_csp()


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-site",
    "Content-Security-Policy": _default_csp(),
}

if WEB_SECURE_COOKIE:
    SECURITY_HEADERS["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Додає HTTP-заголовки безпеки до всіх відповідей."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            if name == "Content-Security-Policy":
                value = content_security_policy_for_path(request.url.path)
            response.headers[name] = value
        return response


# Cookie для CSRF на сторінці логіну (double-submit)
CSRF_LOGIN_COOKIE = "csrf_login"


def cookie_secure_for_request(request: Request) -> bool:
    """
    Secure-cookie лише на реальному HTTPS.
    WEB_SECURE_COOKIE=true на http://localhost інакше ламає CSRF/логін (браузер не шле cookie).
    """
    from config import WEB_SECURE_COOKIE

    if not WEB_SECURE_COOKIE:
        return False
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if forwarded == "https":
        return True
    return request.url.scheme == "https"


# --- Залежність перевірки CSRF для захищених POST ---

def _csrf_token_from_request(request: Request, form_token: str | None = None) -> str | None:
    header = (request.headers.get("X-CSRF-Token") or "").strip()
    if header:
        return header
    return (form_token or "").strip() or None


def _require_csrf(request: Request, received: str | None) -> None:
    expected = getattr(request.state, "csrf_token", None)
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Відсутній CSRF-токен сесії",
        )
    if not verify_csrf_token(expected, received):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недійсний або застарілий запит. Оновіть сторінку та спробуйте знову.",
        )


async def verify_csrf_dependency(
    request: Request,
    csrf_token: str = Form(None),
    _user: str = Depends(get_current_user),
) -> None:
    """Залежність: перевіряє CSRF токен для POST-форм (після завантаження сесії)."""
    _require_csrf(request, _csrf_token_from_request(request, csrf_token))


async def verify_csrf_header_dependency(
    request: Request,
    _user: str = Depends(get_current_user),
) -> None:
    """CSRF для JSON API (токен у заголовку X-CSRF-Token)."""
    _require_csrf(request, _csrf_token_from_request(request, None))


def configure_multipart_upload_limits() -> None:
    """Starlette за замовчуванням обмежує кожне поле multipart 1 MB (не nginx)."""
    import os

    from starlette.formparsers import MultiPartParser

    try:
        mb = int(os.getenv("WEB_FORM_MAX_PART_MB", "64"))
    except ValueError:
        mb = 64
    mb = max(1, min(mb, 512))
    size = mb * 1024 * 1024
    MultiPartParser.max_part_size = size
    MultiPartParser.max_file_size = size
    logger.info("Multipart upload limit: %s MB per field/file", mb)
