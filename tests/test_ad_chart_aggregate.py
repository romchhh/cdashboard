"""Тести агрегації графіка реклами."""

from web.ad_analytics import aggregate_chart_series


def test_aggregate_chart_series_month():
    daily = [
        {"date": "2026-08-01", "revenue": 100.0, "orders": 1, "spend": 10.0},
        {"date": "2026-08-15", "revenue": 200.0, "orders": 2, "spend": 20.0},
        {"date": "2026-09-01", "revenue": 50.0, "orders": 1, "spend": 5.0},
    ]
    out = aggregate_chart_series(daily, "month")
    assert len(out) == 2
    assert out[0]["date"] == "2026-08"
    assert out[0]["revenue"] == 300.0
    assert out[0]["orders"] == 3
    assert out[0]["spend"] == 30.0
    assert out[1]["date"] == "2026-09"


def test_aggregate_chart_series_quarter():
    daily = [
        {"date": "2026-01-10", "revenue": 10.0, "orders": 1, "spend": 2.0},
        {"date": "2026-03-20", "revenue": 20.0, "orders": 1, "spend": 3.0},
        {"date": "2026-04-01", "revenue": 5.0, "orders": 1, "spend": 1.0},
    ]
    out = aggregate_chart_series(daily, "quarter")
    assert [p["date"] for p in out] == ["2026-Q1", "2026-Q2"]
    assert out[0]["revenue"] == 30.0
    assert out[0]["spend"] == 5.0


def test_aggregate_chart_series_day_passthrough():
    daily = [{"date": "2026-08-01", "revenue": 1.0, "orders": 1, "spend": 1.0}]
    assert aggregate_chart_series(daily, "day") == daily
