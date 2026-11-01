"""Тести валідації метрик Google Ads і GSC."""

from web.integrations.google_ads import _normalize_gads_payload
from web.integrations.google_metrics_check import validate_google_ads_payload
from web.integrations.gsc_metrics_check import validate_gsc_payload


def test_normalize_gads_payload_legacy_cache():
    legacy = {
        "spend": 1000.0,
        "clicks": 200,
        "impressions": 10000,
        "conversions": 50.0,
        "campaigns": [{"spend": 1000, "conversions": 50}],
        "daily": [{"date": "2026-08-01", "spend": 1000, "conversions": 50}],
    }
    out = _normalize_gads_payload(legacy)
    assert out["conversions_value"] == 0.0
    assert out["campaigns"][0]["conversion_value"] == 0.0
    assert out["daily"][0]["conversion_value"] == 0.0
    assert out["ctr"] == 2.0
    assert out["cpc"] == 5.0
    assert out["cost_per_conversion"] == 20.0


def test_validate_google_ads_payload_ok():
    gads = {
        "spend": 1000.0,
        "conversions": 50.0,
        "conversions_value": 5000.0,
        "cost_per_conversion": 20.0,
        "campaigns": [
            {"spend": 600, "conversions": 30, "conversion_value": 3000},
            {"spend": 400, "conversions": 20, "conversion_value": 2000},
        ],
        "daily": [
            {"spend": 500, "conversions": 25, "conversion_value": 2500},
            {"spend": 500, "conversions": 25, "conversion_value": 2500},
        ],
        "spend_by_type": [{"spend": 700}, {"spend": 300}],
    }
    assert validate_google_ads_payload(gads) == []


def test_validate_gsc_payload_ok():
    gsc = {
        "clicks": 100,
        "impressions": 10000,
        "ctr": 1.0,
        "daily": [
            {"clicks": 60, "impressions": 6000},
            {"clicks": 40, "impressions": 4000},
        ],
    }
    assert validate_gsc_payload(gsc) == []


def test_crm_sync_is_stale_without_marker(tmp_path, monkeypatch):
    from web.integrations import crm_analytics_cache as mod

    monkeypatch.setattr(mod, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(mod, "LAST_SYNC_FILE", tmp_path / "_last_crm_sync.json")
    assert mod.crm_sync_is_stale() is True

    mod.mark_crm_synced()
    assert mod.crm_sync_is_stale() is False


def test_get_cached_ad_payload_legacy_key(tmp_path, monkeypatch):
    from web.integrations import ad_cache as mod

    monkeypatch.setattr(mod, "CACHE_DIR", tmp_path)
    legacy = {"spend": 100, "conversions": 5}
    mod.cache_set("gads_2026-08-01_2026-08-31", legacy)
    assert mod.get_cached_ad_payload(
        "gads_v2_2026-08-01_2026-08-31",
        legacy_keys=("gads_2026-08-01_2026-08-31",),
    ) == legacy


def test_validate_gsc_payload_clicks_mismatch():
    gsc = {
        "clicks": 100,
        "impressions": 10000,
        "ctr": 1.0,
        "daily": [{"clicks": 50, "impressions": 5000}],
    }
    issues = validate_gsc_payload(gsc)
    assert any("clicks vs daily" in i for i in issues)
