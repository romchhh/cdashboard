"""Клієнт до ParserService worker: лише скрейпінг. БД/крони — локально."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import (
    PARSER_API_KEY,
    PARSER_JOB_MAX_WAIT,
    PARSER_POLL_MAX_STREAK,
    PARSER_POLL_TIMEOUT,
    PARSER_SERVICE_TIMEOUT,
    PARSER_SERVICE_URL,
)

logger = logging.getLogger(__name__)

_START_TIMEOUT = max(float(PARSER_SERVICE_TIMEOUT), 30.0)
_POLL_TIMEOUT = float(PARSER_POLL_TIMEOUT)
_POLL_MAX_STREAK = int(PARSER_POLL_MAX_STREAK)
_JOB_MAX_WAIT = float(PARSER_JOB_MAX_WAIT)
_SEARCH_TIMEOUT = max(float(PARSER_SERVICE_TIMEOUT), 180.0)


def worker_enabled() -> bool:
    return bool((PARSER_SERVICE_URL or "").strip())


def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=2,
        connect=2,
        read=0,  # read timeout обробляємо самі в poll loop
        backoff_factor=0.4,
        status_forcelist=(502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


_SESSION = _session()


def _reset_session() -> None:
    global _SESSION
    try:
        _SESSION.close()
    except Exception:
        pass
    _SESSION = _session()


def _headers() -> dict[str, str]:
    h = {"Accept": "application/json", "Content-Type": "application/json"}
    if PARSER_API_KEY:
        h["X-Parser-Key"] = PARSER_API_KEY
    return h


def _base(base_url: str | None = None) -> str:
    return (base_url or PARSER_SERVICE_URL or "").rstrip("/")


def _url(path: str, base_url: str | None = None) -> str:
    return f"{_base(base_url)}{path}"


def _err_detail(resp: requests.Response) -> str:
    code = resp.status_code
    if code == 410:
        try:
            data = resp.json()
            return str(data.get("error") or "Worker перезапустився під час job")
        except Exception:
            return "Worker перезапустився під час job (OOM/restart)"
    if code in (502, 503, 504):
        return (
            f"Worker HTTP {code} (Bad Gateway) — сервіс cosmyparser.telebots.site "
            "не відповідає. Перезапустіть ParserService: ./start.sh restart"
        )
    try:
        data = resp.json()
        return str(data.get("error") or data.get("detail") or data)[:400]
    except Exception:
        text = (resp.text or "").strip()
        if "<html" in text.lower():
            return f"Worker HTTP {code}"
        return (text or f"HTTP {code}")[:400]


def _is_transient(exc_or_msg: Exception | str) -> bool:
    msg = str(exc_or_msg)
    if isinstance(exc_or_msg, (requests.Timeout, requests.ConnectionError)):
        return True
    return any(x in msg for x in ("HTTP 502", "HTTP 503", "HTTP 504", "Bad Gateway", "Connection"))


def worker_reachable(base_url: str | None = None, timeout: float | None = None) -> tuple[bool, str]:
    """Швидка перевірка перед стартом job."""
    base = _base(base_url)
    if not base:
        return False, "PARSER_SERVICE_URL не задано"
    try:
        resp = _SESSION.get(
            _url("/v1/health", base),
            headers=_headers(),
            timeout=timeout if timeout is not None else min(_START_TIMEOUT, 12),
        )
        if resp.status_code == 200:
            data = resp.json() if resp.content else {}
            eng = data.get("engine") if isinstance(data, dict) else None
            if isinstance(eng, dict) and eng.get("ready") is False:
                return False, f"Worker engine не готовий: {eng.get('error') or 'cosmy_parse missing'}"
            return True, "ok"
        return False, _err_detail(resp)
    except requests.RequestException as exc:
        return False, f"Worker недоступний: {exc}"


def cancel_catalog_job(job_id: str, base_url: str | None = None) -> bool:
    """Попросити worker зупинити scrape. False, якщо ендпоінта ще немає або job уже завершився."""
    if not job_id or not _base(base_url):
        return False
    try:
        resp = _SESSION.post(
            _url(f"/v1/jobs/{job_id}/cancel", base_url),
            headers=_headers(),
            timeout=_START_TIMEOUT,
        )
    except (requests.Timeout, requests.ConnectionError) as exc:
        logger.warning("Worker cancel %s: %s", job_id, exc)
        return False
    if resp.status_code == 200:
        return True
    logger.warning("Worker cancel %s HTTP %s", job_id, resp.status_code)
    return False


def list_worker_jobs(base_url: str | None = None, timeout: float = 4) -> list | None:
    """Список job на worker. None, якщо ендпоінта ще немає."""
    try:
        resp = _SESSION.get(
            _url("/v1/jobs", base_url),
            headers=_headers(),
            timeout=timeout,
        )
    except (requests.Timeout, requests.ConnectionError):
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    jobs = data.get("jobs") if isinstance(data, dict) else None
    return jobs if isinstance(jobs, list) else None


def _tracked_remote_ids() -> set[str]:
    try:
        from web.parser_store import load_all_jobs
    except Exception:
        return set()
    ids: set[str] = set()
    for job in load_all_jobs():
        if job.get("state") != "running":
            continue
        remote_id = str(job.get("remote_job_id") or "").strip()
        if remote_id:
            ids.add(remote_id)
    return ids


def _job_age(job: dict) -> float:
    started = float(job.get("started_epoch") or job.get("updated_epoch") or 0)
    if started <= 0:
        return 10**9
    return max(0.0, time.time() - started)


def settle_worker_slot(
    base_url: str | None = None,
    *,
    brand: str | None = None,
    adopt_recent: bool = False,
    timeout: float = 4,
) -> tuple[str, str]:
    """Зняти завислі catalog-job, про які сайт уже не знає.

    Повертає (free|adopt|busy|unknown, деталь).
    """
    jobs = list_worker_jobs(base_url, timeout=timeout)
    if jobs is None:
        running, limit = _health_job_slot(base_url, timeout)
        if running is None:
            return "unknown", ""
        if running >= limit:
            return "busy", "уже виконує збір"
        return "free", ""
    wanted = (brand or "").strip().lower()
    tracked = _tracked_remote_ids()
    running = [
        job
        for job in jobs
        if isinstance(job, dict)
        and job.get("state") == "running"
        and (job.get("kind") or "catalog") != "search"
    ]
    if adopt_recent and wanted:
        for job in running:
            if (job.get("brand") or "").strip().lower() != wanted:
                continue
            if _job_age(job) < 45 and job.get("id"):
                return "adopt", str(job["id"])
    still: list[dict] = []
    for job in running:
        job_id = str(job.get("id") or "")
        if not job_id:
            continue
        if job_id in tracked or _job_age(job) < 20:
            still.append(job)
            continue
        cancel_catalog_job(job_id, base_url)
    if still:
        name = (still[0].get("brand") or "").strip() or "інший бренд"
        return "busy", f"уже збирає «{name}»"
    return "free", ""


def _health_job_slot(base_url: str | None, timeout: float) -> tuple[int | None, int]:
    """Скільки job зараз на worker. None, якщо health не відповів."""
    try:
        resp = _SESSION.get(
            _url("/v1/health", base_url),
            headers=_headers(),
            timeout=timeout,
        )
    except (requests.Timeout, requests.ConnectionError):
        return None, 1
    if resp.status_code != 200:
        return None, 1
    try:
        data = resp.json()
    except Exception:
        return None, 1
    if not isinstance(data, dict):
        return None, 1
    try:
        running = int(data.get("running_jobs") or 0)
    except (TypeError, ValueError):
        running = 0
    limits = data.get("limits") if isinstance(data.get("limits"), dict) else {}
    try:
        limit = max(1, int(limits.get("max_running_jobs") or 1))
    except (TypeError, ValueError):
        limit = 1
    return running, limit


def _cancel_stored_remote_jobs(base_url: str | None) -> None:
    """Скасувати job, id яких сайт уже знає. Працює і без GET /v1/jobs."""
    try:
        from web.parser_store import load_all_jobs
    except Exception:
        return
    for job in load_all_jobs():
        remote_id = str(job.get("remote_job_id") or "").strip()
        if remote_id:
            cancel_catalog_job(remote_id, base_url)


def health_on_worker() -> dict:
    resp = _SESSION.get(_url("/v1/health"), headers=_headers(), timeout=_START_TIMEOUT)
    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text[:200]}
    data["_http"] = resp.status_code
    return data


def start_catalog_job(
    *,
    products: list[dict],
    settings: dict | None = None,
    brand: str | None = None,
    base_url: str | None = None,
) -> str:
    """Старт catalog на worker → job_id. Скрейп триває навіть якщо клієнт відвалиться."""
    last_err: Exception | None = None
    for attempt in range(1, 3):
        slot, detail = settle_worker_slot(base_url, brand=brand, adopt_recent=attempt > 1)
        if slot == "adopt" and detail:
            return detail
        try:
            resp = _SESSION.post(
                _url("/v1/parse/catalog", base_url),
                headers=_headers(),
                json={
                    "products": products,
                    "settings": settings or {},
                    "brand": brand,
                },
                timeout=_START_TIMEOUT,
            )
            if resp.status_code in (502, 503, 504) and attempt < 3:
                logger.warning("Worker catalog start HTTP %s attempt=%s", resp.status_code, attempt)
                time.sleep(1.2 * attempt)
                _reset_session()
                continue
            if resp.status_code == 409:
                logger.warning("Worker catalog start busy attempt=%s", attempt)
                _cancel_stored_remote_jobs(base_url)
                if attempt >= 2:
                    raise RuntimeError(
                        "На парсері ще висить попередній збір. "
                        "Зупиніть його над списком брендів або перезапустіть ParserService."
                    )
                time.sleep(0.8)
                continue
            if resp.status_code not in (200, 202):
                raise RuntimeError(_err_detail(resp))
            job_id = resp.json().get("job_id")
            if not job_id:
                raise RuntimeError("Worker не повернув job_id")
            return str(job_id)
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_err = exc
            logger.warning("Worker catalog start conn attempt=%s: %s", attempt, exc)
            if attempt >= 2:
                break
            time.sleep(1.2 * attempt)
            _reset_session()
    raise RuntimeError(f"Не вдалося стартувати catalog на worker: {last_err}")


def get_job(job_id: str, *, since_rows: int = 0, timeout: float | None = None, base_url: str | None = None) -> dict:
    st = _SESSION.get(
        _url(f"/v1/jobs/{job_id}", base_url),
        headers=_headers(),
        params={"since_rows": since_rows},
        timeout=timeout if timeout is not None else _POLL_TIMEOUT,
    )
    if st.status_code == 410:
        data = {}
        try:
            data = st.json()
        except Exception:
            pass
        raise RuntimeError(data.get("error") or _err_detail(st))
    if st.status_code >= 400:
        raise RuntimeError(_err_detail(st))
    return st.json()


def poll_catalog_job(
    job_id: str,
    *,
    products_total: int = 0,
    brand: str | None = None,
    on_progress: Callable[[dict], None] | None = None,
    poll_interval: float = 1.2,
    since_rows: int = 0,
    base_url: str | None = None,
) -> dict:
    """Дочитує вже запущений catalog job до done/error.

    Короткі ReadTimeout під час scrape — норма: job на worker триває, просто
    API на мить не відповідає. Не валимо весь бренд через один таймаут.
    """
    seen = max(0, int(since_rows))
    deadline = time.time() + _JOB_MAX_WAIT
    idle_polls = 0
    fail_streak = 0
    while True:
        if time.time() > deadline:
            raise RuntimeError(f"Worker job {job_id} перевищив ліміт очікування ({int(_JOB_MAX_WAIT)}с)")
        try:
            data = get_job(job_id, since_rows=seen, base_url=base_url)
            fail_streak = 0
        except RuntimeError as exc:
            msg = str(exc)
            # Worker restart / OOM — не ретраїмо вічно.
            if "перезапустився" in msg.lower() or "OOM" in msg:
                raise
            if _is_transient(msg):
                fail_streak += 1
                logger.warning("Worker poll gateway job=%s streak=%s: %s", job_id, fail_streak, msg)
                if fail_streak >= _POLL_MAX_STREAK:
                    raise
                if fail_streak in (3, 8, 15):
                    _reset_session()
                time.sleep(min(10.0, poll_interval * (1 + fail_streak * 0.5)))
                continue
            raise
        except (requests.Timeout, requests.ConnectionError) as exc:
            fail_streak += 1
            logger.warning(
                "Worker poll timeout/conn job=%s streak=%s/%s: %s",
                job_id,
                fail_streak,
                _POLL_MAX_STREAK,
                exc,
            )
            if fail_streak >= _POLL_MAX_STREAK:
                raise RuntimeError(
                    f"Worker не відповідає на poll {fail_streak} разів поспіль "
                    f"(job {job_id} ще може йти на сервері). Перевірте ParserService."
                ) from exc
            if fail_streak in (3, 8, 15):
                _reset_session()
            time.sleep(min(10.0, poll_interval * (1 + fail_streak * 0.5)))
            continue

        new_rows = data.get("rows") or []
        if new_rows:
            idle_polls = 0
            for row in new_rows:
                seen += 1
                if on_progress:
                    on_progress(
                        {
                            "state": "running",
                            "phase": data.get("phase") or "products",
                            "current": data.get("current") or 0,
                            "total": data.get("total") or products_total,
                            "retry_current": data.get("retry_current"),
                            "retry_total": data.get("retry_total"),
                            "product": data.get("product") or "",
                            "brand": brand or data.get("brand"),
                            "row": row,
                        }
                    )
        else:
            idle_polls += 1
            if on_progress:
                on_progress(
                    {
                        "state": data.get("state") or "running",
                        "phase": data.get("phase") or "products",
                        "current": data.get("current") or 0,
                        "total": data.get("total") or products_total,
                        "retry_current": data.get("retry_current"),
                        "retry_total": data.get("retry_total"),
                        "product": data.get("product") or "",
                        "brand": brand or data.get("brand"),
                    }
                )

        state = data.get("state")
        if state == "done":
            report = data.get("report")
            if not isinstance(report, dict):
                raise RuntimeError("Worker завершився без report")
            return report
        if state == "error":
            raise RuntimeError(data.get("error") or "Помилка worker-а")

        sleep_for = poll_interval if idle_polls < 3 else min(3.0, poll_interval * 2)
        time.sleep(sleep_for)


def run_catalog_on_worker(
    *,
    products: list[dict],
    settings: dict | None = None,
    brand: str | None = None,
    on_progress: Callable[[dict], None] | None = None,
    on_started: Callable[[str], None] | None = None,
    poll_interval: float = 1.2,
    base_url: str | None = None,
) -> dict:
    job_id = start_catalog_job(products=products, settings=settings, brand=brand, base_url=base_url)
    if on_started:
        on_started(job_id)
    return poll_catalog_job(
        job_id,
        products_total=len(products),
        brand=brand,
        on_progress=on_progress,
        poll_interval=poll_interval,
        base_url=base_url,
    )


def search_on_worker(
    query: str,
    our_price: Any = None,
    settings: dict | None = None,
    on_progress: Callable[[dict], None] | None = None,
    poll_interval: float = 0.9,
) -> dict:
    """Асинхронний пошук на worker з live-оновленнями."""
    last_err: Exception | None = None
    resp = None
    for attempt in range(1, 4):
        try:
            resp = _SESSION.post(
                _url("/v1/parse/search"),
                headers=_headers(),
                json={"query": query, "our_price": our_price, "settings": settings or {}},
                timeout=_START_TIMEOUT,
            )
            break
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_err = exc
            if attempt >= 3:
                raise RuntimeError(f"Worker search start: {exc}") from exc
            time.sleep(1.0 * attempt)
            _reset_session()
    if resp is None:
        raise RuntimeError(f"Worker search start: {last_err}")

    if resp.status_code == 200:
        data = resp.json()
        if on_progress and isinstance(data, dict):
            on_progress(
                {
                    "state": "done",
                    "results": data.get("results") or [],
                    "misses": data.get("misses") or [],
                    "found": len(data.get("results") or []),
                    "sites_done": len(data.get("results") or []) + len(data.get("misses") or []),
                }
            )
        return data
    if resp.status_code not in (200, 202):
        raise RuntimeError(_err_detail(resp))
    job_id = resp.json().get("job_id")
    if not job_id:
        raise RuntimeError("Worker не повернув job_id для search")

    deadline = time.time() + max(_JOB_MAX_WAIT, _SEARCH_TIMEOUT)
    last_sig = None
    fail_streak = 0
    while True:
        if time.time() > deadline:
            raise RuntimeError(f"Worker search {job_id} перевищив ліміт очікування")
        try:
            data = get_job(job_id)
            fail_streak = 0
        except RuntimeError as exc:
            msg = str(exc)
            if "перезапустився" in msg.lower() or "OOM" in msg:
                raise
            if _is_transient(msg):
                fail_streak += 1
                if fail_streak >= _POLL_MAX_STREAK:
                    raise
                time.sleep(min(8.0, poll_interval * (1 + fail_streak * 0.5)))
                continue
            raise
        except (requests.Timeout, requests.ConnectionError) as exc:
            fail_streak += 1
            if fail_streak >= _POLL_MAX_STREAK:
                raise RuntimeError(f"Worker search poll не відповідає: {exc}") from exc
            if fail_streak in (3, 8):
                _reset_session()
            time.sleep(min(8.0, poll_interval * (1 + fail_streak * 0.5)))
            continue
        results = data.get("results") or []
        misses = data.get("misses") or []
        sig = (len(results), len(misses), data.get("state"))
        if on_progress and sig != last_sig:
            last_sig = sig
            on_progress(
                {
                    "state": data.get("state") or "running",
                    "results": results,
                    "misses": misses,
                    "found": len(results),
                    "sites_done": len(results) + len(misses),
                    "current": data.get("current"),
                    "total": data.get("total"),
                    "product": data.get("product"),
                }
            )
        state = data.get("state")
        if state == "done":
            report = data.get("report")
            if isinstance(report, dict):
                return report
            return {
                "query": query,
                "our_price": our_price,
                "results": results,
                "misses": misses,
            }
        if state == "error":
            raise RuntimeError(data.get("error") or "Помилка worker search")
        time.sleep(poll_interval)
