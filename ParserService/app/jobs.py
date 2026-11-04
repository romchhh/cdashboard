"""In-memory job-и скрейпінгу + короткий snapshot на диск (після OOM/restart)."""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from . import config

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}
# Один активний scrape: захист від OOM і гонок глобальних SITES/settings.
_SCRAPE_GATE = threading.Semaphore(1)
_EXEC_PATCH_LOCK = threading.Lock()
_HEAVY_KEYS = frozenset({"raw", "html", "candidates", "alternatives", "options", "debug"})
_STARTED_EPOCH = time.time()


def uptime_sec() -> float:
    return max(0.0, time.time() - _STARTED_EPOCH)


def _slim_dict(obj: Any) -> Any:
    if not isinstance(obj, dict):
        return obj
    return {k: v for k, v in obj.items() if k not in _HEAVY_KEYS}


def _slim_row(row: dict) -> dict:
    """Прибрати важкі поля — інакше 80 SKU × 26 магазинів з'їдають VPS."""
    out = _slim_dict(row)
    shops = out.get("shops")
    if isinstance(shops, dict):
        slim_shops: dict[str, Any] = {}
        for name, info in shops.items():
            if not isinstance(info, dict):
                slim_shops[name] = info
                continue
            slim_shops[name] = _slim_dict(info)
        out["shops"] = slim_shops
    return out


def _slim_site_item(item: dict) -> dict:
    return _slim_dict(item) if isinstance(item, dict) else item


def _clear_engine_caches(full: bool = False) -> None:
    try:
        if full:
            from cosmy_parse import clear_brand_presence_cache  # type: ignore

            clear_brand_presence_cache()
        else:
            from cosmy_parse import clear_scrape_runtime_caches  # type: ignore

            clear_scrape_runtime_caches()
    except Exception:
        try:
            from cosmy_parse import clear_http_cache  # type: ignore

            clear_http_cache()
        except Exception:
            pass
    try:
        import gc

        gc.collect()
    except Exception:
        pass


def _job_path(job_id: str) -> Path:
    return config.JOBS_DIR / f"{job_id}.json"


def _persist_snapshot(job: dict) -> None:
    """Легкий snapshot на диск: після OOM клієнт бачить «worker restart», не вічний poll."""
    try:
        snap = {
            "id": job.get("id"),
            "kind": job.get("kind"),
            "state": job.get("state"),
            "phase": job.get("phase"),
            "brand": job.get("brand"),
            "query": job.get("query"),
            "current": job.get("current"),
            "total": job.get("total"),
            "retry_current": job.get("retry_current"),
            "retry_total": job.get("retry_total"),
            "product": job.get("product"),
            "error": job.get("error"),
            "live_count": job.get("live_count") or len(job.get("rows") or []),
            "started_epoch": job.get("started_epoch"),
            "updated_epoch": job.get("updated_epoch") or time.time(),
            "worker_pid": __import__("os").getpid(),
        }
        path = _job_path(str(job["id"]))
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        logger.debug("job snapshot write failed", exc_info=True)


