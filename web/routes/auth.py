"""Логін і вихід з веб-панелі."""

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from bot.database.engine import AsyncSessionLocal
from bot.database.models import WebUser
from web.auth import (
    COOKIE_NAME,
    REMEMBER_ME_MAX_AGE_SECONDS,
    ROLE_ADMIN,
    ROLE_MANAGER,
    access_from_web_user,
    create_session_token,
    default_home_path,
    normalize_access,
    verify_login,
)
from web.deps import templates
from web.passwords import bcrypt_verify
from web.security import (
    CSRF_LOGIN_COOKIE,
    _get_client_ip,
    clear_login_failures,
    cookie_secure_for_request,
    generate_csrf_token,
    is_login_rate_limited,
    record_login_failure,
    verify_csrf_token,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

_LOGIN_CSRF_MAX_AGE = 3600


def _login_page_response(request: Request, *, error: str | None = None) -> HTMLResponse:
    """Сторінка логіну з новим CSRF (cookie + hidden field) — завжди разом."""
    csrf_token = generate_csrf_token()
    response = templates.TemplateResponse(
        "login.html",
        {"request": request, "csrf_token": csrf_token, "error": error},
    )
    response.set_cookie(
        CSRF_LOGIN_COOKIE,
        csrf_token,
        path="/",
        max_age=_LOGIN_CSRF_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=cookie_secure_for_request(request),
    )
    return response


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    try:
        return _login_page_response(request)
    except Exception:
        logger.exception("GET /login failed")
        return _login_page_response(
            request,
            error="Тимчасова помилка. Спробуйте оновити сторінку.",
        )


@router.post("/login")
async def login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(None),
    remember_me: str = Form(""),
):
    ip = _get_client_ip(request)
    cookie_csrf = request.cookies.get(CSRF_LOGIN_COOKIE)
    if not verify_csrf_token(cookie_csrf, csrf_token):
        logger.warning("Login CSRF mismatch from %s (cookie=%s)", ip, bool(cookie_csrf))
        return _login_page_response(
            request,
            error="Сесія форми застаріла. Оновіть сторінку та спробуйте знову.",
        )
    if is_login_rate_limited(ip):
        return _login_page_response(
            request,
            error="Забагато невдалих спроб. Спробуйте через хвилину.",
        )
    display_name = None
    logged_username = None
    role = None
    access: dict[str, bool] | None = None
    db_unavailable = False

    try:
        async with AsyncSessionLocal() as session:
            res = await session.execute(
                select(WebUser).where(
                    WebUser.username == username.strip(),
                    WebUser.is_active == True,
                )
            )
            web_user = res.scalar_one_or_none()
            if web_user and bcrypt_verify(password, web_user.password_hash):
                logged_username = web_user.username
                role = web_user.role
                display_name = web_user.display_name or (
                    f"Адмін ({web_user.username})"
                    if web_user.role == ROLE_ADMIN
                    else f"Менеджер ({web_user.username})"
                )
                access = access_from_web_user(web_user)
                if role == ROLE_MANAGER and not any(access.values()):
                    record_login_failure(ip)
                    return _login_page_response(
                        request,
                        error="Для цього користувача не налаштовано жодної області доступу. Зверніться до адміністратора.",
                    )
    except Exception:
        logger.exception("Login DB error from %s", ip)
        db_unavailable = True

    if not logged_username or not role:
        logged_username, role = verify_login(username, password)
        if logged_username and role:
            display_name = (
                f"Адмін ({logged_username})"
                if role == ROLE_ADMIN
                else f"Менеджер ({logged_username})"
            )
            access = normalize_access(None, role)

    if db_unavailable and not logged_username:
        record_login_failure(ip)
        return _login_page_response(
            request,
            error="Тимчасова помилка сервера. Спробуйте ще раз через кілька секунд.",
        )

    if not logged_username or not role or access is None:
        record_login_failure(ip)
        return _login_page_response(request, error="Невірний логін або пароль")
    clear_login_failures(ip)
    token = create_session_token(logged_username, role, display_name, access)
    redirect_url = default_home_path(access, role)
    response = RedirectResponse(url=redirect_url, status_code=302)
    remember = bool(
        remember_me and str(remember_me).strip().lower() in ("1", "on", "true", "yes")
    )
    cookie_max_age = REMEMBER_ME_MAX_AGE_SECONDS if remember else None
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        path="/",
        httponly=True,
        max_age=cookie_max_age,
        samesite="lax",
        secure=cookie_secure_for_request(request),
    )
    response.delete_cookie(CSRF_LOGIN_COOKIE, path="/")
    return response


@router.get("/logout")
async def logout(request: Request):
    response = RedirectResponse(url="/login", status_code=302)
    secure = cookie_secure_for_request(request)
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        secure=secure,
        samesite="lax",
    )
    response.delete_cookie(CSRF_LOGIN_COOKIE, path="/", secure=secure, samesite="lax")
    return response
