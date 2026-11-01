"""Допоміжні функції для правил інсайтів."""

from __future__ import annotations

import math
from typing import Any

from web.ad_analytics import _pct_delta, _safe_cpa, _safe_roas
from web.integrations.fx_rates import ad_spend_to_uah
from web.integrations.meta_ads import meta_roas_display


def fmt_currency_compact(value: float | int | None, currency: str | None = "UAH") -> str:
    code = (currency or "UAH").upper()
    suffix = {"UAH": "₴", "USD": "$", "EUR": "€"}.get(code, code)
    n = float(value or 0)
    sign = "−" if n < 0 else ""
    n = abs(n)
    if n >= 1_000_000:
        body = f"{n / 1_000_000:.1f} млн {suffix}"
    elif n >= 1_000:
        body = f"{n / 1_000:.0f} тис {suffix}"
    else:
        body = f"{n:,.2f} {suffix}".replace(",", " ")
    return f"{sign}{body}" if sign else body


def fmt_uah_compact(value: float | int | None) -> str:
    n = float(value or 0)
    sign = "−" if n < 0 else ""
    n = abs(n)
    if n >= 1_000_000:
        body = f"{n / 1_000_000:.1f} млн ₴"
    elif n >= 1_000:
        body = f"{n / 1_000:.0f} тис ₴"
    else:
        body = f"{n:,.0f} ₴".replace(",", " ")
    return f"{sign}{body}" if sign else body


def fmt_roas(roas: float | None) -> str:
    if roas is None:
        return "—"
    if roas > 50:
        return ">50x"
    return meta_roas_display(roas)


def fmt_delta(pct: float | None) -> str:
    if pct is None:
        return ""
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct}%"


def fmt_change_line(cur: float | int, prev: float | int, *, suffix: str = "₴") -> str:
    """Попередній → поточний, з абсолютною різницею."""
    delta = float(cur or 0) - float(prev or 0)
    pct = _pct_delta(cur, prev)
    if suffix == "₴":
        prev_s = fmt_uah_compact(prev)
        cur_s = fmt_uah_compact(cur)
        delta_s = fmt_uah_compact(delta)
    else:
        prev_s = f"{int(prev):,}".replace(",", " ")
        cur_s = f"{int(cur):,}".replace(",", " ")
        sign = "+" if delta >= 0 else "−"
        delta_s = f"{sign}{abs(int(delta)):,}".replace(",", " ")
    pct_part = f" ({fmt_delta(pct)})" if pct is not None else ""
    return f"{prev_s} → {cur_s}, Δ {delta_s}{pct_part}"


def spend_uah(api: dict[str, Any], as_of) -> float:
    return float(
        ad_spend_to_uah(float(api.get("spend") or 0), api.get("currency"), as_of)["amount_uah"]
    )


def channel_row(rows: list[dict[str, Any]], channel: str) -> dict[str, Any]:
    for row in rows:
        if row.get("channel") == channel:
            return row
    return {}


def significant_change(pct: float | None, threshold: float) -> bool:
    return pct is not None and abs(pct) >= threshold


def min_volume_ok(
    *,
    conversions: int = 0,
    prev_conversions: int = 0,
    clicks: int = 0,
    prev_clicks: int = 0,
    min_conversions: int = 0,
    min_clicks: int = 0,
    require_both_periods: bool = True,
) -> bool:
    """Перевірка достатнього обсягу. Для CRM достатньо сумарного мінімуму в одному з періодів."""
    if min_conversions:
        if require_both_periods:
            if conversions < min_conversions or prev_conversions < min_conversions:
                return False
        elif max(conversions, prev_conversions) < min_conversions:
            return False
    if min_clicks:
        if require_both_periods:
            if clicks < min_clicks or prev_clicks < min_clicks:
                return False
        elif max(clicks, prev_clicks) < min_clicks:
            return False
    return True


def contribution_share(part_delta: float, total_delta: float) -> float | None:
    if total_delta == 0:
        return None
    return round(100.0 * part_delta / total_delta, 1)


def top_contributors(
    items: list[dict[str, Any]],
    *,
    value_key: str,
    prev_key: str | None = None,
    label_key: str = "name",
    total_delta: float,
    min_abs: float = 0,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in items:
        cur = float(item.get(value_key) or 0)
        prev = float(item.get(prev_key or f"prev_{value_key}") or 0) if prev_key or f"prev_{value_key}" in item else 0.0
        delta = cur - prev
        if abs(delta) < min_abs:
            continue
        share = contribution_share(delta, total_delta)
        result.append({
            "label": item.get(label_key) or item.get("key") or "—",
            "delta": delta,
            "share_pct": share,
            "pct": _pct_delta(cur, prev),
            "current": cur,
            "previous": prev,
        })
    result.sort(key=lambda x: abs(x["delta"]), reverse=True)
    return result


def trend_from_delta(pct: float | None, *, good_when_up: bool = True) -> str:
    if pct is None or abs(pct) < 1:
        return "neutral"
    if good_when_up:
        return "up" if pct > 0 else "down"
    return "down" if pct > 0 else "up"


def signal_type_from_trend(trend: str) -> str:
    if trend in ("down", "warn"):
        return "risk"
    if trend == "up":
        return "positive"
    return "opportunity"


def z_score(value: float, history: list[float]) -> float | None:
    if len(history) < 3:
        return None
    mean = sum(history) / len(history)
    variance = sum((x - mean) ** 2 for x in history) / len(history)
    if variance <= 0:
        return None
    return (value - mean) / math.sqrt(variance)


def safe_div(numerator: float, denominator: float) -> float | None:
    if not denominator:
        return None
    return numerator / denominator


def roas_metrics(revenue: float, spend: float) -> float | None:
    return _safe_roas(revenue, spend)


def cpa_metrics(spend: float, conversions: int) -> float | None:
    return _safe_cpa(spend, conversions)
