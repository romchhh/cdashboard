"""Міжпроцесний lock для оновлення JWT (refresh одноразовий — не витрачати паралельно)."""

from __future__ import annotations

import contextlib
from pathlib import Path

from config import SESSIONS_DIR, ensure_session_suffix


def _sessions_dir() -> Path:
    return Path(SESSIONS_DIR)


def _lock_path(fop_name: str) -> Path:
    # Той самий suffix, що й у session/refresh — кирилиця більше не згортається в один «_».
    suffix = ensure_session_suffix(fop_name)
    base = _sessions_dir()
    base.mkdir(parents=True, exist_ok=True)
    return base / f".jwt_lock_{suffix}"


@contextlib.contextmanager
def novapay_jwt_lock(fop_name: str):
    path = _lock_path(fop_name)
    with open(path, "a+b") as lock_file:
        try:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass
        try:
            yield
        finally:
            try:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
