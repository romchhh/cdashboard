"""Атрибуція каналів CRM, зокрема Meta (facebook.com / cpm)."""

from types import SimpleNamespace

from web.ad_channel import (
    CHANNEL_EMAIL,
    CHANNEL_GOOGLE_ADS,
    CHANNEL_META_ADS,
    CHANNEL_OTHER,
    CHANNEL_SEO,
    classify_marketing_channel,
    classify_order_channel,
)


def test_facebook_com_cpm_is_meta():
    assert classify_marketing_channel(
        utm_source="facebook.com",
        utm_medium="cpm",
        utm_campaign="retarget",
        referer="instagram.com",
    ) == CHANNEL_META_ADS


def test_short_fb_ig_sources_are_meta():
    assert classify_marketing_channel(utm_source="fb", utm_medium="cpm") == CHANNEL_META_ADS
    assert classify_marketing_channel(utm_source="ig", utm_medium="paid") == CHANNEL_META_ADS


def test_cpm_without_google_defaults_to_meta():
    assert classify_marketing_channel(utm_medium="cpm") == CHANNEL_META_ADS


def test_client_utm_fallback_is_meta():
    order = SimpleNamespace(utm_source=None, utm_medium=None, utm_campaign=None, referer=None)
    client = SimpleNamespace(
        source_raw=None,
        source_category=None,
        source_utm_source="facebook.com",
        source_utm_medium="cpm",
        source_utm_campaign="retarget",
        source_referrer=None,
    )
    assert classify_order_channel(order, client) == CHANNEL_META_ADS


def test_google_organic_is_not_meta():
    assert classify_marketing_channel(
        utm_source="google",
        utm_medium="organic",
    ) == CHANNEL_SEO


def test_gclid_in_referer_is_google_ads():
    assert classify_marketing_channel(
        referer="https://cosmy.com.ua/?gclid=abc123",
    ) == CHANNEL_GOOGLE_ADS


def test_direct_cpc_with_google_campaign_is_google_ads():
    assert classify_marketing_channel(
        utm_source="direct",
        utm_medium="cpc",
        utm_campaign="VA_PMax_yon-ka",
    ) == CHANNEL_GOOGLE_ADS


def test_organic_google_with_pmax_campaign_is_google_ads():
    assert classify_marketing_channel(
        utm_source="organic",
        utm_medium="google",
        utm_campaign="pmax_na_allbrands",
    ) == CHANNEL_GOOGLE_ADS


def test_sms_viber_with_missing_referrer_is_email():
    assert classify_marketing_channel(
        utm_source="sms",
        utm_medium="viber",
        utm_campaign="black_friday",
        source_referrer="missing",
    ) == CHANNEL_EMAIL


def test_chatgpt_cpc_with_remarketing_campaign_is_google_ads():
    assert classify_marketing_channel(
        utm_source="chatgpt.com",
        utm_medium="cpc",
        utm_campaign="va_pmax_remarketing_all_products",
    ) == CHANNEL_GOOGLE_ADS


def test_organic_google_without_gads_campaign_stays_seo():
    assert classify_marketing_channel(
        utm_source="organic",
        utm_medium="google",
        source_referrer="www.google.com",
    ) == CHANNEL_SEO


def test_cpc_without_gads_campaign_stays_other():
    assert classify_marketing_channel(
        utm_source="unknown_partner",
        utm_medium="cpc",
        utm_campaign="partner_promo",
    ) == CHANNEL_OTHER
