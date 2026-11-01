"""Коефіцієнт конверсії в рекламному дашборді."""

from web.ad_analytics import (
    _safe_conversion_rate,
    format_conversion_rate,
    merge_channel_overview,
)
from web.ad_channel import CHANNEL_GOOGLE_ADS, CHANNEL_SEO
from web.ad_reporting import _channel_traffic_metrics


def test_safe_conversion_rate_percent():
    assert _safe_conversion_rate(5, 200) == 2.5
    assert _safe_conversion_rate(0, 100) == 0.0
    assert _safe_conversion_rate(10, 0) is None


def test_format_conversion_rate():
    assert format_conversion_rate(2.5) == "2.5%"
    assert format_conversion_rate(None) is None


def test_merge_channel_overview_includes_cr():
    orders = {
        "current": {
            "total_revenue": 1000,
            "by_channel": {
                CHANNEL_GOOGLE_ADS: {"revenue": 500, "orders": 10},
                CHANNEL_SEO: {"revenue": 500, "orders": 5},
            },
        },
        "previous": {
            "by_channel": {
                CHANNEL_GOOGLE_ADS: {"revenue": 400, "orders": 8},
                CHANNEL_SEO: {"revenue": 300, "orders": 3},
            },
        },
    }
    spend = {CHANNEL_GOOGLE_ADS: {"spend": 100, "prev_spend": 80}, CHANNEL_SEO: {"spend": 0, "prev_spend": 0}}
    traffic = {CHANNEL_GOOGLE_ADS: 200, CHANNEL_SEO: 100}
    prev_traffic = {CHANNEL_GOOGLE_ADS: 160, CHANNEL_SEO: 60}
    rows = merge_channel_overview(orders, spend, traffic, prev_traffic)
    gads = next(r for r in rows if r["channel"] == CHANNEL_GOOGLE_ADS)
    assert gads["conversion_rate"] == 5.0
    assert gads["conversion_rate_pct"] == "5.0%"


def test_channel_traffic_metrics_seo_prefers_gsc_clicks():
    cur, _ = _channel_traffic_metrics(
        {"clicks": 0}, {}, {"clicks": 120}, {"organic": {"sessions": 80}},
    )
    assert cur[CHANNEL_SEO] == 120
