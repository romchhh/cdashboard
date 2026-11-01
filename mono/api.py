# API Monobank — клієнт, виписки, транзакції

import json
import logging
import re
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

from mono.config import BASE_URL

KYIV_TZ = ZoneInfo("Europe/Kyiv")


def now_kyiv() -> datetime:
    """Поточний час у Києві як naive datetime (для збереження в БД)."""
    return datetime.now(KYIV_TZ).replace(tzinfo=None)


def utc_timestamp_to_kyiv(ts: int) -> datetime:
    """Перетворює Unix timestamp (UTC) у naive datetime у київському часі."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(KYIV_TZ).replace(tzinfo=None)


def _get_expense_categories():
    """Категорії витрат з bot.database.init_data (єдине джерело правди)."""
    try:
        from bot.database.init_data import DEFAULT_CATEGORIES
        return {c["name"]: c["keywords"] for c in DEFAULT_CATEGORIES if c.get("keywords")}
    except ImportError:
        return {}


def format_amount(amount):
    """Форматування суми (копійки -> гривні)."""
    return amount / 100


_HTTP_ERRORS = {
    400: "невірний запит (400)",
    401: "токен недійсний або відсутній (401 Unauthorized)",
    403: "доступ заборонено (403 Forbidden)",
    404: "ресурс не знайдено (404)",
    429: "перевищено ліміт запитів API Monobank (429 Too Many Requests) — зачекайте хвилину",
    500: "тимчасовий збій API Monobank (500)",
    502: "шлюз Monobank недоступний (502)",
    503: "сервіс Monobank тимчасово недоступний (503)",
    504: "таймаут шлюза Monobank (504)",
}

MONO_CLIENT_INFO_RETRYABLE = frozenset({429, 500, 502, 503, 504})


def _parse_mono_api_error_body(body: str) -> str | None:
    text = (body or "").strip()
    if not text:
        return None
    if text.startswith("{") or text.startswith("["):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            desc = (
                payload.get("errorDescription")
                or payload.get("errText")
                or payload.get("error")
                or ""
            )
            desc = str(desc).strip()
            if desc:
                low = desc.lower()
                if "jarsbyclient" in low and ("502" in low or "html" in low):
                    return (
                        "тимчасовий збій сервісу Monobank (рахунки ФОП / JarsByClient). "
                        "Це на стороні банку — зазвичай проходить за кілька хвилин; повторіть синк пізніше."
                    )
                return desc[:400]
    if re.search(r"<html", text, re.I):
        return "шлюз Monobank повернув HTML-сторінку помилки (типово 502 Bad Gateway)"
    if len(text) <= 240:
        return text
    return text[:240] + "…"


def _http_error_reason(status_code: int, body: str = "") -> str:
    reason = _HTTP_ERRORS.get(status_code, f"HTTP {status_code}")
    detail = _parse_mono_api_error_body(body)
    if detail:
        return f"{reason} — {detail}"
    if body and len(body.strip()) <= 200:
        return f"{reason} — відповідь: {body.strip()}"
    return reason


def get_client_info(token) -> tuple[dict | None, str | None, int | None]:
    """
    Отримання інформації про клієнта та його рахунки.

    Returns:
        (data, error_reason, http_status) — якщо успішно: (dict, None, 200),
                                             якщо помилка: (None, опис причини, http_status або None)
    """
    headers = {"X-Token": token}
    try:
        response = requests.get(f"{BASE_URL}/personal/client-info", headers=headers, timeout=10)
    except requests.RequestException as e:
        reason = f"мережева помилка: {e}"
        print(f"Помилка отримання інформації: {reason}")
        return None, reason, None

    if response.status_code == 200:
        return response.json(), None, 200

    reason = _http_error_reason(response.status_code, response.text)
    print(f"Помилка отримання інформації: {reason}")
    return None, reason, response.status_code


def get_client_info_retried(
    token: str,
    *,
    max_attempts: int = 4,
) -> tuple[dict | None, str | None, int | None]:
    """
    client-info з повторами при 429 та тимчасових 5xx (типово JarsByClient 502 у тілі 500).
    """
    last_reason: str | None = None
    last_status: int | None = None
    for attempt in range(1, max_attempts + 1):
        data, reason, status = get_client_info(token)
        if data:
            return data, None, 200
        last_reason, last_status = reason, status
        retryable = status in MONO_CLIENT_INFO_RETRYABLE or status is None
        if not retryable or attempt >= max_attempts:
            break
        if status == 429:
            wait_sec = 65 * attempt
        else:
            wait_sec = min(12 * (2 ** (attempt - 1)), 90)
        logger.warning(
            "Monobank client-info: спроба %s/%s не вдалась (%s), чекаємо %s с",
            attempt,
            max_attempts,
            reason,
            wait_sec,
        )
        time.sleep(wait_sec)
    return None, last_reason, last_status


# Офіційний ліміт personal/statement: не більше 31 дня + 1 година.
MONO_STATEMENT_MAX_SECONDS = 31 * 24 * 60 * 60
MONO_STATEMENT_SLEEP_SEC = 60


class MonoStatementError(RuntimeError):
    """Помилка виписки Monobank — не можна трактувати як порожній період."""


def statement_time_windows(from_time: int, to_time: int) -> list[tuple[int, int]]:
    """Нарізає період на вікна ≤31 день, від старих дат до нових."""
    start = int(from_time)
    end = int(to_time)
    if end <= start:
        return [(start, end)]
    windows: list[tuple[int, int]] = []
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + MONO_STATEMENT_MAX_SECONDS, end)
        windows.append((cursor, chunk_end))
        cursor = chunk_end
    return windows


def get_statement(token, account_id, from_time, to_time=None) -> list:
    """Отримання виписки по рахунку. Порожній список = немає операцій; помилка API — виняток."""
    headers = {"X-Token": token}
    url = f"{BASE_URL}/personal/statement/{account_id}/{from_time}"
    if to_time:
        url += f"/{to_time}"
    try:
        response = requests.get(url, headers=headers, timeout=30)
    except requests.RequestException as e:
        raise MonoStatementError(f"мережева помилка: {e}") from e

    if response.status_code == 200:
        payload = response.json()
        return payload if isinstance(payload, list) else []

    if response.status_code == 429:
        wait_sec = MONO_STATEMENT_SLEEP_SEC
        logger.warning("Monobank 429 на виписці — чекаємо %s сек і повторюємо", wait_sec)
        time.sleep(wait_sec)
        try:
            response = requests.get(url, headers=headers, timeout=30)
        except requests.RequestException as e:
            raise MonoStatementError(f"мережева помилка після 429: {e}") from e
        if response.status_code == 200:
            payload = response.json()
            return payload if isinstance(payload, list) else []

    reason = _http_error_reason(response.status_code, response.text)
    raise MonoStatementError(reason)


def _paginate_statement_window(token, account_id, from_time, to_time) -> list:
    """Пагінація однієї вікна виписки (ліміт 500 рядків на запит)."""
    collected: list = []
    current_to = int(to_time)
    window_from = int(from_time)
    while current_to > window_from:
        page = get_statement(token, account_id, window_from, current_to)
        if not page:
            break
        collected.extend(page)
        if len(page) < 500:
            break
        current_to = int(page[-1]["time"]) - 1
        time.sleep(MONO_STATEMENT_SLEEP_SEC)
    return collected


def get_all_statements(token, account_id, from_time, to_time=None):
    """Усі транзакції за період: вікна ≤31 день + пагінація. Помилка API не маскується під «немає даних»."""
    end = int(to_time) if to_time else int(time.time())
    start = int(from_time)
    all_transactions: list = []
    seen_ids: set[str] = set()
    windows = statement_time_windows(start, end)
    for idx, (win_from, win_to) in enumerate(windows):
        if idx > 0:
            time.sleep(MONO_STATEMENT_SLEEP_SEC)
        page = _paginate_statement_window(token, account_id, win_from, win_to)
        for tx in page:
            tx_id = str(tx.get("id") or "")
            if tx_id and tx_id in seen_ids:
                continue
            if tx_id:
                seen_ids.add(tx_id)
            all_transactions.append(tx)
    return all_transactions


def classify_expense(description, counterpart_name, comment=None):
    """Автоматична класифікація витрати за описом, контрагентом та коментарем (категорії з bot.database.init_data)."""
    text = (description + " " + (counterpart_name or "") + " " + (comment or "")).lower()
    categories = _get_expense_categories()
    for category, keywords in categories.items():
        for keyword in keywords:
            if keyword in text:
                return category
    return "Інше"
