"""Локальний OAuth Google Ads через http://localhost (як test2.py)."""

from __future__ import annotations

import json
import logging
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Any
from urllib.parse import parse_qs, urlparse

from config import (
    GOOGLE_ADS_CLIENT_ID,
    GOOGLE_ADS_OAUTH_LOCAL_PORT,
    GOOGLE_ADS_OAUTH_LOCAL_REDIRECT_URI,
)
from web.integrations.google_ads_oauth import (
    GOOGLE_ADS_OAUTH_SCOPE,
    exchange_code_for_tokens,
    oauth_prerequisites_ok,
    save_refresh_token,
)

logger = logging.getLogger(__name__)


class _LocalOAuthState:
    __slots__ = ("state", "error", "refresh_prefix", "refresh_token", "verified_message")

    def __init__(self) -> None:
        self.state = "idle"
        self.error: str | None = None
        self.refresh_prefix: str | None = None
        self.refresh_token: str | None = None
        self.verified_message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "error": self.error,
            "refresh_token_prefix": self.refresh_prefix,
            "verified": self.verified_message,
        }


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def local_oauth_redirect_uri() -> str:
    return (GOOGLE_ADS_OAUTH_LOCAL_REDIRECT_URI or "http://localhost:8765").strip()


def local_oauth_port(redirect_uri: str | None = None) -> int:
    redirect = redirect_uri or local_oauth_redirect_uri()
    parsed = urlparse(redirect)
    if parsed.port:
        return parsed.port
    if GOOGLE_ADS_OAUTH_LOCAL_PORT:
        return int(GOOGLE_ADS_OAUTH_LOCAL_PORT)
    return 443 if parsed.scheme == "https" else 80


def local_oauth_base_url() -> str:
    redirect = local_oauth_redirect_uri()
    parsed = urlparse(redirect)
    host = parsed.hostname or "localhost"
    port = local_oauth_port(redirect)
    if (parsed.scheme == "https" and port == 443) or (parsed.scheme == "http" and port == 80):
        return f"{parsed.scheme}://{host}"
    return f"{parsed.scheme}://{host}:{port}"


def _privileged_port_hint(port: int) -> str:
    if port >= 1024:
        return ""
    return (
        f"\nПорт {port} на macOS/Linux потребує sudo. "
        "Краще використати порт 8765:\n"
        "  GOOGLE_ADS_OAUTH_LOCAL_REDIRECT_URI=http://localhost:8765\n"
        "  GOOGLE_ADS_OAUTH_LOCAL_PORT=8765\n"
        "і додайте http://localhost:8765 у Google Cloud → Authorized redirect URIs."
    )


def build_local_authorize_url() -> str:
    params = {
        "client_id": GOOGLE_ADS_CLIENT_ID,
        "redirect_uri": local_oauth_redirect_uri(),
        "scope": GOOGLE_ADS_OAUTH_SCOPE,
        "response_type": "code",
        "access_type": "offline",
        "prompt": "consent",
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)


def local_oauth_meta() -> dict[str, Any]:
    redirect = local_oauth_redirect_uri()
    port = local_oauth_port(redirect)
    base = local_oauth_base_url()
    return {
        "redirect_uri": redirect,
        "port": port,
        "daemon_base_url": base,
        "command": "python scripts/google_ads_oauth_cli.py",
        "daemon_command": "python scripts/google_ads_oauth_cli.py --daemon",
        "authorize_url": build_local_authorize_url() if oauth_prerequisites_ok() else None,
        "available": oauth_prerequisites_ok(),
    }


def _finish_oauth_code(code: str, oauth_state: _LocalOAuthState) -> str:
    redirect_uri = local_oauth_redirect_uri()
    tokens = exchange_code_for_tokens(code, redirect_uri)
    refresh = (tokens.get("refresh_token") or "").strip()
    if not refresh:
        raise ValueError("Google не повернув refresh_token. Спробуйте ще раз (prompt=consent).")
    verified = save_refresh_token(refresh)
    oauth_state.state = "done"
    oauth_state.error = None
    oauth_state.refresh_token = refresh
    oauth_state.refresh_prefix = refresh[:20]
    oauth_state.verified_message = verified
    logger.info("Google Ads refresh token збережено через локальний OAuth")
    return refresh


