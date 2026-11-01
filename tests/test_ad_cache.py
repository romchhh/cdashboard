"""Кеш рекламних API: stale-first і автоочищення."""

import json
from datetime import timedelta
from pathlib import Path

from mono.api import now_kyiv
from web.integrations import ad_cache as mod


def test_load_cached_prefers_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "CACHE_DIR", Path(tmp_path))
    key = "gads_v4_2026-09-01_2026-09-15"
    path = tmp_path / f"{key}.json"
    path.write_text(
        json.dumps({
            "cached_at": (now_kyiv() - timedelta(hours=48)).isoformat(),
            "payload": {"spend": 10, "configured": True},
        }),
        encoding="utf-8",
    )
    assert mod.cache_get(key) is None
    loaded = mod.load_cached_ad_payload(key)
    assert loaded["spend"] == 10


def test_purge_expired_ad_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "CACHE_DIR", Path(tmp_path))
    old = tmp_path / "old.json"
    old.write_text(
        json.dumps({"cached_at": (now_kyiv() - timedelta(days=30)).isoformat(), "payload": {}}),
        encoding="utf-8",
    )
    fresh = tmp_path / "fresh.json"
    fresh.write_text(
        json.dumps({"cached_at": now_kyiv().isoformat(), "payload": {}}),
        encoding="utf-8",
    )
    removed = mod.purge_expired_ad_cache(max_age_days=14)
    assert removed == 1
    assert not old.exists()
    assert fresh.exists()


def test_iter_periods_includes_compare_window():
    from datetime import datetime
    now = datetime(2026, 9, 15, 12, 0, 0)
    labels = [p[2] for p in mod.iter_ad_api_cache_periods(now)]
    assert "поточний місяць" in labels
    assert "порівняння попереднього періоду" in labels
    assert "YoY поточний місяць" in labels


def test_load_cached_period_uses_yesterday_file(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "CACHE_DIR", Path(tmp_path))
    start = now_kyiv().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    today = now_kyiv()
    yesterday = today - timedelta(days=1)
    key = f"gads_v4_{start.date()}_{yesterday.date()}"
    path = tmp_path / f"{key}.json"
    path.write_text(
        json.dumps({"cached_at": now_kyiv().isoformat(), "payload": {"spend": 42}}),
        encoding="utf-8",
    )
    loaded = mod.load_cached_period_payload("gads_v4", start, today)
    assert loaded["spend"] == 42


def test_load_cached_period_rejects_much_shorter_end(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "CACHE_DIR", Path(tmp_path))
    start = now_kyiv().replace(month=10, day=1, hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(day=7)
    short_key = f"gsc2_{start.date()}_{start.replace(day=3).date()}"
    path = tmp_path / f"{short_key}.json"
    path.write_text(
        json.dumps({"cached_at": now_kyiv().isoformat(), "payload": {"clicks": 1, "daily": []}}),
        encoding="utf-8",
    )
    assert mod.load_cached_period_payload("gsc2", start, end) is None
