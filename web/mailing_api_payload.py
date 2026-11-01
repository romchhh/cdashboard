"""Парсинг JSON для тригерних email API (товари, замовлення, посилання з UTM)."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

SITE_DEFAULT_PRODUCT_URL = "https://cosmy.com.ua"


def _float_price(val: Any) -> float | None:
    if val is None or val == "":
        return None
    try:
        n = float(val)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def _money_amount(val: Any, *, allow_negative: bool = False) -> float | None:
    """Число грн; для знижок OpenCart може прийти від’ємне — беремо модуль."""
    if val is None or val == "":
        return None
    try:
        n = float(val)
    except (TypeError, ValueError):
        return None
    if allow_negative:
        return abs(n)
    return n if n >= 0 else None


def _int_qty(val: Any) -> int:
    try:
        return max(1, int(val))
    except (TypeError, ValueError):
        return 1


def _str_field(it: dict, *keys: str) -> str:
    for k in keys:
        v = it.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def normalize_product_item(it: dict[str, Any], *, price_drop: bool = False) -> dict[str, Any]:
    """Єдиний формат товару для листів."""
    pid_raw = it.get("product_id")
    product_id: int | None = None
    if pid_raw is not None:
        try:
            product_id = int(pid_raw)
        except (TypeError, ValueError):
            product_id = None

    name = _str_field(it, "name", "product_name", "title")
    image_url = _str_field(it, "image_url", "image", "photo_url", "picture")
    product_url = _str_field(it, "product_url", "url", "link", "href")
    quantity = _int_qty(it.get("quantity") or it.get("qty") or 1)

    if price_drop:
        old_price = _float_price(it.get("old_price") or it.get("price_old"))
        new_price = _float_price(
            it.get("new_price") or it.get("price") or it.get("sale_price")
        )
        price = new_price
        out: dict[str, Any] = {
            "product_id": product_id,
            "name": name,
            "image_url": image_url or "/static/cosmy25@2x.png",
            "product_url": product_url,
            "price": price,
            "quantity": quantity,
            "old_price": old_price,
            "new_price": new_price,
        }
    else:
        price = _float_price(it.get("price") or it.get("sale_price") or it.get("unit_price"))
        price_old = _float_price(
            it.get("price_old") or it.get("old_price") or it.get("regular_price")
        )
        out = {
            "product_id": product_id,
            "name": name,
            "image_url": image_url or "/static/cosmy25@2x.png",
            "product_url": product_url,
            "price": price,
            "quantity": quantity,
            "price_old": price_old if price_old and price and price_old > price else None,
        }
    return out


def _field_from_order_payload(payload: dict, *keys: str) -> str:
    """Шукає поле в корені JSON або в об'єкті order { ... }."""
    sources: list[dict] = []
    if isinstance(payload, dict):
        sources.append(payload)
        nested = payload.get("order")
        if isinstance(nested, dict):
            sources.append(nested)
    for src in sources:
        val = _str_field(src, *keys)
        if val:
            return val
    return ""


def _order_payload_sources(payload: dict) -> list[dict]:
    sources: list[dict] = []
    if isinstance(payload, dict):
        sources.append(payload)
        nested = payload.get("order")
        if isinstance(nested, dict):
            sources.append(nested)
    return sources


def _price_from_order_payload(
    payload: dict, *keys: str, allow_negative: bool = False
) -> float | None:
    for src in _order_payload_sources(payload):
        for k in keys:
            if k in src:
                p = _money_amount(src.get(k), allow_negative=allow_negative)
                if p is not None:
                    return p
    return None


_DISCOUNT_TOTAL_CODES = frozenset(
    {
        "coupon",
        "voucher",
        "reward",
        "discount",
        "loyalty",
        "club",
        "cosmy_club",
        "promo",
        "promotion",
        "special",
    }
)
_ORDER_TOTAL_CODES = frozenset({"total", "grand_total", "order_total"})


def _prices_from_opencart_totals(payload: dict) -> tuple[float | None, float | None]:
    """Fallback: масив totals[] як у кошику/замовленні OpenCart (code + value)."""
    discount_sum = 0.0
    found_discount = False
    order_total: float | None = None
    for src in _order_payload_sources(payload):
        rows = src.get("totals") or src.get("order_totals")
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = str(row.get("code") or row.get("key") or "").strip().lower()
            raw_val = row.get("value")
            if raw_val is None:
                raw_val = row.get("amount") or row.get("text")
            if code in _DISCOUNT_TOTAL_CODES:
                amt = _money_amount(raw_val, allow_negative=True)
                if amt is not None and amt > 0:
                    discount_sum += amt
                    found_discount = True
            elif code in _ORDER_TOTAL_CODES and order_total is None:
                order_total = _money_amount(raw_val, allow_negative=False)
    return (discount_sum if found_discount else None, order_total)


