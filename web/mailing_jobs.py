"""Job state for async mass email mailings (progress polling).

Jobs are persisted to disk so progress survives page reload, tab sleep,
and uvicorn worker switches. Checkpoints provide a fallback progress count.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import logging

from config import ROOT_DIR

logger = logging.getLogger(__name__)

_JOB_TTL_SEC = 6 * 3600
# Active job with no heartbeat longer than this is treated as dead (worker crash).
_STALE_ACTIVE_SEC = 25 * 60
# Rarely re-scan checkpoint files: progress is already bumped into the job JSON
# by the sender. Full jsonl scans stall status polls (especially on iPad Safari).
_CHECKPOINT_REFRESH_INTERVAL_SEC = 20.0
# Keep memory counters hot; flush job JSON every N seconds (not every email).
_BUMP_PERSIST_INTERVAL_SEC = 2.0
_JOBS_DIR = Path(ROOT_DIR) / "sessions" / "mailing_jobs"
_lock = threading.Lock()
_jobs: dict[str, "MailingJob"] = {}
_checkpoint_refresh_last: dict[str, float] = {}
_bump_persist_last: dict[str, float] = {}
_ACTIVE_STATUSES = frozenset({"queued", "running"})
_TERMINAL_STATUSES = frozenset({"done", "error", "cancelled"})


@dataclass
class MailingJob:
    id: str
    status: str  # queued | running | done | error | cancelled
    total: int
    sent: int = 0
    failed: int = 0
    subject: str = ""
    message: str = ""
    failures: list[str] = field(default_factory=list)
    created_by: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    campaign_id: int | None = None
    checkpoint_path: str = ""
    cancel_requested: bool = False

    @property
    def processed(self) -> int:
        return int(self.sent) + int(self.failed)

    @property
    def remaining(self) -> int:
        return max(0, int(self.total) - self.processed)

    @property
    def percent(self) -> float:
        if self.total <= 0:
            return 100.0 if self.status in _TERMINAL_STATUSES else 0.0
        return round(min(100.0, (self.processed / self.total) * 100.0), 1)

    def to_dict(self) -> dict[str, Any]:
        active = self.status in _ACTIVE_STATUSES and not self.cancel_requested
        return {
            "job_id": self.id,
            "status": self.status,
            "total": self.total,
            "sent": self.sent,
            "failed": self.failed,
            "processed": self.processed,
            "remaining": self.remaining,
            "percent": self.percent,
            "subject": self.subject,
            "message": self.message,
            "failures": self.failures[:5],
            "campaign_id": self.campaign_id,
            "cancel_requested": bool(self.cancel_requested),
            "can_cancel": active,
            "updated_at": self.updated_at,
            "created_at": self.created_at,
            "done": self.status in _TERMINAL_STATUSES,
        }


def _ensure_jobs_dir() -> Path:
    _JOBS_DIR.mkdir(parents=True, exist_ok=True)
    return _JOBS_DIR


def _job_file(job_id: str) -> Path:
    return _ensure_jobs_dir() / f"{job_id}.json"


def _job_to_payload(job: MailingJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "status": job.status,
        "total": job.total,
        "sent": job.sent,
        "failed": job.failed,
        "subject": job.subject,
        "message": job.message,
        "failures": list(job.failures or [])[:50],
        "created_by": job.created_by,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "finished_at": job.finished_at,
        "campaign_id": job.campaign_id,
        "checkpoint_path": job.checkpoint_path,
        "cancel_requested": bool(job.cancel_requested),
    }


def _job_from_payload(data: dict[str, Any]) -> MailingJob | None:
    job_id = str(data.get("id") or "").strip()
    if not job_id:
        return None
    failures_raw = data.get("failures") or []
    failures = [str(x) for x in failures_raw if str(x).strip()] if isinstance(failures_raw, list) else []
    campaign_id = data.get("campaign_id")
    created_at = float(data.get("created_at") or time.time())
    return MailingJob(
        id=job_id,
        status=str(data.get("status") or "queued"),
        total=max(0, int(data.get("total") or 0)),
        sent=max(0, int(data.get("sent") or 0)),
        failed=max(0, int(data.get("failed") or 0)),
        subject=str(data.get("subject") or ""),
        message=str(data.get("message") or ""),
        failures=failures,
        created_by=str(data.get("created_by") or ""),
        created_at=created_at,
        updated_at=float(data.get("updated_at") or created_at),
        finished_at=float(data["finished_at"]) if data.get("finished_at") else None,
        campaign_id=int(campaign_id) if campaign_id is not None else None,
        checkpoint_path=str(data.get("checkpoint_path") or ""),
        cancel_requested=bool(data.get("cancel_requested")),
    )


def _save_job_unlocked(job: MailingJob) -> None:
    try:
        _job_file(job.id).write_text(
            json.dumps(_job_to_payload(job), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("Failed to persist mailing job %s: %s", job.id, exc)


def _load_job_from_disk_unlocked(job_id: str) -> MailingJob | None:
    path = _job_file(job_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return _job_from_payload(data)


def _get_job_unlocked(job_id: str) -> MailingJob | None:
    jid = str(job_id or "").strip()
    if not jid:
        return None
    job = _jobs.get(jid)
    if job is None:
        job = _load_job_from_disk_unlocked(jid)
        if job is not None:
            _jobs[jid] = job
    return job


def _mark_stale_active_unlocked(now: float | None = None) -> None:
    """Flip abandoned queued/running jobs to error so UI does not revive zombies."""
    ts = now if now is not None else time.time()
    for job in _jobs.values():
        if job.status not in _ACTIVE_STATUSES:
            continue
        heartbeat = float(job.updated_at or job.created_at or 0.0)
        if heartbeat and (ts - heartbeat) < _STALE_ACTIVE_SEC:
            continue
        job.status = "error"
        job.cancel_requested = False
        job.finished_at = ts
        job.updated_at = ts
        job.message = (
            "Розсилку зупинено автоматично: процес давно не оновлював прогрес "
            "(ймовірно сервер перезапускали). Можна запустити нову розсилку."
        )
        _save_job_unlocked(job)


def _purge_old_jobs_unlocked(now: float | None = None) -> None:
    ts = now if now is not None else time.time()
    _mark_stale_active_unlocked(ts)
    stale = [
        jid
        for jid, job in _jobs.items()
        if job.status not in _ACTIVE_STATUSES
        and (job.finished_at or job.created_at) < ts - _JOB_TTL_SEC
    ]
    for jid in stale:
        _jobs.pop(jid, None)
        try:
            _job_file(jid).unlink(missing_ok=True)
        except Exception:
            pass


def create_mailing_job(
    *,
    total: int,
    subject: str,
    created_by: str,
) -> MailingJob:
    job = MailingJob(
        id=uuid.uuid4().hex,
        status="queued",
        total=max(0, int(total)),
        subject=(subject or "").strip(),
        created_by=(created_by or "").strip(),
    )
    with _lock:
        _purge_old_jobs_unlocked()
        _jobs[job.id] = job
        _save_job_unlocked(job)
    return job


def find_active_mailing_job() -> MailingJob | None:
    """Перше активне завдання (queued/running), найновіше за created_at."""
    with _lock:
        _purge_old_jobs_unlocked()
        _ensure_jobs_dir()
        active: list[MailingJob] = []
        seen: set[str] = set()
        for path in _JOBS_DIR.glob("*.json"):
            job = _load_job_from_disk_unlocked(path.stem)
            if job is None or job.id in seen:
                continue
            seen.add(job.id)
            _jobs[job.id] = job
            if job.status in _ACTIVE_STATUSES and not job.cancel_requested:
                active.append(job)
        for job in _jobs.values():
            if job.id in seen:
                continue
            if job.status in _ACTIVE_STATUSES and not job.cancel_requested:
                active.append(job)
        _mark_stale_active_unlocked()
        active = [j for j in active if j.status in _ACTIVE_STATUSES and not j.cancel_requested]
    if not active:
        return None
    active.sort(key=lambda j: j.created_at, reverse=True)
    return active[0]


def get_mailing_job(job_id: str, *, purge: bool = True) -> MailingJob | None:
    with _lock:
        if purge:
            _purge_old_jobs_unlocked()
        job = _get_job_unlocked(job_id)
        if job is not None and job.status in _ACTIVE_STATUSES:
            _mark_stale_active_unlocked()
            job = _jobs.get(str(job_id or "").strip()) or job
        return job


def update_mailing_job(job_id: str, **fields: Any) -> MailingJob | None:
    with _lock:
        job = _get_job_unlocked(job_id)
        if not job:
            return None
        for key, value in fields.items():
            if hasattr(job, key):
                setattr(job, key, value)
        job.updated_at = time.time()
        if job.status in _TERMINAL_STATUSES and job.finished_at is None:
            job.finished_at = time.time()
        _save_job_unlocked(job)
        return job


def request_mailing_job_cancel(job_id: str) -> MailingJob | None:
    """Ask the sender loop to stop; marks cancelled if job already idle/terminal."""
    jid = str(job_id or "").strip()
    if not jid:
        return None
    with _lock:
        job = _get_job_unlocked(jid)
        if not job:
            return None
        now = time.time()
        job.updated_at = now
        if job.status in _TERMINAL_STATUSES:
            _save_job_unlocked(job)
            return job
        job.cancel_requested = True
        if job.status in _ACTIVE_STATUSES:
            job.message = (
                "Зупинка… Дочекайтесь завершення поточного листа "
                f"(уже надіслано {job.sent}, помилок {job.failed})."
            )
        _save_job_unlocked(job)
        return job


def is_mailing_job_cancel_requested(job_id: str) -> bool:
    jid = str(job_id or "").strip()
    if not jid:
        return False
    with _lock:
        job = _get_job_unlocked(jid)
        if not job:
            return False
        return bool(job.cancel_requested) or job.status == "cancelled"


def finalize_mailing_job_cancelled(job_id: str, *, sent: int | None = None, failed: int | None = None) -> MailingJob | None:
    with _lock:
        job = _get_job_unlocked(job_id)
        if not job:
            return None
        if sent is not None:
            job.sent = int(sent)
        if failed is not None:
            job.failed = int(failed)
        job.cancel_requested = False
        job.status = "cancelled"
        job.finished_at = time.time()
        job.updated_at = job.finished_at
        job.message = (
            f"Розсилку зупинено. Надіслано: {job.sent}, помилок: {job.failed} "
            f"з {job.total}."
        )
        _save_job_unlocked(job)
        return job


def list_mailing_jobs(*, only_active: bool = False) -> list[dict[str, Any]]:
    with _lock:
        _purge_old_jobs_unlocked()
        _ensure_jobs_dir()
        seen: set[str] = set()
        jobs: list[MailingJob] = []
        for path in sorted(_JOBS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            job = _load_job_from_disk_unlocked(path.stem)
            if job is None or job.id in seen:
                continue
            seen.add(job.id)
            _jobs[job.id] = job
            jobs.append(job)
        for job in _jobs.values():
            if job.id in seen:
                continue
            jobs.append(job)
        _mark_stale_active_unlocked()
    jobs.sort(key=lambda j: j.created_at, reverse=True)
    out: list[dict[str, Any]] = []
    for job in jobs:
        if only_active and (job.status not in _ACTIVE_STATUSES or job.cancel_requested):
            continue
        out.append(job.to_dict())
    return out


def bump_mailing_job_progress(
    job_id: str, *, sent: int, failed: int, force_persist: bool = False
) -> None:
    """Update in-memory counters; persist to disk throttled (or force_persist)."""
    jid = str(job_id or "").strip()
    if not jid:
        return
    with _lock:
        job = _get_job_unlocked(jid)
        if not job:
            return
        # Ignore bumps after cancel finalized.
        if job.status in _TERMINAL_STATUSES:
            return
        job.sent = int(sent)
        job.failed = int(failed)
        job.updated_at = time.time()
        if job.status == "queued":
            job.status = "running"
        now = time.time()
        last = _bump_persist_last.get(jid, 0.0)
        processed = int(job.sent) + int(job.failed)
        should_save = (
            force_persist
            or job.cancel_requested
            or (now - last) >= _BUMP_PERSIST_INTERVAL_SEC
            or processed <= 1
            or processed % 25 == 0
        )
        if should_save:
            _save_job_unlocked(job)
            _bump_persist_last[jid] = now


def refresh_mailing_job_from_checkpoint(job_id: str, *, force: bool = False) -> None:
    """If job is active, refresh sent/failed from checkpoint file on disk.

    File scan runs outside the jobs lock so status polls are not blocked.
    """
    from web.mailing_resume import load_checkpoint_progress

    jid = str(job_id or "").strip()
    if not jid:
        return
    if not force:
        with _lock:
            last = _checkpoint_refresh_last.get(jid, 0.0)
            if time.time() - last < _CHECKPOINT_REFRESH_INTERVAL_SEC:
                return

    with _lock:
        job = _get_job_unlocked(jid)
        if not job or job.status not in _ACTIVE_STATUSES or job.cancel_requested:
            return
        checkpoint_path = job.checkpoint_path
        if not checkpoint_path:
            return

    sent, failed = load_checkpoint_progress(checkpoint_path)

    with _lock:
        job = _get_job_unlocked(jid)
        if not job or job.status not in _ACTIVE_STATUSES or job.cancel_requested:
            return
        # Prefer higher counts: sender bumps may race ahead of a stale scan.
        job.sent = max(int(job.sent), int(sent))
        job.failed = max(int(job.failed), int(failed))
        job.updated_at = time.time()
        if job.status == "queued" and (job.sent or job.failed):
            job.status = "running"
        _save_job_unlocked(job)
        _checkpoint_refresh_last[jid] = time.time()
