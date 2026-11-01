"""Повідомлення про помилки Monobank API."""

from mono.api import _http_error_reason, _parse_mono_api_error_body


def test_jars_by_client_502_message():
    body = '{"errorDescription":"power_banka.JarsByClient 502 html"}'
    detail = _parse_mono_api_error_body(body)
    assert detail is not None
    assert "JarsByClient" in detail
    assert "банку" in detail.lower() or "Monobank" in detail
    reason = _http_error_reason(500, body)
    assert "500" in reason
    assert "JarsByClient" in reason or "банку" in reason