def parse_order_context(payload: dict) -> dict[str, Any]:
    order_number = _field_from_order_payload(
        payload, "order_number", "order_id", "order_no", "number"
    )
    delivery_address = _field_from_order_payload(
        payload, "delivery_address", "address", "shipping_address"
    )
    payment_method = _field_from_order_payload(
        payload,
        "payment_method",
        "payment_method_title",
        "payment",
        "payment_type",
        "payment_title",
    )
    delivery_info = _field_from_order_payload(
        payload,
        "delivery_info",
        "delivery",
        "shipping_method",
        "delivery_method",
        "delivery_title",
    )
    delivery_cost = _field_from_order_payload(
        payload,
        "delivery_cost",
        "shipping_cost",
        "delivery_price",
        "shipping_price",
    )
    phone = _field_from_order_payload(payload, "phone", "telephone", "customer_phone")
    customer_full_name = _customer_full_name_from_payload(payload)

    # Як у кошику: 1) знижка Cosmy club / промокод (грн), 2) «Разом» = сума замовлення.
    order_total = _price_from_order_payload(
        payload,
        "order_total",
        "total",
        "grand_total",
        "total_amount",
        "amount",
        "sum",
    )
    discount = _price_from_order_payload(
        payload,
        "discount",
        "discount_amount",
        "loyalty_discount",
        "promo_discount",
        "coupon_discount",
        "club_discount",
        "cosmy_club_discount",
        "reward_discount",
        "voucher_discount",
        allow_negative=True,
    )
    if order_total is None or discount is None:
        totals_discount, totals_order = _prices_from_opencart_totals(payload)
        if discount is None:
            discount = totals_discount
        if order_total is None:
            order_total = totals_order

    return {
        "order_number": order_number,
        "delivery_address": delivery_address or None,
        "payment_method": payment_method or None,
        "delivery_info": delivery_info or None,
        "delivery_cost": delivery_cost or delivery_info or None,
        "phone": phone or None,
        "customer_full_name": customer_full_name or None,
        "order_total": order_total,
        "discount": discount,
    }


def _customer_full_name_from_payload(payload: dict) -> str:
    direct = _field_from_order_payload(
        payload,
        "customer_full_name",
        "full_name",
        "customer_name",
        "recipient_name",
    )
    if direct:
        return direct
    lastname = _field_from_order_payload(payload, "lastname", "last_name", "surname")
    firstname = _field_from_order_payload(payload, "firstname", "first_name", "name")
    if lastname and firstname:
        return f"{lastname} {firstname}".strip()
    if lastname:
        return lastname
    return firstname


def recipient_name(payload: dict) -> str:
    full = _customer_full_name_from_payload(payload)
    if full:
        parts = full.split()
        if len(parts) >= 2:
            return parts[1]
        return parts[0]
    return str(payload.get("name") or payload.get("first_name") or "").strip()


def tracking_url(payload: dict, *keys: str, default: str = "") -> str | None:
    for k in keys:
        v = _str_field(payload, k)
        if v:
            return v
    return default or None


def _items_from_payload(payload: dict) -> list[dict] | None:
    items = payload.get("items")
    if isinstance(items, list):
        return [x for x in items if isinstance(x, dict)]
    products = payload.get("products")
    if isinstance(products, list):
        return [x for x in products if isinstance(x, dict)]
    return None


def resolve_products_from_payload(
    payload: dict, *, price_drop: bool = False
) -> tuple[list[dict] | None, str | None]:
    raw_items = _items_from_payload(payload)
    if not raw_items:
        return None, "Передайте items[] (або products[]) — масив товарів з сайту."
    products: list[dict] = []
    for raw in raw_items:
        p = normalize_product_item(raw, price_drop=price_drop)
        if not p.get("name"):
            return None, "Кожен елемент items[] має містити name (назву товару)."
        if price_drop and (p.get("old_price") is None or p.get("new_price") is None):
            return None, "Для зниження ціни вкажіть old_price та new_price в кожному товарі."
        products.append(p)
    if not products:
        return None, "items[] не містить валідних товарів."
    return products, None


async def enrich_products_from_db(
    products: list[dict],
    load_by_ids: Callable[[list[int], dict[int, int]], Awaitable[tuple[list[dict], list[int]]]],
) -> None:
    quantities = {
        int(p["product_id"]): int(p.get("quantity") or 1)
        for p in products
        if p.get("product_id")
    }
    for p in products:
        if not p.get("product_url"):
            if p.get("product_id"):
                p["product_url"] = (
                    f"{SITE_DEFAULT_PRODUCT_URL}/index.php?route=product/product"
                    f"&product_id={int(p['product_id'])}"
                )
            else:
                p["product_url"] = SITE_DEFAULT_PRODUCT_URL

    ids_for_db = [int(p["product_id"]) for p in products if p.get("product_id")]
    if not ids_for_db:
        return

    db_products, _ = await load_by_ids(ids_for_db, quantities)
    by_id = {int(x["product_id"]): x for x in db_products if x.get("product_id")}
    for p in products:
        dbp = by_id.get(int(p["product_id"])) if p.get("product_id") else None
        if not dbp:
            continue
        if not str(p.get("name") or "").strip():
            p["name"] = dbp.get("name")
        img = str(p.get("image_url") or "")
        if not img or img == "/static/cosmy25@2x.png":
            p["image_url"] = dbp.get("image_url") or img
        if p.get("price") is None and not p.get("new_price"):
            p["price"] = dbp.get("price")


async def resolve_api_products(
    payload: dict,
    *,
    load_by_ids: Callable[[list[int], dict[int, int]], Awaitable[tuple[list[dict], list[int]]]],
    price_drop: bool = False,
) -> tuple[list[dict] | None, str | None, list[int]]:
    products, err = resolve_products_from_payload(payload, price_drop=price_drop)
    if err or not products:
        return None, err, []
    await enrich_products_from_db(products, load_by_ids)
    pids = [int(p["product_id"]) for p in products if p.get("product_id")]
    return products, None, pids