class _LocalOAuthHandler(BaseHTTPRequestHandler):
    def _send_cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._send_cors()
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status: int, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._send_cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors()
        self.end_headers()

    def do_GET(self) -> None:
        oauth_state: _LocalOAuthState = self.server.oauth_state
        path = urlparse(self.path).path.rstrip("/") or "/"
        params = parse_qs(urlparse(self.path).query)

        if path == "/health":
            self._send_json(200, {"ok": True})
            return

        if path == "/status":
            self._send_json(200, oauth_state.as_dict())
            return

        if path == "/start":
            if oauth_state.state == "authorizing":
                self._send_json(409, {"ok": False, "error": "Авторизація вже запущена"})
                return
            oauth_state.state = "authorizing"
            oauth_state.error = None
            oauth_state.refresh_prefix = None
            auth_url = build_local_authorize_url()

            def _open_browser() -> None:
                webbrowser.open(auth_url)

            threading.Thread(target=_open_browser, daemon=True).start()
            self._send_json(200, {"ok": True, "started": True, "authorize_url": auth_url})
            return

        if params.get("error"):
            oauth_state.state = "error"
            oauth_state.error = params["error"][0]
            self._send_html(
                400,
                "<h2>❌ Google OAuth: помилка авторизації</h2><p>Можна закрити вкладку.</p>",
            )
            return

        if "code" in params:
            try:
                _finish_oauth_code(params["code"][0], oauth_state)
                self._send_html(
                    200,
                    "<h2>✅ Підключено!</h2><p>Refresh token збережено. Можна закрити вкладку і повернутись у налаштування.</p>",
                )
            except Exception as exc:
                oauth_state.state = "error"
                oauth_state.error = str(exc)
                self._send_html(500, f"<h2>❌ {exc}</h2>")
            return

        self._send_json(404, {"ok": False, "error": "not_found"})

    def log_message(self, format: str, *args: Any) -> None:
        pass


def run_local_oauth_daemon() -> None:
    """Фоновий localhost-сервер для OAuth з кнопки в налаштуваннях веб-панелі."""
    if not oauth_prerequisites_ok():
        raise RuntimeError("Задайте GOOGLE_ADS_CLIENT_ID і GOOGLE_ADS_CLIENT_SECRET у marketing_config.json")

    redirect_uri = local_oauth_redirect_uri()
    port = local_oauth_port(redirect_uri)
    base = local_oauth_base_url()

    try:
        server = ThreadingHTTPServer(("localhost", port), _LocalOAuthHandler)
    except PermissionError as exc:
        raise PermissionError(
            f"Немає доступу до порту {port}.{_privileged_port_hint(port)}"
        ) from exc
    except OSError as exc:
        raise OSError(f"Не вдалось запустити сервер на порту {port}: {exc}") from exc

    server.oauth_state = _LocalOAuthState()
    print(f"✅ Локальний OAuth daemon: {base}")
    print("   Залиште цей термінал відкритим і натисніть «Підключити через Google» у налаштуваннях.")
    print("   Ctrl+C — зупинити.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nЗупинено.")
        server.shutdown()


def run_local_oauth_flow(*, open_browser: bool = True) -> str:
    """Одноразовий OAuth (як test2.py): сервер → браузер → callback → marketing_config.json."""
    if not oauth_prerequisites_ok():
        raise RuntimeError("Задайте GOOGLE_ADS_CLIENT_ID і GOOGLE_ADS_CLIENT_SECRET у marketing_config.json")

    redirect_uri = local_oauth_redirect_uri()
    port = local_oauth_port(redirect_uri)
    oauth_state = _LocalOAuthState()
    oauth_state.state = "authorizing"

    auth_url = build_local_authorize_url()
    print(f"⏳ Запускаю локальний сервер на {redirect_uri} (порт {port})...")
    try:
        server = ThreadingHTTPServer(("localhost", port), _LocalOAuthHandler)
    except PermissionError as exc:
        raise PermissionError(
            f"Немає доступу до порту {port}.{_privileged_port_hint(port)}"
        ) from exc
    except OSError as exc:
        raise OSError(f"Не вдалось запустити сервер на порту {port}: {exc}") from exc

    server.oauth_state = oauth_state

    if open_browser:
        print("🌐 Відкриваю браузер для авторизації...")
        webbrowser.open(auth_url)
    else:
        print("Відкрийте цей URL у браузері:")
        print(auth_url)

    print("Очікую callback від Google...")
    while oauth_state.state == "authorizing":
        server.handle_request()

    if oauth_state.state != "done" or not oauth_state.refresh_token:
        raise RuntimeError(oauth_state.error or "Не вдалось отримати код авторизації від Google")

    return oauth_state.refresh_token
