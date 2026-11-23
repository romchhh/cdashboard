"""Стан ручних синхронізацій для сторінки /sync."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "databases" / "sync_jobs.json"
_LOCK = threading.Lock()

JOBS = ("transactions", "products", "analytics", "clients", "ads")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _blank(job: str) -> dict:
    return {
        "id": job,
        "state": "idle",
        "message": "",
        "error": "",
        "done": 0,
        "total": 0,
        "updated_at": "",
    }


def _load() -> dict:
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    for job in JOBS:
        row = data.get(job)
        if not isinstance(row, dict):
            data[job] = _blank(job)
            continue
        merged = _blank(job)
        merged.update(row)
        merged["id"] = job
        data[job] = merged
    return data


def _save(data: dict) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_PATH)


def snapshot() -> dict:
    with _LOCK:
        return {"jobs": _load()}


def start(job: str, message: str) -> None:
    _set(job, state="running", message=message, error="", done=0, total=0)


def update(job: str, message: str, done: int = 0, total: int = 0) -> None:
    _set(job, state="running", message=message, done=int(done or 0), total=int(total or 0))


def finish(job: str, message: str) -> None:
    _set(job, state="ok", message=message, error="")


def fail(job: str, error: str) -> None:
    text = str(error).strip() or "невідома помилка"
    _set(job, state="error", error=text[:800])


def _set(job: str, **fields) -> None:
    with _LOCK:
        data = _load()
        row = data.get(job) or _blank(job)
        row.update(fields)
        row["id"] = job
        row["updated_at"] = _now()
        data[job] = row
        _save(data)
