"""Серверне сортування таблиць закупівель (усі SKU, не лише поточна сторінка)."""

from __future__ import annotations

from typing import Any, Callable, Mapping
from urllib.parse import urlencode

CATALOG_SORT_KEYS = frozenset({
    "revenue",
    "sales",
    "sales_avg",
    "frozen_capital",
    "turnover",
    "turnover_slow",
    "stock",
    "stock_low",
    "stock_high",
    "name",
    "brand",
    "model",
    "price",
    "purchase_price",
    "margin",
    "dynamics",
    "buy",
    "forecast",
    "status",
})

RECOMMENDATION_SORT_KEYS = frozenset({
    "buy",
    "name",
    "model",
    "stock",
    "price",
    "purchase_price",
    "turnover",
    "sales_avg",
    "dynamics",
    "forecast",
    "status",
    "action",
})

TEXT_SORT_DEFAULT_ASC = frozenset({"name", "model", "brand", "status", "action"})


def normalize_sort_dir(sort_dir: str | None, *, sort_by: str) -> str:
    d = (sort_dir or "").strip().lower()
    if d in ("asc", "desc"):
        return d
    return "asc" if sort_by in TEXT_SORT_DEFAULT_ASC else "desc"


def make_sort_url(
    base_path: str,
    column: str,
    *,
    sort_by: str,
    sort_dir: str,
    extra: Mapping[str, Any] | None = None,
    default_sort_by: str,
    default_sort_dir: str = "desc",
) -> str:
    """Посилання для кліку по заголовку колонки (toggle asc/desc)."""
    if column == sort_by:
        new_dir = "asc" if sort_dir == "desc" else "desc"
    else:
        new_dir = "asc" if column in TEXT_SORT_DEFAULT_ASC else "desc"

    params: dict[str, str] = {}
    if extra:
        for k, v in extra.items():
            if v is None or v == "":
                continue
            params[str(k)] = str(v)

    if column != default_sort_by:
        params["sort_by"] = column
    else:
        params.pop("sort_by", None)

    if new_dir != default_sort_dir or column != default_sort_by:
        params["sort_dir"] = new_dir
    elif column == default_sort_by and new_dir == default_sort_dir:
        params.pop("sort_dir", None)

    params.pop("page", None)
    qs = urlencode(params)
    return base_path + (f"?{qs}" if qs else "")


def make_sort_url_factory(
    base_path: str,
    *,
    sort_by: str,
    sort_dir: str,
    extra: Mapping[str, Any] | None = None,
    default_sort_by: str,
    default_sort_dir: str = "desc",
) -> Callable[[str], str]:
    def _fn(column: str) -> str:
        return make_sort_url(
            base_path,
            column,
            sort_by=sort_by,
            sort_dir=sort_dir,
            extra=extra,
            default_sort_by=default_sort_by,
            default_sort_dir=default_sort_dir,
        )

    return _fn
