"""Обгортка над standalone cosmyparser для веб-розділу «Парсер»."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, unquote

from web import parser_store as store

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
PARSER_ROOT = ROOT / "cosmyparser"
DATA_DIR = ROOT / "databases" / "parser"
BRANDS_DIR = DATA_DIR / "brands"
REPORT_XLSX = DATA_DIR / "price_report.xlsx"
JOBS_DIR = DATA_DIR / "jobs"
JOB_LOG = JOBS_DIR / "job.log"
# Усі увімкнені бренди оновлюються за 14 днів: кожної ночі своя частка, по одному бренду.
CYCLE_DAYS = 14
KYIV_TZ = ZoneInfo("Europe/Kiev")
# Автопарсинг лише вночі (Київ): доробити чергу з 01:00, нова черга після синку ~03:00.
ROTATION_NIGHT_START_HOUR = 1   # з 01:00
ROTATION_NIGHT_END_HOUR = 7     # до 07:00 (не включно) — ~6 год на 5–10 брендів
ROTATION_QUEUE_HOUR = 3
MAX_HISTORY = 40
# option SKU ids must not collide with product_id from the Cosmy feed
_OPTION_ID_OFFSET = 1_000_000_000
_CATALOG_CACHE_TTL = 60.0

_REPORT_LOCK = threading.Lock()
_SEARCH_LOCK = threading.Lock()
_SEARCH_THREAD: threading.Thread | None = None
_ROTATION_LOCK = threading.Lock()
_JOB_LOCK = threading.Lock()
_REMOTE_POLL_LOCK = threading.Lock()
_REMOTE_POLL_THREADS: dict[str, threading.Thread] = {}
_REPORT_STATUS: dict[str, Any] = {"state": "idle"}
_LIVE_ROWS: dict[Any, dict] = {}  # id → slim row під час поточного прогону
_LIVE_BRAND: str | None = None
_LAST_FLUSH = 0.0
_LAST_LIVE_FLUSH = 0.0
_JOB_LOG_HANDLE = None
_CATALOG_CACHE: dict[str, Any] = {"at": 0.0, "items": None}
_ASYNC_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="parser-async")
_JOB_FLUSH_INTERVAL = 0.6
_LIVE_FLUSH_INTERVAL = 1.25
_JOB_LOG_MAX_BYTES = 2_000_000

_DEFAULT_SETTINGS = {
    "sku_workers": 4,
    "price_low_ratio": 0.45,
    "price_high_ratio": 2.2,
    "at_market_pct": 3.0,
    "suspicious_gap_pct": 35.0,
    "min_similarity": 0.58,
    "enabled_sites": [],  # відсутній ключ у файлі = усі; збережений [] = усі вимкнені
    "disabled_brands": [],  # brand names excluded from parsing
}


def _ensure_parser_path() -> None:
    root = str(PARSER_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


def _process_cmdline(pid: int | None) -> str | None:
    """Текст команди процесу або None, якщо ps недоступний / pid зник."""
    if not pid:
        return None
    for fmt in ("args=", "command=", "comm="):
        try:
            out = subprocess.check_output(
                ["ps", "-p", str(int(pid)), "-o", fmt],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError, ValueError):
            continue
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        # перший рядок інколи заголовок ARGS/COMMAND
        if not lines:
            continue
        cmd = lines[-1] if len(lines) == 1 else (lines[1] if lines[0].upper() in {"ARGS", "COMMAND", "COMM"} else lines[0])
        if cmd.upper() in {"ARGS", "COMMAND", "COMM"}:
            continue
        return cmd
    return None


def _parser_cmdline_ours(pid: int | None) -> bool | None:
    """True — наш воркер; False — явно чужий pid; None — не вдалося прочитати cmdline."""
    cmd = _process_cmdline(pid)
    if cmd is None:
        return None
    if "run_brand_job" in cmd:
        return True
    lower = cmd.lower()
    # Під час старту ps інколи показує лише «python3» / короткий comm.
    if "python" in lower:
        return True
    return False


def _parser_process_alive(pid: int | None) -> bool:
    """Чи живий фоновий парсер. Якщо pid живий, а ps мовчить — вважаємо живим (без хибних «зупинився»)."""
    if not _pid_alive(pid):
        return False
    ours = _parser_cmdline_ours(pid)
    if ours is False:
        return False
    return True


def _dead_job_error(data: dict) -> dict:
    return {
        **data,
        "state": "error",
        "error": data.get("error")
        or "Фоновий процес зупинився. Запустіть моніторинг ще раз.",
        "updated_epoch": time.time(),
    }


def _resolve_job_payload(data: dict, *, persist: bool = False) -> dict:
    """Для state=running: помилка лише коли pid реально мертвий довше grace period.

    Якщо є remote_job_id — скрейп на worker-і; вихід зі сторінки / смерть локального
    poller-а НЕ зупиняє job. Замість error — підхоплюємо опитування знову.
    """
    if data.get("state") != "running":
        return data
    remote_id = (data.get("remote_job_id") or "").strip()
    pid = data.get("pid")
    age = time.time() - float(data.get("updated_epoch") or 0)
    grace = 25.0
    if remote_id:
        # Scrape на worker: сторінка/вкладка нічого не скасовує.
        # Підхоплюємо poll лише якщо локальний воркер реально зник.
        local_ok = _parser_process_alive(pid)
        if not local_ok and age > grace:
            _ensure_remote_poller(data)
        return data
    resolved: dict | None = None
    if _pid_alive(pid):
        # Підозра на reuse PID: процес живий, cmdline чужий, статус давно не оновлювався.
        if age > 90 and _parser_cmdline_ours(pid) is False:
            resolved = _dead_job_error(data)
    elif age > grace:
        resolved = _dead_job_error(data)
    if resolved is None:
        return data
    if persist:
        try:
            store.save_job({k: v for k, v in resolved.items() if k != "row"})
        except Exception:
            logger.exception("Failed to persist dead parser job")
    return resolved


def _ensure_remote_poller(job: dict) -> None:
    """Якщо remote catalog ще running, а локальний poller зник — підхопити дочитування."""
    remote_id = (job.get("remote_job_id") or "").strip()
    if not remote_id or job.get("state") != "running":
        return
    with _REMOTE_POLL_LOCK:
        alive = {
            jid: th
            for jid, th in _REMOTE_POLL_THREADS.items()
            if th is not None and th.is_alive()
        }
        _REMOTE_POLL_THREADS.clear()
        _REMOTE_POLL_THREADS.update(alive)
        existing = _REMOTE_POLL_THREADS.get(remote_id)
        if existing and existing.is_alive():
            return
        brand = job.get("brand")
        total = int(job.get("total") or 0)
        seen = int(job.get("live_count") or 0)

        def _reattach() -> None:
            try:
                from web.parser_worker_client import poll_catalog_job, worker_enabled

                if not worker_enabled():
                    return
                logger.info("Reattach remote parser job %s brand=%s", remote_id, brand)

                def progress(status: dict) -> None:
                    status = dict(status)
                    row = status.pop("row", None)
                    if brand:
                        status["brand"] = brand
                    status["remote_job_id"] = remote_id
                    _publish_status(status, row=row if isinstance(row, dict) else None)

                report = poll_catalog_job(
                    remote_id,
                    products_total=total,
                    brand=brand,
                    on_progress=progress,
                    since_rows=seen,
                )
                # Фіналізація як у _run_report_job (мінімум — зберегти звіт і done).
                try:
                    scope = brand_slug(brand) if brand else store.FULL_SCOPE
                    store.save_report(scope, report)
                    if brand:
                        _json_path, out_xlsx = brand_report_paths(brand)
                    else:
                        out_xlsx = REPORT_XLSX
                    try:
                        _ensure_parser_path()
                        from catalog_report import write_excel  # type: ignore

                        out_xlsx.parent.mkdir(parents=True, exist_ok=True)
                        write_excel(report, out_xlsx)
                    except Exception:
                        logger.exception("Reattach: excel write failed")
                    generated = report.get("generated_at")
                    if brand:
                        _update_brand_status(
                            brand,
                            state="done",
                            last_parsed_at=generated,
                            total=report.get("total"),
                            source="brand_report",
                            error=None,
                        )
                        _rotation_mark_finished(brand, ok=True)
                    _publish_status(
                        {
                            "state": "done",
                            "phase": "done",
                            "current": report.get("total"),
                            "total": report.get("total"),
                            "brand": brand,
                            "product": "готово",
                            "generated_at": generated,
                            "remote_job_id": remote_id,
                        },
                        force=True,
                    )
                    _schedule_night_chain()
                except Exception as exc:
                    logger.exception("Reattach finalize failed")
                    _publish_status(
                        {
                            "state": "error",
                            "error": str(exc),
                            "brand": brand,
                            "remote_job_id": remote_id,
                        },
                        force=True,
                    )
                    if brand:
                        _rotation_mark_finished(brand, ok=False, error=str(exc))
                    _schedule_night_chain()
            except Exception as exc:
                msg = str(exc)
                # Worker OOM/restart — job мертвий, фіксуємо error у БД.
                if "перезапустився" in msg.lower() or "OOM" in msg or "job not found" in msg.lower():
                    logger.error("Reattach: remote job %s lost: %s", remote_id, exc)
                    _publish_status(
                        {
                            "state": "error",
                            "error": msg,
                            "brand": brand,
                            "remote_job_id": remote_id,
                        },
                        force=True,
                    )
                    if brand:
                        _rotation_mark_finished(brand, ok=False, error=msg)
                    _schedule_night_chain()
                else:
                    # Worker ще може бути running — не гасимо одразу, лише лог.
                    logger.warning("Reattach poll failed for %s: %s", remote_id, exc)
            finally:
                with _REMOTE_POLL_LOCK:
                    cur = _REMOTE_POLL_THREADS.get(remote_id)
                    if cur is threading.current_thread():
                        _REMOTE_POLL_THREADS.pop(remote_id, None)

        thread = threading.Thread(
            target=_reattach,
            name=f"parser-reattach-{remote_id[:8]}",
            daemon=True,
        )
        _REMOTE_POLL_THREADS[remote_id] = thread
        thread.start()


def _write_job_status(status: dict) -> None:
    store.save_job({k: v for k, v in status.items() if k != "row"})


def _write_live_rows() -> None:
    store.save_live_rows(list(_LIVE_ROWS.values()))


def _write_job_files(status: dict) -> None:
    """Повний flush (job + live) — для фіналу / force."""
    _write_job_status(status)
    _write_live_rows()


def _merge_running_job_fields(status: dict) -> dict:
    """Heartbeat воркера не повинен затирати total / started_epoch з батьківського старту."""
    if status.get("state") != "running":
        return status
    prev = _REPORT_STATUS if isinstance(_REPORT_STATUS, dict) else {}
    if prev.get("state") != "running":
        disk = store.load_job() or {}
        if disk.get("state") == "running":
            prev = disk
    if prev.get("state") != "running":
        return status
    for key in ("total", "started_epoch", "started_at", "brand", "remote_job_id"):
        if status.get(key) in (None, "") and prev.get(key) not in (None, ""):
            status[key] = prev[key]
    return status


def _publish_status(status: dict, *, row: dict | None = None, force: bool = False) -> None:
    """Памʼять процесу-воркера + файл, який читає веб (навіть інший процес).

    Job-статус і live-рядки пишемо окремо: повний rewrite live у SQLite дорогий,
    тому лише коли зʼявився/оновився рядок (або force/фінал).
    """
    global _REPORT_STATUS, _LAST_FLUSH, _LAST_LIVE_FLUSH
    status = dict(status)
    status.pop("row", None)
    with _JOB_LOCK:
        row_changed = False
        complete = False
        if isinstance(row, dict) and row.get("id") is not None:
            slim = _slim_live_row(row)
            if row.get("partial"):
                slim["partial"] = True
            else:
                complete = True
            _LIVE_ROWS[row["id"]] = slim
            row_changed = True
        status = _merge_running_job_fields(status)
        status["live_count"] = len(_LIVE_ROWS)
        status["visible"] = len(_LIVE_ROWS)
        status["pid"] = os.getpid()
        status["updated_epoch"] = time.time()
        terminal = status.get("state") in ("done", "error")
        _REPORT_STATUS = status
        now = time.time()
        flush_job = force or terminal or complete or now - _LAST_FLUSH >= _JOB_FLUSH_INTERVAL
        flush_live = force or terminal or complete or (
            row_changed and now - _LAST_LIVE_FLUSH >= _LIVE_FLUSH_INTERVAL
        )
        if flush_job:
            _LAST_FLUSH = now
            _write_job_status(status)
        if flush_live:
            _LAST_LIVE_FLUSH = now
            _write_live_rows()


def _job_from_disk() -> dict | None:
    data = store.load_job()
    if not isinstance(data, dict) or not data.get("state"):
        return None
    state = data.get("state")
    age = time.time() - float(data.get("updated_epoch") or 0)
    if state == "running":
        return _resolve_job_payload(data, persist=True)
    if state in ("done", "error") and age < 90:
        return data
    return None


def _live_rows_from_disk() -> list[dict]:
    return [r for r in store.load_live_rows() if isinstance(r, dict)]


def brand_slug(name: str) -> str:
    raw = (name or "").strip() or "unknown"
    slug = re.sub(r"[^\w\-]+", "-", raw, flags=re.UNICODE).strip("-").lower()
    return slug or quote(raw, safe="")


def catalog_brand_names() -> list[str]:
    """Імена брендів з каталогу без seed/mutate SQLite — безпечно для live-poll."""
    names: set[str] = set()
    for item in load_catalog():
        brand = (item.get("brand") or "").strip() or "—"
        names.add(brand)
    return sorted(names, key=str.lower)


def brand_from_slug(slug: str, brands: list[str] | None = None) -> str | None:
    decoded = unquote(slug or "")
    known = brands or catalog_brand_names()
    for name in known:
        if brand_slug(name) == slug or name == decoded or name.lower() == decoded.lower():
            return name
    return decoded if decoded in known else None


def _brand_key(name: str) -> str:
    text = (name or "").replace("\u00a0", " ").strip().lower()
    return re.sub(r"\s+", " ", text)


def _read_disabled_names() -> list[str]:
    return store.read_disabled_brands()


def load_settings() -> dict:
    data = store.read_settings()
    out = dict(_DEFAULT_SETTINGS)
    if isinstance(data, dict):
        out.update({k: data[k] for k in _DEFAULT_SETTINGS if k in data and k != "enabled_sites"})
    _ensure_parser_path()
    from cosmy_parse import SITES  # type: ignore

    all_names = [s.name for s in SITES]
    # Немає збереженого списку — усі магазини. Порожній список після збереження — усі вимкнені.
    if not isinstance(data, dict) or "enabled_sites" not in data or data.get("enabled_sites") is None:
        out["enabled_sites"] = all_names
    else:
        saved = data.get("enabled_sites") or []
        if isinstance(saved, str):
            saved = [x.strip() for x in saved.split(",") if x.strip()]
        known = [n for n in saved if n in all_names]
        if saved and not known:
            out["enabled_sites"] = all_names
        else:
            out["enabled_sites"] = known
    out["disabled_brands"] = _read_disabled_names()
    return out


def save_settings(payload: dict) -> dict:
    current = load_settings()
    update_sites = False
    for key in _DEFAULT_SETTINGS:
        if key not in payload or key == "disabled_brands":
            continue
        val = payload[key]
        if key == "enabled_sites":
            if isinstance(val, str):
                val = [x.strip() for x in val.split(",") if x.strip()]
            current[key] = [str(x).strip() for x in (val or []) if str(x).strip()]
            update_sites = True
        elif key in ("sku_workers",):
            current[key] = max(1, min(16, int(val)))
        else:
            current[key] = float(val)
    scalars = {
        key: current[key]
        for key in _DEFAULT_SETTINGS
        if key not in ("enabled_sites", "disabled_brands")
    }
    store.write_settings(scalars, current.get("enabled_sites") or [], update_sites=update_sites)
    current["disabled_brands"] = _read_disabled_names()
    return current


def disabled_brand_set() -> set[str]:
    return {_brand_key(b) for b in _read_disabled_names()}


def is_brand_enabled(brand: str) -> bool:
    name = _brand_key(brand)
    if not name:
        return False
    return name not in disabled_brand_set()


def set_brand_enabled(brand: str, enabled: bool) -> dict:
    """Увімкнути/вимкнути бренд у парсингу. Повертає {name, enabled, slug}."""
    name = (brand or "").strip()
    if not name:
        raise ValueError("бренд не вказано")
    # канонічна назва з каталогу
    for item in load_catalog():
        b = (item.get("brand") or "").strip()
        if _brand_key(b) == _brand_key(name):
            name = b
            break

    def _apply(disabled: list[str]) -> list[str]:
        key = _brand_key(name)
        if enabled:
            return [b for b in disabled if _brand_key(b) != key]
        if key not in {_brand_key(b) for b in disabled}:
            disabled.append(name)
        return disabled

    store.mutate_disabled_brands(_apply)
    logger.info("parser brand %s enabled=%s", name, enabled)
    return {"name": name, "enabled": enabled, "slug": brand_slug(name)}


def _split_feed_name(name: str) -> tuple[str, str]:
    """«Укр назва - English Name» → (english, ukrainian)."""
    raw = (name or "").strip()
    if " - " in raw:
        left, right = raw.split(" - ", 1)
        uk, en = left.strip(), right.strip()
        if en:
            return en, uk
    return raw, ""


def _cosmy_product_url(
    product_id: int | None,
    *,
    product_option_value_id: int | None = None,
) -> str | None:
    """Сторінка товару на Cosmy з преселектом варіанту (обʼєм).

    На вітрині JS читає ``product_option_value_id`` і клікає ``[data-ov="…"]``.
    Без параметра відкривається перша опція (часто не той обʼєм, що в моніторингу).
    """
    try:
        pid = int(product_id)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    url = f"https://cosmy.com.ua/index.php?route=product/product&product_id={pid}"
    try:
        pov = int(product_option_value_id)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        pov = 0
    if pov > 0:
        url += f"&product_option_value_id={pov}"
    return url


def _infer_product_option_value_id(model: str | None) -> int | None:
    """У фіді Cosmy суфікс моделі (04906-2712) часто = product_option_value_id на вітрині."""
    import re

    if not model:
        return None
    match = re.search(r"-(\d{3,8})$", str(model).strip())
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _catalog_item_product_url(item: dict) -> str | None:
    pov = item.get("product_option_value_id")
    if pov is None:
        pov = _infer_product_option_value_id(item.get("model"))
    return _cosmy_product_url(item.get("product_id"), product_option_value_id=pov)


def _normalize_catalog_items(items: list[dict]) -> list[dict]:
    out: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        pov = row.get("product_option_value_id")
        if pov is None:
            pov = _infer_product_option_value_id(row.get("model"))
            if pov is not None:
                row["product_option_value_id"] = pov
        row["product_url"] = _catalog_item_product_url(row)
        out.append(row)
    return out


def _build_parser_query(
    *,
    en_name: str,
    uk_name: str = "",
    volume: str = "",
    model: str | None = None,
) -> str:
    """Латиниця + укр. назва + артикул — щоб UA-магазини знаходили товар без французького ключа."""
    import re

    parts: list[str] = []
    en = (en_name or "").strip()
    uk = (uk_name or "").strip()
    vol = (volume or "").strip()
    if en:
        parts.append(en)
    if uk:
        en_l, uk_l = en.lower(), uk.lower()
        if uk_l not in en_l and en_l not in uk_l:
            parts.append(uk)
    code = (model or "").strip()
    if code and re.fullmatch(r"\d{4,8}(?:-\d{2,8})?", code):
        blob = " ".join(parts).lower()
        if code.lower() not in blob:
            parts.append(code)
    if vol:
        blob = " ".join(parts).lower()
        if vol.lower() not in blob:
            parts.append(vol)
    return " ".join(parts).strip()


def _catalog_row(
    *,
    item_id: int,
    brand: str,
    name: str,
    name_uk: str,
    query: str,
    model: str | None,
    our_price: float | None,
    product_id: int | None = None,
    option_value: str | None = None,
    product_option_value_id: int | None = None,
    image_url: str | None = None,
) -> dict:
    pov = product_option_value_id
    return {
        "id": item_id,
        "product_id": product_id,
        "model": (model or "").strip() or None,
        "brand": brand,
        "name": name,
        "name_uk": name_uk or None,
        "query": query,
        "our_price": our_price,
        "option_value": option_value,
        "product_option_value_id": pov,
        "image_url": image_url,
        "product_url": _cosmy_product_url(product_id, product_option_value_id=pov),
    }


async def load_catalog_from_feed() -> list[dict]:
    """Каталог для парсера = товари з Cosmy XML-фіда (та сама БД, що й «Товари»)."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from bot.database.products_engine import AsyncProductsSessionLocal
    from bot.database.products_models import Product

    rows: list[dict] = []
    async with AsyncProductsSessionLocal() as session:
        result = await session.execute(
            select(Product)
            .options(selectinload(Product.options), selectinload(Product.brand))
            .order_by(Product.brand_name, Product.name, Product.product_id)
        )
        products = result.scalars().all()

    for product in products:
        brand = (product.brand_name or "").strip()
        if not brand and product.brand is not None:
            brand = (product.brand.name or "").strip()
        brand = brand or "—"
        en_name, uk_name = _split_feed_name(product.name or "")
        options = list(product.options or [])
        use_options = bool(product.has_options and options)

        if use_options:
            for opt in options:
                price = opt.selling_price_catalog or opt.selling_price_order or product.selling_price_catalog
                if price is not None:
                    try:
                        price = float(price)
                    except (TypeError, ValueError):
                        price = None
                vol = (opt.option_value_name or "").strip()
                model = opt.option_model or product.model or product.model_internal
                query = _build_parser_query(
                    en_name=en_name,
                    uk_name=uk_name,
                    volume=vol,
                    model=model,
                )
                pov = opt.product_option_value_id
                item_id = (_OPTION_ID_OFFSET + int(pov)) if pov is not None else (_OPTION_ID_OFFSET + int(opt.id))
                rows.append(
                    _catalog_row(
                        item_id=item_id,
                        brand=brand,
                        name=en_name,
                        name_uk=uk_name,
                        query=query,
                        model=model,
                        our_price=price,
                        product_id=product.product_id,
                        option_value=vol or None,
                        product_option_value_id=opt.product_option_value_id,
                        image_url=opt.option_image_url or product.image_url,
                    )
                )
            continue

        price = product.selling_price_catalog or product.selling_price_order
        if price is not None:
            try:
                price = float(price)
            except (TypeError, ValueError):
                price = None
        model = product.model or product.model_internal or product.sku
        en = en_name or (product.name or "").strip()
        rows.append(
            _catalog_row(
                item_id=int(product.product_id),
                brand=brand,
                name=en,
                name_uk=uk_name,
                query=_build_parser_query(en_name=en, uk_name=uk_name, model=model),
                model=model,
                our_price=price,
                product_id=product.product_id,
                image_url=product.image_url,
            )
        )
    return rows


