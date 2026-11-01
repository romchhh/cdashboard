"""Тести OAuth Google Ads."""

from types import SimpleNamespace

from web.integrations.google_ads_oauth import (
    create_oauth_state,
    load_oauth_state,
    oauth_redirect_uri,
    public_base_from_request,
    public_base_url,
    resolve_oauth_redirect_uri,
)


def _request(host: str, proto: str = "https", forwarded_host: str | None = None):
    headers = {"Host": host}
    if forwarded_host:
        headers["X-Forwarded-Host"] = forwarded_host
        headers["X-Forwarded-Proto"] = proto
    return SimpleNamespace(
        headers=headers,
        base_url=f"{proto}://{host}/",
        url=SimpleNamespace(scheme=proto),
    )


def test_oauth_state_roundtrip():
    state = create_oauth_state(
        return_to="/clients/advertising/google_ads",
        redirect_uri="https://example.com/advertising/oauth/google-ads/callback",
        username="admin",
    )
    payload = load_oauth_state(state)
    assert payload is not None
    assert payload["return_to"] == "/clients/advertising/google_ads"
    assert payload["username"] == "admin"


def test_oauth_redirect_uri():
    assert oauth_redirect_uri("https://fin.cosmy.com.ua") == (
        "https://fin.cosmy.com.ua/advertising/oauth/google-ads/callback"
    )


def test_public_base_from_request_uses_forwarded_host():
    req = _request("localhost:8000", forwarded_host="new-ngrok.example.dev")
    assert public_base_from_request(req) == "https://new-ngrok.example.dev"


def test_resolve_oauth_redirect_uri_prefers_request_when_env_host_differs(monkeypatch):
    monkeypatch.setattr(
        "web.integrations.google_ads_oauth.WEB_PUBLIC_BASE_URL",
        "https://old-ngrok.example.dev",
    )
    monkeypatch.setattr("web.integrations.google_ads_oauth.GOOGLE_ADS_OAUTH_REDIRECT_URI", "")
    req = _request("localhost:8000", forwarded_host="new-ngrok.example.dev")
    assert resolve_oauth_redirect_uri(req) == (
        "https://new-ngrok.example.dev/advertising/oauth/google-ads/callback"
    )


def test_public_base_url_prefers_explicit_base_over_env(monkeypatch):
    monkeypatch.setattr(
        "web.integrations.google_ads_oauth.WEB_PUBLIC_BASE_URL",
        "https://old-ngrok.example.dev",
    )
    assert public_base_url("https://cosmyfinances.telebots.site") == "https://cosmyfinances.telebots.site"


def test_public_base_url_uses_env_when_hosts_match(monkeypatch):
    monkeypatch.setattr(
        "web.integrations.google_ads_oauth.WEB_PUBLIC_BASE_URL",
        "https://fin.cosmy.com.ua",
    )
    req = _request("fin.cosmy.com.ua", forwarded_host="fin.cosmy.com.ua")
    assert public_base_url(str(req.base_url), request=req) == "https://fin.cosmy.com.ua"


def test_ad_dash_return_path_rewrites_legacy_advertising_url():
    from web.routes.advertising import _safe_return_path

    assert _safe_return_path("/clients/advertising/google_ads") == "/clients/marketing-report/google_ads"
    assert _safe_return_path("/clients/ad-analytics/overview") == "/clients/marketing-report/overview"
    assert _safe_return_path("//evil.example/x") == "/clients/marketing-report/google_ads"
    assert _safe_return_path("/logout") == "/clients/marketing-report/google_ads"
