"""Тести парсингу метрик Meta Ads."""

from web.integrations.meta_ads import (
    _insight_window,
    meta_roas_display,
    parse_meta_purchase_metrics,
)


def test_parse_meta_purchase_metrics_uses_omni_not_sum():
    row = {
        "spend": "1234.77",
        "actions": [
            {"action_type": "omni_purchase", "value": "232"},
            {"action_type": "purchase", "value": "232"},
            {"action_type": "offsite_conversion.fb_pixel_purchase", "value": "232"},
        ],
        "action_values": [
            {"action_type": "omni_purchase", "value": "15594.4"},
            {"action_type": "purchase", "value": "15594.4"},
        ],
        "purchase_roas": [{"action_type": "omni_purchase", "value": "12.629397"}],
    }
    m = parse_meta_purchase_metrics(row)
    assert m["purchases"] == 232
    assert m["purchase_value"] == 15594.4
    assert m["purchase_roas"] == 12.6294


def test_parse_meta_purchase_metrics_fallback_roas():
    row = {
        "spend": "100",
        "actions": [{"action_type": "purchase", "value": "5"}],
        "action_values": [{"action_type": "purchase", "value": "250"}],
        "purchase_roas": [],
    }
    m = parse_meta_purchase_metrics(row)
    assert m["purchases"] == 5
    assert m["purchase_value"] == 250.0
    assert m["purchase_roas"] == 2.5


def test_meta_roas_display():
    assert meta_roas_display(12.629397) == "12.63x"
    assert meta_roas_display(None) == "—"


def test_validate_meta_payload_ok():
    from web.integrations.meta_metrics_check import validate_meta_payload

    meta = {
        "spend": 100.0,
        "purchases": 10,
        "purchase_value": 250.0,
        "purchase_roas": 2.5,
        "cost_per_purchase": 10.0,
        "campaigns": [{"spend": 100, "purchases": 10, "purchase_value": 250}],
        "daily": [{"spend": 100, "purchases": 10, "purchase_value": 250}],
        "placements": [{"spend": 100}],
    }
    assert validate_meta_payload(meta) == []


def test_insight_window_uses_conversion_time():
    params = _insight_window({"since": "2026-10-05", "until": "2026-10-12"}, "spend,actions")
    assert params["action_report_time"] == "conversion"
    assert params["use_account_attribution_setting"] == "true"
    assert "2026-10-05" in params["time_range"]
