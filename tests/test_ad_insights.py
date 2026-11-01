"""Тести генерації інсайтів."""

from datetime import datetime
from unittest.mock import patch

from web.ad_insights import build_ad_insight_feed, insight_chips_from_feed
from web.insights.engine import build_marketing_insights


def _orders(rev=100_000, prev_rev=80_000, orders=50, prev_orders=40):
    return {
        "current": {
            "total_revenue": rev,
            "total_orders": orders,
            "by_channel": {
                "google_ads": {"revenue": 60_000, "orders": 30},
                "meta_ads": {"revenue": 25_000, "orders": 15},
                "seo": {"revenue": 10_000, "orders": 5},
            },
        },
        "previous": {
            "total_revenue": prev_rev,
            "total_orders": prev_orders,
            "by_channel": {
                "google_ads": {"revenue": 55_000, "orders": 28},
                "meta_ads": {"revenue": 20_000, "orders": 10},
                "seo": {"revenue": 5_000, "orders": 2},
            },
        },
        "period": {"compare_label": "vs попередній період"},
    }


def _overview_rows():
    return [
        {
            "channel": "google_ads",
            "label": "Google Ads",
            "revenue": 60_000,
            "orders": 30,
            "spend": 20_000,
            "roas": 3.0,
        },
        {
            "channel": "meta_ads",
            "label": "Meta Ads",
            "revenue": 25_000,
            "orders": 15,
            "spend": 5_000,
            "roas": 5.0,
        },
        {
            "channel": "seo",
            "label": "SEO",
            "revenue": 10_000,
            "orders": 5,
            "spend": 0,
            "roas": None,
        },
    ]


@patch("web.insights.utils.ad_spend_to_uah")
def test_insight_feed_no_duplicates(mock_fx):
    mock_fx.side_effect = lambda amount, currency, as_of: {
        "amount_uah": float(amount or 0) * (41.0 if (currency or "").upper() == "USD" else 1.0),
    }

    feed = build_ad_insight_feed(
        overview_rows=_overview_rows(),
        orders=_orders(),
        gads={"spend": 20_000, "currency": "UAH", "clicks": 5000, "conversions": 30},
        gads_prev={"spend": 18_000, "currency": "UAH", "clicks": 4800, "conversions": 28},
        meta={"spend": 500, "currency": "USD", "frequency": 2.8, "purchases": 15},
        meta_prev={"spend": 400, "currency": "USD", "purchases": 12},
        gsc={"clicks": 1000, "position": 18.5, "impressions": 50000, "ctr": 2.0},
        gsc_prev={"clicks": 900, "position": 17.0, "impressions": 48000, "ctr": 1.9},
        top_brands=[{"brand": "Keune", "revenue": 40_000, "revenue_share_pct": 40}],
        period_end=datetime(2026, 9, 7),
        prev_period_end=datetime(2026, 8, 31),
    )

    ids = [item["id"] for item in feed]
    assert len(ids) == len(set(ids))
    assert any(item["id"] == "GEN02" for item in feed)
    assert any(item.get("what_happened") for item in feed)
    assert not any("20082%" in item.get("text", "") for item in feed)

    chips = insight_chips_from_feed(feed, limit=3)
    assert len(chips) == 3
    assert len({c["text"] for c in chips}) == 3


@patch("web.insights.utils.ad_spend_to_uah")
def test_seo_rules_subset_only(mock_fx):
    mock_fx.side_effect = lambda amount, currency, as_of: {"amount_uah": float(amount or 0)}

    feed = build_marketing_insights(
        overview_rows=_overview_rows(),
        orders=_orders(rev=50_000, prev_rev=80_000, orders=20, prev_orders=35),
        gads={"spend": 0},
        gads_prev={"spend": 0},
        meta={"spend": 0},
        meta_prev={"spend": 0},
        gsc={
            "clicks": 500,
            "clicks_prev": 900,
            "impressions": 20000,
            "position": 9.5,
            "ctr": 2.5,
            "pages": [
                {"key": "/category/light", "clicks": 50, "impressions": 2000},
                {"key": "/product/1", "clicks": 30, "impressions": 1000},
            ],
            "queries": [{"key": "косметика", "page": "/new-url", "impressions": 100}],
        },
        gsc_prev={
            "clicks": 900,
            "impressions": 40000,
            "position": 7.2,
            "ctr": 2.8,
            "pages": [
                {"key": "/category/light", "clicks": 200, "impressions": 8000},
                {"key": "/product/1", "clicks": 150, "impressions": 5000},
                {"key": "/page-a", "clicks": 10, "impressions": 500},
                {"key": "/page-b", "clicks": 10, "impressions": 500},
                {"key": "/page-c", "clicks": 10, "impressions": 500},
            ],
            "queries": [{"key": "косметика", "page": "/old-url", "impressions": 100}],
        },
        ga4={"organic": {"sessions": 480, "revenue": 8000, "conversions": 4}},
        ga4_prev={"organic": {"sessions": 850, "revenue": 12000, "conversions": 8}},
        top_brands=[],
        period_end=datetime(2026, 9, 7),
        prev_period_end=datetime(2026, 8, 31),
        categories={"seo"},
    )

    allowed_prefixes = ("SEO01", "SEO02", "SEO03", "SEO04", "SEO05", "SEO06", "SEO10", "SEO15", "SEO16", "SEO22", "SEO28")
    seo_ids = [item["id"] for item in feed]
    assert seo_ids, "expected at least one SEO insight"
    for iid in seo_ids:
        assert any(iid.startswith(p) for p in allowed_prefixes), f"unexpected SEO rule: {iid}"
    forbidden = ("SEO17", "SEO18", "SEO19", "SEO20", "SEO21")
    assert not any(iid.startswith(f) for iid in seo_ids for f in forbidden)


@patch("web.insights.utils.ad_spend_to_uah")
def test_structured_fields_present(mock_fx):
    mock_fx.side_effect = lambda amount, currency, as_of: {"amount_uah": float(amount or 0)}

    feed = build_marketing_insights(
        overview_rows=_overview_rows(),
        orders=_orders(rev=50_000, prev_rev=80_000),
        gads={"spend": 20_000, "currency": "UAH"},
        gads_prev={"spend": 15_000, "currency": "UAH"},
        meta={"spend": 5000, "currency": "UAH"},
        meta_prev={"spend": 4000, "currency": "UAH"},
        gsc={"clicks": 100},
        gsc_prev={"clicks": 100},
        top_brands=[],
        period_end=datetime(2026, 9, 7),
        prev_period_end=datetime(2026, 8, 31),
        limit=5,
    )
    item = feed[0]
    assert "title" in item
    assert "what_happened" in item
    assert "impact_score" in item
    assert "signal_type" in item


if __name__ == "__main__":
    test_insight_feed_no_duplicates()
    test_seo_rules_subset_only()
    test_structured_fields_present()
    print("OK")
