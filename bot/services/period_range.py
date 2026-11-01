"""Єдині межі періодів для звітів, транзакцій і дашборду (київський час, включно)."""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta

from mono.api import now_kyiv


def day_start(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def day_end(dt: datetime) -> datetime:
    return dt.replace(hour=23, minute=59, second=59, microsecond=999999)


def subtract_calendar_months(dt: datetime, months: int) -> datetime:
    """Той самий календарний день N місяців тому (31.03 → 28/29.02 тощо)."""
    if months <= 0:
        return dt
    year = dt.year
    month = dt.month - months
    while month <= 0:
        month += 12
        year -= 1
    last_day = calendar.monthrange(year, month)[1]
    day = min(dt.day, last_day)
    return dt.replace(year=year, month=month, day=day)


TAX_QUARTER_LABELS = {
    1: "I квартал",
    2: "II квартал (півріччя)",
    3: "III квартал (9 місяців)",
    4: "IV квартал (рік)",
}

TAX_QUARTER_MONTHS = {
    1: "січень — березень",
    2: "квітень — червень",
    3: "липень — вересень",
    4: "жовтень — грудень",
}


def tax_quarter(dt: datetime) -> int:
    """Податковий (звітний) квартал України: I–IV."""
    return (dt.month - 1) // 3 + 1


def tax_quarter_label(year: int, quarter: int) -> str:
    return f"{TAX_QUARTER_LABELS[quarter]} {year}"


def tax_quarter_start(year: int, quarter: int) -> datetime:
    month = (quarter - 1) * 3 + 1
    return datetime(year, month, 1, 0, 0, 0, 0)


def tax_quarter_end(year: int, quarter: int) -> datetime:
    month = quarter * 3
    last_day = calendar.monthrange(year, month)[1]
    return datetime(year, month, last_day, 23, 59, 59, 999999)


def previous_tax_quarter(year: int, quarter: int) -> tuple[int, int]:
    if quarter <= 1:
        return year - 1, 4
    return year, quarter - 1


def period_dates(period: str) -> tuple[datetime, datetime, str]:
    """Повертає (date_from, date_to, period_name) — межі дня включно."""
    now = now_kyiv()
    end_today = day_end(now)
    start_today = day_start(now)

    if period == "today":
        date_from = start_today
        date_to = end_today
        period_name = now.strftime("%d.%m.%Y") + " (сьогодні)"
    elif period == "yesterday":
        yesterday = now - timedelta(days=1)
        date_from = day_start(yesterday)
        date_to = day_end(yesterday)
        period_name = yesterday.strftime("%d.%m.%Y") + " (вчора)"
    elif period == "week":
        date_from = day_start(now - timedelta(days=6))
        date_to = end_today
        period_name = (
            f"{date_from.strftime('%d.%m.%Y')} – {date_to.strftime('%d.%m.%Y')} (тиждень)"
        )
    elif period == "month":
        date_from = day_start(subtract_calendar_months(now, 1))
        date_to = end_today
        period_name = (
            f"{date_from.strftime('%d.%m.%Y')} – {date_to.strftime('%d.%m.%Y')} (місяць)"
        )
    elif period == "quarter":
        q = tax_quarter(now)
        date_from = tax_quarter_start(now.year, q)
        date_to = min(end_today, tax_quarter_end(now.year, q))
        period_name = (
            f"{date_from.strftime('%d.%m.%Y')} – {date_to.strftime('%d.%m.%Y')} "
            f"({tax_quarter_label(now.year, q)}, {TAX_QUARTER_MONTHS[q]})"
        )
    elif period == "year":
        date_from = day_start(now.replace(month=1, day=1))
        date_to = end_today
        period_name = (
            f"{date_from.strftime('%d.%m.%Y')} – {date_to.strftime('%d.%m.%Y')} "
            f"(календарний {now.year})"
        )
    else:
        date_from = day_start(now.replace(day=1))
        date_to = end_today
        period_name = date_from.strftime("%B %Y")

    return date_from, date_to, period_name


def calendar_year_to_date() -> tuple[datetime, datetime]:
    """Початок календарного року — кінець сьогодні (Київ)."""
    now = now_kyiv()
    return day_start(now.replace(month=1, day=1)), day_end(now)


def days_since_year_start() -> int:
    """Скільки днів від 1 січня до сьогодні включно (для YTD-синку)."""
    start, end = calendar_year_to_date()
    return max(1, (end.date() - start.date()).days + 1)
