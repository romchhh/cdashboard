"""Тести розподілу CRM-продажів для закупівель."""

from bot.services.procurement_crm_sales import (
    SALES_PERIODS,
    _distribute_qty,
    _metrics_from_period_qty,
)


def test_distribute_qty_equal_when_no_weights():
    assert _distribute_qty(10, [0, 0, 0]) == [4, 3, 3]


def test_distribute_qty_proportional():
    assert _distribute_qty(10, [1, 3]) == [2, 8]


def test_distribute_qty_zero_total():
    assert _distribute_qty(0, [5, 5]) == [0, 0]


def test_metrics_from_period_qty():
    metrics = _metrics_from_period_qty({30: 15, 7: 7})
    assert metrics["sales_qty_30d"] == 15
    assert metrics["sales_avg_per_day_30d"] == 0.5
    assert metrics["sales_qty_7d"] == 7
    assert metrics["sales_avg_per_day_7d"] == 1.0
    for days in SALES_PERIODS:
        assert f"sales_qty_{days}d" in metrics
        assert f"sales_avg_per_day_{days}d" in metrics
