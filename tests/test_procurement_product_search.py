"""Пошук товарів у каталозі закупівель."""

from types import SimpleNamespace

from web.procurement_table_filters import (
    catalog_row_search_text,
    filter_catalog_by_product_query,
)
from web.routes.products import _filter_sku_catalog_by_product_query


def _row(name: str, option_name: str = ""):
    product = SimpleNamespace(
        name=name,
        model="",
        sku="",
        brand_name="TestBrand",
        stock_qty=1,
        selling_price_catalog=100.0,
    )
    option = None
    if option_name:
        option = SimpleNamespace(
            option_name="Color",
            option_value_name=option_name,
            option_model="",
            dilovod_model="",
            stock_qty=1,
            selling_price_catalog=100.0,
        )
    analytics = SimpleNamespace(
        status="fast_mover",
        procurement_action="buy_normal",
        days_without_sales=0,
        avg_daily_sales=1.0,
        avg_daily_sales_7d=1.0,
        turnover_days=10.0,
        frozen_capital=50.0,
        recommended_purchase_1d=0,
        recommended_purchase_7d=0,
        recommended_purchase_14d=0,
        recommended_purchase_30d=0,
        surplus_qty=0,
    )
    return (product, option, analytics)


def test_filter_by_product_name():
    items = [_row("Vitamin C Serum"), _row("Night Cream")]
    out = _filter_sku_catalog_by_product_query(items, "serum", 7, 7)
    assert len(out) == 1
    assert out[0][0].name == "Vitamin C Serum"


def test_filter_matches_option_value():
    items = [_row("Base", option_name="50ml")]
    assert len(_filter_sku_catalog_by_product_query(items, "50ml", 7, 7)) == 1
    assert len(_filter_sku_catalog_by_product_query(items, "100ml", 7, 7)) == 0


def test_filter_by_sku_and_brand_in_haystack():
    product = _row("Hidden name")[0]
    product.sku = "ART-999"
    product.brand_name = "RareBrand"
    items = [(product, None, _row("x")[2])]
    assert len(filter_catalog_by_product_query(items, "art-999", 7, 7)) == 1
    assert len(filter_catalog_by_product_query(items, "rarebrand", 7, 7)) == 1
    hay = catalog_row_search_text(product, None)
    assert "art-999" in hay
    assert "rarebrand" in hay
