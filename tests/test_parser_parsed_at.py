from datetime import datetime
from zoneinfo import ZoneInfo

from web import parser_service as ps

KYIV = ZoneInfo("Europe/Kiev")


def test_parsed_at_timestamp_naive_treated_as_kyiv():
    raw = "2026-03-16T14:30:00"
    ts = ps.parsed_at_timestamp(raw)
    assert ts is not None
    dt = datetime.fromtimestamp(ts, tz=KYIV)
    assert dt.hour == 14 and dt.day == 16


def test_format_parsed_at_empty():
    assert ps.format_parsed_at(None) == "Ще не парсився"
    assert ps.format_parsed_at("") == "Ще не парсився"