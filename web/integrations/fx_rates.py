"""Курси валют для зведення витрат реклами (НБУ API)."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

NBU_EXCHANGE_URL = "https://bank.gov.ua/NBUStatService/v1/statdirectory/exchange"
_RATE_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_LOCK = Lock()
_DEFAULT_FALLBACK_RATES: dict[str, float] = {"USD": 41.0, "EUR": 45.0}


def _as_date(value: datetime | date | None) -> date:
    if value is None:
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    return value


def _fetch_nbu_rate(valcode: str, on_date: date) -> tuple[float, str, bool]:
    """
    Повертає (курс, дата YYYY-MM-DD, approximate).
    approximate=True — якщо на дату немає курсу і взято попередній робочий день.
    """
    valcode = (valcode or "USD").upper()
    cache_key = f"{valcode}:{on_date.isoformat()}"
    with _CACHE_LOCK:
        cached = _RATE_CACHE.get(cache_key)
        if cached:
            return float(cached["rate"]), str(cached["date"]), bool(cached.get("approximate"))

    for offset in range(0, 10):
        probe = on_date - timedelta(days=offset)
        url = f"{NBU_EXCHANGE_URL}?valcode={valcode}&date={probe.strftime('%Y%m%d')}&json"
        try:
            with urllib.request.urlopen(url, timeout=8) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("NBU FX fetch failed for %s %s: %s", valcode, probe, exc)
            continue

        if not payload:
            continue
        row = payload[0] if isinstance(payload, list) else payload
        rate = float(row.get("rate") or 0)
        if rate <= 0:
            continue
        exchangedate = str(row.get("exchangedate") or probe.strftime("%d.%m.%Y"))
        # NBU format DD.MM.YYYY → ISO
        iso_date = probe.isoformat()
        if len(exchangedate) == 10 and exchangedate[2] == ".":
            d, m, y = exchangedate.split(".")
            iso_date = f"{y}-{m}-{d}"

        approximate = offset > 0
        with _CACHE_LOCK:
            _RATE_CACHE[cache_key] = {
                "rate": rate,
                "date": iso_date,
                "approximate": approximate,
            }
        return rate, iso_date, approximate

    return _fallback_rate(valcode, on_date)


def _fallback_rate(valcode: str, on_date: date) -> tuple[float, str, bool]:
    """Резервний курс, якщо НБУ недоступний — не ламаємо дашборд."""
    valcode = (valcode or "USD").upper()
    with _CACHE_LOCK:
        for key, cached in _RATE_CACHE.items():
            if key.startswith(f"{valcode}:") and float(cached.get("rate") or 0) > 0:
                logger.warning(
                    "NBU unavailable for %s — using cached rate %.4g from %s",
                    valcode,
                    cached["rate"],
                    cached.get("date"),
                )
                return float(cached["rate"]), str(cached["date"]), True

    env_key = f"AD_FX_FALLBACK_{valcode}_UAH"
    env_val = os.getenv(env_key) or (os.getenv("AD_FX_FALLBACK_USD_UAH") if valcode == "USD" else None)
    if env_val:
        try:
            rate = float(env_val)
            if rate > 0:
                iso = on_date.isoformat()
                logger.warning("NBU unavailable for %s — using env fallback %.4g", valcode, rate)
                return rate, iso, True
        except ValueError:
            pass

    rate = _DEFAULT_FALLBACK_RATES.get(valcode, _DEFAULT_FALLBACK_RATES["USD"])
    iso = on_date.isoformat()
    logger.error("NBU unavailable for %s — using default fallback %.4g", valcode, rate)
    return rate, iso, True


def get_usd_uah_rate(as_of: datetime | date | None = None) -> dict[str, Any]:
    rate, iso_date, approximate = _fetch_nbu_rate("USD", _as_date(as_of))
    return {
        "rate": rate,
        "currency": "USD",
        "target": "UAH",
        "date": iso_date,
        "source": "nbu",
        "approximate": approximate,
    }


def ad_spend_to_uah(
    amount: float | int | None,
    currency: str | None,
    as_of: datetime | date | None = None,
) -> dict[str, Any]:
    """
    Конвертує витрати рекламного каналу в UAH для зведених KPI.
    UAH — без конвертації; USD — курс НБУ на дату (або найближчий попередній).
    """
    raw = round(float(amount or 0), 2)
    code = (currency or "UAH").upper()

    if not raw or code == "UAH":
        return {
            "amount_uah": raw,
            "amount_raw": raw,
            "currency": code,
            "fx_rate": None,
            "fx_date": None,
            "fx_approximate": False,
            "converted": False,
        }

    if code == "USD":
        fx = get_usd_uah_rate(as_of)
        return {
            "amount_uah": round(raw * fx["rate"], 2),
            "amount_raw": raw,
            "currency": code,
            "fx_rate": fx["rate"],
            "fx_date": fx["date"],
            "fx_approximate": fx["approximate"],
            "converted": True,
        }

    if code == "EUR":
        rate, iso_date, approximate = _fetch_nbu_rate("EUR", _as_date(as_of))
        return {
            "amount_uah": round(raw * rate, 2),
            "amount_raw": raw,
            "currency": code,
            "fx_rate": rate,
            "fx_date": iso_date,
            "fx_approximate": approximate,
            "converted": True,
        }

    logger.warning("Невідома валюта витрат %s — без конвертації", code)
    return {
        "amount_uah": raw,
        "amount_raw": raw,
        "currency": code,
        "fx_rate": None,
        "fx_date": None,
        "fx_approximate": True,
        "converted": False,
    }


def convert_daily_spend_series(
    daily: list[dict[str, Any]] | None,
    currency: str | None,
) -> list[dict[str, Any]]:
    """Конвертує денні витрати Meta (USD) у UAH для зведеного графіка."""
    code = (currency or "UAH").upper()
    if code == "UAH" or not daily:
        return list(daily or [])

    out: list[dict[str, Any]] = []
    for row in daily:
        day = (row.get("date") or "").strip()
        as_of: datetime | date | None = None
        if day:
            try:
                as_of = datetime.strptime(day[:10], "%Y-%m-%d").date()
            except ValueError:
                as_of = None
        conv = ad_spend_to_uah(float(row.get("spend") or 0), code, as_of)
        out.append({**row, "spend": conv["amount_uah"]})
    return out


def format_fx_spend_note(
    *,
    meta_native: dict[str, Any] | None,
    gads_native: dict[str, Any] | None = None,
) -> str | None:
    """Текст підказки для UI про орієнтовну конвертацію."""
    parts: list[str] = []
    meta_native = meta_native or {}
    gads_native = gads_native or {}

    if meta_native.get("converted") and meta_native.get("fx_rate"):
        parts.append(
            f"Meta ${meta_native['amount_raw']:,.2f} → ≈{meta_native['amount_uah']:,.0f} ₴ "
            f"(курс НБУ {meta_native['fx_rate']:.4g} ₴/$, {meta_native.get('fx_date') or '—'})"
        )
    if gads_native.get("converted") and gads_native.get("fx_rate"):
        parts.append(
            f"Google Ads {gads_native['amount_raw']:,.2f} {gads_native['currency']} "
            f"→ ≈{gads_native['amount_uah']:,.0f} ₴"
        )

    if not parts:
        return None
    note = ". ".join(parts).replace(",", " ")
    if meta_native.get("fx_approximate"):
        note += ". Курс на найближчу доступну дату НБУ."
    return f"Орієнтовні витрати: {note}"
