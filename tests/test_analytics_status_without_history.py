"""Статус після імпорту, який стирає історію продажів."""

import asyncio
from types import SimpleNamespace

from bot.services.analytics import ProductAnalyticsService


def test_bestsellers_stay_fast_when_history_was_wiped():
    service = ProductAnalyticsService()
    item = SimpleNamespace(
        stock_qty=8,
        sales_qty_30d=12,
        sales_qty_60d=20,
        sales_qty_90d=40,
        last_sale_date=None,
    )
    status = asyncio.run(
        service._determine_product_status(item, avg_daily_sales=0.4, turnover_days=20, history=[])
    )
    assert status == "fast_mover"


def test_no_sales_and_no_history_is_not_a_fast_mover():
    service = ProductAnalyticsService()
    item = SimpleNamespace(
        stock_qty=4,
        sales_qty_30d=0,
        sales_qty_60d=0,
        sales_qty_90d=0,
        last_sale_date=None,
    )
    status = asyncio.run(
        service._determine_product_status(item, avg_daily_sales=0, turnover_days=None, history=[])
    )
    assert status == "dead_stock"
