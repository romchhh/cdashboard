"""Розклад парсера: бренди діляться на 14 днів і не губляться при переносі черги."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from web.parser_service import (
    CYCLE_DAYS,
    KYIV_TZ,
    brands_for_rotation_day,
    in_rotation_night,
    is_weekend_kyiv,
    next_rotation_info,
    parser_auto_rotation_allowed,
    plan_rotation_update,
)


def test_brands_split_evenly_across_cycle():
    names = [f"Brand {index:03d}" for index in range(25)]
    seen = []
    for day in range(CYCLE_DAYS):
        chunk = brands_for_rotation_day(names, day)
        assert chunk
        assert len(chunk) in (1, 2)
        seen.extend(chunk)
    assert sorted(seen, key=str.lower) == sorted(names, key=str.lower)
    assert len(seen) == len(set(seen))


def test_same_brand_stays_on_the_same_day():
    names = ["Academie", "Sesderma", "Abril et Nature", "Moroccanoil"]
    first = {day: brands_for_rotation_day(names, day) for day in range(CYCLE_DAYS)}
    again = {day: brands_for_rotation_day(names, day) for day in range(CYCLE_DAYS)}
    assert first == again
    assert brands_for_rotation_day(names, 0) == brands_for_rotation_day(names, CYCLE_DAYS)


def test_blank_and_placeholder_brands_are_skipped():
    assert brands_for_rotation_day(["Sesderma", "—", "  ", ""], 0) == ["Sesderma"]
    assert brands_for_rotation_day(["Sesderma", "—"], 1) == []


def test_carry_over_merges_pending_with_new_day_slot():
    today = date(2026, 10, 12)
    names = ["Academie", "Sesderma", "Abril"]
    state = {"built_for": "2026-10-11", "pending": ["Academie"], "day": 1}
    planned = plan_rotation_update(state, today, names)
    assert planned is not None
    assert planned["built_for"] == "2026-10-12"
    assert planned["pending"][0] == "Academie"
    today_slot = brands_for_rotation_day(names, today.toordinal() % CYCLE_DAYS)
    for brand in today_slot:
        assert brand in planned["pending"]


def test_same_day_pending_is_not_rebuilt():
    today = date(2026, 10, 12)
    state = {"built_for": "2026-10-12", "pending": ["Academie"], "day": 1}
    assert plan_rotation_update(state, today, ["Academie", "Sesderma"]) is None


def test_parse_log_keeps_shop_errors(tmp_path, monkeypatch):
    import web.parser_service as ps

    monkeypatch.setattr(ps.store, "DB_PATH", tmp_path / "parser.db")
    monkeypatch.setattr(ps.store, "JSON_DIR", tmp_path / "no-json")
    ps.append_parse_event(
        level="error",
        kind="shop",
        brand="Academie",
        site="Fabo",
        message="«Fabo»: 3 товарів — не відповів за 70 с",
    )
    rows = ps.load_parse_log()
    assert rows[0]["brand"] == "Academie"
    assert rows[0]["site"] == "Fabo"
    assert rows[0]["level"] == "error"
    ps.clear_parse_log()
    assert ps.load_parse_log() == []


def test_new_day_builds_only_that_days_brands():
    today = date(2026, 10, 12)
    names = [f"Brand {index:02d}" for index in range(20)]
    planned = plan_rotation_update({"pending": [], "built_for": "2026-10-11"}, today, names)
    assert planned is not None
    assert planned["built_for"] == "2026-10-12"
    assert planned["pending"] == brands_for_rotation_day(names, today.toordinal() % CYCLE_DAYS)
    assert plan_rotation_update(planned, today, names) is None


def test_rotation_night_window_kyiv():
    night = datetime(2026, 10, 13, 2, 30, tzinfo=KYIV_TZ)
    day = datetime(2026, 10, 13, 14, 0, tzinfo=KYIV_TZ)
    late = datetime(2026, 10, 13, 23, 0, tzinfo=KYIV_TZ)
    assert in_rotation_night(night) is True
    assert in_rotation_night(day) is True
    assert parser_auto_rotation_allowed(day) is True
    assert in_rotation_night(late) is False


def test_weekend_blocks_auto_rotation():
    sat_night = datetime(2026, 10, 17, 2, 0, tzinfo=KYIV_TZ)  # Saturday
    assert is_weekend_kyiv(sat_night) is True
    assert in_rotation_night(sat_night) is True
    assert parser_auto_rotation_allowed(sat_night) is False
    fri_night = datetime(2026, 10, 16, 2, 0, tzinfo=KYIV_TZ)  # Friday
    assert parser_auto_rotation_allowed(fri_night) is True


def test_advance_parser_rotation_skips_weekend(monkeypatch):
    import web.parser_service as ps

    monkeypatch.setattr(
        ps,
        "_kyiv_now",
        lambda: datetime(2026, 10, 18, 2, 0, tzinfo=ZoneInfo("Europe/Kiev")),
    )
    monkeypatch.setattr(ps, "_load_rotation", lambda: {"pending": ["Sesderma"], "current": None})
    monkeypatch.setattr(ps, "_rotation_busy", lambda: False)
    result = ps.advance_parser_rotation()
    assert result["action"] == "weekend"


def test_next_rotation_info_daytime_points_to_tonight(monkeypatch):
    import web.parser_service as ps

    monkeypatch.setattr(ps, "_enabled_brand_names", lambda: ["Academie", "Sesderma", "Abril"])
    monkeypatch.setattr(ps, "_rotation_busy", lambda: False)
    now = datetime(2026, 10, 13, 15, 0, tzinfo=KYIV_TZ)
    info = next_rotation_info(
        now=now,
        state={"pending": [], "built_for": "2026-10-13"},
        names=["Academie", "Sesderma", "Abril"],
    )
    assert info["in_night"] is True
    assert info["auto_rotation_allowed"] is True
    assert info["next"]["kind"] == "next_night"
    assert "01:00–22:00" in info["night_label"]
    assert info["next"]["at_label"]


def test_advance_parser_rotation_skips_during_day(monkeypatch):
    import web.parser_service as ps

    monkeypatch.setattr(
        ps,
        "_kyiv_now",
        lambda: datetime(2026, 10, 13, 23, 0, tzinfo=ZoneInfo("Europe/Kiev")),
    )
    monkeypatch.setattr(ps, "_load_rotation", lambda: {"pending": ["Sesderma"], "current": None})
    monkeypatch.setattr(ps, "_rotation_busy", lambda: False)
    result = ps.advance_parser_rotation()
    assert result["action"] == "daytime"


def test_query_variants_add_compact_brand_and_product_forms():
    from cosmyparser.cosmy_parse import query_variants

    variants = query_variants("Sesderma C-Vit")
    assert "sesdermacvit" in variants
    assert "sesderma cvit" in variants

    variants = query_variants("MOROCCANOIL Moisture Repair")
    assert "moroccanoilmoisturerepair" in variants
    assert "moroccanoilrepair" in variants
