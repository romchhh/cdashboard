"""Тести ліміту ФОП, календарного року і вікон виписки Моно."""

from datetime import datetime
from unittest.mock import patch

from bot.services.fop_filter import fop_surname
from bot.services.period_range import (
    days_since_year_start,
    period_dates,
    previous_tax_quarter,
    tax_quarter,
    tax_quarter_end,
    tax_quarter_start,
)
from mono.api import statement_time_windows


def test_fop_surname_groups_kryvoruchko():
    assert fop_surname("Криворучко Альона") == fop_surname("Криворучко")


def test_year_period_is_calendar_ytd():
    with patch("bot.services.period_range.now_kyiv", return_value=datetime(2026, 9, 15, 12, 0, 0)):
        start, end, name = period_dates("year")
    assert start.date().isoformat() == "2026-01-01"
    assert end.date().isoformat() == "2026-09-15"
    assert "календарний 2026" in name


def test_quarter_is_ukrainian_tax_quarter():
    """15.09 = Q3 (01.07–30.09), не «мінус 3 місяці» (15.06–15.09)."""
    with patch("bot.services.period_range.now_kyiv", return_value=datetime(2026, 9, 15, 12, 0, 0)):
        start, end, name = period_dates("quarter")
    assert start.date().isoformat() == "2026-07-01"
    assert end.date().isoformat() == "2026-09-15"
    assert "III квартал (9 місяців) 2026" in name
    assert "липень — вересень" in name

    with patch("bot.services.period_range.now_kyiv", return_value=datetime(2026, 1, 10, 9, 0, 0)):
        start, end, name = period_dates("quarter")
    assert start.date().isoformat() == "2026-01-01"
    assert end.date().isoformat() == "2026-01-10"
    assert "I квартал 2026" in name

    with patch("bot.services.period_range.now_kyiv", return_value=datetime(2026, 4, 1, 9, 0, 0)):
        start, _, name = period_dates("quarter")
    assert start.date().isoformat() == "2026-04-01"
    assert "II квартал (півріччя) 2026" in name

    with patch("bot.services.period_range.now_kyiv", return_value=datetime(2026, 12, 31, 18, 0, 0)):
        start, end, name = period_dates("quarter")
    assert start.date().isoformat() == "2026-10-01"
    assert end.date().isoformat() == "2026-12-31"
    assert "IV квартал (рік) 2026" in name


def test_tax_quarter_boundaries_and_previous():
    assert tax_quarter(datetime(2026, 3, 31)) == 1
    assert tax_quarter(datetime(2026, 4, 1)) == 2
    assert tax_quarter_start(2026, 3).date().isoformat() == "2026-07-01"
    assert tax_quarter_end(2026, 3).date().isoformat() == "2026-09-30"
    assert previous_tax_quarter(2026, 1) == (2025, 4)
    assert previous_tax_quarter(2026, 3) == (2026, 2)


def test_days_since_year_start():
    with patch("bot.services.period_range.now_kyiv", return_value=datetime(2026, 1, 1, 10, 0, 0)):
        assert days_since_year_start() == 1
    with patch("bot.services.period_range.now_kyiv", return_value=datetime(2026, 1, 31, 10, 0, 0)):
        assert days_since_year_start() == 31


def test_mono_epoch_range_ytd_starts_jan1():
    from zoneinfo import ZoneInfo

    from bot.services.period_range import mono_epoch_range

    kyiv = ZoneInfo("Europe/Kyiv")
    with patch("bot.services.period_range.now_kyiv", return_value=datetime(2026, 9, 15, 12, 0, 0)):
        from_ts, to_ts = mono_epoch_range(ytd=True)
    start = datetime.fromtimestamp(from_ts, tz=kyiv)
    end = datetime.fromtimestamp(to_ts, tz=kyiv)
    assert start.year == 2026 and start.month == 1 and start.day == 1
    assert end.year == 2026 and end.month == 9 and end.day == 15


def test_mono_statement_windows_chunk_31_days():
    start = 1_700_000_000
    end = start + 90 * 24 * 60 * 60
    windows = statement_time_windows(start, end)
    assert len(windows) >= 3
    for win_from, win_to in windows:
        assert win_to - win_from <= 31 * 24 * 60 * 60
    assert windows[0][0] == start
    assert windows[-1][1] == end


def test_overlimit_math():
    np_sum = 1_970_792
    mono_stmt = 5_267_630
    limit = 7_211_598
    assert np_sum + mono_stmt - limit == 26_824


def test_sum_mono_credits_aligned_skips_cash_in_db():
    from bot.database.models import Transaction, TransactionStatus, TransactionType
    from bot.services.fop_income_limit import mono_sync_missing_and_drift, sum_mono_credits_aligned

    skip = {99}
    tx_live = [{"id": "a", "amount": 20040}, {"id": "b", "amount": 10000}]
    income = Transaction(
        external_id="mono_a",
        amount=200.40,
        transaction_type=TransactionType.INCOME,
        status=TransactionStatus.CONFIRMED,
        category_id=1,
    )
    cash = Transaction(
        external_id="mono_b",
        amount=100.0,
        transaction_type=TransactionType.INCOME,
        status=TransactionStatus.CONFIRMED,
        category_id=99,
    )
    by_ext = {"mono_a": income, "mono_b": cash}
    aligned = sum_mono_credits_aligned(tx_live, by_ext, skip)
    assert aligned == 200.40
    missing, drift = mono_sync_missing_and_drift(tx_live, by_ext, skip)
    assert missing == 0.0
    assert drift == 0.0


def test_mono_sync_missing_counts_only_absent_rows():
    from bot.database.models import Transaction, TransactionStatus, TransactionType
    from bot.services.fop_income_limit import mono_sync_missing_and_drift

    skip: set[int] = set()
    tx_live = [{"id": "x", "amount": 50000}]
    missing, drift = mono_sync_missing_and_drift(tx_live, {}, skip)
    assert missing == 500.0
    assert drift == 0.0

    ok = Transaction(
        external_id="mono_x",
        amount=500.0,
        transaction_type=TransactionType.INCOME,
        status=TransactionStatus.CONFIRMED,
        category_id=None,
    )
    missing2, drift2 = mono_sync_missing_and_drift(tx_live, {"mono_x": ok}, skip)
    assert missing2 == 0.0
    assert drift2 == 0.0


def test_summarize_fop_limits_total_remaining():
    from bot.services.fop_income_limit import summarize_fop_limits

    rows = [
        {"remaining": 100_000.0},
        {"remaining": 50_000.0},
        {"remaining": -10_000.0},
    ]
    summary = summarize_fop_limits(rows)
    assert summary["total_remaining"] == 140_000.0
    assert summary["total_overlimit"] == 0.0

    over = summarize_fop_limits([{"remaining": -5_000.0}, {"remaining": -2_000.0}])
    assert over["total_remaining"] == -7_000.0
    assert over["total_overlimit"] == 7_000.0


if __name__ == "__main__":
    test_fop_surname_groups_kryvoruchko()
    test_year_period_is_calendar_ytd()
    test_quarter_is_ukrainian_tax_quarter()
    test_tax_quarter_boundaries_and_previous()
    test_days_since_year_start()
    test_mono_statement_windows_chunk_31_days()
    test_overlimit_math()
    test_summarize_fop_limits_total_remaining()
    print("OK")
