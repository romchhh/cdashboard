from web.ad_analytics import format_compare_range_display, resolve_ad_period
from datetime import datetime


def test_compare_prev_range_for_three_days():
    _, _, _, _, meta = resolve_ad_period("2026-10-01", "2026-10-03", compare="prev")
    assert meta["prev_from_iso"] == "2026-09-28"
    assert meta["prev_to_iso"] == "2026-09-30"
    assert meta["compare_range_display"] == "28.09.2026 — 30.09.2026"
    assert meta["compare_range_display"] in meta["compare_label"]
    assert meta["compare_kind"] == "vs попередній період"


def test_compare_yoy_range():
    _, _, _, _, meta = resolve_ad_period("2026-10-01", "2026-10-03", compare="yoy")
    assert meta["prev_from_iso"] == "2025-10-01"
    assert meta["prev_to_iso"] == "2025-10-03"
    assert meta["compare_range_display"] == "01.10.2025 — 03.10.2025"
    assert meta["compare_kind"] == "vs аналогічний період минулого року"


def test_format_compare_range_display_uses_dates_only():
    d = format_compare_range_display(
        datetime(2026, 9, 28, 23, 59, 59),
        datetime(2026, 9, 30, 0, 0, 0),
    )
    assert d == "28.09.2026 — 30.09.2026"
