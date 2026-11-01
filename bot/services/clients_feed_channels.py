"""
Мітки каналів продажу з фіду analytics_customer.xml (Instagram, Xlash, Сайт).

Синтетична мітка «site» додається при синку, якщо частина замовлень не покрита
мітками з фіду (orders_count клієнта > сума orders_count по <tag>).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

# Канали з окремими мітками у фіді (не змішувати з «site» при нормалізації).
FEED_CHANNEL_TAG_CODES: frozenset[str] = frozenset({"instagram", "xlash"})

# Коди з фіду, що означають основний сайт Cosmy — зводимо до canonical «site».
FEED_SITE_TAG_ALIASES: frozenset[str] = frozenset(
    {"site", "cosmy", "website", "web", "сайт", "shop", "store"}
)

FEED_SITE_CANONICAL_CODE = "site"
FEED_SITE_LABEL = "Сайт"

# Порядок чіпів на /clients (решта — за кількістю клієнтів).
FEED_TAG_PIN_ORDER: tuple[str, ...] = ("instagram", "xlash", FEED_SITE_CANONICAL_CODE)


def _merge_tag_dates(
    existing: dict[str, Any],
    first: datetime | None,
    last: datetime | None,
) -> None:
    if first is not None:
        prev = existing.get("first_order_date")
        if prev is None or first < prev:
            existing["first_order_date"] = first
    if last is not None:
        prev = existing.get("last_order_date")
        if prev is None or last > prev:
            existing["last_order_date"] = last


def augment_customer_feed_tags(
    customer_orders_count: int | None,
    tags: list[dict[str, Any]],
    *,
    first_order_date: datetime | None = None,
    last_order_date: datetime | None = None,
) -> list[dict[str, Any]]:
    """Нормалізує коди міток і додає «site» для невіднесених замовлень."""
    total = int(customer_orders_count or 0)
    merged: dict[str, dict[str, Any]] = {}

    for raw in tags:
        code = str(raw.get("code") or "").strip().lower()
        if not code:
            continue
        if code in FEED_SITE_TAG_ALIASES:
            code = FEED_SITE_CANONICAL_CODE
        label = str(raw.get("label") or code).strip() or code
        if code == FEED_SITE_CANONICAL_CODE:
            label = FEED_SITE_LABEL

        bucket = merged.get(code)
        if bucket is None:
            bucket = {
                "code": code,
                "label": label,
                "orders_count": raw.get("orders_count"),
                "first_order_date": raw.get("first_order_date"),
                "last_order_date": raw.get("last_order_date"),
            }
            merged[code] = bucket
        else:
            oc = raw.get("orders_count")
            if oc is not None:
                prev = bucket.get("orders_count")
                bucket["orders_count"] = (int(prev) if prev is not None else 0) + int(oc)
            _merge_tag_dates(bucket, raw.get("first_order_date"), raw.get("last_order_date"))

    out = list(merged.values())
    if total <= 0:
        return out

    tag_sum = sum(int(t.get("orders_count") or 0) for t in out)
    has_any_tag_count = any(t.get("orders_count") is not None for t in out)

    if not out:
        out.append(
            {
                "code": FEED_SITE_CANONICAL_CODE,
                "label": FEED_SITE_LABEL,
                "orders_count": total,
                "first_order_date": first_order_date,
                "last_order_date": last_order_date,
            }
        )
        return out

    if not has_any_tag_count:
        return out

    residual = total - tag_sum
    if residual <= 0:
        return out

    site = merged.get(FEED_SITE_CANONICAL_CODE)
    if site is None:
        site = {
            "code": FEED_SITE_CANONICAL_CODE,
            "label": FEED_SITE_LABEL,
            "orders_count": residual,
            "first_order_date": first_order_date,
            "last_order_date": last_order_date,
        }
        out.append(site)
    else:
        prev = site.get("orders_count")
        site["orders_count"] = (int(prev) if prev is not None else 0) + residual
        _merge_tag_dates(site, first_order_date, last_order_date)

    return out


def sort_feed_tag_filter_options(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Instagram → Xlash → Сайт, далі за спаданням clients_count."""
    pin_rank = {code: idx for idx, code in enumerate(FEED_TAG_PIN_ORDER)}

    def sort_key(row: dict[str, Any]) -> tuple:
        code = str(row.get("code") or "").lower()
        return (
            pin_rank.get(code, len(FEED_TAG_PIN_ORDER) + 1),
            -int(row.get("clients_count") or 0),
            str(row.get("label") or code).casefold(),
        )

    return sorted(options, key=sort_key)
