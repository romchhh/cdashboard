"""Простий файловий кеш для відповідей рекламних API (оновлення раз на добу)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from mono.api import now_kyiv

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[2] / "databases" / "marketing_cache"
DEFAULT_TTL_HOURS = 24
DEFAULT_PURGE_MAX_AGE_DAYS = 14
CACHE_NOT_WARMED = (
    "Кеш ще не зібрано для цього періоду. Натисніть «Оновити з API» "
    "або зачекайте автоматичне оновлення (щодня о 02:00 і кожні 6 год)."
)


def _cache_path(key: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in key)
    return CACHE_DIR / f"{safe}.json"


def cache_get(key: str, *, ttl_hours: int = DEFAULT_TTL_HOURS) -> dict[str, Any] | None:
    """Повертає кешовані дані, якщо вони свіжіші за ttl_hours. Інакше None."""
    path = _cache_path(key)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cached_at = datetime.fromisoformat(data.get("cached_at", ""))
        if now_kyiv() - cached_at > timedelta(hours=ttl_hours):
            return None
        return data.get("payload")
    except Exception as e:
        logger.warning("ad_cache read %s: %s", key, e)
        return None


def cache_get_stale(key: str) -> dict[str, Any] | None:
    """Повертає кешовані дані незалежно від їхнього віку (stale-ok).
    Використовується для швидкої відповіді на запит сторінки.
    None — тільки якщо кешу взагалі немає."""
    path = _cache_path(key)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("payload")
    except Exception as e:
        logger.warning("ad_cache stale read %s: %s", key, e)
        return None


def cache_set(key: str, payload: dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    body = {"cached_at": now_kyiv().isoformat(), "payload": payload}
    _cache_path(key).write_text(json.dumps(body, ensure_ascii=False, default=str), encoding="utf-8")


def cache_last_updated(key: str) -> str | None:
    path = _cache_path(key)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cached_at = datetime.fromisoformat(data.get("cached_at", ""))
        return cached_at.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return None


def cache_is_fresh(key: str, *, ttl_hours: int = DEFAULT_TTL_HOURS) -> bool:
    """True, якщо кеш існує і ще актуальний (молодший за ttl_hours)."""
    return cache_get(key, ttl_hours=ttl_hours) is not None


def get_cached_ad_payload(
    cache_key: str,
    *,
    legacy_keys: tuple[str, ...] = (),
    ttl_hours: int = DEFAULT_TTL_HOURS,
) -> dict[str, Any] | None:
    """Свіжий кеш (в межах TTL) для основного або legacy-ключа."""
    payload = cache_get(cache_key, ttl_hours=ttl_hours)
    if payload is not None:
        return payload
    for key in legacy_keys:
        payload = cache_get(key, ttl_hours=ttl_hours)
        if payload is not None:
            return payload
    return None


def load_cached_ad_payload(
    cache_key: str,
    *,
    legacy_keys: tuple[str, ...] = (),
    ttl_hours: int = DEFAULT_TTL_HOURS,
) -> dict[str, Any] | None:
    """Свіжий або застарілий кеш — для рендеру сторінки без live API."""
    payload = get_cached_ad_payload(cache_key, legacy_keys=legacy_keys, ttl_hours=ttl_hours)
    if payload is not None:
        return payload
    return get_stale_ad_payload(cache_key, legacy_keys=legacy_keys)


def load_cached_period_payload(
    prefix: str,
    start: datetime,
    end: datetime,
    *,
    legacy_prefix: str | None = None,
) -> dict[str, Any] | None:
    """
    Кеш точного періоду або найближчий файл з тією ж датою старту
    (вчорашній «поточний місяць», поки нічний/6-годинний прогрів ще не пройшов).
    """
    start_s = start.date().isoformat()
    end_s = end.date().isoformat()
    prefixes = (prefix, legacy_prefix) if legacy_prefix else (prefix,)
    exact_keys: tuple[str, ...] = tuple(f"{p}_{start_s}_{end_s}" for p in prefixes)
    payload = load_cached_ad_payload(exact_keys[0], legacy_keys=exact_keys[1:])
    if payload is not None:
        return payload

    if not CACHE_DIR.is_dir():
        return None
    best_payload: dict[str, Any] | None = None
    best_end = None
    for pref in prefixes:
        for path in CACHE_DIR.glob(f"{pref}_{start_s}_*.json"):
            try:
                file_end = datetime.strptime(path.stem.rsplit("_", 1)[-1], "%Y-%m-%d").date()
            except ValueError:
                continue
            if file_end > end.date():
                continue
            if best_end is not None and file_end <= best_end:
                continue
            data = cache_get_stale(path.stem)
            if isinstance(data, dict):
                best_payload = data
                best_end = file_end
    if best_payload is not None and best_end is not None:
        # Не підставляти кеш із «коротшим» кінцем періоду (напр. 01–03, коли обрано 01–07) —
        # інакше графік GSC виглядає обрізаним. Допускаємо лише «вчорашній» кінець (±1 день).
        if (end.date() - best_end).days > 1:
            return None
    return best_payload


def purge_expired_ad_cache(*, max_age_days: int = DEFAULT_PURGE_MAX_AGE_DAYS) -> int:
    """Видаляє старі файли кешу реклами/CRM (ключі з датами накопичуються щодня)."""
    if not CACHE_DIR.is_dir():
        return 0
    cutoff = now_kyiv() - timedelta(days=max(1, int(max_age_days)))
    removed = 0
    for path in CACHE_DIR.rglob("*.json"):
        if path.name.startswith("_"):
            continue
        try:
            stale = False
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                cached_at = datetime.fromisoformat(str(data.get("cached_at") or ""))
                stale = cached_at < cutoff
            except Exception:
                stale = datetime.fromtimestamp(path.stat().st_mtime) < cutoff
            if stale:
                path.unlink()
                removed += 1
        except OSError as exc:
            logger.warning("ad_cache purge %s: %s", path, exc)
    if removed:
        logger.info("ad_cache: видалено %s застарілих файлів (>%s дн)", removed, max_age_days)
    return removed


def get_stale_ad_payload(
    cache_key: str,
    *,
    legacy_keys: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    """Застарілий кеш — fallback, якщо live API недоступний."""
    payload = cache_get_stale(cache_key)
    if payload is not None:
        return payload
    for key in legacy_keys:
        payload = cache_get_stale(key)
        if payload is not None:
            return payload
    return None


def iter_ad_api_cache_periods(now: datetime | None = None) -> Iterator[tuple[datetime, datetime, str]]:
    """
    Періоди для нічного/ручного прогріву кешу рекламних API.
    Поточний і попередній місяць + пресети 7 / 30 / 90 днів (без дублікатів ключів).
    """
    now = now or now_kyiv()
    seen: set[tuple[str, str]] = set()
    periods: list[tuple[datetime, datetime, str]] = []

    def _add(start: datetime, end: datetime, label: str) -> None:
        key = (start.date().isoformat(), end.date().isoformat())
        if key in seen:
            return
        seen.add(key)
        periods.append((start, end, label))

    cur_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    cur_end = now.replace(hour=23, minute=59, second=59, microsecond=0)
    prev_end_dt = cur_start - timedelta(seconds=1)
    prev_start = prev_end_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    _add(cur_start, cur_end, "поточний місяць")
    _add(prev_start, prev_end_dt, "попередній місяць")

    # Той самий проміжок, що й compare=prev на дашборді (не календарний попередній місяць)
    span = (cur_end.date() - cur_start.date()).days + 1
    prev_cmp_end = cur_start - timedelta(seconds=1)
    prev_cmp_start = (prev_cmp_end - timedelta(days=span - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    _add(prev_cmp_start, prev_cmp_end, "порівняння попереднього періоду")

    def _shift_year(dt: datetime) -> datetime:
        try:
            return dt.replace(year=dt.year - 1)
        except ValueError:
            return dt.replace(month=2, day=28, year=dt.year - 1)

    _add(_shift_year(cur_start), _shift_year(cur_end), "YoY поточний місяць")

    for days, label in ((7, "7 днів"), (30, "30 днів"), (90, "90 днів")):
        end = now.replace(hour=23, minute=59, second=59, microsecond=0)
        start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
        _add(start, end, label)
        prev_end = start - timedelta(seconds=1)
        prev_start_span = (prev_end - timedelta(days=days - 1)).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        _add(prev_start_span, prev_end, f"порівняння {label}")

    yield from periods

