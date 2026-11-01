# Авторизація веб-апки (логін/пароль та ролі: admin, manager)

import secrets
from itsdangerous import URLSafeTimedSerializer, BadSignature
from fastapi import Request, HTTPException, status, Depends
from fastapi.responses import RedirectResponse

from config import (
    WEB_USERNAME,
    WEB_PASSWORD,
    WEB_MANAGER_USERNAME,
    WEB_MANAGER_PASSWORD,
    WEB_SECRET_KEY,
)

COOKIE_NAME = "fin_session"
# Максимальний вік підписаного токена сесії (має збігатися з «Запамʼятати мене» у main.py).
# Раніше було 7 днів при cookie 30 днів — токен ставав недійсним, хоча браузер ще тримав cookie.
REMEMBER_ME_MAX_AGE_SECONDS = 30 * 86400
ROLE_ADMIN = "admin"
ROLE_MANAGER = "manager"

SCOPE_FINANCE = "finance"
SCOPE_MARKETING = "marketing"
SCOPE_WAREHOUSE = "warehouse"
SCOPE_CLIENTS_EXPORT = "clients_export"
SCOPE_ADVERTISING = "advertising"

# Експорт для main
__all__ = [
    "verify_login",
    "create_session_token",
    "get_current_user",
    "require_admin",
    "get_operator_display",
    "COOKIE_NAME",
    "ROLE_ADMIN",
    "ROLE_MANAGER",
    "REMEMBER_ME_MAX_AGE_SECONDS",
    "get_session_data",
    "normalize_access",
    "access_from_web_user",
    "default_home_path",
    "SCOPE_FINANCE",
    "SCOPE_MARKETING",
    "SCOPE_WAREHOUSE",
    "SCOPE_CLIENTS_EXPORT",
    "SCOPE_ADVERTISING",
]

_serializer = URLSafeTimedSerializer(WEB_SECRET_KEY, salt="fin-admin-session")


def verify_login(username: str, password: str) -> tuple[str | None, str | None]:
    """Перевірка логіну/пароля з конфігу (fallback). Повертає (username, role) або (None, None)."""
    if username == WEB_USERNAME and password == WEB_PASSWORD:
        return username, ROLE_ADMIN
    if username == WEB_MANAGER_USERNAME and password == WEB_MANAGER_PASSWORD:
        return username, ROLE_MANAGER
    return None, None


def _full_access() -> dict[str, bool]:
    return {
        SCOPE_FINANCE: True,
        SCOPE_MARKETING: True,
        SCOPE_WAREHOUSE: True,
        SCOPE_CLIENTS_EXPORT: True,
        SCOPE_ADVERTISING: True,
    }


def normalize_access(raw: dict | None, role: str) -> dict[str, bool]:
    """Єдиний формат scope у сесії; для admin завжди повний доступ."""
    if role == ROLE_ADMIN:
        return _full_access()
    if not raw:
        # Старі токени без поля access — як раніше: лише фінанси
        return {
            SCOPE_FINANCE: True,
            SCOPE_MARKETING: False,
            SCOPE_WAREHOUSE: False,
            SCOPE_CLIENTS_EXPORT: False,
            SCOPE_ADVERTISING: False,
        }
    return {
        SCOPE_FINANCE: bool(raw.get(SCOPE_FINANCE)),
        SCOPE_MARKETING: bool(raw.get(SCOPE_MARKETING)),
        SCOPE_WAREHOUSE: bool(raw.get(SCOPE_WAREHOUSE)),
        SCOPE_CLIENTS_EXPORT: bool(raw.get(SCOPE_CLIENTS_EXPORT)),
        SCOPE_ADVERTISING: bool(raw.get(SCOPE_ADVERTISING)),
    }


