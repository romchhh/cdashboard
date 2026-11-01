"""Тести налаштувань API інтеграцій."""

from web.integrations.ad_integration_settings import (
    build_section_integration_settings,
    classify_integration_status,
    mask_secret,
)


def test_mask_secret():
    assert mask_secret("abcdefghij") == "••••••••ghij"
    assert mask_secret("") == "—"


def test_classify_auth_error():
    assert classify_integration_status(
        configured=True,
        error="invalid_grant: Token has been expired or revoked.",
    ) == "auth_error"


def test_build_section_alerts():
    panel = [
        {
            "key": "google_ads",
            "label": "Google Ads",
            "configured": True,
            "status": "auth_error",
            "error": "invalid_grant",
            "updated": None,
        }
    ]
    data = build_section_integration_settings("google_ads", panel)
    assert data["has_auth_issue"] is True
    assert any(a["severity"] == "auth_error" for a in data["alerts"])
