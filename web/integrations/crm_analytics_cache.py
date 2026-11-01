"""Файловий кеш агрегатів CRM для розділу «Аналіз реклами»."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from mono.api import now_kyiv

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[2] / "databases" / "marketing_cache" / "crm"
DEFAULT_TTL_HOURS = 24


def _cache_path(key: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in key)
    return CACHE_DIR / f"{safe}.json"


def cache_get(key: str, *, ttl_hours: int = DEFAULT_TTL_HOURS) -> dict[str, Any] | None:
    path = _cache_path(key)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cached_at = datetime.fromisoformat(data.get("cached_at", ""))
        if now_kyiv() - cached_at > timedelta(hours=ttl_hours):
            return None
        payload = data.get("payload")
        return payload if isinstance(payload, dict) else None
    except Exception as exc:
        logger.warning("crm_analytics_cache read %s: %s", key, exc)
        return None


def cache_set(key: str, payload: dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    body = {"cached_at": now_kyiv().isoformat(), "payload": payload}
    _cache_path(key).write_text(json.dumps(body, ensure_ascii=False, default=str), encoding="utf-8")


def cache_invalidate_all() -> int:
    """Скидає кеш після синхронізації CRM. Повертає кількість видалених файлів."""
    if not CACHE_DIR.is_dir():
        return 0
    removed = 0
    for path in CACHE_DIR.glob("*.json"):
        try:
            path.unlink()
            removed += 1
        except OSError as exc:
            logger.warning("crm_analytics_cache unlink %s: %s", path, exc)
    return removed


def orders_cache_key(channel: str | None, from_iso: str, to_iso: str, compare: str = "prev") -> str:
    ch = (channel or "all").strip() or "all"
    cmp = (compare or "prev").strip() or "prev"
    return f"orders_v4_{ch}_{from_iso}_{to_iso[:10]}_{cmp}"


def business_cache_key(from_iso: str, to_iso: str) -> str:
    return f"business_v3_{from_iso}_{to_iso[:10]}"


LAST_SYNC_FILE = CACHE_DIR / "_last_crm_sync.json"
DEFAULT_CRM_SYNC_TTL_HOURS = 6


def mark_crm_synced() -> None:
    """Позначити успішну синхронізацію CRM (для авто-оновлення на дашборді)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    LAST_SYNC_FILE.write_text(
        json.dumps({"synced_at": now_kyiv().isoformat()}, ensure_ascii=False),
        encoding="utf-8",
    )


def crm_sync_is_stale(*, ttl_hours: int = DEFAULT_CRM_SYNC_TTL_HOURS) -> bool:
    """True, якщо CRM давно не синхронізувався з фідом Cosmy."""
    if not LAST_SYNC_FILE.is_file():
        return True
    try:
        data = json.loads(LAST_SYNC_FILE.read_text(encoding="utf-8"))
        synced_at = datetime.fromisoformat(data.get("synced_at", ""))
        return now_kyiv() - synced_at > timedelta(hours=ttl_hours)
    except Exception:
        return True