def access_from_web_user(web_user) -> dict[str, bool]:
    """Області доступу з рядка WebUser (для сесії після логіну)."""
    if getattr(web_user, "role", ROLE_MANAGER) == ROLE_ADMIN:
        return _full_access()
    return normalize_access(
        {
            SCOPE_FINANCE: bool(getattr(web_user, "access_finance", True)),
            SCOPE_MARKETING: bool(getattr(web_user, "access_marketing", False)),
            SCOPE_WAREHOUSE: bool(getattr(web_user, "access_warehouse", False)),
            SCOPE_CLIENTS_EXPORT: bool(getattr(web_user, "access_clients_export", False)),
            SCOPE_ADVERTISING: bool(getattr(web_user, "access_advertising", False)),
        },
        ROLE_MANAGER,
    )


def default_home_path(access: dict[str, bool], role: str) -> str:
    """Перша доступна головна сторінка після входу."""
    if role == ROLE_ADMIN:
        return "/dashboard"
    if access.get(SCOPE_FINANCE):
        return "/dashboard"
    if access.get(SCOPE_MARKETING):
        return "/clients"
    if access.get(SCOPE_ADVERTISING):
        return "/clients/marketing-report"
    if access.get(SCOPE_WAREHOUSE):
        return "/procurement/dashboard"
    return "/dashboard"


def create_session_token(
    username: str,
    role: str,
    display_name: str | None = None,
    access: dict[str, bool] | None = None,
) -> str:
    """Створити підписаний токен сесії з роллю, CSRF, областями доступу та ім'ям оператора."""
    payload = {
        "username": username,
        "role": role,
        "csrf": secrets.token_urlsafe(32),
        "access": normalize_access(access, role),
    }
    if display_name:
        payload["display_name"] = display_name
    return _serializer.dumps(payload, salt="fin-admin-session")


def get_session_data(token: str) -> dict | None:
    """Отримати дані сесії з токена; None якщо невалідний."""
    try:
        data = _serializer.loads(
            token,
            salt="fin-admin-session",
            max_age=REMEMBER_ME_MAX_AGE_SECONDS,
        )
        return data
    except BadSignature:
        return None


def get_username_from_token(token: str) -> str | None:
    """Отримати username з токена; None якщо невалідний."""
    data = get_session_data(token)
    return data.get("username") if data else None


def get_current_user(request: Request) -> str:
    """Отримати поточного користувача з cookie; встановити request.state.role та request.state.display_name; 401 якщо не залогінений."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Не авторизовано",
            headers={"Location": "/login"},
        )
    data = get_session_data(token)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Сесія закінчилась",
            headers={"Location": "/login"},
        )
    username = data.get("username")
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Не авторизовано",
            headers={"Location": "/login"},
        )
    request.state.role = data.get("role", ROLE_ADMIN)
    request.state.csrf_token = data.get("csrf", "")
    request.state.display_name = data.get("display_name") or (
        f"Адмін ({username})" if request.state.role == ROLE_ADMIN else f"Менеджер ({username})"
    )
    access = normalize_access(data.get("access"), request.state.role)
    request.state.access = access
    request.state.access_finance = access.get(SCOPE_FINANCE, False)
    request.state.access_marketing = access.get(SCOPE_MARKETING, False)
    request.state.access_warehouse = access.get(SCOPE_WAREHOUSE, False)
    request.state.access_clients_export = access.get(SCOPE_CLIENTS_EXPORT, False) or request.state.role == ROLE_ADMIN
    request.state.access_advertising = access.get(SCOPE_ADVERTISING, False) or request.state.role == ROLE_ADMIN
    return username


def get_operator_display(request: Request) -> str:
    """Повертає рядок для відображення оператора (хто зробив дію): ім'я менеджера або «Адмін (логін)»."""
    return getattr(request.state, "display_name", "") or getattr(request.state, "username", "—")


def require_admin(request: Request, user: str = Depends(get_current_user)):
    """Залежність: доступ тільки для ролі admin. Інакше 403."""
    if getattr(request.state, "role", ROLE_ADMIN) != ROLE_ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Доступ заборонено")
    return user


def redirect_to_login():
    """Редірект на сторінку логіну."""
    return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
