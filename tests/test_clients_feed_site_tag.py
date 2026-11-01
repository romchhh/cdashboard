"""Мітка «Сайт» (synthetic) при синку клієнтів з фіду."""

from bot.services.clients_feed_channels import augment_customer_feed_tags


def test_site_only_client_no_feed_tags():
    tags = augment_customer_feed_tags(5, [])
    assert len(tags) == 1
    assert tags[0]["code"] == "site"
    assert tags[0]["label"] == "Сайт"
    assert tags[0]["orders_count"] == 5


def test_residual_orders_become_site():
    feed = [
        {"code": "instagram", "label": "Instagram", "orders_count": 7},
        {"code": "xlash", "label": "Xlash", "orders_count": 2},
    ]
    tags = augment_customer_feed_tags(12, feed)
    by_code = {t["code"]: t for t in tags}
    assert by_code["instagram"]["orders_count"] == 7
    assert by_code["xlash"]["orders_count"] == 2
    assert by_code["site"]["orders_count"] == 3


def test_cosmy_alias_merged_to_site():
    feed = [{"code": "cosmy", "label": "Cosmy shop", "orders_count": 4}]
    tags = augment_customer_feed_tags(4, feed)
    assert len(tags) == 1
    assert tags[0]["code"] == "site"
    assert tags[0]["label"] == "Сайт"


def test_no_residual_when_tag_sum_covers_total():
    feed = [{"code": "instagram", "label": "Instagram", "orders_count": 10}]
    tags = augment_customer_feed_tags(10, feed)
    assert {t["code"] for t in tags} == {"instagram"}


def test_skip_residual_when_tag_counts_missing():
    feed = [{"code": "instagram", "label": "Instagram", "orders_count": None}]
    tags = augment_customer_feed_tags(10, feed)
    assert len(tags) == 1
    assert tags[0]["code"] == "instagram"
