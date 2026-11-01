"""Тимчасове збереження HTML листа перед запуском розсилки (обхід multipart/проксі)."""

from __future__ import annotations

import re
import time
import uuid
from pathlib import Path

from config import ROOT_DIR

_STAGING_DIR = Path(ROOT_DIR) / "sessions" / "mailing_body_staging"
_TTL_SEC = 2 * 3600
_MAX_BYTES = 32 * 1024 * 1024
_KEY_RE = re.compile(r"^[a-f0-9]{32}$")


def _ensure_dir() -> Path:
    _STAGING_DIR.mkdir(parents=True, exist_ok=True)
    return _STAGING_DIR


def _safe_user_slug(user: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", (user or "").strip())[:40]
    return s or "user"


def save_staged_body(raw: bytes, *, user: str) -> str:
    if len(raw) > _MAX_BYTES:
        raise ValueError("body_too_large")
    _purge_stale()
    key = uuid.uuid4().hex
    path = _ensure_dir() / f"{_safe_user_slug(user)}_{key}.html"
    path.write_bytes(raw)
    return key


def load_staged_body(key: str, *, user: str) -> str | None:
    key = str(key or "").strip()
    if not _KEY_RE.match(key):
        return None
    prefix = f"{_safe_user_slug(user)}_{key}"
    path = _ensure_dir() / f"{prefix}.html"
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


def delete_staged_body(key: str, *, user: str) -> None:
    key = str(key or "").strip()
    if not _KEY_RE.match(key):
        return
    path = _ensure_dir() / f"{_safe_user_slug(user)}_{key}.html"
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


def _purge_stale() -> None:
    cutoff = time.time() - _TTL_SEC
    try:
        for path in _STAGING_DIR.glob("*.html"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except Exception:
                continue
    except Exception:
        pass
