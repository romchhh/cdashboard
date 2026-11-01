"""OAuth 2.0 для отримання GOOGLE_ADS_REFRESH_TOKEN через веб-налаштування."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from itsdangerous import BadSignature, URLSafeTimedSerializer

from config import (
    GOOGLE_ADS_CLIENT_ID,
    GOOGLE_ADS_CLIENT_SECRET,
    GOOGLE_ADS_OAUTH_REDIRECT_URI,
    WEB_PUBLIC_BASE_URL,
    WEB_SECRET_KEY,
)

logger = logging.getLogger(__name__)

GOOGLE_ADS_OAUTH_SCOPE = "https://www.googleapis.com/auth/adwords"
OAUTH_STATE_MAX_AGE_SECONDS = 600
_oauth_state = URLSafeTimedSerializer(WEB_SECRET_KEY, salt="google-ads-oauth-state")


def _host_from_url(url: str) -> str:
    return urllib.parse.urlparse(url).netloc.lower()


def public_base_from_request(request: Any) -> str | None:
    """Публічний URL панелі з заголовків проксі (ngrok, nginx)."""
    if request is None:
        return None
    proto = (request.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip()
    host = (request.headers.get("X-Forwarded-Host") or "").split(",")[0].strip()
    if not host:
        host = (request.headers.get("Host") or "").split(",")[0].strip()
    if not host:
        return str(request.base_url).rstrip("/")
    scheme = proto or getattr(getattr(request, "url", None), "scheme", None) or "https"
    return f"{scheme}://{host}".rstrip("/")


def public_base_url(request_base: str, request: Any = None) -> str:
    """Базовий URL для OAuth — узгоджений з тим, що бачить браузер."""
    request_public = public_base_from_request(request)
    env_base = WEB_PUBLIC_BASE_URL.rstrip("/") if WEB_PUBLIC_BASE_URL else ""
    explicit_base = (request_base or "").rstrip("/")

    if explicit_base and env_base and _host_from_url(explicit_base) != _host_from_url(env_base):
        return explicit_base

    if env_base and request_public:
        if _host_from_url(env_base) != _host_from_url(request_public):
            logger.info(
                "OAuth: WEB_PUBLIC_BASE_URL (%s) != поточний хост (%s), використовуємо запит",
                env_base,
                request_public,
            )
            return request_public
        return env_base

    if env_base:
        return env_base
    if request_public:
        return request_public
    return explicit_base


def oauth_redirect_uri(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/advertising/oauth/google-ads/callback"


def resolve_oauth_redirect_uri(request: Any = None, request_base: str = "") -> str:
    """Redirect URI, який треба додати в Google Cloud Console (Authorized redirect URIs)."""
    if GOOGLE_ADS_OAUTH_REDIRECT_URI:
        return GOOGLE_ADS_OAUTH_REDIRECT_URI.strip()
    base = public_base_url(request_base, request=request)
    return oauth_redirect_uri(base)


def oauth_prerequisites_ok() -> bool:
    return bool(GOOGLE_ADS_CLIENT_ID and GOOGLE_ADS_CLIENT_SECRET)


def create_oauth_state(*, return_to: str, redirect_uri: str, username: str) -> str:
    return _oauth_state.dumps(
        {
            "return_to": return_to,
            "redirect_uri": redirect_uri,
            "username": username,
        }
    )


def load_oauth_state(state: str) -> dict[str, Any] | None:
    try:
        data = _oauth_state.loads(state, max_age=OAUTH_STATE_MAX_AGE_SECONDS)
        return data if isinstance(data, dict) else None
    except BadSignature:
        return None


def build_authorize_url(*, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": GOOGLE_ADS_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": GOOGLE_ADS_OAUTH_SCOPE,
        "response_type": "code",
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)


def exchange_code_for_tokens(code: str, redirect_uri: str) -> dict[str, Any]:
    body = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": GOOGLE_ADS_CLIENT_ID,
            "client_secret": GOOGLE_ADS_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
            desc = payload.get("error_description") or payload.get("error") or raw
        except json.JSONDecodeError:
            desc = raw or str(exc)
        raise ValueError(f"Google OAuth: {desc}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"Google OAuth: {exc}") from exc


def save_refresh_token(refresh_token: str) -> str:
    from config import GOOGLE_ADS_REFRESH_TOKEN, reload_marketing_config, set_marketing_value
    from web.integrations.google_ads import verify_google_ads_credentials

    token = (refresh_token or "").strip()
    if not token:
        raise ValueError("Google не повернув refresh_token. Спробуйте ще раз з prompt=consent.")

    previous = (GOOGLE_ADS_REFRESH_TOKEN or "").strip()
    set_marketing_value("GOOGLE_ADS_REFRESH_TOKEN", token)
    reload_marketing_config()
    try:
        return verify_google_ads_credentials()
    except ValueError:
        set_marketing_value("GOOGLE_ADS_REFRESH_TOKEN", previous)
        reload_marketing_config()
        raise
