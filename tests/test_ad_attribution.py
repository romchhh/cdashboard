"""Зіставлення utm_campaign з назвами кампаній Ads."""

from types import SimpleNamespace

from web.ad_attribution import (
    attribution_unassigned_totals,
    build_campaign_attribution,
    campaign_match_score,
    resolve_campaign_attribution,
)
from web.ad_channel import CHANNEL_META_ADS


def test_loose_substring_promo_does_not_match_long_campaign():
    score = campaign_match_score(
        "Покупка/каталог/promo/24.07",
        "promo",
    )
    assert score == 0.0


def test_exact_and_token_match():
    assert campaign_match_score(
        "Покупка/каталог/promo/24.07",
        "Покупка/каталог/promo/24.07",
    ) == 100.0
    assert campaign_match_score(
        "Трафік/блогер Надін/ 05.09",
        "Трафік/блогер Надін/ 05.09",
    ) == 100.0


def test_exclusive_assignment_prevents_double_counting():
    attribution = {
        "покупка/каталог/promo/24.07": {"revenue": 43915.0, "orders": 45},
        "трафік/блогер надін/ 05.09": {"revenue": 22581.0, "orders": 22},
    }
    names = [
        "Покупка/каталог/promo/24.07",
        "Трафік/блогер Надін/ 05.09",
        "Трафік/ gift theramid/ is clinical/ 10.09",
    ]
    resolved = resolve_campaign_attribution(names, attribution)
    assert resolved[names[0]][1] == 45
    assert resolved[names[1]][1] == 22
    assert resolved[names[2]][1] == 0
    assert sum(row[1] for row in resolved.values()) == 67


def test_build_campaign_attribution_respects_channel():
    order = SimpleNamespace(
        utm_source="facebook.com",
        utm_medium="cpm",
        utm_campaign="promo",
        referer=None,
        order_total=100.0,
    )
    client = SimpleNamespace(
        source_raw=None,
        source_category=None,
        source_utm_source=None,
        source_utm_medium=None,
        source_utm_campaign=None,
        source_referrer=None,
    )
    attr = build_campaign_attribution([(order, client)], channel=CHANNEL_META_ADS)
    assert attr["promo"]["orders"] == 1


def test_unassigned_totals():
    attribution = {
        "promo": {"revenue": 100.0, "orders": 10},
        "other-camp": {"revenue": 50.0, "orders": 5},
    }
    resolved = {
        "Ads A": (100.0, 10, True, "promo"),
        "Ads B": (0.0, 0, False, None),
    }
    orders, revenue = attribution_unassigned_totals(attribution, resolved)
    assert orders == 5
    assert revenue == 50.0
