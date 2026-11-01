"""Перевірка якості та дедуплікації інсайтів."""

from datetime import datetime
from unittest.mock import patch

from web.insights.engine import build_marketing_insights
from web.insights.postprocess import postprocess_insights


def _base_orders(**kwargs):
    cur = {
        "total_revenue": kwargs.get("rev", 120_000),
        "total_orders": kwargs.get("orders", 60),
        "by_channel": {
            "google_ads": {"revenue": 70_000, "orders": 35},
            "meta_ads": {"revenue": 30_000, "orders": 15},
            "seo": {"revenue": 15_000, "orders": 8},
        },
    }
    prev = {
        "total_revenue": kwargs.get("prev_rev", 150_000),
        "total_orders": kwargs.get("prev_orders", 75),
        "by_channel": {
            "google_ads": {"revenue": 90_000, "orders": 45},
            "meta_ads": {"revenue": 40_000, "orders": 20},
            "seo": {"revenue": 12_000, "orders": 6},
        },
    }
    return {"current": cur, "previous": prev, "period": {"compare_label": "vs попередній період"}}


def _overview():
    return [
        {"channel": "google_ads", "label": "Google Ads", "revenue": 70_000, "orders": 35, "spend": 25_000, "roas": 2.8},
        {"channel": "meta_ads", "label": "Meta Ads", "revenue": 30_000, "orders": 15, "spend": 8_000, "roas": 3.75},
        {"channel": "seo", "label": "SEO", "revenue": 15_000, "orders": 8, "spend": 0, "roas": None},
    ]


@patch("web.insights.utils.ad_spend_to_uah")
def test_revenue_decline_merges_duplicates(mock_fx):
    mock_fx.side_effect = lambda amount, currency, as_of: {"amount_uah": float(amount or 0)}

    feed = build_marketing_insights(
        overview_rows=_overview(),
        orders=_base_orders(),
        gads={"spend": 25_000, "currency": "UAH", "clicks": 8000, "conversions": 35, "cpc": 3.1},
        gads_prev={"spend": 22_000, "currency": "UAH", "clicks": 7500, "conversions": 40, "cpc": 2.9},
        meta={"spend": 8000, "currency": "UAH", "purchases": 15, "clicks": 12000},
        meta_prev={"spend": 7000, "currency": "UAH", "purchases": 18, "clicks": 11000},
        gsc={"clicks": 2000, "impressions": 80000, "position": 8.2, "ctr": 2.5},
        gsc_prev={"clicks": 2100, "impressions": 82000, "position": 7.9, "ctr": 2.6},
        top_brands=[{"brand": "A", "revenue": 50_000}],
        period_end=datetime(2026, 9, 7),
        prev_period_end=datetime(2026, 8, 31),
        limit=30,
    )
    ids = {i["id"] for i in feed}
    assert "GEN01" in ids
    assert "GEN06" not in ids
    assert "GEN23" not in ids
    gen01 = next(i for i in feed if i["id"] == "GEN01")
    assert gen01.get("why")


@patch("web.insights.utils.ad_spend_to_uah")
def test_seo_stable_traffic_uses_seo10_not_seo01(mock_fx):
    mock_fx.side_effect = lambda amount, currency, as_of: {"amount_uah": float(amount or 0)}
    seo_overview = [
        {"channel": "google_ads", "label": "Google Ads", "revenue": 50_000, "orders": 20, "spend": 10_000, "roas": 5},
        {"channel": "meta_ads", "label": "Meta Ads", "revenue": 20_000, "orders": 10, "spend": 5_000, "roas": 4},
        {"channel": "seo", "label": "SEO", "revenue": 8_000, "orders": 8, "spend": 0, "roas": None},
    ]
    orders = _base_orders(rev=78_000, prev_rev=102_000, orders=38, prev_orders=42)
    orders["current"]["by_channel"]["seo"] = {"revenue": 8_000, "orders": 8}
    orders["previous"]["by_channel"]["seo"] = {"revenue": 12_000, "orders": 10}
    feed = build_marketing_insights(
        overview_rows=seo_overview,
        orders=orders,
        gads={"spend": 0},
        gads_prev={"spend": 0},
        meta={"spend": 0},
        meta_prev={"spend": 0},
        gsc={"clicks": 1000, "impressions": 50000, "position": 8.0, "ctr": 2.0},
        gsc_prev={"clicks": 1020, "impressions": 51000, "position": 8.1, "ctr": 2.0},
        ga4={"organic": {"sessions": 980, "revenue": 8_000}},
        ga4_prev={"organic": {"sessions": 1000, "revenue": 12_000}},
        top_brands=[],
        period_end=datetime(2026, 9, 7),
        prev_period_end=datetime(2026, 8, 31),
        categories={"seo"},
        limit=10,
    )
    ids = {i["id"] for i in feed}
    assert "SEO10" in ids or "SEO02" in ids
    assert "SEO01" not in ids


def test_postprocess_drops_gen19_when_cross09():
    items = [
        {"id": "GEN19", "title": "A", "impact_score": 60, "what_happened": "x"},
        {"id": "CROSS09", "title": "B", "impact_score": 65, "what_happened": "y"},
    ]
    out = postprocess_insights(items)
    ids = {i["id"] for i in out}
    assert "CROSS09" in ids
    assert "GEN19" not in ids


if __name__ == "__main__":
    test_revenue_decline_merges_duplicates()
    test_seo_stable_traffic_uses_seo10_not_seo01()
    test_postprocess_drops_gen19_when_cross09()
    print("OK")
