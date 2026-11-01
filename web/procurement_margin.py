"""Розрахунок маржі товарів закупівельного модуля."""

from __future__ import annotations

from typing import Optional

from bot.database.products_models import Product, ProductOption

# У фіді/Dilovod «немає закупівельної» часто приходить як 0 або 1 ₴ — не реальна ціна.
MIN_VALID_PURCHASE_UAH = 1.0


def is_valid_purchase_price(purchase_price: float | int | None) -> bool:
    if purchase_price is None:
        return False
    return float(purchase_price) > MIN_VALID_PURCHASE_UAH


def calc_margin_pct(
    selling_price: float | int | None,
    purchase_price: float | int | None,
) -> float | None:
    """
    Маржа, % = (ціна продажу − закупівельна) ÷ ціна продажу × 100.
  """
    if selling_price is None or purchase_price is None:
        return None
    sell = float(selling_price)
    buy = float(purchase_price)
    if sell <= 0 or not is_valid_purchase_price(buy):
        return None
    return (sell - buy) / sell * 100.0


def sku_selling_price(product: Product, option: Optional[ProductOption] = None) -> float | None:
    if option is not None:
        if option.selling_price_catalog is not None:
            return float(option.selling_price_catalog)
        if product.selling_price_catalog is not None:
            return float(product.selling_price_catalog)
        return None
    if product.selling_price_catalog is not None:
        return float(product.selling_price_catalog)
    return None


def sku_purchase_price(product: Product, option: Optional[ProductOption] = None) -> float | None:
    if option is not None:
        if option.purchase_price is not None:
            return float(option.purchase_price)
        if product.purchase_price is not None:
            return float(product.purchase_price)
        return None
    if product.purchase_price is not None:
        return float(product.purchase_price)
    return None


def sku_margin_pct(product: Product, option: Optional[ProductOption] = None) -> float | None:
    return calc_margin_pct(sku_selling_price(product, option), sku_purchase_price(product, option))


def format_margin_pct(value: float | None, *, digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}%"
