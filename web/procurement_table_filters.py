"""Серверні фільтри таблиць закупівель (усі SKU, не лише поточна сторінка)."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Mapping, Optional
from urllib.parse import urlencode

from bot.database.products_models import Product, ProductAnalytics, ProductOption
from bot.services.abc_analysis import recommendation_row_horizon_view

STATUS_ALIASES: dict[str, tuple[str, ...]] = {
    "fast_mover": ("fast", "fast_mover", "швидк"),
    "medium_mover": ("medium", "medium_mover", "середн"),
    "slow_mover": ("slow", "slow_mover", "повільн"),
    "dead_stock": ("dead", "dead_stock", "мертв"),
    "new": ("new", "нов"),
    "out_of_stock": ("out", "out_of_stock", "немає", "нема"),
}

ACTION_ALIASES: dict[str, tuple[str, ...]] = {
    "buy_urgent": ("buy_urgent", "терміново", "urgent"),
    "buy_normal": ("buy_normal", "закупити", "normal"),
    "buy_careful": ("buy_careful", "обережно", "careful"),
    "reduce": ("reduce", "зменшити"),
    "stop": ("stop", "стоп"),
    "sell_off": ("sell_off", "розпродаж", "sell"),
}


def _parse_float(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    s = str(raw).strip().replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def normalize_product_search_query(q: str | None) -> str:
    return (q or "").strip()


def _text_match(haystack: str, needle: str) -> bool:
    n = normalize_product_search_query(needle).lower()
    if not n:
        return True
    return n in (haystack or "").lower()


def catalog_row_search_text(product: Product, option: ProductOption | None) -> str:
    """Канонічний haystack для пошуку SKU (сервер + data-product-search у таблицях)."""
    return " ".join(p for p in _row_text_parts(product, option) if p).lower()


def product_search_matches_row(
    product: Product,
    option: ProductOption | None,
    q: str | None,
) -> bool:
    needle = normalize_product_search_query(q)
    if not needle:
        return True
    return _text_match(catalog_row_search_text(product, option), needle)


def filter_catalog_by_product_query(
    items: list[tuple[Product, ProductOption | None, ProductAnalytics]],
    q: str | None,
    horizon_days: int,
    purchase_horizon_days: int,
) -> list[tuple[Product, ProductOption | None, ProductAnalytics]]:
    needle = normalize_product_search_query(q)
    if not needle:
        return items
    filters = SkuTableFilters(q=needle)
    return filter_catalog_items(items, filters, horizon_days, purchase_horizon_days)


def _num_in_range(value: Optional[float], lo: Optional[float], hi: Optional[float]) -> bool:
    if lo is not None and (value is None or value < lo):
        return False
    if hi is not None and (value is None or value > hi):
        return False
    return True


def _dynamics_trend_30d(item: Product | ProductOption) -> str:
    s30 = float(getattr(item, "sales_qty_30d", None) or 0)
    s60 = float(getattr(item, "sales_qty_60d", None) or 0)
    prev30 = s60 - s30
    if prev30 > 0:
        t30 = (s30 - prev30) / prev30 * 100
        if t30 > 5:
            return "up"
        if t30 < -5:
            return "down"
        return "flat"
    if s30 > 0:
        return "up"
    return "flat"


def _status_matches(analytics: ProductAnalytics, needle: str) -> bool:
    n = (needle or "").strip().lower()
    if not n:
        return True
    status = (analytics.status or "").lower()
    if n in status or status in n:
        return True
    for key, aliases in STATUS_ALIASES.items():
        if status == key and any(a in n or n in a for a in aliases):
            return True
    return any(a in n for a in STATUS_ALIASES.get(status, ()))


def _action_matches(analytics: ProductAnalytics, needle: str) -> bool:
    n = (needle or "").strip().lower()
    if not n:
        return True
    action = (analytics.procurement_action or "").lower()
    if n in action or action in n:
        return True
    for key, aliases in ACTION_ALIASES.items():
        if action == key and any(a in n or n in a for a in aliases):
            return True
    return any(a in n for a in ACTION_ALIASES.get(action, ()))


@dataclass
class SkuTableFilters:
    q: str = ""
    f_model: str = ""
    f_brand: str = ""
    f_stock_min: Optional[float] = None
    f_stock_max: Optional[float] = None
    f_price_min: Optional[float] = None
    f_price_max: Optional[float] = None
    f_turn_min: Optional[float] = None
    f_turn_max: Optional[float] = None
    f_sales_min: Optional[float] = None
    f_sales_max: Optional[float] = None
    f_buy_min: Optional[float] = None
    f_buy_max: Optional[float] = None
    f_fcst_min: Optional[float] = None
    f_fcst_max: Optional[float] = None
    f_status: str = ""
    f_action: str = ""
    f_dyn: str = ""

    @classmethod
    def from_query_params(cls, params: Mapping[str, Any]) -> "SkuTableFilters":
        return cls(
            q=(params.get("q") or "").strip(),
            f_model=(params.get("f_model") or "").strip(),
            f_brand=(params.get("f_brand") or "").strip(),
            f_stock_min=_parse_float(params.get("f_stock_min")),
            f_stock_max=_parse_float(params.get("f_stock_max")),
            f_price_min=_parse_float(params.get("f_price_min")),
            f_price_max=_parse_float(params.get("f_price_max")),
            f_turn_min=_parse_float(params.get("f_turn_min")),
            f_turn_max=_parse_float(params.get("f_turn_max")),
            f_sales_min=_parse_float(params.get("f_sales_min")),
            f_sales_max=_parse_float(params.get("f_sales_max")),
            f_buy_min=_parse_float(params.get("f_buy_min")),
            f_buy_max=_parse_float(params.get("f_buy_max")),
            f_fcst_min=_parse_float(params.get("f_fcst_min")),
            f_fcst_max=_parse_float(params.get("f_fcst_max")),
            f_status=(params.get("f_status") or "").strip(),
            f_action=(params.get("f_action") or "").strip(),
            f_dyn=(params.get("f_dyn") or "").strip().lower(),
        )

    def has_any(self) -> bool:
        for f in fields(self):
            val = getattr(self, f.name)
            if val is None:
                continue
            if isinstance(val, str) and not val:
                continue
            return True
        return False

    def to_query_dict(self, *, page: int | None = None, extra: dict | None = None) -> dict[str, str]:
        data: dict[str, str] = {}
        for f in fields(self):
            val = getattr(self, f.name)
            if val is None:
                continue
            if isinstance(val, str):
                if not val:
                    continue
                data[f.name] = val
            else:
                data[f.name] = str(val)
        if extra:
            for k, v in extra.items():
                if v is None or v == "":
                    data.pop(k, None)
                else:
                    data[k] = str(v)
        if page is not None and page > 1:
            data["page"] = str(page)
        elif "page" in data and (page is None or page <= 1):
            data.pop("page", None)
        return data

    def query_string(self, *, page: int | None = None, extra: dict | None = None) -> str:
        return urlencode(self.to_query_dict(page=page, extra=extra))


def build_pagination_url(base_path: str, filters: SkuTableFilters, page: int, extra: dict | None = None) -> str:
    qs = filters.query_string(page=page, extra=extra)
    return base_path + (f"?{qs}" if qs else "")


def _row_text_parts(product: Product, option: ProductOption | None) -> list[str]:
    parts = [product.name or "", product.model or "", product.sku or "", product.brand_name or ""]
    if option:
        parts.extend([
            option.option_name or "",
            option.option_value_name or "",
            option.option_model or "",
            option.dilovod_model or "",
        ])
    return parts


def _stock_qty(product: Product, option: ProductOption | None) -> int:
    if option is not None and option.stock_qty is not None:
        return int(option.stock_qty or 0)
    return int(product.stock_qty or 0)


def _site_price(product: Product, option: ProductOption | None) -> Optional[float]:
    item = option or product
    price = getattr(item, "selling_price_catalog", None)
    if price is None and option:
        price = product.selling_price_catalog
    return float(price) if price else None


def _buy_qty(analytics: ProductAnalytics, hv: dict) -> float:
    action = analytics.procurement_action
    if action == "reduce":
        return float(-(hv.get("surplus_qty") or 0))
    if action in ("stop", "sell_off"):
        return 0.0
    return float(hv.get("recommended_purchase") or 0)


def matches_catalog_row(
    product: Product,
    option: ProductOption | None,
    analytics: ProductAnalytics,
    filters: SkuTableFilters,
    horizon_days: int,
    purchase_horizon_days: int,
) -> bool:
    hv = recommendation_row_horizon_view(
        product, option, analytics, horizon_days, purchase_horizon_days
    )
    if not product_search_matches_row(product, option, filters.q):
        return False

    model = ""
    if option and option.option_model:
        model = option.option_model
    elif option and option.dilovod_model:
        model = option.dilovod_model
    else:
        model = product.model or ""
    if not _text_match(model, filters.f_model):
        return False
    if not _text_match(product.brand_name or "", filters.f_brand):
        return False

    stock = _stock_qty(product, option)
    if not _num_in_range(float(stock), filters.f_stock_min, filters.f_stock_max):
        return False

    price = _site_price(product, option)
    if not _num_in_range(price, filters.f_price_min, filters.f_price_max):
        return False

    turnover = hv.get("turnover_days_display")
    if not _num_in_range(float(turnover) if turnover is not None else None, filters.f_turn_min, filters.f_turn_max):
        return False

    avg = float(hv.get("avg_daily_sales") or 0)
    if not _num_in_range(avg, filters.f_sales_min, filters.f_sales_max):
        return False

    buy = _buy_qty(analytics, hv)
    if not _num_in_range(buy, filters.f_buy_min, filters.f_buy_max):
        return False

    fcst = float(hv.get("forecast_qty") or 0)
    if not _num_in_range(fcst, filters.f_fcst_min, filters.f_fcst_max):
        return False

    if not _status_matches(analytics, filters.f_status):
        return False
    if not _action_matches(analytics, filters.f_action):
        return False

    dyn = filters.f_dyn
    if dyn:
        trend = _dynamics_trend_30d(option or product)
        norm = {
            "up": "up", "u": "up", "↑": "up", "ріст": "up",
            "down": "down", "d": "down", "↓": "down", "пад": "down",
            "flat": "flat", "f": "flat", "→": "flat", "0": "flat",
        }
        want = norm.get(dyn, dyn)
        if want != trend:
            return False

    return True


def filter_catalog_items(
    items: list[tuple[Product, ProductOption | None, ProductAnalytics]],
    filters: SkuTableFilters,
    horizon_days: int,
    purchase_horizon_days: int,
) -> list[tuple[Product, ProductOption | None, ProductAnalytics]]:
    if not filters.has_any():
        return items
    return [
        it for it in items
        if matches_catalog_row(it[0], it[1], it[2], filters, horizon_days, purchase_horizon_days)
    ]


def matches_recommendation_row(
    product: Product,
    option: ProductOption | None,
    analytics: ProductAnalytics,
    hv: dict,
    filters: SkuTableFilters,
) -> bool:
    return matches_catalog_row(
        product,
        option,
        analytics,
        filters,
        int(hv.get("horizon_days") or 7),
        int(hv.get("purchase_horizon_days") or hv.get("horizon_days") or 7),
    )


def filter_recommendation_rows(
    rows: list[tuple[Product, ProductOption | None, ProductAnalytics, dict]],
    filters: SkuTableFilters,
) -> list[tuple[Product, ProductOption | None, ProductAnalytics, dict]]:
    if not filters.has_any():
        return rows
    return [r for r in rows if matches_recommendation_row(r[0], r[1], r[2], r[3], filters)]
