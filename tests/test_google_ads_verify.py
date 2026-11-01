"""Тести перевірки Google Ads credentials."""

from unittest.mock import MagicMock, patch

from web.integrations.google_ads import (
    _format_google_ads_error,
    _map_known_google_ads_error,
    verify_google_ads_credentials,
)


def test_format_invalid_grant():
    assert "invalid_grant" in _format_google_ads_error(Exception("invalid_grant: Bad Request")).lower()


def test_map_developer_token_invalid():
    raw = (
        "DEVELOPER_TOKEN_INVALID The developer token is not valid. "
        "_SingleThreadedRendezvous UNAUTHENTICATED"
    )
    msg = _map_known_google_ads_error(raw)
    assert "Developer Token" in msg
    assert "_SingleThreadedRendezvous" not in msg


def test_format_google_ads_exception():
    try:
        from google.ads.googleads.errors import GoogleAdsException
    except ImportError:
        return

    err = MagicMock()
    err.message = "The developer token is not valid."
    failure = MagicMock()
    failure.errors = [err]
    exc = GoogleAdsException(error=MagicMock(), call=MagicMock(), failure=failure, request_id="req")
    msg = _format_google_ads_error(exc)
    assert "Developer Token" in msg
    assert "not valid" not in msg or "недійсний" in msg


def test_verify_google_ads_credentials_success(monkeypatch):
    monkeypatch.setattr(
        "web.integrations.google_ads.GOOGLE_ADS_DEVELOPER_TOKEN",
        "dev",
    )
    monkeypatch.setattr("web.integrations.google_ads.GOOGLE_ADS_CLIENT_ID", "cid")
    monkeypatch.setattr("web.integrations.google_ads.GOOGLE_ADS_CLIENT_SECRET", "secret")
    monkeypatch.setattr("web.integrations.google_ads.GOOGLE_ADS_REFRESH_TOKEN", "refresh")
    monkeypatch.setattr("web.integrations.google_ads.GOOGLE_ADS_CUSTOMER_ID", "405-605-6141")

    row = MagicMock()
    row.customer.currency_code = "UAH"

    with patch("web.integrations.google_ads._client_from_dict") as client_factory:
        ga = MagicMock()
        client_factory.return_value.get_service.return_value = ga
        with patch("web.integrations.google_ads._search_rows", return_value=[row]):
            msg = verify_google_ads_credentials()
    assert "Підключення OK" in msg
    assert "UAH" in msg


def test_verify_google_ads_credentials_invalid_grant(monkeypatch):
    monkeypatch.setattr("web.integrations.google_ads.GOOGLE_ADS_DEVELOPER_TOKEN", "dev")
    monkeypatch.setattr("web.integrations.google_ads.GOOGLE_ADS_CLIENT_ID", "cid")
    monkeypatch.setattr("web.integrations.google_ads.GOOGLE_ADS_CLIENT_SECRET", "secret")
    monkeypatch.setattr("web.integrations.google_ads.GOOGLE_ADS_REFRESH_TOKEN", "bad")
    monkeypatch.setattr("web.integrations.google_ads.GOOGLE_ADS_CUSTOMER_ID", "405-605-6141")

    with patch("web.integrations.google_ads._client_from_dict") as client_factory:
        client_factory.return_value.get_service.side_effect = Exception("invalid_grant")
        try:
            verify_google_ads_credentials()
            assert False, "expected ValueError"
        except ValueError as exc:
            assert "invalid_grant" in str(exc).lower()
