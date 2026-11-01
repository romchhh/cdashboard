"""Тести конвертації витрат реклами в UAH."""

import urllib.error
from datetime import date
from unittest.mock import patch

from web.integrations.fx_rates import ad_spend_to_uah, convert_daily_spend_series, format_fx_spend_note


def test_ad_spend_uah_no_conversion():
    out = ad_spend_to_uah(1000, "UAH", date(2026, 9, 7))
    assert out["amount_uah"] == 1000
    assert out["converted"] is False


@patch("web.integrations.fx_rates.get_usd_uah_rate")
def test_ad_spend_usd_to_uah(mock_rate):
    mock_rate.return_value = {
        "rate": 41.5,
        "currency": "USD",
        "target": "UAH",
        "date": "2026-09-07",
        "source": "nbu",
        "approximate": False,
    }
    out = ad_spend_to_uah(100, "USD", date(2026, 9, 7))
    assert out["amount_uah"] == 4150.0
    assert out["converted"] is True
    assert out["fx_rate"] == 41.5


@patch("web.integrations.fx_rates.ad_spend_to_uah")
def test_convert_daily_spend_series(mock_conv):
    mock_conv.side_effect = lambda amount, currency, as_of: {
        "amount_uah": float(amount) * 40,
        "amount_raw": float(amount),
        "currency": currency,
        "converted": True,
    }
    rows = convert_daily_spend_series(
        [{"date": "2026-09-01", "spend": 10}, {"date": "2026-09-02", "spend": 5}],
        "USD",
    )
    assert rows[0]["spend"] == 400
    assert rows[1]["spend"] == 200


@patch("web.integrations.fx_rates.urllib.request.urlopen")
def test_nbu_fallback_when_unavailable(mock_urlopen):
    mock_urlopen.side_effect = urllib.error.URLError("network down")
    from web.integrations.fx_rates import _fetch_nbu_rate

    rate, iso_date, approximate = _fetch_nbu_rate("USD", date(2026, 9, 15))
    assert rate > 0
    assert iso_date
    assert approximate is True


def test_format_fx_spend_note():
    note = format_fx_spend_note(
        meta_native={
            "converted": True,
            "amount_raw": 100.0,
            "amount_uah": 4150.0,
            "fx_rate": 41.5,
            "fx_date": "2026-09-07",
            "fx_approximate": False,
        }
    )
    assert note is not None
    assert "Meta" in note
    assert "41.5" in note


if __name__ == "__main__":
    test_ad_spend_uah_no_conversion()
    from unittest.mock import patch

    with patch("web.integrations.fx_rates.get_usd_uah_rate") as mock_rate:
        mock_rate.return_value = {
            "rate": 41.5,
            "currency": "USD",
            "target": "UAH",
            "date": "2026-09-07",
            "source": "nbu",
            "approximate": False,
        }
        out = ad_spend_to_uah(100, "USD", date(2026, 9, 7))
        assert out["amount_uah"] == 4150.0
    rows = convert_daily_spend_series(
        [{"date": "2026-09-01", "spend": 10}],
        "UAH",
    )
    assert rows[0]["spend"] == 10
    test_format_fx_spend_note()
    print("OK")