def _run_coro_sync(coro):
    """Запуск async з sync-контексту (фоновий потік парсера / sync helpers)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    return _ASYNC_POOL.submit(asyncio.run, coro).result()


def load_catalog(*, force: bool = False) -> list[dict]:
    """Каталог моніторингу з фіда Cosmy; fallback на локальний catalog.json."""
    now = time.monotonic()
    cached = _CATALOG_CACHE.get("items")
    if (
        not force
        and cached is not None
        and (now - float(_CATALOG_CACHE.get("at") or 0)) < _CATALOG_CACHE_TTL
    ):
        return list(cached)

    # На мікросервісі без products DB — лише синхронізований каталог у SQLite.
    if os.getenv("PARSER_CATALOG_SQLITE_ONLY", "").strip().lower() in ("1", "true", "yes", "on"):
        data = store.load_catalog_products()
        fallback = _normalize_catalog_items(data if isinstance(data, list) else [])
        _CATALOG_CACHE["items"] = fallback
        _CATALOG_CACHE["at"] = now
        return list(fallback)

    try:
        items = _run_coro_sync(load_catalog_from_feed())
        if items:
            normalized = _normalize_catalog_items(items)
            _CATALOG_CACHE["items"] = normalized
            _CATALOG_CACHE["at"] = now
            return list(normalized)
    except Exception:
        logger.exception("Failed to load parser catalog from products feed DB")

    data = store.load_catalog_products()
    fallback = _normalize_catalog_items(data if isinstance(data, list) else [])
    _CATALOG_CACHE["items"] = fallback
    _CATALOG_CACHE["at"] = now
    return list(fallback)


def replace_catalog(products: list[dict]) -> dict:
    """Повна заміна каталогу (для синхронізації з веб-сервера на мікросервіс)."""
    rows = _normalize_catalog_items([p for p in (products or []) if isinstance(p, dict)])
    store.save_catalog_products(rows)
    _CATALOG_CACHE["items"] = list(rows)
    _CATALOG_CACHE["at"] = time.monotonic()
    return {"ok": True, "count": len(rows)}


def get_report_excel(brand: str | None = None) -> tuple[bytes | None, str]:
    """Байти Excel + імʼя файлу. (None, msg) якщо звіту немає."""
    if brand:
        brand_name = brand_from_slug(brand) or brand
        xlsx = ensure_brand_excel(brand_name)
        if xlsx is None or not Path(xlsx).is_file():
            return None, "Звіт цього бренду ще не зібрано"
        return Path(xlsx).read_bytes(), f"cosmy-price-{brand_slug(brand_name)}.xlsx"
    if not REPORT_XLSX.exists():
        return None, "Звіт ще не згенеровано"
    return REPORT_XLSX.read_bytes(), "cosmy-price-report.xlsx"


def catalog_for_brand(brand: str) -> list[dict]:
    brand_l = (brand or "").strip().lower()
    return [p for p in load_catalog() if (p.get("brand") or "").strip().lower() == brand_l]


def merge_brand_rows(brand: str, report: dict | None) -> list[dict]:
    """Усі товари бренду з фіда + дані останнього парсингу (якщо є)."""
    catalog = catalog_for_brand(brand)
    report_rows = (report or {}).get("rows") or []
    by_id: dict[Any, dict] = {}
    by_model: dict[str, dict] = {}
    by_query: dict[str, dict] = {}
    for row in report_rows:
        if row.get("id") is not None:
            by_id[row["id"]] = row
        model = (row.get("model") or "").strip()
        if model:
            by_model.setdefault(model, row)
        q = (row.get("query") or row.get("name") or "").strip().lower()
        if q:
            by_query.setdefault(q, row)

    merged: list[dict] = []
    seen_report_ids: set[Any] = set()
    for item in catalog:
        match = by_id.get(item.get("id"))
        if not match:
            model = (item.get("model") or "").strip()
            if model:
                match = by_model.get(model)
        if not match:
            q = (item.get("query") or item.get("name") or "").strip().lower()
            if q:
                match = by_query.get(q)
        if match:
            seen_report_ids.add(match.get("id"))
            row = dict(match)
            row.setdefault("brand", brand)
            row["name"] = row.get("name") or item.get("name")
            row["name_uk"] = row.get("name_uk") or item.get("name_uk")
            row["model"] = row.get("model") or item.get("model")
            row["our_price"] = row.get("our_price") if row.get("our_price") is not None else item.get("our_price")
            row["option_value"] = item.get("option_value") or row.get("option_value")
            row["image_url"] = item.get("image_url") or row.get("image_url")
            row["product_url"] = _catalog_item_product_url({**row, **item})
            row["product_id"] = item.get("product_id") or row.get("product_id")
            row["from_report"] = True
            merged.append(row)
        else:
            merged.append(
                {
                    **item,
                    "shops": {},
                    "found": 0,
                    "found_label": "ще не парсився",
                    "position": None,
                    "advice": "Запустіть моніторинг бренду, щоб порівняти ціни",
                    "diff_min_pct": None,
                    "market_min": None,
                    "from_report": False,
                }
            )

    # рядки зі старого звіту, яких уже немає у фіді
    for row in report_rows:
        if row.get("id") in seen_report_ids:
            continue
        extra = dict(row)
        extra["from_report"] = True
        extra["orphan"] = True
        merged.append(extra)

    merged.sort(
        key=lambda r: (
            0 if r.get("from_report") and r.get("diff_min_pct") is not None else 1,
            float(r.get("diff_min_pct") or 0) if r.get("diff_min_pct") is not None else 0,
            (r.get("name") or r.get("query") or "").lower(),
        )
    )
    return merged


def load_history() -> list[dict]:
    return store.load_history()


def get_history_item(item_id: str) -> dict | None:
    for item in load_history():
        if item.get("id") == item_id:
            return item
    return None


def save_history(items: list) -> None:
    store.save_history(list(items), limit=MAX_HISTORY)


def append_history(record: dict) -> list[dict]:
    """Додає запис під транзакцією — паралельні пошуки не затирають одне одного."""
    return store.append_history(record, limit=MAX_HISTORY)


def history_preview(item: dict) -> dict:
    return {
        "id": item.get("id"),
        "query": item.get("query"),
        "our_price": item.get("our_price"),
        "created_at": item.get("created_at"),
        "found": item.get("found"),
        "market_avg": item.get("market_avg"),
        "market_min": item.get("market_min"),
        "market_max": item.get("market_max"),
        "position": item.get("position"),
    }


def site_names() -> list[str]:
    _ensure_parser_path()
    from cosmy_parse import SITES  # type: ignore

    return [s.name for s in SITES]


def site_meta() -> list[dict]:
    _ensure_parser_path()
    from catalog_report import SITE_KINDS  # type: ignore
    from cosmy_parse import SITES  # type: ignore

    settings = load_settings()
    enabled = set(settings.get("enabled_sites") or [])
    return [
        {
            "name": s.name,
            "kind": getattr(s, "kind", ""),
            "kind_label": SITE_KINDS.get(getattr(s, "kind", ""), getattr(s, "kind", "")),
            "base_url": getattr(s, "base_url", ""),
            "enabled": s.name in enabled,
        }
        for s in SITES
    ]


def load_brand_status() -> dict:
    return store.load_brand_status()


def _update_brand_status(brand: str, **fields: Any) -> None:
    def _apply(status: dict) -> None:
        row = dict(status.get(brand) or {})
        row.update(fields)
        row["brand"] = brand
        status[brand] = row

    store.mutate_brand_status(_apply)


def _fail_brand_status(brand: str, error: str) -> None:
    """Помилка прогону лише якщо бренд уже мав звіт; інакше лишається «ще не парсився»."""
    name = (brand or "").strip()
    if not name:
        return
    rep = _reports_stats_by_brand().get(name) or {}
    st = load_brand_status().get(name) or {}
    parsed = int(rep.get("parsed_count") or st.get("total") or 0)
    if _brand_ever_parsed(st, rep, parsed_count=parsed):
        _update_brand_status(name, state="error", error=error)
    else:
        _update_brand_status(name, state="never", error=None)


_LAST_BRAND_SEED_AT = 0.0
_BRAND_SEED_TTL = 300.0  # не частіше раз на 5 хв з list_brands / live


def _brand_ever_parsed(st: dict, rep: dict, *, parsed_count: int) -> bool:
    """Чи є збережений звіт або минулий успішний прогін (не лише запис про fail)."""
    if int(parsed_count or 0) > 0:
        return True
    if (rep.get("last_parsed_at") or "").strip():
        return True
    if (st.get("state") or "").strip() == "done":
        return True
    last = (st.get("last_parsed_at") or "").strip()
    if last:
        return True
    return False


def _effective_brand_state(
    st: dict,
    rep: dict,
    *,
    catalog_count: int,
    parsed_count: int,
) -> str:
    """UI-стан: без звіту — «ще не парсився», не «помилка»."""
    state = (st.get("state") or "").strip() or "never"
    if state == "error" and not _brand_ever_parsed(st, rep, parsed_count=parsed_count):
        return "never"
    if state != "error":
        return state
    rep_at = (rep.get("last_parsed_at") or "").strip()
    st_at = (st.get("last_parsed_at") or "").strip()
    if rep_at and (not st_at or rep_at >= st_at):
        return "done"
    if catalog_count and parsed_count >= catalog_count:
        return "done"
    return "error"


def _brand_row_from_report(
    rep: dict,
    *,
    catalog_count: int,
    parsed_count: int,
) -> dict:
    """Поля brand_status після успішного звіту або reconcile."""
    last_at = (rep.get("last_parsed_at") or "").strip()
    total = int(parsed_count or rep.get("parsed_count") or 0)
    return {
        "state": "done",
        "error": None,
        "last_parsed_at": last_at or None,
        "total": total or None,
        "source": "reconciled",
    }


def _reconcile_brand_status(
    report_index: dict[str, dict],
    catalog_counts: dict[str, int],
    *,
    brands: list[str] | None = None,
    job_error: str | None = None,
) -> None:
    """Скидає залиплі running/error, якщо у SQLite уже є звіт або job не активний."""
    job = report_status()
    job_state = job.get("state")
    job_brand = (job.get("brand") or "").strip().lower()
    targets = brands if brands is not None else list(catalog_counts.keys())

    def _apply(status: dict) -> None:
        for brand in targets:
            count = int(catalog_counts.get(brand) or 0)
            st = dict(status.get(brand) or {})
            prev_state = (st.get("state") or "").strip()
            if prev_state not in ("running", "error"):
                continue
            rep = report_index.get(brand) or {}
            parsed = int(rep.get("parsed_count") or st.get("total") or 0)
            if (
                prev_state == "running"
                and job_state == "running"
                and job_brand
                and job_brand == brand.strip().lower()
            ):
                continue
            eff = _effective_brand_state(
                st, rep, catalog_count=count, parsed_count=parsed
            )
            if eff == "done" or rep.get("last_parsed_at"):
                st.update(_brand_row_from_report(rep, catalog_count=count, parsed_count=parsed))
            elif prev_state == "running":
                if _brand_ever_parsed(st, rep, parsed_count=parsed):
                    st.update(
                        state="error",
                        error=job_error or st.get("error") or "прогін перервано",
                    )
                else:
                    st.update(state="never", error=None)
            elif prev_state == "error":
                if not _brand_ever_parsed(st, rep, parsed_count=parsed):
                    st.update(state="never", error=None)
                else:
                    continue
            else:
                continue
            st["brand"] = brand
            status[brand] = st

    try:
        store.mutate_brand_status(_apply)
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower():
            logger.warning("Skip brand status reconcile: database is locked")
            return
        raise


def _seed_brand_status_from_report(report: dict | None = None, *, force: bool = False) -> None:
    """Підсів статусів з full-звіту. На read-шляхах — throttled, щоб не лочити SQLite."""
    global _LAST_BRAND_SEED_AT
    explicit = report is not None
    if not explicit and not force and time.time() - _LAST_BRAND_SEED_AT < _BRAND_SEED_TTL:
        return
    report = report if explicit else store.load_report(store.FULL_SCOPE)
    if not isinstance(report, dict):
        return
    generated = report.get("generated_at")
    rows = report.get("rows") or []
    if not generated or not rows:
        return
    by_brand: dict[str, list] = {}
    for row in rows:
        brand = (row.get("brand") or "").strip() or "—"
        by_brand.setdefault(brand, []).append(row)

    changed = False

    def _apply(status: dict) -> None:
        nonlocal changed
        for brand, brand_rows in by_brand.items():
            prev = status.get(brand) or {}
            if prev.get("state") == "running":
                continue
            prev_at = (prev.get("last_parsed_at") or "").strip()
            if prev.get("state") == "done" and prev_at and generated and prev_at >= generated:
                continue
            if prev.get("state") == "error" and prev_at and generated and prev_at > generated:
                continue
            status[brand] = {
                "brand": brand,
                "last_parsed_at": generated,
                "total": len(brand_rows),
                "state": "done",
                "source": "full_report",
                "error": None,
            }
            changed = True

    try:
        store.mutate_brand_status(_apply)
    except sqlite3.OperationalError as exc:
        # Під час активного парсингу write-lock — пропускаємо seed, не валимо live API.
        if "locked" in str(exc).lower():
            logger.warning("Skip brand status seed: database is locked")
            return
        raise
    _LAST_BRAND_SEED_AT = time.time()


def compute_price_stats(rows: list[dict] | None) -> dict:
    """Загальна статистика цін по рядках звіту / merged-таблиці бренду."""
    rows = rows or []
    parsed = [
        r
        for r in rows
        if r.get("from_report")
        or r.get("shops")
        or r.get("diff_min_pct") is not None
        or (isinstance(r.get("found"), int) and r.get("found") > 0)
    ]
    # якщо це чистий звіт без from_report — усі рядки зпаршені
    if not parsed and rows and any(r.get("position") or r.get("diff_min_pct") is not None for r in rows):
        parsed = list(rows)

    diffs: list[float] = []
    for r in parsed:
        v = r.get("diff_min_pct")
        if v is None:
            continue
        try:
            diffs.append(float(v))
        except (TypeError, ValueError):
            continue

    cheaper = sum(1 for d in diffs if d < -0.5)  # ми дешевші за мін. ринку
    at_min = sum(1 for d in diffs if -0.5 <= d <= 3)
    dearer = sum(1 for d in diffs if d > 3)
    no_market = len(parsed) - len(diffs)

    positions: dict[str, int] = {}
    for r in parsed:
        pos = (r.get("position") or "").strip()
        if pos:
            positions[pos] = positions.get(pos, 0) + 1

    avg_diff = round(sum(diffs) / len(diffs), 1) if diffs else None
    med_diff = None
    if diffs:
        s = sorted(diffs)
        mid = len(s) // 2
        med_diff = round(s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2, 1)

    return {
        "parsed": len(parsed),
        "with_diff": len(diffs),
        "avg_diff_min_pct": avg_diff,
        "median_diff_min_pct": med_diff,
        "cheaper_count": cheaper,
        "at_market_count": at_min,
        "dearer_count": dearer,
        "no_market_count": no_market,
        "positions": positions,
        "best_count": positions.get("дешевше за всіх", 0),
        "min_level_count": positions.get("на рівні мінімуму", 0),
        "in_market_count": positions.get("в ринку", 0),
        "overpriced_count": positions.get("дорожче за ринок", 0),
    }


def _reports_stats_by_brand() -> dict[str, dict]:
    """Швидкий індекс: бренд → {parsed_count, last_parsed_at, stats} зі збережених звітів."""
    index: dict[str, dict] = {}

    def _ingest(brand: str, rows: list, generated_at: str | None) -> None:
        brand = (brand or "").strip() or "—"
        stats = compute_price_stats(rows)
        prev = index.get(brand)
        if prev and prev.get("last_parsed_at") and generated_at and prev["last_parsed_at"] >= generated_at:
            return
        index[brand] = {
            "parsed_count": stats["parsed"] or len(rows),
            "last_parsed_at": generated_at,
            "stats": stats,
        }

    full_later: list[tuple[str | None, str, list]] = []
    for report in store.load_stat_reports():
        rows = report.get("rows") or []
        if not rows:
            continue
        generated = report.get("generated_at")
        if report.get("_scope") == store.FULL_SCOPE:
            by_brand: dict[str, list] = {}
            for row in rows:
                brand = (row.get("brand") or "").strip() or "—"
                by_brand.setdefault(brand, []).append(row)
            for brand, brand_rows in by_brand.items():
                full_later.append((generated, brand, brand_rows))
            continue
        brand = next((r.get("brand") for r in rows if r.get("brand")), None) or "—"
        _ingest(str(brand), rows, generated)
    for generated, brand, brand_rows in full_later:
        _ingest(brand, brand_rows, generated)
    return index


def list_brands() -> list[dict]:
    catalog = load_catalog()
    counts: dict[str, int] = {}
    for item in catalog:
        brand = (item.get("brand") or "").strip() or "—"
        counts[brand] = counts.get(brand, 0) + 1
    _seed_brand_status_from_report()
    report_index = _reports_stats_by_brand()
    _reconcile_brand_status(report_index, counts)
    status = load_brand_status()
    disabled = disabled_brand_set()
    out = []
    for brand, total in sorted(counts.items(), key=lambda x: x[0].lower()):
        st = status.get(brand) or {}
        rep = report_index.get(brand) or {}
        enabled = brand.lower() not in disabled
        parsed_count = rep.get("parsed_count")
        if parsed_count is None:
            parsed_count = st.get("total")
        last_at = rep.get("last_parsed_at") or st.get("last_parsed_at")
        parsed_n = int(parsed_count or 0)
        state = _effective_brand_state(
            st,
            rep,
            catalog_count=total,
            parsed_count=parsed_n,
        )
        if state == "never" and last_at:
            state = "done"
        out.append(
            {
                "name": brand,
                "slug": brand_slug(brand),
                "catalog_count": total,
                "parsed_count": parsed_n,
                "last_parsed_at": last_at,
                "state": state,
                "error": st.get("error") if state == "error" else None,
                "enabled": enabled,
                "stats": rep.get("stats") or {},
                "url": f"/parser/brands/{quote(brand_slug(brand), safe='')}",
            }
        )
    return out


def brand_report_paths(brand: str) -> tuple[Path, Path]:
    slug = brand_slug(brand)
    folder = BRANDS_DIR / slug
    return folder / "report.json", folder / "report.xlsx"


def ensure_brand_excel(brand: str) -> Path | None:
    """Файл Excel одного бренду. Якщо його ще немає — збирає зі збереженого звіту."""
    _slug_path, xlsx_path = brand_report_paths(brand)
    report = load_brand_report(brand)
    if not report or not report.get("rows"):
        return xlsx_path if xlsx_path.is_file() else None
    saved_at = float(report.get("_saved_at") or 0)
    if xlsx_path.is_file() and xlsx_path.stat().st_mtime >= saved_at:
        return xlsx_path
    _ensure_parser_path()
    from catalog_report import write_excel  # type: ignore

    xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    write_excel(report, xlsx_path)
    return xlsx_path


def load_brand_report(brand: str) -> dict | None:
    data = store.load_report(brand_slug(brand))
    if isinstance(data, dict) and data.get("rows"):
        return data
    full = store.load_report(store.FULL_SCOPE)
    if not isinstance(full, dict):
        return None
    rows = [r for r in (full.get("rows") or []) if (r.get("brand") or "").strip() == brand]
    if not rows:
        return None
    return {
        "generated_at": full.get("generated_at"),
        "started_at": full.get("started_at"),
        "total": len(rows),
        "sites": full.get("sites") or [],
        "rows": rows,
        "source": "full_report",
        "_saved_at": full.get("_saved_at") or 0,
    }


def _slim_near(items: Any, limit: int = 2) -> list[dict]:
    out: list[dict] = []
    for it in (items or [])[:limit]:
        if isinstance(it, dict) and (it.get("title") or it.get("url")):
            out.append(
                {
                    "title": it.get("title"),
                    "url": it.get("url"),
                    "price": it.get("price"),
                    "match": it.get("match"),
                    "volume": it.get("volume"),
                }
            )
    return out


def slim_row(row: dict) -> dict:
    """Стиснутий рядок для таблиці бренду та live-API (без raw, з найближчими кандидатами)."""
    shops_out: dict[str, dict] = {}
    for site, info in (row.get("shops") or {}).items():
        if not isinstance(info, dict):
            continue
        status = info.get("status")
        shop = {
            "status": status,
            "price": info.get("price"),
            "url": info.get("url") or info.get("best_url"),
            "diff_pct": info.get("diff_pct"),
            "note": info.get("note"),
            "title": info.get("title") or info.get("best_title"),
            "volume": info.get("volume"),
            "reason": info.get("reason"),
            "match": info.get("match"),
            "in_stock": info.get("in_stock"),
        }
        if status not in ("match", "no_brand"):
            shop["near"] = _slim_near(info.get("near") or info.get("alternatives"))
        shops_out[site] = {k: v for k, v in shop.items() if v not in (None, [], "")}
    return {
        "id": row.get("id"),
        "model": row.get("model"),
        "brand": row.get("brand"),
        "name": row.get("name"),
        "name_uk": row.get("name_uk"),
        "query": row.get("query"),
        "our_price": row.get("our_price"),
        "option_value": row.get("option_value"),
        "ref_volume": row.get("ref_volume"),
        "image_url": row.get("image_url"),
        "product_url": _catalog_item_product_url(row),
        "diff_min_pct": row.get("diff_min_pct"),
        "diff_min_uah": row.get("diff_min_uah"),
        "diff_median_pct": row.get("diff_median_pct"),
        "market_min": row.get("market_min"),
        "market_median": row.get("market_median"),
        "market_max": row.get("market_max"),
        "min_site": row.get("min_site"),
        "min_url": row.get("min_url"),
        "found": row.get("found"),
        "found_label": row.get("found_label"),
        "shops_with_brand": row.get("shops_with_brand"),
        "position": row.get("position"),
        "advice": row.get("advice"),
        "shops": shops_out,
        "from_report": row.get("from_report", True),
    }


_slim_live_row = slim_row


def report_status() -> dict:
    global _REPORT_STATUS
    disk = _job_from_disk()
    if disk is not None:
        payload = _resolve_job_payload({k: v for k, v in disk.items() if k != "row"})
        if payload.get("state") in ("done", "error"):
            _REPORT_STATUS = dict(payload)
    else:
        payload = {k: v for k, v in _REPORT_STATUS.items() if k != "row"}
        payload["live_count"] = len(_LIVE_ROWS)
        if _LIVE_BRAND:
            payload["brand"] = payload.get("brand") or _LIVE_BRAND
        payload = _resolve_job_payload(payload)
        if payload.get("state") == "error" and "Фоновий процес зупинився" in (payload.get("error") or ""):
            # У памʼяті без актуального файлу — просто idle, щоб не блокувати новий запуск.
            payload = {**payload, "state": "idle"}
            payload.pop("error", None)
            _REPORT_STATUS = dict(payload)
    payload["has_file"] = REPORT_XLSX.exists()
    if payload.get("state") != "running":
        data = store.load_report(store.FULL_SCOPE) or {}
        if isinstance(data, dict):
            payload.setdefault("generated_at", data.get("generated_at"))
            payload.setdefault("total", data.get("total"))
    return payload


def report_status_for_brand(brand: str | None) -> dict:
    """Статус для сторінки бренду: чужий прогін/помилку не показуємо як свої."""
    status = report_status()
    if not brand:
        return status
    job_brand = (status.get("brand") or "").strip()
    if not job_brand:
        return status
    if job_brand.lower() == brand.strip().lower():
        return status
    # Інший бренд (авторотація чи ручний прогін) — на цій сторінці idle.
    if status.get("state") in ("running", "error", "done"):
        clean = {**status, "state": "idle"}
        clean.pop("error", None)
        clean.pop("phase", None)
        clean.pop("product", None)
        return clean
    return status


def live_brand_snapshot(brand: str, known: set | None = None) -> dict:
    """Статус + зібрані рядки для бренду. Незавершені рядки віддаємо знову, щоб дописати магазини."""
    brand_l = (brand or "").strip().lower()
    status = report_status_for_brand(brand)
    rows: list[dict] = []
    sites: list[str] = []
    try:
        sites = site_names()
    except Exception:
        sites = []

    job_brand = (report_status().get("brand") or "").strip().lower()
    disk_rows = _live_rows_from_disk()
    if status.get("state") == "running" and disk_rows and (not job_brand or job_brand == brand_l):
        rows = [
            r for r in disk_rows
            if not (r.get("brand") or "").strip() or (r.get("brand") or "").strip().lower() == brand_l
        ]
    elif _LIVE_BRAND and _LIVE_BRAND.strip().lower() == brand_l and _LIVE_ROWS:
        rows = list(_LIVE_ROWS.values())
    else:
        report = load_brand_report(brand)
        if report and report.get("rows"):
            rows = [_slim_live_row(r) for r in report["rows"]]
            if not sites:
                sites = list(report.get("sites") or [])

    if known:
        rows = [r for r in rows if r.get("partial") or str(r.get("id")) not in known]
    return {
        "status": status,
        "brand": brand,
        "rows": rows,
        "sites": sites,
    }


def _apply_runtime_settings(settings: dict) -> None:
    _ensure_parser_path()
    import catalog_report as cr  # type: ignore
    import cosmy_parse as cp  # type: ignore

    cr.SKU_WORKERS = int(settings.get("sku_workers") or cr.SKU_WORKERS)
    cr.PRICE_LOW_RATIO = float(settings.get("price_low_ratio") or cr.PRICE_LOW_RATIO)
    cr.PRICE_HIGH_RATIO = float(settings.get("price_high_ratio") or cr.PRICE_HIGH_RATIO)
    cr.AT_MARKET_PCT = float(settings.get("at_market_pct") or cr.AT_MARKET_PCT)
    cr.SUSPICIOUS_GAP_PCT = float(settings.get("suspicious_gap_pct") or cr.SUSPICIOUS_GAP_PCT)
    min_sim = float(settings.get("min_similarity") or 0.58)

    if not getattr(cp, "_ALL_SITES", None):
        cp._ALL_SITES = list(cp.SITES)
    enabled_names = settings.get("enabled_sites")
    if enabled_names is None:
        active = list(cp._ALL_SITES)
    else:
        enabled = set(enabled_names)
        active = [s for s in cp._ALL_SITES if s.name in enabled]
    for site in cp._ALL_SITES:
        site.min_similarity = min_sim
    cp.SITES = active
    cr.SITE_NAMES = [s.name for s in active]
    cr.SITES = active


def search_product(query: str, our_price: Any = None) -> dict:
    _ensure_parser_path()
    from cosmy_parse import parse_price, search_all  # type: ignore

    price = parse_price(our_price)
    result = search_all(query.strip(), our_price=price, debug=False)
    record = {
        "id": uuid.uuid4().hex[:12],
        "created_at": datetime.now(timezone.utc).isoformat(),
        **result,
    }
    history = append_history(record)
    record["history"] = [history_preview(item) for item in history]
    return record


def _read_search_status() -> dict:
    data = store.load_search()
    return data if isinstance(data, dict) else {"state": "idle"}


def search_status() -> dict:
    data = _read_search_status()
    if data.get("state") != "running":
        if data.get("state") == "done":
            data["history"] = [history_preview(item) for item in load_history()]
        return data
    alive = _SEARCH_THREAD is not None and _SEARCH_THREAD.is_alive()
    age = time.time() - float(data.get("updated_epoch") or 0)
    if not alive and age > 20:
        data["state"] = "error"
        data["error"] = "Фоновий пошук зупинився. Запустіть ще раз."
        store.save_search(data)
    return data


def _write_search_status(payload: dict) -> None:
    payload["updated_epoch"] = time.time()
    store.save_search(payload)


def _run_search_job(job_id: str, query: str, our_price: Any) -> None:
    settings = load_settings()
    try:
        _apply_runtime_settings(settings)
    except Exception:
        logger.exception("Search settings were not applied")

    def _finish_ok(result: dict) -> None:
        record = {
            "id": job_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "state": "done",
            **result,
        }
        history = append_history({k: v for k, v in record.items() if k != "state"})
        record["history"] = [history_preview(item) for item in history]
        current = _read_search_status()
        if current.get("id") == job_id:
            current.update(record)
            _write_search_status(current)

    def _finish_err(exc: Exception) -> None:
        logger.exception("Background product search failed")
        append_parse_event(level="error", kind="job", message=f"Пошук «{query}» зупинився: {exc}")
        current = _read_search_status()
        if current.get("id") == job_id:
            current["state"] = "error"
            current["error"] = str(exc)
            _write_search_status(current)

    try:
        from config import PARSER_FALLBACK_LOCAL
        from web.parser_worker_client import search_on_worker, worker_enabled, worker_reachable

        if worker_enabled():
            ok, reason = worker_reachable()
            if not ok and not PARSER_FALLBACK_LOCAL:
                _finish_err(RuntimeError(reason))
                return

            def on_remote_progress(info: dict) -> None:
                current = _read_search_status()
                if current.get("id") != job_id:
                    return
                current["results"] = list(info.get("results") or [])
                current["misses"] = list(info.get("misses") or [])
                current["found"] = int(info.get("found") or len(current["results"]))
                current["sites_done"] = int(
                    info.get("sites_done")
                    or (len(current["results"]) + len(current["misses"]))
                )
                current["updated_epoch"] = time.time()
                _write_search_status(current)

            if ok:
                result = search_on_worker(
                    query,
                    our_price,
                    settings=settings,
                    on_progress=on_remote_progress,
                )
                _finish_ok(result)
                return
            append_parse_event(
                level="warn",
                kind="job",
                message=f"Worker пошу недоступний ({reason}), локальний пошук",
            )
    except Exception as exc:
        logger.exception("Remote search worker failed")
        from config import PARSER_FALLBACK_LOCAL, PARSER_SERVICE_URL

        if PARSER_SERVICE_URL and not PARSER_FALLBACK_LOCAL:
            _finish_err(exc)
            return
        append_parse_event(
            level="warn",
            kind="job",
            message=f"Worker пошу недоступний ({exc}), локальний пошук",
        )

    _ensure_parser_path()
    from cosmy_parse import parse_price, search_all  # type: ignore

    price = parse_price(our_price)
    bucket = {"results": [], "misses": []}
    last_flush = 0.0

    def publish(*, final: dict | None = None) -> None:
        nonlocal last_flush
        now = time.time()
        if final is None and now - last_flush < 0.4:
            return
        last_flush = now
        current = _read_search_status()
        if current.get("id") != job_id:
            return
        if final is not None:
            current.update(final)
        else:
            current["results"] = list(bucket["results"])
            current["misses"] = list(bucket["misses"])
            current["found"] = len(bucket["results"])
            current["sites_done"] = len(bucket["results"]) + len(bucket["misses"])
        _write_search_status(current)

    def on_site(item: dict) -> None:
        if item.get("status") == "ok":
            bucket["results"].append(item)
        else:
            bucket["misses"].append(item)
        publish()

    try:
        result = search_all(query, our_price=price, debug=False, on_site=on_site)
        record = {
            "id": job_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "state": "done",
            **result,
        }
        history = append_history({k: v for k, v in record.items() if k != "state"})
        record["history"] = [history_preview(item) for item in history]
        publish(final=record)
    except Exception as exc:
        _finish_err(exc)


def start_search(query: str, our_price: Any = None) -> tuple[dict, int]:
    """Пошук у фоновому потоці: запит до сайту повертається одразу і не блокує решту адмінки."""
    global _SEARCH_THREAD
    query = (query or "").strip()
    with _SEARCH_LOCK:
        current = search_status()
        if (
            current.get("state") == "running"
            and _SEARCH_THREAD is not None
            and _SEARCH_THREAD.is_alive()
        ):
            return current, 409
        job_id = uuid.uuid4().hex[:12]
        status = {
            "state": "running",
            "id": job_id,
            "query": query,
            "our_price": our_price,
            "results": [],
            "misses": [],
            "found": 0,
            "sites_done": 0,
            "started_epoch": time.time(),
            "updated_epoch": time.time(),
        }
        _write_search_status(status)
        thread = threading.Thread(
            target=_run_search_job,
            args=(job_id, query, our_price),
            name="parser-search",
            daemon=True,
        )
        _SEARCH_THREAD = thread
        thread.start()
        return dict(status), 200


def _run_report_job(*, brand: str | None = None) -> None:
    global _REPORT_STATUS, _LIVE_ROWS, _LIVE_BRAND
    _LIVE_ROWS.clear()
    settings = load_settings()
    try:
        _apply_runtime_settings(settings)
    except Exception as exc:
        logger.exception("Failed to apply parser settings")
        append_parse_event(level="error", kind="job", brand=brand, message=f"Налаштування парсера: {exc}")
        _publish_status({"state": "error", "error": f"Налаштування парсера: {exc}", "brand": brand}, force=True)
        if brand:
            _fail_brand_status(brand, str(exc))
        return

    catalog = load_catalog(force=True)
    disabled = disabled_brand_set()
    ids = None
    job_catalog = catalog
    out_xlsx = REPORT_XLSX
    report_scope = store.FULL_SCOPE
    brands_to_touch: list[str] = []

    if brand:
        brand_l = brand.strip().lower()
        brand_items = [p for p in catalog if (p.get("brand") or "").strip().lower() == brand_l]
        job_catalog = brand_items
        ids = {p.get("id") for p in brand_items if p.get("id") is not None}
        _json_path, out_xlsx = brand_report_paths(brand)
        report_scope = brand_slug(brand)
        brands_to_touch = [brand]
        _LIVE_BRAND = brand
        _update_brand_status(brand, state="running", error=None)
    else:
        job_catalog = [
            p for p in catalog if (p.get("brand") or "").strip().lower() not in disabled
        ]
        brands_to_touch = sorted(
            {(p.get("brand") or "").strip() or "—" for p in job_catalog}
        )
        _LIVE_BRAND = None

    def progress(status: dict) -> None:
        status = dict(status)
        row = status.pop("row", None)
        if brand:
            status["brand"] = brand
        _publish_status(status, row=row if isinstance(row, dict) else None)

    def _persist_report(report: dict) -> None:
        """Зберегти звіт і Excel у локальній БД основного софта."""
        store.save_report(report_scope, report)
        try:
            _ensure_parser_path()
            from catalog_report import write_excel  # type: ignore

            out_xlsx.parent.mkdir(parents=True, exist_ok=True)
            write_excel(report, out_xlsx)
        except Exception:
            logger.exception("Failed to write report excel for scope %s", report_scope)

    def _finalize_ok(report: dict) -> None:
        generated = report.get("generated_at")
        for row in report.get("rows") or []:
            if isinstance(row, dict) and row.get("id") is not None:
                _LIVE_ROWS[row["id"]] = _slim_live_row(row)
        if brand:
            _update_brand_status(
                brand,
                state="done",
                last_parsed_at=generated,
                total=report.get("total"),
                source="brand_report",
                error=None,
            )
        else:
            _seed_brand_status_from_report(report)
            for b in brands_to_touch:
                _update_brand_status(
                    b,
                    state="done",
                    last_parsed_at=generated,
                    source="full_report",
                    error=None,
                )
        _publish_status({
            "state": "done",
            "phase": "done",
            "current": report.get("total"),
            "total": report.get("total"),
            "brand": brand,
            "product": "готово",
            "generated_at": generated,
        }, force=True)
        _log_report_problems(report, brand)
        if brand:
            _rotation_mark_finished(brand, ok=True)
        _schedule_night_chain()

    def _finalize_err(exc: Exception) -> None:
        logger.exception("Parser report failed")
        append_parse_event(
            level="error",
            kind="job",
            brand=brand,
            message=f"Прогін зупинився: {exc}",
        )
        _publish_status({"state": "error", "error": str(exc), "brand": brand}, force=True)
        if brand:
            _fail_brand_status(brand, str(exc))
        else:
            counts = {
                (b or "").strip() or "—": 0
                for b in brands_to_touch
            }
            for item in job_catalog:
                bname = (item.get("brand") or "").strip() or "—"
                counts[bname] = counts.get(bname, 0) + 1
            _reconcile_brand_status(
                _reports_stats_by_brand(),
                counts,
                brands=brands_to_touch,
                job_error=str(exc),
            )
        if brand:
            _rotation_mark_finished(brand, ok=False, error=str(exc))
        _schedule_night_chain()

    # --- remote scrape worker (БД лишається тут) ---
    try:
        from config import PARSER_FALLBACK_LOCAL
        from web.parser_worker_client import (
            run_catalog_on_worker,
            worker_enabled,
            worker_reachable,
        )

        if worker_enabled():
            ok, reason = worker_reachable()
            if not ok:
                append_parse_event(
                    level="error",
                    kind="job",
                    brand=brand,
                    message=f"Worker недоступний: {reason}",
                )
                if not PARSER_FALLBACK_LOCAL:
                    _finalize_err(RuntimeError(reason))
                    return
                append_parse_event(
                    level="warn",
                    kind="job",
                    brand=brand,
                    message=f"Worker недоступний ({reason}), локальний прогін (PARSER_FALLBACK_LOCAL=1)",
                )
            else:
                report = None
                try:
                    remote_job_holder: dict[str, str] = {}

                    def on_started(job_id: str) -> None:
                        remote_job_holder["id"] = job_id
                        _publish_status(
                            {
                                "state": "running",
                                "phase": "products",
                                "brand": brand,
                                "remote_job_id": job_id,
                                "current": 0,
                                "total": len(job_catalog),
                                "product": "worker scrape…",
                            },
                            force=True,
                        )

                    def progress_remote(status: dict) -> None:
                        status = dict(status)
                        if brand:
                            status["brand"] = brand
                        if remote_job_holder.get("id"):
                            status["remote_job_id"] = remote_job_holder["id"]
                        progress(status)

                    report = run_catalog_on_worker(
                        products=job_catalog,
                        settings=settings,
                        brand=brand,
                        on_progress=progress_remote,
                        on_started=on_started,
                    )
                except Exception as exc:
                    logger.exception("Remote catalog worker failed")
                    append_parse_event(
                        level="error",
                        kind="job",
                        brand=brand,
                        message=f"Worker scrape помилка: {exc}",
                    )
                    if not PARSER_FALLBACK_LOCAL:
                        _finalize_err(exc)
                        return
                    append_parse_event(
                        level="warn",
                        kind="job",
                        brand=brand,
                        message=f"Worker scrape помилка ({exc}), локальний прогін",
                    )
                else:
                    try:
                        _persist_report(report)
                        _finalize_ok(report)
                    except Exception as exc:
                        _finalize_err(exc)
                    return
    except Exception:
        logger.exception("Worker client import failed")
        from config import PARSER_FALLBACK_LOCAL, PARSER_SERVICE_URL

        if PARSER_SERVICE_URL and not PARSER_FALLBACK_LOCAL:
            _finalize_err(RuntimeError("Не вдалося підключити клієнт ParserService"))
            return

    _ensure_parser_path()
    try:
        from catalog_report import run_catalog  # type: ignore
    except Exception as exc:
        logger.exception("Failed to import catalog_report")
        append_parse_event(
            level="error",
            kind="job",
            brand=brand,
            message=f"Не вдалося завантажити парсер: {exc}",
        )
        _publish_status({
            "state": "error",
            "error": f"Не вдалося завантажити парсер (перевірте залежності cosmyparser): {exc}",
            "brand": brand,
        }, force=True)
        if brand:
            _fail_brand_status(brand, str(exc))
        return

    try:
        report = run_catalog(
            progress=progress,
            ids=ids,
            workers=int(settings.get("sku_workers") or 4),
            report_scope=report_scope,
            output_xlsx=out_xlsx,
            write_excel_file=True,
            catalog=job_catalog,
        )
        _finalize_ok(report)
    except Exception as exc:
        _finalize_err(exc)


def _kyiv_now() -> datetime:
    return datetime.now(KYIV_TZ)


def _kyiv_today() -> date:
    return _kyiv_now().date()


def _kyiv_moment(now: datetime | None = None) -> datetime:
    moment = now or _kyiv_now()
    if moment.tzinfo is None:
        return moment.replace(tzinfo=KYIV_TZ)
    return moment.astimezone(KYIV_TZ)


def is_weekend_kyiv(now: datetime | None = None) -> bool:
    """Субота/неділя за Києвом — автопарсинг вимкнено."""
    return _kyiv_moment(now).weekday() >= 5


def in_rotation_night(now: datetime | None = None) -> bool:
    """Нічне вікно 01:00–07:00 за Києвом (без урахування вихідних)."""
    moment = _kyiv_moment(now)
    return ROTATION_NIGHT_START_HOUR <= moment.hour < ROTATION_NIGHT_END_HOUR


def parser_auto_rotation_allowed(now: datetime | None = None) -> bool:
    """Автопарсинг: нічне вікно в будні (пн–пт)."""
    return in_rotation_night(now) and not is_weekend_kyiv(now)


def _next_weekday_on_or_after(day: date) -> date:
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def brands_for_rotation_day(names: list[str], day: int, cycle: int = CYCLE_DAYS) -> list[str]:
    """Рівна частка брендів на один день циклу. День стабільний для позиції в алфавіті."""
    ordered = sorted(
        {(name or "").strip() for name in names if (name or "").strip() and (name or "").strip() != "—"},
        key=str.lower,
    )
    slot = day % cycle
    return [name for index, name in enumerate(ordered) if index % cycle == slot]


def brand_rotation_day(name: str, names: list[str], cycle: int = CYCLE_DAYS) -> int | None:
    ordered = sorted(
        {(item or "").strip() for item in names if (item or "").strip() and (item or "").strip() != "—"},
        key=str.lower,
    )
    needle = (name or "").strip()
    for index, item in enumerate(ordered):
        if item.lower() == needle.lower():
            return index % cycle
    return None


def next_date_for_rotation_day(slot: int, today: date | None = None, cycle: int = CYCLE_DAYS) -> date:
    start = today or _kyiv_today()
    slot = slot % cycle
    for offset in range(0, cycle + 1):
        candidate = start + timedelta(days=offset)
        if candidate.toordinal() % cycle == slot:
            return candidate
    return start


def plan_rotation_update(state: dict, today: date, names: list[str]) -> dict | None:
    """Нова денна черга або доповнення незавершеної з попередньої ночі.

    Якщо вчорашня черга не допарсилась — сьогоднішній слот додається в кінець (catch-up).
    Свіжу чергу з нуля збираємо лише коли pending порожній і built_for != сьогодні.
    """
    today_s = today.isoformat()
    pending = list(state.get("pending") or [])
    built = state.get("built_for")

    if pending:
        if built == today_s:
            return None
        slot = today.toordinal() % CYCLE_DAYS
        today_brands = brands_for_rotation_day(names, slot)
        seen = {(b or "").strip().lower() for b in pending if (b or "").strip()}
        for brand in today_brands:
            key = (brand or "").strip().lower()
            if key and key not in seen:
                pending.append(brand)
                seen.add(key)
        return {
            **state,
            "built_for": today_s,
            "day": slot,
            "pending": pending,
            "done": list(state.get("done") or []),
            "skipped": list(state.get("skipped") or []),
            "current": state.get("current"),
            "start_retries": dict(state.get("start_retries") or {}),
            "run_retries": dict(state.get("run_retries") or {}),
        }

    if built == today_s:
        return None

    slot = today.toordinal() % CYCLE_DAYS
    return {
        "built_for": today_s,
        "day": slot,
        "pending": brands_for_rotation_day(names, slot),
        "done": [],
        "skipped": [],
        "current": None,
        "start_retries": {},
        "run_retries": {},
    }


def _format_kyiv(moment: datetime) -> str:
    local = moment.astimezone(KYIV_TZ) if moment.tzinfo else moment.replace(tzinfo=KYIV_TZ)
    return local.strftime("%d.%m.%Y %H:%M")


def next_rotation_info(
    *,
    brand: str | None = None,
    now: datetime | None = None,
    state: dict | None = None,
    names: list[str] | None = None,
) -> dict:
    """Коли наступне автооновлення і що саме буде в черзі."""
    moment = now or _kyiv_now()
    today = moment.date()
    enabled = names if names is not None else _enabled_brand_names()
    rot = state if state is not None else _load_rotation()
    pending = list(rot.get("pending") or [])
    night = in_rotation_night(moment)
    weekend = is_weekend_kyiv(moment)
    auto_allowed = parser_auto_rotation_allowed(moment)
    running = _rotation_busy()
    current = (rot.get("current") or "").strip() or None
    # Ручний старт не завжди пише rotation.current — беремо бренд з активного job.
    if running:
        job_brand = (report_status().get("brand") or "").strip()
        if job_brand:
            current = job_brand
    night_label = f"{ROTATION_NIGHT_START_HOUR:02d}:00–{ROTATION_NIGHT_END_HOUR:02d}:00"

    def night_start_on(day: date) -> datetime:
        return datetime(
            day.year,
            day.month,
            day.day,
            ROTATION_QUEUE_HOUR,
            20,
            tzinfo=KYIV_TZ,
        )

    brand_slot = brand_rotation_day(brand, enabled) if brand else None
    brand_next_day = (
        next_date_for_rotation_day(brand_slot, today) if brand_slot is not None else None
    )

    if running:
        nxt = {
            "kind": "running",
            "at": None,
            "at_label": "зараз",
            "summary": f"Зараз іде «{current or 'прогін'}»",
            "brands": pending[:8],
            "pending_count": len(pending),
        }
    elif auto_allowed and pending:
        nxt = {
            "kind": "drain",
            "at": moment,
            "at_label": "протягом ~10 хв",
            "summary": f"Наступний уночі: «{pending[0]}»",
            "brands": pending[:8],
            "pending_count": len(pending),
        }
    elif auto_allowed and moment.hour >= ROTATION_QUEUE_HOUR and rot.get("built_for") != today.isoformat():
        planned = brands_for_rotation_day(enabled, today.toordinal() % CYCLE_DAYS)
        at = night_start_on(today)
        if moment > at:
            at = moment
        nxt = {
            "kind": "build",
            "at": at,
            "at_label": _format_kyiv(at),
            "summary": f"Сьогодні вночі черга дня {today.toordinal() % CYCLE_DAYS + 1}",
            "brands": planned[:8],
            "pending_count": len(planned),
        }
    else:
        # День, або ніч уже розібрана / ще до збору черги.
        built_today = rot.get("built_for") == today.isoformat()
        if moment.hour < ROTATION_NIGHT_START_HOUR:
            target_day = today
        elif night and not built_today and moment.hour < ROTATION_QUEUE_HOUR:
            target_day = today
        elif night and built_today and not pending:
            target_day = today + timedelta(days=1)
        elif not night:
            target_day = _next_weekday_on_or_after(today + timedelta(days=1))
        else:
            target_day = today
        if weekend and not auto_allowed:
            target_day = _next_weekday_on_or_after(today + timedelta(days=1))
        target_day = _next_weekday_on_or_after(target_day)
        at = night_start_on(target_day)
        if moment >= at and target_day == today and not built_today:
            at = moment
        planned = brands_for_rotation_day(enabled, target_day.toordinal() % CYCLE_DAYS)
        nxt = {
            "kind": "tonight" if target_day == today else "next_night",
            "at": at,
            "at_label": _format_kyiv(at),
            "summary": (
                f"Наступне автооновлення {at.strftime('%d.%m.%Y')} о {at.strftime('%H:%M')} "
                f"(ніч {night_label})"
            ),
            "brands": planned[:8],
            "pending_count": len(planned),
        }

    brand_info = None
    if brand and brand_slot is not None and brand_next_day is not None:
        brand_at = night_start_on(brand_next_day)
        # Якщо бренд уже в сьогоднішній незавершеній черзі — він ще цієї ночі.
        if brand in pending or (running and current and current.lower() == brand.lower()):
            brand_summary = (
                f"«{brand}» у нічній черзі"
                + (" — зараз іде" if running and current and current.lower() == brand.lower() else "")
            )
            brand_at_label = "цієї ночі"
        elif brand_next_day == today and auto_allowed and moment.hour < ROTATION_NIGHT_END_HOUR:
            brand_summary = f"«{brand}» заплановано на цю ніч"
            brand_at_label = _format_kyiv(brand_at)
        else:
            brand_summary = (
                f"«{brand}» у циклі раз на {CYCLE_DAYS} дн. — наступна ніч "
                f"{brand_next_day.strftime('%d.%m.%Y')} після {ROTATION_QUEUE_HOUR:02d}:20"
            )
            brand_at_label = _format_kyiv(brand_at)
        brand_info = {
            "day": brand_slot + 1,
            "date": brand_next_day.isoformat(),
            "at_label": brand_at_label,
            "summary": brand_summary,
        }

    return {
        "night_label": night_label,
        "in_night": night,
        "weekend": weekend,
        "auto_rotation_allowed": auto_allowed,
        "queue_hour": ROTATION_QUEUE_HOUR,
        "next": {
            **nxt,
            "at_iso": nxt["at"].isoformat(timespec="minutes") if isinstance(nxt.get("at"), datetime) else None,
        },
        "brand": brand_info,
    }


def append_parse_event(
    *,
    level: str,
    message: str,
    brand: str | None = None,
    site: str | None = None,
    kind: str = "job",
) -> None:
    store.append_parse_event(
        ts=datetime.now(KYIV_TZ).isoformat(timespec="seconds"),
        level=level,
        kind=kind,
        brand=brand or None,
        site=site or None,
        message=message,
    )


def load_parse_log(limit: int = 250) -> list[dict]:
    return store.load_parse_events(limit)


def clear_parse_log() -> None:
    store.clear_parse_events()


def _log_report_problems(report: dict, brand: str | None) -> None:
    buckets: dict[tuple, int] = {}
    rows = [row for row in (report.get("rows") or []) if isinstance(row, dict)]
    for row in rows:
        row_brand = brand or (row.get("brand") or "").strip() or None
        for site, info in (row.get("shops") or {}).items():
            if not isinstance(info, dict):
                continue
            status = info.get("status")
            if status not in ("unavailable", "error"):
                continue
            reason = (info.get("reason") or status or "").strip()
            key = (row_brand, site, reason)
            buckets[key] = buckets.get(key, 0) + 1
    for (row_brand, site, reason), count in sorted(buckets.items(), key=lambda item: ((item[0][0] or ""), item[0][1])):
        append_parse_event(
            level="error",
            kind="shop",
            brand=row_brand,
            site=site,
            message=f"«{site}»: {count} товарів — {reason}",
        )
    total = report.get("total") or len(rows)
    label = brand or "каталог"
    shops = len({key[1] for key in buckets})
    if buckets:
        append_parse_event(
            level="warn",
            kind="job",
            brand=brand,
            message=f"«{label}» зібрано ({total} товарів), магазини з помилками: {shops}",
        )
    else:
        append_parse_event(
            level="info",
            kind="job",
            brand=brand,
            message=f"«{label}» зібрано без помилок магазинів ({total} товарів)",
        )


def _load_rotation() -> dict:
    return store.load_rotation()


def _save_rotation(state: dict) -> None:
    store.save_rotation(state)


def _enabled_brand_names() -> list[str]:
    disabled = disabled_brand_set()
    return [
        name
        for name in catalog_brand_names()
        if name != "—" and name.lower() not in disabled
    ]


def _rotation_busy() -> bool:
    """Чи реально йде прогін. Чужий/мертвий PID не блокує нічну чергу."""
    current = report_status()
    if current.get("state") != "running":
        return False
    return _parser_process_alive(current.get("pid"))


def _rotation_mark_finished(brand: str, *, ok: bool, error: str | None = None) -> None:
    """Після успіху — у done. Після помилки skipped не чіпаємо (retry робить advance)."""
    brand = (brand or "").strip()
    if not brand:
        return
    try:
        with _ROTATION_LOCK:
            state = _load_rotation()
            current = (state.get("current") or "").strip()
            if current and current.lower() != brand.lower():
                # Ручний прогін іншого бренду — ротацію не чіпаємо.
                return
            pending = [
                b for b in (state.get("pending") or [])
                if (b or "").strip().lower() != brand.lower()
            ]
            state["pending"] = pending
            if ok:
                done = [
                    b for b in (state.get("done") or [])
                    if (b or "").strip().lower() != brand.lower()
                ]
                done.append(brand)
                skipped = [
                    item for item in (state.get("skipped") or [])
                    if ((item.get("brand") if isinstance(item, dict) else str(item)) or "").strip().lower()
                    != brand.lower()
                ]
                state["done"] = done
                state["skipped"] = skipped
            elif error:
                state["current"] = None
            _save_rotation(state)
    except Exception:
        logger.exception("Failed to mark rotation finished for %s", brand)


def _schedule_night_chain(*, delay: float = 0.4) -> None:
    """Після завершення бренда одразу тягнемо наступний з нічної черги."""
    if not parser_auto_rotation_allowed():
        return

    def _run() -> None:
        time.sleep(delay)
        try:
            result = advance_parser_rotation()
            action = (result or {}).get("action")
            if action == "started":
                logger.info(
                    "Парсер: ланцюжок ночі → «%s», ще %s",
                    result.get("brand"),
                    result.get("left"),
                )
        except Exception:
            logger.exception("Парсер: нічний ланцюжок не вдався")

    threading.Thread(target=_run, name="parser-night-chain", daemon=True).start()


def _trim_job_log() -> None:
    try:
        if not JOB_LOG.is_file() or JOB_LOG.stat().st_size <= _JOB_LOG_MAX_BYTES:
            return
        text = JOB_LOG.read_text(encoding="utf-8", errors="replace")
        JOB_LOG.write_text(text[-_JOB_LOG_MAX_BYTES // 2 :], encoding="utf-8")
    except OSError:
        logger.exception("Failed to trim parser job.log")


def _requeue_failed_night_brand(state: dict, job: dict) -> dict:
    """Якщо нічний бренд упав після старту — один повтор у черзі (не чекати 14 днів)."""
    current = (state.get("current") or "").strip()
    if not current:
        return state
    if job.get("state") != "error":
        return state
    job_brand = (job.get("brand") or "").strip()
    if not job_brand or job_brand.lower() != current.lower():
        return state
    pending = list(state.get("pending") or [])
    if any((b or "").strip().lower() == current.lower() for b in pending):
        return state
    retries = dict(state.get("run_retries") or {})
    used = int(retries.get(current) or 0)
    if used >= 1:
        return state
    retries[current] = used + 1
    pending.insert(0, current)
    state = dict(state)
    state["pending"] = pending
    state["run_retries"] = retries
    append_parse_event(
        level="warn",
        kind="schedule",
        brand=current,
        message=f"«{current}» упав уночі — повтор у черзі (спроба {used + 1})",
    )
    return state


def _note_start_retry(state: dict, brand: str, *, max_retries: int = 2) -> tuple[dict, bool]:
    """Повернути бренд у кінець черги при тимчасовій помилці старту. True = ще буде retry."""
    retries = dict(state.get("start_retries") or {})
    used = int(retries.get(brand) or 0)
    if used >= max_retries:
        return state, False
    retries[brand] = used + 1
    pending = list(state.get("pending") or [])
    pending.append(brand)
    state = dict(state)
    state["pending"] = pending
    state["start_retries"] = retries
    return state, True


def rotation_overview(*, brand: str | None = None) -> dict:
    today = _kyiv_today()
    slot = today.toordinal() % CYCLE_DAYS
    try:
        names = _enabled_brand_names()
    except Exception:
        logger.exception("Failed to list brands for parser rotation")
        names = []
    state = _load_rotation()
    running = _rotation_busy()
    schedule = next_rotation_info(brand=brand, state=state, names=names)
    return {
        "cycle_days": CYCLE_DAYS,
        "day": slot + 1,
        "today": today.isoformat(),
        "today_brands": brands_for_rotation_day(names, slot),
        "per_day": [
            {
                "day": index + 1,
                "count": len(brands_for_rotation_day(names, index)),
                "active": index == slot,
            }
            for index in range(CYCLE_DAYS)
        ],
        "pending": list(state.get("pending") or []),
        "current": state.get("current"),
        "built_for": state.get("built_for"),
        "running": running,
        "enabled_total": len(names),
        "night_label": schedule["night_label"],
        "in_night": schedule["in_night"],
        "weekend": schedule.get("weekend"),
        "auto_rotation_allowed": schedule.get("auto_rotation_allowed"),
        "queue_hour": schedule["queue_hour"],
        "next": schedule["next"],
        "brand_schedule": schedule.get("brand"),
    }


def advance_parser_rotation() -> dict:
    """Лише вночі (пн–пт): черга дня і один бренд, коли парсер вільний."""
    try:
        return _advance_parser_rotation_locked()
    except Exception as exc:
        logger.exception("advance_parser_rotation failed")
        try:
            append_parse_event(
                level="error",
                kind="schedule",
                message=f"Планувальник парсера: {exc}",
            )
        except Exception:
            pass
        return {"action": "error", "error": str(exc)}


def _advance_parser_rotation_locked() -> dict:
    with _ROTATION_LOCK:
        state = _load_rotation()
        now = _kyiv_now()
        if is_weekend_kyiv(now):
            return {
                "action": "weekend",
                "next": next_rotation_info(state=state, now=now).get("next"),
            }
        if not in_rotation_night(now):
            return {
                "action": "daytime",
                "next": next_rotation_info(state=state, now=now).get("next"),
            }
        # Спочатку reclaim зомбі running → error, інакше черга може «висити».
        job = report_status()
        if not _rotation_busy():
            resolved = _resolve_job_payload(
                {k: v for k, v in job.items() if k != "row"},
                persist=True,
            )
            if resolved.get("state") != job.get("state"):
                job = resolved
        state = _requeue_failed_night_brand(state, job)
        _save_rotation(state)
        if _rotation_busy():
            live = report_status()
            return {"action": "busy", "brand": live.get("brand") or state.get("current")}
        today = now.date()
        today_s = today.isoformat()
        names = _enabled_brand_names()
        pending_before = list(state.get("pending") or [])
        built = state.get("built_for")
        planned = None
        if pending_before and built != today_s:
            planned = plan_rotation_update(state, today, names)
        elif not pending_before and built != today_s and now.hour >= ROTATION_QUEUE_HOUR:
            planned = plan_rotation_update(state, today, names)
        if planned is not None:
            carry = bool(pending_before and built != today_s)
            prev = state.get("built_for")
            if not carry and prev:
                try:
                    gap = (today - date.fromisoformat(str(prev))).days
                except ValueError:
                    gap = 0
                if gap > 1:
                    append_parse_event(
                        level="warn",
                        kind="schedule",
                        message=(
                            f"Попередня черга завершилась із запізненням на {gap} дн. "
                            "Незавершені бренди доповнюються в наступні ночі."
                        ),
                    )
            state = planned
            _save_rotation(state)
            pending_n = len(state.get("pending") or [])
            msg = (
                f"Нічна черга на {today.isoformat()}: день {state['day'] + 1} з {CYCLE_DAYS}, "
                f"брендів {pending_n} (вікно {ROTATION_NIGHT_START_HOUR:02d}:00–"
                f"{ROTATION_NIGHT_END_HOUR:02d}:00)"
            )
            if carry:
                msg = f"Доробка черги + слот дня: {pending_n} бренд(ів). " + msg
            append_parse_event(level="info", kind="schedule", message=msg)
        pending = list(state.get("pending") or [])
        if not pending:
            return {"action": "empty"}
        attempts = 0
        while pending and attempts < 8:
            attempts += 1
            brand = pending.pop(0)
            state["pending"] = pending
            state["current"] = brand
            _save_rotation(state)
            status, code = start_report(brand=brand)
            if code == 200:
                append_parse_event(
                    level="info",
                    kind="schedule",
                    brand=brand,
                    message=f"Нічний автозапуск «{brand}». У черзі ще {len(pending)}",
                )
                return {"action": "started", "brand": brand, "left": len(pending)}
            if code == 409:
                pending.insert(0, brand)
                state["pending"] = pending
                state["current"] = status.get("brand") or state.get("current")
                _save_rotation(state)
                return {"action": "busy", "brand": state.get("current")}
            # 5xx / раптовий краш старту — не викидаємо бренд з ночі одразу.
            if code >= 500:
                state, again = _note_start_retry(state, brand, max_retries=2)
                pending = list(state.get("pending") or [])
                _save_rotation(state)
                if again:
                    append_parse_event(
                        level="warn",
                        kind="schedule",
                        brand=brand,
                        message=(
                            f"Старт «{brand}» не вдався — буде повтор у черзі. "
                            f"{status.get('error') or ''}"
                        ).strip(),
                    )
                    continue
            skipped = list(state.get("skipped") or [])
            skipped.append({"brand": brand, "error": status.get("error")})
            state["skipped"] = skipped[-40:]
            state["pending"] = pending
            _save_rotation(state)
            append_parse_event(
                level="warn",
                kind="schedule",
                brand=brand,
                message=status.get("error") or f"Пропущено «{brand}»",
            )
        return {"action": "skipped"}


def start_report(*, brand: str | None = None) -> tuple[dict, int]:
    """Запускає парсинг окремим процесом: перезапуск сайту і закриття вкладки його не зупиняють."""
    global _REPORT_STATUS, _LIVE_ROWS, _LIVE_BRAND, _JOB_LOG_HANDLE
    with _REPORT_LOCK:
        current = report_status()
        if current.get("state") == "running" and _parser_process_alive(current.get("pid")):
            other = (current.get("brand") or "").strip() or "весь каталог"
            message = (
                f"Зараз уже йде збір «{other}» "
                f"({current.get('current') or 0} з {current.get('total') or '?'}). "
                "Одночасно може працювати лише один прогін."
            )
            append_parse_event(level="warn", kind="job", brand=brand, message=message)
            return {**current, "error": message}, 409
        # Свіжий running без живого pid (ще не встиг піднятися / глітч) — не стартуємо другий.
        if (
            current.get("state") == "running"
            and (time.time() - float(current.get("updated_epoch") or 0)) < 25
        ):
            other = (current.get("brand") or "").strip() or "весь каталог"
            message = (
                f"Зараз уже йде збір «{other}» "
                f"({current.get('current') or 0} з {current.get('total') or '?'}). "
                "Одночасно може працювати лише один прогін."
            )
            return {**current, "error": message}, 409
        catalog = load_catalog(force=True)
        disabled = disabled_brand_set()
        if brand:
            if brand.strip().lower() in disabled:
                message = f"Бренд «{brand}» вимкнено з парсингу. Увімкніть його в списку брендів."
                append_parse_event(level="warn", kind="job", brand=brand, message=message)
                return {"state": "error", "error": message}, 400
            brand_l = brand.strip().lower()
            items = [p for p in catalog if (p.get("brand") or "").strip().lower() == brand_l]
            total = len(items)
            if total == 0:
                message = f"У каталозі немає товарів бренду «{brand}»"
                append_parse_event(level="error", kind="job", brand=brand, message=message)
                return {"state": "error", "error": message}, 400
        else:
            items = [p for p in catalog if (p.get("brand") or "").strip().lower() not in disabled]
            total = len(items)
            if total == 0:
                message = "Немає увімкнених брендів для парсингу"
                append_parse_event(level="error", kind="job", brand=brand, message=message)
                return {"state": "error", "error": message}, 400
        script = PARSER_ROOT / "scripts" / "run_brand_job.py"
        now = time.time()
        status = {
            "state": "running",
            "phase": "starting",
            "current": 0,
            "total": total,
            "brand": brand,
            "product": "запуск у фоні…",
            "visible": 0,
            "live_count": 0,
            "started_epoch": now,
            "updated_epoch": now,
        }
        try:
            JOBS_DIR.mkdir(parents=True, exist_ok=True)
            store.clear_live_rows()
            _trim_job_log()
            if _JOB_LOG_HANDLE is not None:
                try:
                    _JOB_LOG_HANDLE.close()
                except OSError:
                    pass
            _JOB_LOG_HANDLE = open(JOB_LOG, "a", encoding="utf-8")
            _JOB_LOG_HANDLE.write(f"\n--- start {datetime.now().isoformat(timespec='seconds')} brand={brand or '*'} ---\n")
            _JOB_LOG_HANDLE.flush()
            proc = subprocess.Popen(
                [sys.executable, "-u", str(script), brand or ""],
                cwd=str(ROOT),
                start_new_session=True,
                stdout=_JOB_LOG_HANDLE,
                stderr=subprocess.STDOUT,
            )
        except OSError as exc:
            logger.exception("Failed to start parser process")
            append_parse_event(
                level="error",
                kind="job",
                brand=brand,
                message=f"Не вдалося запустити фоновий парсинг: {exc}",
            )
            return {"state": "error", "error": f"Не вдалося запустити фоновий парсинг: {exc}"}, 500
        status["pid"] = proc.pid
        store.save_job(status)
        _LIVE_ROWS = {}
        _LIVE_BRAND = brand
        _REPORT_STATUS = dict(status)
        boot_error = _confirm_worker_started(proc, status)
        if boot_error:
            err_status = {
                **status,
                "state": "error",
                "phase": "error",
                "error": boot_error,
                "updated_epoch": time.time(),
            }
            store.save_job(err_status)
            _REPORT_STATUS = dict(err_status)
            append_parse_event(level="error", kind="job", brand=brand, message=boot_error)
            return dict(err_status), 500
        # Щоб у блоці розкладу було видно актуальний бренд (не застарілий з ротації).
        if brand:
            try:
                rot = _load_rotation()
                if (rot.get("current") or "").strip() != brand:
                    rot["current"] = brand
                    _save_rotation(rot)
            except Exception:
                logger.exception("Failed to sync rotation.current after manual start")
    return dict(status), 200


def _job_log_tail(lines: int = 40) -> str:
    try:
        if not JOB_LOG.is_file():
            return ""
        text = JOB_LOG.read_text(encoding="utf-8", errors="replace")
        parts = text.strip().splitlines()
        return "\n".join(parts[-lines:])
    except OSError:
        return ""


def _confirm_worker_started(proc: subprocess.Popen, status: dict, *, timeout: float = 5.0) -> str | None:
    """None = воркер живий. Інакше текст помилки (процес упав під час буту)."""
    expected_pid = int(status.get("pid") or 0)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        disk = store.load_job() or {}
        disk_pid = int(disk.get("pid") or 0)
        if disk.get("state") == "error" and disk_pid == expected_pid:
            return str(disk.get("error") or "Воркер завершився з помилкою під час старту")
        if disk_pid == expected_pid and disk.get("state") == "running":
            product = (disk.get("product") or "").strip()
            phase = (disk.get("phase") or "").strip()
            # Heartbeat з run_brand_job або вже products.
            if product == "воркер стартував…" or phase in {"products", "brand_check", "done"}:
                return None
            if float(disk.get("updated_epoch") or 0) > float(status.get("updated_epoch") or 0) + 0.05:
                return None
        time.sleep(0.2)
    if proc.poll() is None and _pid_alive(expected_pid):
        # Повільний імпорт — процес ще живий, вважаємо старт успішним.
        return None
    tip = _job_log_tail(40)
    message = "Фоновий процес зупинився одразу після старту. Перевірте логи парсера."
    if tip:
        message = f"{message} Останні рядки: {tip[-280:]}"
    return message


def format_parsed_at(value: str | None) -> str:
    if not value:
        return "Ще не парсився"
    try:
        # accept with/without timezone
        raw = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        return dt.strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return value
