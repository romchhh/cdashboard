"""Тимчасове збереження CSV/XLSX отримувачів для async-розсилки."""

from __future__ import annotations

import io
import re
import time
import uuid
from pathlib import Path

from starlette.datastructures import UploadFile

from config import ROOT_DIR

_UPLOAD_DIR = Path(ROOT_DIR) / "sessions" / "mailing_recipients_uploads"
_TTL_SEC = 2 * 3600
_MAX_BYTES = 64 * 1024 * 1024
_KEY_RE = re.compile(r"^[a-f0-9]{32}$")
# Above this count, filter→mailings prefill is staged on disk (not dumped into textarea HTML).
PREFILL_TEXTAREA_MAX = 80


def _ensure_dir() -> Path:
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    return _UPLOAD_DIR


def _safe_user_slug(user: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", (user or "").strip())[:40]
    return s or "user"


def _purge_stale() -> None:
    cutoff = time.time() - _TTL_SEC
    try:
        for path in _UPLOAD_DIR.glob("*"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except Exception:
                continue
    except Exception:
        pass


def save_recipients_text_list(emails: list[str], *, user: str) -> str:
    """Stage newline-separated emails; returns opaque key (not a filesystem path)."""
    lines = [str(e).strip() for e in emails if str(e).strip()]
    raw = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
    if len(raw) > _MAX_BYTES:
        raise ValueError("recipients_too_large")
    _purge_stale()
    key = uuid.uuid4().hex
    path = _ensure_dir() / f"{_safe_user_slug(user)}_{key}.txt"
    path.write_bytes(raw)
    return key


def load_recipients_text_list(key: str, *, user: str) -> str | None:
    key = str(key or "").strip()
    if not _KEY_RE.match(key):
        return None
    path = _ensure_dir() / f"{_safe_user_slug(user)}_{key}.txt"
    if not path.is_file():
        return None
    try:
        if path.stat().st_mtime < time.time() - _TTL_SEC:
            path.unlink(missing_ok=True)
            return None
    except Exception:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def delete_recipients_text_list(key: str, *, user: str) -> None:
    key = str(key or "").strip()
    if not _KEY_RE.match(key):
        return
    path = _ensure_dir() / f"{_safe_user_slug(user)}_{key}.txt"
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


async def save_recipients_upload_from_file(upload, *, user: str) -> str:
    if not upload or not getattr(upload, "filename", None):
        raise ValueError("missing_file")
    content = await upload.read()
    if len(content) > _MAX_BYTES:
        raise ValueError("file_too_large")
    _purge_stale()
    ext = Path(str(upload.filename)).suffix.lower() or ".csv"
    key = uuid.uuid4().hex
    path = _ensure_dir() / f"{_safe_user_slug(user)}_{key}{ext}"
    path.write_bytes(content)
    return str(path)


def open_saved_recipients_upload(path: str | None) -> UploadFile | None:
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    try:
        if p.stat().st_mtime < time.time() - _TTL_SEC:
            p.unlink(missing_ok=True)
            return None
    except Exception:
        return None
    return UploadFile(filename=p.name, file=io.BytesIO(p.read_bytes()))


def delete_saved_recipients_upload(path: str | None) -> None:
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass
