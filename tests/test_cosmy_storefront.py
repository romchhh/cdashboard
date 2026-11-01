"""Вітрина cosmy.com.ua + cosmy.ua — referer і GSC properties."""

from web.ad_channel import CHANNEL_GOOGLE_ADS, CHANNEL_OTHER, CHANNEL_REFERRAL, classify_marketing_channel
from web.cosmy_storefront import gsc_site_urls_from_config, is_cosmy_storefront_referrer


def test_cosmy_ua_referrer_is_internal():
    assert is_cosmy_storefront_referrer("https://cosmy.ua/product")
    assert is_cosmy_storefront_referrer("https://www.cosmy.ua/")
    assert is_cosmy_storefront_referrer("https://cosmy.com.ua/ru")


def test_external_referrer_not_storefront():
    assert not is_cosmy_storefront_referrer("https://google.com/")
    assert not is_cosmy_storefront_referrer("missing")


def test_gclid_on_cosmy_ua_is_google_ads():
    assert (
        classify_marketing_channel(referer="https://cosmy.ua/?gclid=abc123")
        == CHANNEL_GOOGLE_ADS
    )


def test_cosmy_ua_referrer_without_utm_not_referral_channel():
    assert classify_marketing_channel(referer="https://www.cosmy.ua/catalog") == CHANNEL_OTHER


def test_external_referrer_is_referral():
    assert classify_marketing_channel(referer="https://partner.example/deal") == CHANNEL_REFERRAL


def test_gsc_urls_merge_pair_only_when_requested():
    urls = gsc_site_urls_from_config("https://cosmy.ua")
    assert urls == ["https://cosmy.ua"]
    merged = gsc_site_urls_from_config("https://cosmy.ua", include_storefront_pair=True)
    assert "https://cosmy.ua" in merged
    assert "https://cosmy.com.ua" in merged


def test_gsc_urls_parse_comma_separated():
    urls = gsc_site_urls_from_config("https://cosmy.com.ua,https://cosmy.ua")
    assert urls == ["https://cosmy.com.ua", "https://cosmy.ua"]
