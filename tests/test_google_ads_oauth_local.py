"""Тести локального OAuth Google Ads."""

from web.integrations.google_ads_oauth_local import (
    build_local_authorize_url,
    local_oauth_meta,
    local_oauth_port,
    local_oauth_redirect_uri,
)


def test_local_oauth_redirect_uri_default(monkeypatch):
    monkeypatch.setattr(
        "web.integrations.google_ads_oauth_local.GOOGLE_ADS_OAUTH_LOCAL_REDIRECT_URI",
        "http://localhost:8765",
    )
    assert local_oauth_redirect_uri() == "http://localhost:8765"


def test_local_oauth_port_from_redirect(monkeypatch):
    monkeypatch.setattr("web.integrations.google_ads_oauth_local.GOOGLE_ADS_OAUTH_LOCAL_PORT", 0)
    assert local_oauth_port("http://localhost:8765") == 8765
    assert local_oauth_port("http://localhost") == 80


def test_build_local_authorize_url(monkeypatch):
    monkeypatch.setattr(
        "web.integrations.google_ads_oauth_local.GOOGLE_ADS_CLIENT_ID",
        "test-client.apps.googleusercontent.com",
    )
    monkeypatch.setattr(
        "web.integrations.google_ads_oauth_local.GOOGLE_ADS_OAUTH_LOCAL_REDIRECT_URI",
        "http://localhost",
    )
    url = build_local_authorize_url()
    assert "client_id=test-client.apps.googleusercontent.com" in url
    assert "redirect_uri=http%3A%2F%2Flocalhost" in url
    assert "access_type=offline" in url


def test_local_oauth_meta(monkeypatch):
    monkeypatch.setattr("web.integrations.google_ads_oauth_local.oauth_prerequisites_ok", lambda: True)
    monkeypatch.setattr(
        "web.integrations.google_ads_oauth_local.GOOGLE_ADS_CLIENT_ID",
        "test-client.apps.googleusercontent.com",
    )
    meta = local_oauth_meta()
    assert meta["command"] == "python scripts/google_ads_oauth_cli.py"
    assert meta["daemon_command"] == "python scripts/google_ads_oauth_cli.py --daemon"
    assert meta["daemon_base_url"] == "http://localhost:8765"
    assert meta["redirect_uri"] == "http://localhost:8765"
    assert meta["authorize_url"]
