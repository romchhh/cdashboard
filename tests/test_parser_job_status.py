"""Статус фонового job і нічна ротація мають бути стійкими до падінь."""

from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from web import parser_service as ps


def _paths(tmp_path, monkeypatch):
    monkeypatch.setattr(ps.store, "DB_PATH", tmp_path / "parser.db")
    monkeypatch.setattr(ps.store, "JSON_DIR", tmp_path / "no-json")


def test_foreign_error_hidden_on_other_brand(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    ps.store.save_job(
        {
            "state": "error",
            "brand": "Smart4derma",
            "error": "Фоновий процес зупинився. Запустіть моніторинг ще раз.",
            "updated_epoch": time.time(),
            "pid": 1,
        }
    )
    monkeypatch.setattr(ps, "_REPORT_STATUS", {"state": "idle"})
    status = ps.report_status_for_brand("Abril et Nature")
    assert status.get("state") == "idle"
    assert not status.get("error")


def test_same_brand_error_visible(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    ps.store.save_job(
        {
            "state": "error",
            "brand": "Abril et Nature",
            "error": "Фоновий процес зупинився. Запустіть моніторинг ще раз.",
            "updated_epoch": time.time(),
            "pid": 1,
        }
    )
    monkeypatch.setattr(ps, "_REPORT_STATUS", {"state": "idle"})
    status = ps.report_status_for_brand("Abril et Nature")
    assert status.get("state") == "error"
    assert "Фоновий процес" in (status.get("error") or "")


def test_dead_running_job_persisted(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    ps.store.save_job(
        {
            "state": "running",
            "brand": "Smart4derma",
            "pid": 99999999,
            "updated_epoch": time.time() - 60,
        }
    )
    monkeypatch.setattr(ps, "_pid_alive", lambda _pid: False)
    resolved = ps._resolve_job_payload(ps.store.load_job(), persist=True)
    assert resolved["state"] == "error"
    disk = ps.store.load_job()
    assert disk["state"] == "error"


def test_publish_keeps_total_from_parent(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ps,
        "_REPORT_STATUS",
        {
            "state": "running",
            "brand": "Abril et Nature",
            "total": 81,
            "started_epoch": 100.0,
            "pid": 1,
        },
    )
    monkeypatch.setattr(ps, "_LIVE_ROWS", {})
    ps._publish_status(
        {
            "state": "running",
            "phase": "starting",
            "brand": "Abril et Nature",
            "product": "воркер стартував…",
            "current": 0,
        },
        force=True,
    )
    job = ps.store.load_job()
    assert job["total"] == 81
    assert job["started_epoch"] == 100.0
    assert job["product"] == "воркер стартував…"


def test_rotation_busy_ignores_foreign_pid(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    ps.store.save_job(
        {
            "state": "running",
            "brand": "Smart4derma",
            "pid": 42,
            "updated_epoch": time.time(),
        }
    )
    monkeypatch.setattr(ps, "_REPORT_STATUS", {"state": "idle"})
    monkeypatch.setattr(ps, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(ps, "_parser_cmdline_ours", lambda _pid: False)
    assert ps._rotation_busy() is False


def test_two_lanes_do_not_overwrite(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    ps.store.save_job({"state": "running", "brand": "Atache", "lane": "remote"}, lane="remote")
    ps.store.save_job({"state": "running", "brand": "Academie", "lane": "local"}, lane="local")
    brands = {job["brand"] for job in ps.store.load_all_jobs()}
    assert brands == {"Atache", "Academie"}
    assert ps.store.load_job("remote")["brand"] == "Atache"
    assert ps.store.load_job("local")["brand"] == "Academie"


def test_pick_lane_uses_local_when_remote_is_busy(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    ps.store.save_job(
        {
            "state": "running",
            "brand": "Atache",
            "lane": "remote",
            "pid": 7,
            "updated_epoch": time.time(),
        },
        lane="remote",
    )
    monkeypatch.setattr(ps, "_parser_process_alive", lambda _pid: True)
    monkeypatch.setattr(
        "web.parser_worker_client.worker_reachable",
        lambda url, timeout=None: (True, "ok"),
    )
    lane, why = ps._pick_lane()
    assert lane == "local"
    assert why == ""


def test_stop_report_marks_job_cancelled(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    ps.store.save_job(
        {
            "state": "running",
            "brand": "Atache",
            "pid": 4242,
            "phase": "retry",
            "current": 150,
            "total": 1771,
            "updated_epoch": time.time(),
        }
    )
    monkeypatch.setattr(ps, "_kill_parser_process", lambda _pid: None)
    monkeypatch.setattr(ps, "_load_rotation", lambda: {"current": "Atache", "pending": []})
    saved = {}
    monkeypatch.setattr(ps, "_save_rotation", lambda state: saved.update(state))
    monkeypatch.setattr(ps, "append_parse_event", lambda **_kw: None)
    status, code = ps.stop_report(brand="Atache")
    assert code == 200
    assert status["state"] == "cancelled"
    assert ps.store.load_job()["state"] == "cancelled"
    assert saved.get("current") is None


def test_requeue_failed_night_brand_once(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    events = []
    monkeypatch.setattr(ps, "append_parse_event", lambda **kw: events.append(kw))
    state = {"current": "Smart4derma", "pending": ["Other"], "run_retries": {}}
    job = {"state": "error", "brand": "Smart4derma", "error": "boom"}
    updated = ps._requeue_failed_night_brand(state, job)
    assert updated["pending"][0] == "Smart4derma"
    assert updated["run_retries"]["Smart4derma"] == 1
    # другий раз — без повторного додавання
    again = ps._requeue_failed_night_brand(updated, job)
    assert again["pending"].count("Smart4derma") == 1


def test_start_retry_puts_brand_back(tmp_path, monkeypatch):
    state = {"pending": ["B"], "start_retries": {}}
    state, again = ps._note_start_retry(state, "Abril", max_retries=2)
    assert again is True
    assert state["pending"][-1] == "Abril"
    assert state["start_retries"]["Abril"] == 1
    state, again = ps._note_start_retry(state, "Abril", max_retries=2)
    assert again is True
    assert state["start_retries"]["Abril"] == 2
    state, again = ps._note_start_retry(state, "Abril", max_retries=2)
    assert again is False


def test_brand_from_slug_does_not_seed(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ps,
        "load_catalog",
        lambda force=False: [{"brand": "Abril et Nature", "id": 1}],
    )
    calls = {"n": 0}
    monkeypatch.setattr(ps, "_seed_brand_status_from_report", lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))
    name = ps.brand_from_slug("abril-et-nature")
    assert name == "Abril et Nature"
    assert calls["n"] == 0


def test_rotation_retries_persist_roundtrip(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    ps.store.save_rotation(
        {
            "built_for": "2026-10-15",
            "day": 2,
            "current": "Abril et Nature",
            "pending": ["Sesderma"],
            "done": [],
            "skipped": [],
            "start_retries": {"Abril et Nature": 1},
            "run_retries": {"Smart4derma": 1},
        }
    )
    loaded = ps.store.load_rotation()
    assert loaded["start_retries"]["Abril et Nature"] == 1
    assert loaded["run_retries"]["Smart4derma"] == 1
    assert loaded["pending"] == ["Sesderma"]
    assert loaded["current"] == "Abril et Nature"


def test_status_heartbeat_skips_live_rewrite(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    live_calls = {"n": 0}
    job_calls = {"n": 0}
    monkeypatch.setattr(ps, "_LIVE_ROWS", {})
    monkeypatch.setattr(ps, "_REPORT_STATUS", {"state": "running", "total": 10, "started_epoch": 1.0})
    monkeypatch.setattr(ps, "_LAST_FLUSH", 0.0)
    monkeypatch.setattr(ps, "_LAST_LIVE_FLUSH", 0.0)
    monkeypatch.setattr(ps.store, "save_job", lambda *_a, **_k: job_calls.__setitem__("n", job_calls["n"] + 1))
    monkeypatch.setattr(ps.store, "save_live_rows", lambda *_a, **_k: live_calls.__setitem__("n", live_calls["n"] + 1))

    ps._publish_status(
        {"state": "running", "phase": "starting", "product": "воркер стартував…", "current": 0},
        force=False,
    )
    assert job_calls["n"] == 1
    assert live_calls["n"] == 0

    ps._publish_status(
        {"state": "running", "phase": "products", "current": 1, "total": 10},
        row={"id": 1, "brand": "Abril", "partial": False, "shops": {}},
        force=False,
    )
    assert live_calls["n"] == 1


def test_advance_retries_transient_start_failure(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    night = datetime(2026, 10, 15, 4, 0, tzinfo=ZoneInfo("Europe/Kiev"))
    monkeypatch.setattr(ps, "_kyiv_now", lambda: night)
    monkeypatch.setattr(ps, "_enabled_brand_names", lambda: ["Abril et Nature", "Sesderma"])
    monkeypatch.setattr(ps, "_rotation_busy", lambda: False)
    monkeypatch.setattr(ps, "report_status", lambda: {"state": "idle"})
    monkeypatch.setattr(ps, "append_parse_event", lambda **_kw: None)

    state = {
        "built_for": "2026-10-15",
        "day": 0,
        "pending": ["Abril et Nature"],
        "current": None,
        "skipped": [],
        "start_retries": {},
        "run_retries": {},
    }
    saved = {}

    def _load():
        return dict(state)

    def _save(payload):
        state.clear()
        state.update(payload)
        saved["last"] = dict(payload)

    calls = {"n": 0}

    def _start(brand=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"state": "error", "error": "boot fail", "brand": brand}, 500
        return {"state": "running", "brand": brand, "pid": 1}, 200

    monkeypatch.setattr(ps, "_load_rotation", _load)
    monkeypatch.setattr(ps, "_save_rotation", _save)
    monkeypatch.setattr(ps, "start_report", _start)
    monkeypatch.setattr(ps, "plan_rotation_update", lambda *_a, **_k: None)

    result = ps.advance_parser_rotation()
    assert result["action"] == "started"
    assert result["brand"] == "Abril et Nature"
    assert calls["n"] == 2
    assert state["start_retries"].get("Abril et Nature") == 1
