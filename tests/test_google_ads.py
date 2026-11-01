"""
Перевірка Google Ads.

Unit-тести (без мережі):
    python -m unittest tests.test_google_ads

Live-перевірка ключів з marketing_config.json:
    python tests/test_google_ads.py
    python tests/test_google_ads.py --live
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from web.integrations.google_ads import (  # noqa: E402
    _format_google_ads_error,
    _map_known_google_ads_error,
    fetch_google_ads_stats,
    verify_google_ads_credentials,
)


class TestGoogleAdsErrorFormatting(unittest.TestCase):
    def test_invalid_grant_message(self) -> None:
        msg = _format_google_ads_error(Exception("invalid_grant: Token revoked"))
        self.assertIn("invalid_grant", msg.lower())

    def test_developer_token_invalid_strips_grpc_noise(self) -> None:
        raw = (
            "DEVELOPER_TOKEN_INVALID The developer token is not valid. "
            "_SingleThreadedRendezvous UNAUTHENTICATED"
        )
        msg = _map_known_google_ads_error(raw)
        self.assertIn("Developer Token", msg)
        self.assertNotIn("_SingleThreadedRendezvous", msg)

    def test_google_ads_exception_message(self) -> None:
        try:
            from google.ads.googleads.errors import GoogleAdsException
        except ImportError:
            self.skipTest("google-ads not installed")

        err = MagicMock()
        err.message = "The developer token is not valid."
        failure = MagicMock()
        failure.errors = [err]
        exc = GoogleAdsException(
            error=MagicMock(), call=MagicMock(), failure=failure, request_id="req"
        )
        msg = _format_google_ads_error(exc)
        self.assertIn("Developer Token", msg)


class TestVerifyGoogleAdsCredentials(unittest.TestCase):
    _CREDS = {
        "GOOGLE_ADS_DEVELOPER_TOKEN": "dev",
        "GOOGLE_ADS_CLIENT_ID": "cid",
        "GOOGLE_ADS_CLIENT_SECRET": "secret",
        "GOOGLE_ADS_REFRESH_TOKEN": "refresh",
        "GOOGLE_ADS_CUSTOMER_ID": "405-605-6141",
    }

    def _patch_creds(self, overrides: dict[str, str] | None = None) -> None:
        data = dict(self._CREDS)
        if overrides:
            data.update(overrides)
        patcher = patch("web.integrations.google_ads.load_marketing_config", return_value=data)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_success(self) -> None:
        self._patch_creds()
        row = MagicMock()
        row.customer.currency_code = "UAH"

        with patch("web.integrations.google_ads._client_from_dict") as client_factory:
            client_factory.return_value.get_service.return_value = MagicMock()
            with patch("web.integrations.google_ads._search_rows", return_value=[row]):
                msg = verify_google_ads_credentials()
        self.assertIn("Підключення OK", msg)
        self.assertIn("UAH", msg)

    def test_missing_fields(self) -> None:
        self._patch_creds({"GOOGLE_ADS_REFRESH_TOKEN": ""})
        with self.assertRaises(ValueError) as ctx:
            verify_google_ads_credentials()
        self.assertIn("GOOGLE_ADS_REFRESH_TOKEN", str(ctx.exception))

    def test_invalid_grant_raises_value_error(self) -> None:
        self._patch_creds()
        with patch("web.integrations.google_ads._client_from_dict") as client_factory:
            client_factory.return_value.get_service.side_effect = Exception("invalid_grant")
            with self.assertRaises(ValueError) as ctx:
                verify_google_ads_credentials()
        self.assertIn("invalid_grant", str(ctx.exception).lower())


class TestFetchGoogleAdsStats(unittest.TestCase):
    def test_not_configured_returns_empty_payload(self) -> None:
        with patch("web.integrations.google_ads.google_ads_configured", return_value=False):
            with patch("web.integrations.google_ads.get_stale_ad_payload", return_value=None):
                end = datetime.now()
                start = end - timedelta(days=7)
                payload = fetch_google_ads_stats(start, end, use_cache=False)
        self.assertFalse(payload.get("configured"))
        self.assertIn("marketing_config", payload.get("error", ""))

    def test_uses_click_dated_conversions(self) -> None:
        """Конверсії — metrics.conversions (дата кліку), як колонка Conversions у UI."""
        end = datetime(2026, 10, 12)
        start = datetime(2026, 10, 5)
        queries: list[str] = []

        currency_row = MagicMock()
        currency_row.customer.currency_code = "UAH"

        traffic_row = MagicMock()
        traffic_row.metrics.clicks = 10
        traffic_row.metrics.impressions = 100
        traffic_row.metrics.cost_micros = 5_000_000
        traffic_row.metrics.ctr = 0.1
        traffic_row.metrics.average_cpc = 500_000

        conv_row = MagicMock()
        conv_row.metrics.conversions = 3.5
        conv_row.metrics.conversions_value = 7000.0

        def fake_search(_ga, _cid, query: str):
            queries.append(query)
            q = " ".join(query.split())
            if "customer.currency_code" in q and "metrics." not in q:
                return [currency_row]
            if "metrics.conversions" in q and "by_conversion_date" not in q and "segments.date" not in q and "campaign" not in q and "keyword" not in q:
                return [conv_row]
            if "metrics.cost_micros" in q and "FROM customer" in q and "segments.date" not in q:
                return [traffic_row]
            if "segments.date" in q and "metrics.conversions" in q and "by_conversion_date" not in q:
                day = MagicMock()
                day.segments.date = "2026-10-06"
                day.metrics.conversions = 3.5
                day.metrics.conversions_value = 7000.0
                return [day]
            if "segments.date" in q and "cost_micros" in q:
                day = MagicMock()
                day.segments.date = "2026-10-06"
                day.metrics.cost_micros = 5_000_000
                day.metrics.clicks = 10
                day.metrics.impressions = 100
                return [day]
            return []

        with patch("web.integrations.google_ads.google_ads_configured", return_value=True), \
             patch("web.integrations.google_ads._customer_id", return_value="123"), \
             patch("web.integrations.google_ads._client") as client_factory, \
             patch("web.integrations.google_ads._search_rows", side_effect=fake_search), \
             patch("web.integrations.google_ads.cache_set"), \
             patch("web.integrations.google_ads.get_stale_ad_payload", return_value=None):
            client_factory.return_value.get_service.return_value = MagicMock()
            payload = fetch_google_ads_stats(start, end, use_cache=False)

        self.assertTrue(any(
            "metrics.conversions" in " ".join(q.split()) and "by_conversion_date" not in q
            for q in queries
        ))
        self.assertFalse(any("conversions_by_conversion_date" in q for q in queries))
        self.assertEqual(payload.get("conversions"), 3.5)
        self.assertEqual(payload.get("conversions_value"), 7000.0)
        self.assertEqual(payload.get("conversions_time_basis"), "click_date")
        self.assertEqual(payload.get("spend"), 5.0)
        self.assertEqual(payload.get("daily")[0]["conversions"], 3.5)


def _print_section(title: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))


def run_live_check() -> int:
    """Live-перевірка: marketing_config.json → Google Ads API."""
    from config import GOOGLE_ADS_CUSTOMER_ID, google_ads_configured
    from marketing_config import MARKETING_CONFIG_PATH

    print("Google Ads — live-перевірка")
    print(f"Конфіг: {MARKETING_CONFIG_PATH}")

    if not google_ads_configured():
        print("❌ Google Ads не налаштовано — заповніть marketing_config.json")
        return 1

    try:
        verify_msg = verify_google_ads_credentials()
        print(f"✅ Авторизація: {verify_msg}")
    except ValueError as exc:
        print(f"❌ Авторизація: {exc}")
        return 1

    _print_section("Доступні акаунти")
    try:
        from web.integrations.google_ads import _client

        client = _client()
        service = client.get_service("CustomerService")
        accessible = service.list_accessible_customers()
        names = list(accessible.resource_names or [])
        if not names:
            print("⚠️  Немає доступних акаунтів")
        else:
            for name in names:
                print(f"  • {name}")
    except Exception as exc:
        print(f"❌ { _format_google_ads_error(exc) }")

    _print_section(f"Статистика за 7 днів (акаунт {GOOGLE_ADS_CUSTOMER_ID})")
    end = datetime.now()
    start = end - timedelta(days=7)
    payload = fetch_google_ads_stats(start, end, use_cache=False)

    if payload.get("error"):
        print(f"❌ {payload['error']}")
        return 1

    print(f"  Витрати:     {payload.get('spend')} {payload.get('currency') or ''}")
    print(f"  Кліки:       {payload.get('clicks')}")
    print(f"  Покази:      {payload.get('impressions')}")
    print(f"  Конверсії:   {payload.get('conversions')}")
    print(f"  Дохід (Ads): {payload.get('conversions_value')}")
    print(f"  CTR:         {payload.get('ctr')}%")
    print(f"  CPC:         {payload.get('cpc')}")

    campaigns = payload.get("campaigns") or []
    if campaigns:
        _print_section("Топ кампаній")
        for row in campaigns[:5]:
            print(
                f"  • {row.get('name', '—')[:40]:<40} "
                f"витрати {row.get('spend')}  кліки {row.get('clicks')}"
            )
    else:
        print("\n⚠️  Немає кампаній за період")

    print("\n✅ Live-перевірка завершена успішно")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if "--unit" in args:
        unittest.main(argv=[sys.argv[0]])
        return 0
    return run_live_check()


if __name__ == "__main__":
    raise SystemExit(main())
