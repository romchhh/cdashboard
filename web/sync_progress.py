"""Стан ручних синхронізацій для сторінки /sync."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_PATH = Path(__file__).resolve().parents[1] / "databases" / "sync_jobs.json"
_LOCK = threading.Lock()
_TASKS: set[asyncio.Task] = set()

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


def is_running(job: str) -> bool:
    with _LOCK:
        row = _load().get(job) or {}
        return row.get("state") == "running"


def launch(job: str, message: str, factory) -> bool:
    """Тримає фонову задачу, щоб її не прибрав збирач сміття, і не стартує другу копію."""
    if is_running(job):
        return False
    start(job, message)

    async def _guard() -> None:
        try:
            await factory()
        except asyncio.CancelledError:
            fail(job, "Зупинено перезапуском. Запустіть ще раз.")
            raise
        except Exception as exc:
            logger.exception("Синхронізація %s впала", job)
            fail(job, exc)

    task = asyncio.create_task(_guard(), name=f"sync-{job}")
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return True


def start(job: str, message: str) -> None:
    _set(job, state="running", message=message, error="", done=0, total=0)


def update(job: str, message: str, done: int = 0, total: int = 0) -> None:
    _set(job, state="running", message=message, done=int(done or 0), total=int(total or 0))


def finish(job: str, message: str) -> None:
    _set(job, state="ok", message=message, error="")


def mark_interrupted(reason: str = "Процес перезапущено під час цього кроку. Запустіть ще раз.") -> list[str]:
    """Завислі running-стани після systemctl restart не є живою задачею."""
    stopped: list[str] = []
    with _LOCK:
        data = _load()
        for job, row in data.items():
            if row.get("state") != "running":
                continue
            row["state"] = "error"
            row["error"] = reason
            row["updated_at"] = _now()
            data[job] = row
            stopped.append(job)
        if stopped:
            _save(data)
    return stopped


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
