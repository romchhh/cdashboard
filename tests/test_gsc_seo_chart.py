from datetime import datetime

from web.ad_analytics import build_gsc_crm_daily_chart, gsc_freshness_note_ua


def test_build_gsc_crm_daily_chart_fills_calendar_days():
    start = datetime(2026, 10, 1)
    end = datetime(2026, 10, 3, 23, 59, 59)
    gsc_daily = [{"date": "2026-10-01", "clicks": 10}]
    crm = {"2026-10-03": {"revenue": 500.0, "orders": 2}}
    chart = build_gsc_crm_daily_chart(start, end, gsc_daily, crm)
    assert [c["date"] for c in chart] == ["2026-10-01", "2026-10-02", "2026-10-03"]
    assert chart[0]["clicks"] == 10
    assert chart[1]["clicks"] == 0
    assert chart[2]["revenue"] == 500.0


def test_gsc_freshness_note_when_through_before_period_end():
    gsc = {
        "daily_through": "2026-10-04",
        "gsc_site_urls": ["https://cosmy.com.ua", "https://cosmy.ua"],
    }
    note = gsc_freshness_note_ua(gsc, datetime(2026, 10, 7))
    assert note is not None
    assert "2026-10-04" in note
    assert "cosmy.ua" in note
