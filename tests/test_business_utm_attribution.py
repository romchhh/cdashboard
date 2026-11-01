"""Атрибуція бізнес-блоку за UTM (не source_category Cosmy)."""

from types import SimpleNamespace

from web.ad_analytics import build_business_utm_attribution
from web.ad_channel import (
    CHANNEL_GOOGLE_ADS,
    CHANNEL_META_ADS,
    CHANNEL_SEO,
)


def _order(us="", um="", uc="", rev=100.0):
    return SimpleNamespace(
        order_total=rev,
        utm_source=us or None,
        utm_medium=um or None,
        utm_campaign=uc or None,
        referer=None,
        client_customer_id="1",
    )


def _client(cat="ads", us="", um=""):
    return SimpleNamespace(
        source_category=cat,
        source_raw=None,
        source_utm_source=us or None,
        source_utm_medium=um or None,
        source_utm_campaign=None,
        source_referrer=None,
        geo_city="Київ",
    )


def test_top_channels_use_utm_not_cosmy_category():
    pairs = [
        (_order("facebook.com", "cpm", rev=500), _client(cat="ads")),
        (_order("google", "cpc", rev=300), _client(cat="ads")),
        (_order("google", "organic", rev=200), _client(cat="organic")),
    ]
    stats = build_business_utm_attribution(pairs)
    channels = {r["channel"]: r for r in stats["top_channels"]}
    assert channels[CHANNEL_META_ADS]["revenue"] == 500.0
    assert channels[CHANNEL_GOOGLE_ADS]["revenue"] == 300.0
    assert channels[CHANNEL_SEO]["revenue"] == 200.0
    assert stats["top_cosmy_categories"][0]["label"] == "Реклама (Cosmy feed)"


def test_top_utm_tags_include_channel_mapping():
    pairs = [(_order("google", "cpc", "brand", rev=150), _client())]
    stats = build_business_utm_attribution(pairs)
    row = stats["top_utm_tags"][0]
    assert row["utm_source"] == "google"
    assert row["utm_medium"] == "cpc"
    assert row["channel"] == CHANNEL_GOOGLE_ADS
    assert row["channel_label"] == "Google Ads"
