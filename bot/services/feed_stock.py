"""Залишки з XML-фіду Cosmy: зріз для тестів і діагностики на сервері."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Optional
from xml.etree import ElementTree as ET

from bot.services.products_sync import FEED_URL, _int, _iter_feed_nodes, _text


@dataclass
class FeedOptionStock:
    product_option_value_id: Optional[int]
    option_value_name: Optional[str]
    stock_qty: Optional[int]
    stock_qty_opencart: Optional[int]
    stock_status: Optional[str]


@dataclass
class FeedProductStock:
    product_id: int
    name: str
    model: Optional[str]
    brand_name: Optional[str]
    has_options: bool
    product_stock_qty: Optional[int]
    product_stock_qty_opencart: Optional[int]
    options: list[FeedOptionStock] = field(default_factory=list)

    @property
    def options_stock_sum(self) -> int:
        return sum(int(o.stock_qty or 0) for o in self.options)

    @property
    def feed_effective_stock(self) -> int:
        """Як у UI: для варіацій — сума option.stock_qty, інакше product.stock_qty."""
        if self.has_options or self.options:
            if self.options:
                return self.options_stock_sum
        return int(self.product_stock_qty or 0)

    def describe_mismatch_hint(self, db_product_stock: Optional[int], db_options_sum: int) -> str:
        if self.feed_effective_stock == (db_product_stock or 0) and not self.options:
            return ""
        if self.options and db_options_sum == self.options_stock_sum:
            if (db_product_stock or 0) == (self.product_stock_qty or 0):
                return ""
            return (
                "У БД product.stock_qty не збігається з фідом, але сума опцій ок — "
                "в інтерфейсі дивіться залишок варіації, не рядок product.stock_qty."
            )
        hints = []
        if self.has_options and (self.product_stock_qty or 0) == 0 and self.options_stock_sum > 0:
            hints.append(
                f"У фіді has_options: залишок {self.options_stock_sum} шт на опціях, "
                f"product.stock_qty={self.product_stock_qty!r} (часто 0 — це норма для варіацій)."
            )
        if db_product_stock != self.product_stock_qty and not self.options:
            hints.append(
                f"product.stock_qty: фід={self.product_stock_qty!r}, БД={db_product_stock!r}"
            )
        if self.options and db_options_sum != self.options_stock_sum:
            hints.append(
                f"сума option.stock_qty: фід={self.options_stock_sum}, БД={db_options_sum}"
            )
        return " · ".join(hints)


def parse_feed_product_stock(prod_el: ET.Element) -> FeedProductStock | None:
    pid = _int(prod_el.find("product_id"))
    if pid is None:
        return None
    has_options = bool(_int(prod_el.find("has_options")) or 0)
    options: list[FeedOptionStock] = []
    options_el = prod_el.find("options")
    if options_el is not None:
        for opt_el in options_el.findall("option"):
            options.append(
                FeedOptionStock(
                    product_option_value_id=_int(opt_el.find("product_option_value_id")),
                    option_value_name=_text(opt_el.find("option_value_name")) or None,
                    stock_qty=_int(opt_el.find("stock_qty")),
                    stock_qty_opencart=_int(opt_el.find("stock_qty_opencart")),
                    stock_status=_text(opt_el.find("stock_status")) or None,
                )
            )
    return FeedProductStock(
        product_id=pid,
        name=_text(prod_el.find("name")) or f"Товар {pid}",
        model=_text(prod_el.find("model")) or None,
        brand_name=_text(prod_el.find("brand")) or None,
        has_options=has_options,
        product_stock_qty=_int(prod_el.find("stock_qty")),
        product_stock_qty_opencart=_int(prod_el.find("stock_qty_opencart")),
        options=options,
    )


def iter_feed_products_stock(path: str) -> Iterator[FeedProductStock]:
    for tag, elem in _iter_feed_nodes(path):
        if tag != "product":
            continue
        parsed = parse_feed_product_stock(elem)
        if parsed:
            yield parsed


def find_feed_products_by_query(path: str, query: str) -> list[FeedProductStock]:
    q = (query or "").strip().casefold()
    if not q:
        return []
    out: list[FeedProductStock] = []
    for item in iter_feed_products_stock(path):
        hay = " ".join(
            filter(
                None,
                (
                    item.name,
                    item.model or "",
                    item.brand_name or "",
                    str(item.product_id),
                ),
            )
        ).casefold()
        if q in hay:
            out.append(item)
    return out


__all__ = [
    "FEED_URL",
    "FeedOptionStock",
    "FeedProductStock",
    "find_feed_products_by_query",
    "iter_feed_products_stock",
    "parse_feed_product_stock",
]
