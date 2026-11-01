"""Mailing job TTL and checkpoint consistency."""

import time

from web.mailing_jobs import (
    _jobs,
    _purge_old_jobs_unlocked,
    _save_job_unlocked,
    create_mailing_job,
    get_mailing_job,
    update_mailing_job,
)
from web.mailing_resume import (
    append_checkpoint,
    load_checkpoint_progress,
    load_successful_emails_from_checkpoint,
    new_checkpoint_path,
)


def test_checkpoint_latest_row_wins(tmp_path, monkeypatch):
    monkeypatch.setattr("web.mailing_resume.CHECKPOINT_DIR", tmp_path)
    path = new_checkpoint_path(subject="Test", job_id="abc123def0")
    append_checkpoint(path, email="a@example.com", success=True, subject="Test")
    append_checkpoint(path, email="a@example.com", success=False, error="timeout", subject="Test")
    append_checkpoint(path, email="b@example.com", success=True, subject="Test")

    assert load_successful_emails_from_checkpoint(path) == {"b@example.com"}
    sent, failed = load_checkpoint_progress(path)
    assert sent == 1
    assert failed == 1


def test_purge_skips_running_jobs(monkeypatch, tmp_path):
    monkeypatch.setattr("web.mailing_jobs._JOBS_DIR", tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)

    job = create_mailing_job(total=10, subject="S", created_by="u")
    update_mailing_job(job.id, status="running")
    old = time.time() - 7 * 3600
    job_obj = get_mailing_job(job.id)
    assert job_obj is not None
    job_obj.created_at = old
    _jobs[job.id] = job_obj
    _save_job_unlocked(job_obj)

    _purge_old_jobs_unlocked(now=time.time())
    assert get_mailing_job(job.id) is not None

    update_mailing_job(job.id, status="done", finished_at=old)
    _purge_old_jobs_unlocked(now=time.time())
    assert get_mailing_job(job.id) is None
