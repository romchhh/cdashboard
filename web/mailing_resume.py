"""Checkpoint + skip already-sent helpers for mass mailings."""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable
from urllib import error as urlerror
from urllib import request as urlrequest

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.clients_models import EmailDeliveryLog
from config import RESEND_API_BASE, RESEND_API_KEY, ROOT_DIR

CHECKPOINT_DIR = Path(ROOT_DIR) / "sessions" / "mailing_checkpoints"


def ensure_checkpoint_dir() -> Path:
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    return CHECKPOINT_DIR


def _safe_subject_slug(subject: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9а-яА-ЯіІїЇєЄ_]+", "_", (subject or "").strip())[:80]
    return s or "mailing"


def new_checkpoint_path(*, subject: str, job_id: str) -> str:
    ensure_checkpoint_dir()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"{stamp}_{_safe_subject_slug(subject)}_{job_id[:10]}.jsonl"
    return str(CHECKPOINT_DIR / name)


def append_checkpoint(
    path: str | None,
    *,
    email: str,
    success: bool,
    error: str | None = None,
    subject: str = "",
) -> None:
    if not path:
        return
    try:
        ensure_checkpoint_dir()
        row = {
            "ts": time.time(),
            "email": str(email or "").strip().lower(),
            "success": bool(success),
            "error": (error or "")[:400] or None,
            "subject": (subject or "")[:500],
        }
        if not row["email"]:
            return
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _load_checkpoint_last_status(path: str | None) -> dict[str, bool]:
    """Останній статус на адресу (узгоджено для progress і skip-логіки)."""
    last_status: dict[str, bool] = {}
    if not path or not os.path.isfile(path):
        return last_status
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                email = str(row.get("email") or "").strip().lower()
                if not email:
                    continue
                last_status[email] = bool(row.get("success"))
    except Exception:
        return {}
    return last_status


def load_checkpoint_progress(path: str | None) -> tuple[int, int]:
    """Return (sent, failed) using the latest row per email in a checkpoint file."""
    last_status = _load_checkpoint_last_status(path)
    sent = sum(1 for ok in last_status.values() if ok)
    failed = sum(1 for ok in last_status.values() if not ok)
    return sent, failed


def load_successful_emails_from_checkpoint(path: str | None) -> set[str]:
    last_status = _load_checkpoint_last_status(path)
    return {email for email, ok in last_status.items() if ok}


def load_successful_emails_from_recent_checkpoints(
    subject: str,
    *,
    max_age_hours: int = 72,
) -> set[str]:
    """Усі успішні email з локальних checkpoint-файлів за останні N годин (та сама тема)."""
    out: set[str] = set()
    ensure_checkpoint_dir()
    subj = (subject or "").strip()
    cutoff = time.time() - max_age_hours * 3600
    for path in CHECKPOINT_DIR.glob("*.jsonl"):
        try:
            if path.stat().st_mtime < cutoff:
                continue
        except Exception:
            continue
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    if not row.get("success"):
                        continue
                    row_subj = str(row.get("subject") or "").strip()
                    if subj and row_subj and row_subj != subj:
                        continue
                    email = str(row.get("email") or "").strip().lower()
                    if email:
                        out.add(email)
        except Exception:
            continue
    return out


async def fetch_successful_emails_from_db(
    session: AsyncSession,
    subject: str,
    *,
    days: int = 14,
) -> set[str]:
    subj = (subject or "").strip()
    if not subj:
        return set()
    since = datetime.utcnow() - timedelta(days=max(1, days))
    rows = (
        await session.execute(
            select(EmailDeliveryLog.recipient_email).where(
                and_(
                    EmailDeliveryLog.subject == subj,
                    EmailDeliveryLog.success.is_(True),
                    EmailDeliveryLog.sent_at >= since,
                )
            )
        )
    ).scalars().all()
    return {str(x).strip().lower() for x in rows if str(x).strip()}


def fetch_resend_sent_emails_for_subject(
    subject: str,
    *,
    max_pages: int = 40,
    page_size: int = 100,
) -> set[str]:
    """
    Підтягнути з Resend адреси, яким уже пішов лист з цією темою.
    Корисно після збою, коли локальний лог ще не встиг записатися.
    """
    subj = (subject or "").strip()
    if not subj or not RESEND_API_KEY:
        return set()

    out: set[str] = set()
    after: str | None = None
    base = RESEND_API_BASE.rstrip("/")

    for _ in range(max_pages):
        url = f"{base}/emails?limit={page_size}"
        if after:
            url += f"&after={after}"
        req = urlrequest.Request(
            url,
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Accept": "application/json",
            },
            method="GET",
        )
        try:
            with urlrequest.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urlerror.HTTPError:
            break
        except Exception:
            break

        items = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items:
            break

        matched_any = False
        for item in items:
            if not isinstance(item, dict):
                continue
            item_subj = str(item.get("subject") or "").strip()
            if item_subj != subj:
                continue
            matched_any = True
            tos = item.get("to") or []
            if isinstance(tos, str):
                tos = [tos]
            for addr in tos:
                email = str(addr or "").strip().lower()
                if email:
                    out.add(email)

        last_id = str(items[-1].get("id") or "").strip()
        has_more = bool(payload.get("has_more")) if isinstance(payload, dict) else False
        if not has_more or not last_id or last_id == after:
            break
        # якщо вже пішли сторінки без цієї теми — можна зупинитись раніше,
        # але листи можуть бути перемішані з іншими розсилками, тому крутимо max_pages
        after = last_id
        if not matched_any and len(out) > 0 and _ > 5:
            # евристика: кілька порожніх сторінок після знахідок — ок, продовжуємо ще трохи
            pass

    return out


def merge_already_sent(*sets: Iterable[str]) -> set[str]:
    out: set[str] = set()
    for s in sets:
        for x in s or []:
            email = str(x or "").strip().lower()
            if email:
                out.add(email)
    return out


def filter_unsent_recipients(recipients: list[str], already: set[str]) -> tuple[list[str], int]:
    if not already:
        return list(recipients), 0
    kept: list[str] = []
    skipped = 0
    for addr in recipients:
        email = str(addr or "").strip().lower()
        if not email:
            continue
        if email in already:
            skipped += 1
            continue
        kept.append(email)
    return kept, skipped