def load_job_snapshot(job_id: str) -> dict[str, Any] | None:
    path = _job_path(job_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _delete_snapshot(job_id: str) -> None:
    try:
        _job_path(job_id).unlink(missing_ok=True)
    except Exception:
        pass


def _trim_jobs() -> None:
    now = time.time()
    ttl = max(60, int(config.JOB_TTL_SEC))
    keep = max(1, int(config.MAX_JOBS_KEPT))
    # Спочатку викинути протерміновані done/error.
    expired = [
        jid
        for jid, j in _JOBS.items()
        if j.get("state") in ("done", "error")
        and now - float(j.get("updated_epoch") or 0) > ttl
    ]
    for jid in expired:
        _JOBS.pop(jid, None)
        _delete_snapshot(jid)
    if len(_JOBS) <= keep:
        return
    finished = sorted(
        (
            (jid, j.get("updated_epoch") or 0)
            for jid, j in _JOBS.items()
            if j.get("state") in ("done", "error")
        ),
        key=lambda x: x[1],
    )
    for jid, _ in finished[: max(0, len(_JOBS) - keep)]:
        _JOBS.pop(jid, None)
        _delete_snapshot(jid)


class ParserJobCancelled(Exception):
    """Ручна зупинка з сайту."""


def cancel_job(job_id: str) -> bool:
    """Позначити job на зупинку. Прогін вийде на наступному progress."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job or job.get("state") != "running":
            return False
        job["cancel_requested"] = True
        job["updated_epoch"] = time.time()
        return True


def job_cancel_requested(job_id: str) -> bool:
    with _LOCK:
        job = _JOBS.get(job_id)
        return bool(job and job.get("cancel_requested"))


def running_job_count() -> int:
    with _LOCK:
        return sum(
            1
            for job in _JOBS.values()
            if job.get("state") == "running" and not job.get("cancel_requested")
        )


def get_job(job_id: str) -> dict[str, Any] | None:
    with _LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def _update(job_id: str, **fields: Any) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return
        job.update(fields)
        job["updated_epoch"] = time.time()
        snap_needed = fields.get("state") in ("done", "error", "running") or "phase" in fields
        if snap_needed or int(job.get("current") or 0) % 3 == 0:
            _persist_snapshot(job)


def _append_row(job_id: str, row: dict) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return
        rows = job.setdefault("rows", [])
        rid = row.get("id")
        if rid is not None:
            for i, existing in enumerate(rows):
                if existing.get("id") == rid:
                    rows[i] = row
                    break
            else:
                rows.append(row)
        else:
            rows.append(row)
        job["updated_epoch"] = time.time()
        job["live_count"] = len(rows)
        if len(rows) == 1 or len(rows) % 5 == 0:
            _persist_snapshot(job)


def _use_ephemeral_store() -> None:
    try:
        import web.parser_store as store

        store.DB_PATH = config.WORK_DIR / "ephemeral.db"
        store.JSON_DIR = config.WORK_DIR
        store._READY_DB_PATHS.discard(str(store.DB_PATH))
    except Exception as exc:
        logger.warning("Ephemeral parser_store недоступний: %s", exc)


def _cap_workers(settings: dict) -> dict:
    out = dict(settings or {})
    want = int(out.get("sku_workers") or config.MAX_SKU_WORKERS)
    out["sku_workers"] = max(1, min(want, config.MAX_SKU_WORKERS))
    return out


def _apply_settings(settings: dict | None) -> None:
    from .bootstrap import require_engine

    require_engine()
    import catalog_report as cr  # type: ignore
    import cosmy_parse as cp  # type: ignore

    settings = _cap_workers(settings or {})
    if not getattr(cp, "_ALL_SITES", None):
        cp._ALL_SITES = list(cp.SITES)
    enabled = settings.get("enabled_sites")
    if enabled is None:
        active = list(cp._ALL_SITES)
    else:
        names = set(enabled)
        active = [s for s in cp._ALL_SITES if s.name in names]
    min_sim = float(settings.get("min_similarity") or 0.58)
    for site in cp._ALL_SITES:
        site.min_similarity = min_sim
    cp.SITES = active
    # Головний анти-OOM: catalog (N SKU) × search_all (магазини) більше не = N×26 потоків.
    if hasattr(cp, "SITE_SEARCH_WORKERS"):
        cp.SITE_SEARCH_WORKERS = config.MAX_SITE_WORKERS
    cr.SITE_NAMES = [s.name for s in active]
    cr.SITES = active
    cr.SKU_WORKERS = int(settings["sku_workers"])
    if settings.get("price_low_ratio") is not None:
        cr.PRICE_LOW_RATIO = float(settings["price_low_ratio"])
    if settings.get("price_high_ratio") is not None:
        cr.PRICE_HIGH_RATIO = float(settings["price_high_ratio"])
    if settings.get("at_market_pct") is not None:
        cr.AT_MARKET_PCT = float(settings["at_market_pct"])
    if settings.get("suspicious_gap_pct") is not None:
        cr.SUSPICIOUS_GAP_PCT = float(settings["suspicious_gap_pct"])


def _can_accept_job() -> tuple[bool, str]:
    running = running_job_count()
    if running >= max(1, config.MAX_RUNNING_JOBS):
        return (
            False,
            f"Уже виконується {running} job(s). Зачекайте завершення або опитуйте /v1/jobs/{{id}}.",
        )
    return True, "ok"


def list_jobs() -> list[dict[str, Any]]:
    with _LOCK:
        return [
            {
                "id": job.get("id"),
                "kind": job.get("kind"),
                "state": job.get("state"),
                "phase": job.get("phase"),
                "brand": job.get("brand"),
                "current": job.get("current"),
                "total": job.get("total"),
                "started_epoch": job.get("started_epoch"),
                "updated_epoch": job.get("updated_epoch"),
            }
            for job in _JOBS.values()
        ]


def _release_catalog_slot(brand: str | None, *, max_age: float = 20.0) -> str | None:
    """Свіжий job того самого бренда повертаємо як є. Інший catalog звільняє слот."""
    wanted = (brand or "").strip().lower()
    now = time.time()
    with _LOCK:
        for job in _JOBS.values():
            if job.get("state") != "running" or job.get("cancel_requested"):
                continue
            if (job.get("kind") or "catalog") != "catalog":
                continue
            if (job.get("brand") or "").strip().lower() != wanted:
                continue
            started = float(job.get("started_epoch") or 0)
            if started and now - started <= max_age:
                return str(job.get("id") or "")
        for job in _JOBS.values():
            if job.get("state") != "running" or (job.get("kind") or "catalog") != "catalog":
                continue
            job["cancel_requested"] = True
            job["state"] = "error"
            job["phase"] = "cancelled"
            job["error"] = "Замінено новим прогоном"
            job["product"] = "зупинено"
            job["updated_epoch"] = now
    return None


class _site_workers_cap:
    """Патч ThreadPoolExecutor на час scrape — навіть старий runtime без SITE_SEARCH_WORKERS."""

    def __init__(self, max_workers: int):
        self.max_workers = max(1, int(max_workers))
        self._real = None

    def __enter__(self):
        import concurrent.futures as cf

        self._real = cf.ThreadPoolExecutor
        max_workers = self.max_workers
        real = self._real

        class CappedExecutor(real):  # type: ignore[misc, valid-type]
            def __init__(self, *args, **kwargs):
                want = kwargs.get("max_workers")
                if want is None and args:
                    want = args[0]
                    args = args[1:]
                try:
                    n = int(want) if want is not None else max_workers
                except (TypeError, ValueError):
                    n = max_workers
                kwargs["max_workers"] = max(1, min(n, max_workers))
                super().__init__(*args, **kwargs)

        _EXEC_PATCH_LOCK.acquire()
        cf.ThreadPoolExecutor = CappedExecutor  # type: ignore[misc]
        return self

    def __exit__(self, *exc):
        import concurrent.futures as cf

        if self._real is not None:
            cf.ThreadPoolExecutor = self._real  # type: ignore[misc]
        _EXEC_PATCH_LOCK.release()
        return False


def _heartbeat(job_id: str, stop: threading.Event) -> None:
    """Оновлювати updated_epoch під час довгого scrape — health/poll не виглядають «мертвими»."""
    while not stop.wait(8.0):
        with _LOCK:
            job = _JOBS.get(job_id)
            if not job or job.get("state") != "running":
                return
            job["updated_epoch"] = time.time()
            job["phase"] = job.get("phase") or "running"


def start_catalog_job(
    *,
    products: list[dict],
    settings: dict | None = None,
    brand: str | None = None,
) -> str:
    if len(products) > config.MAX_CATALOG_PRODUCTS:
        raise RuntimeError(
            f"Занадто багато товарів ({len(products)}). Ліміт worker: {config.MAX_CATALOG_PRODUCTS}."
        )
    existing = _release_catalog_slot(brand)
    if existing:
        return existing
    ok, reason = _can_accept_job()
    if not ok:
        raise RuntimeError(reason)
    job_id = uuid.uuid4().hex[:16]
    now = time.time()
    with _LOCK:
        _trim_jobs()
        _JOBS[job_id] = {
            "id": job_id,
            "kind": "catalog",
            "state": "running",
            "phase": "queued",
            "brand": brand,
            "current": 0,
            "total": len(products),
            "product": "у черзі…",
            "rows": [],
            "report": None,
            "error": None,
            "started_epoch": now,
            "updated_epoch": now,
        }
        _persist_snapshot(_JOBS[job_id])
    threading.Thread(
        target=_run_catalog,
        args=(job_id, products, _cap_workers(settings or {}), brand),
        name=f"parse-{job_id}",
        daemon=True,
    ).start()
    return job_id


def _run_catalog(job_id: str, products: list[dict], settings: dict, brand: str | None) -> None:
    acquired = _SCRAPE_GATE.acquire(timeout=float(config.SCRAPE_GATE_WAIT_SEC))
    if not acquired:
        _update(job_id, state="error", phase="error", error="Worker зайнятий іншим scrape")
        return
    stop_hb = threading.Event()
    hb = threading.Thread(target=_heartbeat, args=(job_id, stop_hb), name=f"hb-{job_id}", daemon=True)
    hb.start()
    try:
        _update(job_id, phase="starting", product="запуск…")
        _use_ephemeral_store()
        _apply_settings(settings)
        _clear_engine_caches(full=True)
        from catalog_report import run_catalog  # type: ignore

        workers = int(settings.get("sku_workers") or config.MAX_SKU_WORKERS)
        every = max(1, config.CACHE_CLEAR_EVERY)
        last_clear_at = 0

        def progress(status: dict) -> None:
            nonlocal last_clear_at
            status = dict(status)
            if job_cancel_requested(job_id):
                raise ParserJobCancelled()
            row = status.pop("row", None)
            current = int(status.get("current") or 0)
            patch = {
                "state": status.get("state") or "running",
                "phase": status.get("phase"),
                "current": current,
                "total": status.get("total") or len(products),
                "product": status.get("product"),
                "retry_current": status.get("retry_current"),
                "retry_total": status.get("retry_total"),
            }
            if brand:
                patch["brand"] = brand
            _update(job_id, **{k: v for k, v in patch.items() if v is not None})
            if isinstance(row, dict) and not row.get("partial"):
                _append_row(job_id, _slim_row(row))
            if current and current - last_clear_at >= every:
                last_clear_at = current
                _clear_engine_caches(full=False)

        with _site_workers_cap(config.MAX_SITE_WORKERS):
            report = run_catalog(
                progress=progress,
                workers=workers,
                catalog=products,
                write_excel_file=False,
                report_scope=f"worker_{job_id}",
            )
        rows = [_slim_row(r) for r in (report.get("rows") or []) if isinstance(r, dict)]
        _update(
            job_id,
            state="done",
            phase="done",
            current=report.get("total") or len(rows),
            total=report.get("total") or len(rows),
            product="готово",
            rows=rows,
            report={
                "generated_at": report.get("generated_at"),
                "started_at": report.get("started_at"),
                "total": report.get("total"),
                "catalog_total": report.get("catalog_total"),
                "sites": report.get("sites"),
                "rows": rows,
            },
        )
    except ParserJobCancelled:
        logger.info("Worker catalog job %s stopped", job_id)
        _update(job_id, state="error", phase="cancelled", error="Зупинено вручну", product="зупинено")
    except Exception as exc:
        logger.exception("Worker catalog job %s failed", job_id)
        _update(job_id, state="error", phase="error", error=str(exc))
    finally:
        stop_hb.set()
        _clear_engine_caches(full=True)
        _SCRAPE_GATE.release()


def start_search_job(
    *,
    query: str,
    our_price: Any = None,
    settings: dict | None = None,
) -> str:
    """Фоновий пошук з live results/misses — HTTP одразу 202 (без nginx 502)."""
    ok, reason = _can_accept_job()
    if not ok:
        raise RuntimeError(reason)
    job_id = uuid.uuid4().hex[:16]
    now = time.time()
    with _LOCK:
        _trim_jobs()
        _JOBS[job_id] = {
            "id": job_id,
            "kind": "search",
            "state": "running",
            "phase": "queued",
            "query": query,
            "our_price": our_price,
            "current": 0,
            "total": 0,
            "product": query,
            "results": [],
            "misses": [],
            "rows": [],
            "report": None,
            "error": None,
            "started_epoch": now,
            "updated_epoch": now,
        }
        _persist_snapshot(_JOBS[job_id])
    threading.Thread(
        target=_run_search,
        args=(job_id, query, our_price, _cap_workers(settings or {})),
        name=f"search-{job_id}",
        daemon=True,
    ).start()
    return job_id


def _run_search(job_id: str, query: str, our_price: Any, settings: dict) -> None:
    acquired = _SCRAPE_GATE.acquire(timeout=float(config.SCRAPE_GATE_WAIT_SEC))
    if not acquired:
        _update(job_id, state="error", phase="error", error="Worker зайнятий іншим scrape")
        return
    stop_hb = threading.Event()
    hb = threading.Thread(target=_heartbeat, args=(job_id, stop_hb), name=f"hb-{job_id}", daemon=True)
    hb.start()
    try:
        _update(job_id, phase="sites", product=query)
        _apply_settings(settings)
        from cosmy_parse import SITES, parse_price, search_all  # type: ignore

        price = parse_price(our_price)
        total_sites = len(SITES)
        _update(job_id, total=total_sites, our_price=price)

        def on_site(item: dict) -> None:
            slim = _slim_site_item(item)
            with _LOCK:
                job = _JOBS.get(job_id)
                if not job:
                    return
                if slim.get("status") == "ok":
                    job.setdefault("results", []).append(slim)
                else:
                    job.setdefault("misses", []).append(slim)
                done = len(job.get("results") or []) + len(job.get("misses") or [])
                job["current"] = done
                job["total"] = total_sites
                job["product"] = slim.get("site") or query
                job["updated_epoch"] = time.time()

        with _site_workers_cap(config.MAX_SITE_WORKERS):
            try:
                result = search_all(
                    query.strip(),
                    our_price=price,
                    debug=False,
                    on_site=on_site,
                    max_workers=config.MAX_SITE_WORKERS,
                )
            except TypeError:
                # старий runtime без max_workers — cap через ThreadPoolExecutor patch
                result = search_all(
                    query.strip(),
                    our_price=price,
                    debug=False,
                    on_site=on_site,
                )
        payload = {
            k: v
            for k, v in (result or {}).items()
            if k not in ("raw", "html", "candidates")
        }
        payload["query"] = query.strip()
        payload["our_price"] = price
        payload["results"] = [_slim_site_item(x) for x in (payload.get("results") or [])]
        payload["misses"] = [_slim_site_item(x) for x in (payload.get("misses") or [])]
        _update(
            job_id,
            state="done",
            phase="done",
            current=len(payload.get("results") or []) + len(payload.get("misses") or []),
            total=total_sites,
            product="готово",
            results=list(payload.get("results") or []),
            misses=list(payload.get("misses") or []),
            report=payload,
        )
    except Exception as exc:
        logger.exception("Worker search job %s failed", job_id)
        _update(job_id, state="error", phase="error", error=str(exc))
    finally:
        stop_hb.set()
        _clear_engine_caches(full=False)
        _SCRAPE_GATE.release()


def list_sites() -> dict:
    from .bootstrap import require_engine

    require_engine()
    from cosmy_parse import SITES  # type: ignore

    return {
        "sites": [s.name for s in SITES],
        "meta": [
            {
                "name": s.name,
                "kind": getattr(s, "kind", ""),
                "base_url": getattr(s, "base_url", ""),
            }
            for s in SITES
        ],
    }


def process_rss_mb() -> float | None:
    try:
        import resource

        # Linux: ru_maxrss у KB; macOS — bytes
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        import sys

        if sys.platform == "darwin":
            return round(rss / (1024 * 1024), 1)
        return round(rss / 1024, 1)
    except Exception:
        return None
