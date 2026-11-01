"""Узгодженість формул CRM-метрик між оглядом і розділами."""

from web.ad_analytics import CHANNEL_GOOGLE_ADS, CHANNEL_META_ADS, merge_channel_overview
from web.ad_reporting import _build_overview_extra, _safe_cpa, _safe_roas, _roas_pct


def _orders_stub():
    cur_ch = {
        CHANNEL_GOOGLE_ADS: {"revenue": 100000.0, "orders": 50},
        CHANNEL_META_ADS: {"revenue": 80000.0, "orders": 23},
        CHANNEL_SEO: {"revenue": 20000.0, "orders": 10},
    }
    return {
        "current": {
            "by_channel": cur_ch,
            "by_day": {},
            "total_revenue": 200000.0,
            "total_orders": 83,
        },
        "previous": {
            "by_channel": {k: {"revenue": 0.0, "orders": 0} for k in cur_ch},
        },
        "campaign_attribution": {},
        "prev_campaign_attribution": {},
    }


def test_overview_google_preview_uses_uah_for_crm_roas_cpa():
    orders = _orders_stub()
    gads = {
        "spend": 400.0,
        "currency": "USD",
        "clicks": 1000,
        "conversions": 60,
        "conversions_value": 0,
        "campaigns": [],
        "daily": [],
    }
    extra = _build_overview_extra(
        orders,
        gads,
        {"daily": [], "clicks": 0},
        {},
        gads_spend_uah=16000.0,
    )
    gp = extra["google_preview"]
    assert gp["cpa"] == _safe_cpa(16000.0, 50)
    assert gp["roas_pct"] == _roas_pct(_safe_roas(100000.0, 16000.0))
    assert gp["revenue"] == 100000.0


def test_overview_channel_table_matches_crm_roas_formula():
    orders = _orders_stub()
    spend_by_channel = {
        CHANNEL_GOOGLE_ADS: {"spend": 16000.0, "prev_spend": 0.0},
        CHANNEL_META_ADS: {"spend": 8000.0, "prev_spend": 0.0},
    }
    rows = merge_channel_overview(orders, spend_by_channel)
    google = next(r for r in rows if r["channel"] == CHANNEL_GOOGLE_ADS)
    assert google["roas"] == _safe_roas(100000.0, 16000.0)
    assert google["revenue_share_pct"] == 50.0


def test_overview_extra_campaign_attribution_flag():
    orders = _orders_stub()
    gads = {
        "spend": 100.0,
        "currency": "UAH",
        "clicks": 10,
        "conversions": 1,
        "campaigns": [{"name": "test", "spend": 100.0, "conversions": 1, "conversion_value": 0}],
        "daily": [],
    }
    extra = _build_overview_extra(
        orders,
        gads,
        {"daily": [], "clicks": 0},
        {},
        gads_spend_uah=100.0,
    )
    assert "campaign_attribution_utm" in extra
    assert extra["google_preview"]["campaigns"][0]["spend_uah"] == 100.0


def test_meta_preview_primary_cpa_is_crm():
    orders = _orders_stub()
    meta = {
        "spend": 200.0,
        "currency": "USD",
        "purchases": 35,
        "clicks": 500,
        "campaigns": [],
    }
    extra = _build_overview_extra(
        orders,
        {"spend": 0, "campaigns": [], "daily": []},
        {"daily": [], "clicks": 0},
        {},
        meta_api=meta,
        meta_prev={"campaigns": []},
        meta_spend_uah=8000.0,
    )
    mp = extra["meta_preview"]
    assert mp["cpa"] == _safe_cpa(8000.0, 23)
    assert mp["meta_cpa"] is not None
