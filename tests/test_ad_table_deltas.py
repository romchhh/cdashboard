"""Порівняння рядків таблиць реклами з попереднім періодом."""

from web.ad_reporting import (
    _attach_brand_deltas,
    _attach_metric_deltas,
    _build_channel_top_brands,
    _crm_brands_with_compare,
    _seo_queries_with_position_change,
    _source_table,
)


def test_attach_metric_deltas_orders_percent():
    current = [{"name": "Brand Search", "crm_orders": 12, "spend": 200}]
    previous = [{"name": "Brand Search", "crm_orders": 8, "spend": 100}]
    _attach_metric_deltas(
        current,
        previous,
        key="name",
        metrics=(("crm_orders", "delta_orders_pct"), ("spend", "delta_spend_pct")),
    )
    assert current[0]["delta_orders_pct"] == 50.0
    assert current[0]["delta_spend_pct"] == 100.0


def test_attach_metric_deltas_new_row_is_100_pct():
    current = [{"name": "New Camp", "purchases": 4}]
    _attach_metric_deltas(
        current,
        [],
        key="name",
        metrics=(("purchases", "delta_orders_pct"),),
    )
    assert current[0]["delta_orders_pct"] == 100.0


def test_attach_brand_deltas():
    rows = [{"brand": "CeraVe", "orders": 20, "revenue": 15000}]
    prev = {"CeraVe": {"orders": 10, "revenue": 10000}}
    _attach_brand_deltas(rows, prev)
    assert rows[0]["delta_orders_pct"] == 100.0
    assert rows[0]["delta_revenue_pct"] == 50.0


def test_channel_top_brands_include_order_delta():
    current = {"La Roche": {"revenue": 2000, "orders": 8, "qty": 8}}
    previous = {"La Roche": {"revenue": 1000, "orders": 4, "qty": 4}}
    rows = _build_channel_top_brands(current, previous, 2000, 1000, 400)
    assert rows[0]["delta_orders_pct"] == 100.0
    assert rows[0]["delta_revenue_pct"] == 100.0


def test_crm_brands_with_compare():
    rows = _crm_brands_with_compare(
        {"Avene": {"revenue": 900, "orders": 9}},
        {"Avene": {"revenue": 600, "orders": 6}},
        900,
    )
    assert rows[0]["delta_orders_pct"] == 50.0
    assert rows[0]["delta_revenue_pct"] == 50.0


def test_source_table_deltas():
    rows = _source_table(
        {"instagram.com": {"revenue": 300, "orders": 6}},
        {"instagram.com": {"revenue": 200, "orders": 4}},
    )
    assert rows[0]["delta_orders_pct"] == 50.0
    assert rows[0]["delta_revenue_pct"] == 50.0


def test_seo_queries_clicks_delta():
    out = _seo_queries_with_position_change(
        [{"key": "cerave", "clicks": 30, "position": 4.0}],
        [{"key": "cerave", "clicks": 20, "position": 5.0}],
    )
    assert out[0]["delta_clicks_pct"] == 50.0
    assert out[0]["position_change"] == 1.0
