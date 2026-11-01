"""Злиття CRM-продажів у історію графіка складу."""

from datetime import date

from bot.services.sales_history import (
    _impute_spurious_zero_snapshots,
    _merge_crm_daily_sales_into_history,
    aggregate_history_granularity,
    resolve_history_granularity,
)


def test_merge_crm_overrides_sales_revenue_only():
    d1 = date(2026, 9, 1)
    d2 = date(2026, 9, 2)
    rows = [
        {"date": d1, "sales": 100, "stock": 5000, "revenue": 999.0, "frozen": 1000.0},
    ]
    crm = {
        d1: {"sales": 42, "revenue": 4200.5},
        d2: {"sales": 10, "revenue": 800.0},
    }
    out = _merge_crm_daily_sales_into_history(rows, crm)
    by = {r["date"]: r for r in out}
    assert by[d1]["sales"] == 42
    assert by[d1]["revenue"] == 4200.5
    assert by[d1]["stock"] == 5000
    assert by[d1]["frozen"] == 1000.0
    assert by[d2]["sales"] == 10
    assert by[d2]["stock"] == 5000
    assert by[d2]["frozen"] == 1000.0
    assert by[d2]["has_warehouse_snapshot"] is False


def test_resolve_granularity_auto():
    assert resolve_history_granularity(90, "auto") == "day"
    assert resolve_history_granularity(180, "auto") == "week"
    assert resolve_history_granularity(90, "week") == "week"


def test_aggregate_week_sums_sales_keeps_last_stock():
    d1 = date(2026, 9, 1)  # Tue
    d2 = date(2026, 9, 2)
    d3 = date(2026, 9, 8)  # next week Mon
    rows = [
        {
            "date": d1,
            "sales": 2,
            "revenue": 100.0,
            "stock": 50,
            "frozen": 0,
            "has_warehouse_snapshot": True,
        },
        {
            "date": d2,
            "sales": 3,
            "revenue": 50.0,
            "stock": 48,
            "frozen": 0,
            "has_warehouse_snapshot": True,
        },
        {
            "date": d3,
            "sales": 10,
            "revenue": 200.0,
            "stock": 40,
            "frozen": 0,
            "has_warehouse_snapshot": True,
        },
    ]
    out = aggregate_history_granularity(rows, "week")
    assert len(out) == 2
    assert out[0]["sales"] == 5
    assert out[0]["revenue"] == 150.0
    assert out[0]["stock"] == 48
    assert out[1]["sales"] == 10


def test_aggregate_week_ignores_crm_only_day_for_stock():
    """Неділя лише з CRM не повинна обнулити залишок тижня."""
    mon = date(2026, 9, 7)
    fri = date(2026, 9, 11)
    sun = date(2026, 9, 13)
    rows = [
        {
            "date": mon,
            "sales": 1,
            "revenue": 10.0,
            "stock": 100,
            "frozen": 5000.0,
            "has_warehouse_snapshot": True,
        },
        {
            "date": fri,
            "sales": 2,
            "revenue": 20.0,
            "stock": 90,
            "frozen": 4500.0,
            "has_warehouse_snapshot": True,
        },
        {
            "date": sun,
            "sales": 5,
            "revenue": 50.0,
            "stock": 90,
            "frozen": 4500.0,
            "has_warehouse_snapshot": False,
        },
    ]
    out = aggregate_history_granularity(rows, "week")
    assert len(out) == 1
    assert out[0]["sales"] == 8
    assert out[0]["stock"] == 90
    assert out[0]["frozen"] == 4500.0


def test_impute_spurious_zero_snapshot_between_nonempty_days():
    d1 = date(2026, 7, 25)
    d2 = date(2026, 7, 26)
    d3 = date(2026, 7, 28)
    rows = [
        {"date": d1, "sales": 0, "revenue": 0.0, "stock": 8000, "frozen": 1_000_000.0, "has_warehouse_snapshot": True},
        {"date": d2, "sales": 0, "revenue": 0.0, "stock": 0, "frozen": 0.0, "has_warehouse_snapshot": True},
        {"date": d3, "sales": 0, "revenue": 0.0, "stock": 7900, "frozen": 990_000.0, "has_warehouse_snapshot": True},
    ]
    out = _impute_spurious_zero_snapshots(rows)
    by = {r["date"]: r for r in out}
    assert by[d2]["stock"] == 8000
    assert by[d2]["frozen"] == 1_000_000.0
