"""
Сервіс для збору історії продажів продуктів.
Записує щоденні зрізи даних для подальшого аналізу та прогнозування.
"""

import logging
from collections import defaultdict
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import and_, func, select

from bot.database.products_engine import AsyncProductsSessionLocal
from bot.database.products_models import (
    Product,
    ProductOption,
    ProductSalesHistory,
)

logger = logging.getLogger(__name__)


def _frozen_value_expr():
    return (
        func.coalesce(ProductSalesHistory.stock_qty, 0)
        * func.coalesce(ProductSalesHistory.purchase_price, 0)
    )


async def _aggregate_history_rows(
    session,
    cutoff: date,
    *,
    brand_id: int | None = None,
) -> list[dict]:
    """
    Агрегує історію по датах без подвійного обліку SKU.

    Товари без опцій — лише product-level записи (product_option_id IS NULL).
    Товари з опціями — лише option-level записи (has_options=1).
    """
    frozen_expr = _frozen_value_expr()
    metrics = (
        func.coalesce(func.sum(ProductSalesHistory.sales_qty), 0).label("sales"),
        func.coalesce(func.sum(ProductSalesHistory.stock_qty), 0).label("stock"),
        func.coalesce(func.sum(ProductSalesHistory.revenue), 0).label("revenue"),
        func.coalesce(func.sum(frozen_expr), 0).label("frozen"),
    )

    no_opt_filters = [
        ProductSalesHistory.date >= cutoff,
        ProductSalesHistory.product_option_id.is_(None),
        Product.has_options.is_(False),
    ]
    if brand_id is not None:
        no_opt_filters.append(Product.brand_id == brand_id)

    no_opt_query = (
        select(ProductSalesHistory.date, *metrics)
        .select_from(ProductSalesHistory)
        .join(Product, Product.id == ProductSalesHistory.product_id)
        .where(and_(*no_opt_filters))
        .group_by(ProductSalesHistory.date)
    )

    opt_filters = [
        ProductSalesHistory.date >= cutoff,
        ProductSalesHistory.product_option_id.isnot(None),
        Product.has_options.is_(True),
    ]
    if brand_id is not None:
        opt_filters.append(Product.brand_id == brand_id)

    opt_query = (
        select(ProductSalesHistory.date, *metrics)
        .select_from(ProductSalesHistory)
        .join(ProductOption, ProductOption.id == ProductSalesHistory.product_option_id)
        .join(Product, Product.id == ProductOption.product_id)
        .where(and_(*opt_filters))
        .group_by(ProductSalesHistory.date)
    )

    by_date: dict[date, dict[str, float | int]] = defaultdict(
        lambda: {"sales": 0, "stock": 0, "revenue": 0.0, "frozen": 0.0}
    )

    for query in (no_opt_query, opt_query):
        result = await session.execute(query)
        for row in result.all():
            bucket = by_date[row.date]
            bucket["sales"] += int(row.sales or 0)
            bucket["stock"] += int(row.stock or 0)
            bucket["revenue"] += float(row.revenue or 0)
            bucket["frozen"] += float(row.frozen or 0)

    return [
        {
            "date": day,
            "sales": int(vals["sales"]),
            "stock": int(vals["stock"]),
            "revenue": float(vals["revenue"]),
            "frozen": float(vals["frozen"]),
        }
        for day, vals in sorted(by_date.items(), key=lambda x: x[0])
    ]


def resolve_history_granularity(history_days: int, granularity: str | None) -> str:
    """day | week; auto → тиждень для довгих періодів (менш «рваний» графік)."""
    g = (granularity or "auto").strip().lower()
    if g in ("day", "week"):
        return g
    return "week" if history_days >= 180 else "day"


def aggregate_history_granularity(
    rows: list[dict],
    granularity: str,
) -> list[dict]:
    """
    Агрегація історії для графіків.
    week: продажі/виручка — сума за ISO-тиждень (пн–нд), залишок — на останній день тижня в даних.
    """
    if granularity != "week" or not rows:
        return rows

    from collections import defaultdict

    buckets: dict[date, list[dict]] = defaultdict(list)
    for row in rows:
        d = row["date"]
        if isinstance(d, str):
            d = date.fromisoformat(d[:10])
        week_start = d - timedelta(days=d.weekday())
        buckets[week_start].append({**row, "date": d})

    merged: list[dict] = []
    for week_start in sorted(buckets):
        items = sorted(buckets[week_start], key=lambda x: x["date"])
        week_end = week_start + timedelta(days=6)
        snap_items = [x for x in items if x.get("has_warehouse_snapshot")]
        last = snap_items[-1] if snap_items else items[-1]
        label = (
            f"{week_start.strftime('%d.%m')}–{week_end.strftime('%d.%m')}"
            if week_start.month == week_end.month
            else f"{week_start.strftime('%d.%m')}–{week_end.strftime('%d.%m.%y')}"
        )
        merged.append(
            {
                "date": week_start,
                "date_label": label,
                "sales": int(sum(int(x.get("sales") or 0) for x in items)),
                "revenue": round(sum(float(x.get("revenue") or 0) for x in items), 2),
                "stock": int(last.get("stock") or 0),
                "frozen": round(float(last.get("frozen") or 0), 2),
            }
        )
    return merged


def _merge_crm_daily_sales_into_history(
    history_rows: list[dict],
    crm_by_day: dict[date, dict[str, float | int]],
) -> list[dict]:
    """
    Залишок/заморожено — щоденний зріз складу; продажі та виручка — CRM по order_date.
    Дні лише з CRM (без зрізу фіда) не обнуляють залишок: carry-forward останнього зрізу.
    """
    by_date: dict[date, dict] = {}
    for row in history_rows:
        d = row["date"]
        if isinstance(d, str):
            d = date.fromisoformat(d[:10])
        by_date[d] = {
            **row,
            "date": d,
            "has_warehouse_snapshot": True,
        }

    all_dates = sorted(set(by_date) | set(crm_by_day))
    merged: list[dict] = []
    last_stock = 0
    last_frozen = 0.0
    for day in all_dates:
        snap = by_date.get(day)
        if snap is not None:
            last_stock = int(snap.get("stock") or 0)
            last_frozen = float(snap.get("frozen") or 0)
            row = {
                "date": day,
                "sales": int(snap.get("sales") or 0),
                "stock": last_stock,
                "revenue": float(snap.get("revenue") or 0),
                "frozen": last_frozen,
                "has_warehouse_snapshot": True,
            }
        else:
            row = {
                "date": day,
                "sales": 0,
                "stock": last_stock,
                "revenue": 0.0,
                "frozen": last_frozen,
                "has_warehouse_snapshot": False,
            }
        crm = crm_by_day.get(day)
        if crm:
            row["sales"] = int(crm["sales"])
            row["revenue"] = float(crm["revenue"])
        merged.append(row)
    return merged


def _future_has_inventory_snapshot(rows: list[dict], start_idx: int, window: int = 21) -> bool:
    """Чи є пізніший зріз складу з ненульовим залишком/заморожено (ознака «битого» дня 0/0)."""
    for j in range(start_idx + 1, min(len(rows), start_idx + 1 + window)):
        row = rows[j]
        if not row.get("has_warehouse_snapshot"):
            continue
        if int(row.get("stock") or 0) > 0 or float(row.get("frozen") or 0) > 0:
            return True
    return False


def _impute_spurious_zero_snapshots(rows: list[dict]) -> list[dict]:
    """
    Дні, коли зріз у БД дав 0 шт і 0 ₴, але до/після були нормальні залишки —
    типово пропущений або неповний sync фіда, не реальний порожній склад.
    """
    if not rows:
        return rows
    out: list[dict] = []
    prev_stock = 0
    prev_frozen = 0.0
    for i, row in enumerate(rows):
        r = dict(row)
        if r.get("has_warehouse_snapshot"):
            stock = int(r.get("stock") or 0)
            frozen = float(r.get("frozen") or 0)
            if (
                stock == 0
                and frozen == 0
                and (prev_stock > 0 or prev_frozen > 0)
                and _future_has_inventory_snapshot(rows, i)
            ):
                r["stock"] = prev_stock
                r["frozen"] = prev_frozen
            else:
                prev_stock = stock
                prev_frozen = frozen
        else:
            r["stock"] = prev_stock
            r["frozen"] = prev_frozen
        out.append(r)
    return out


def _densify_daily_history(rows: list[dict]) -> list[dict]:
    """Усі календарні дні між першою і останньою датою (залишок/заморожено — carry-forward)."""
    if not rows:
        return rows
    ordered = sorted(rows, key=lambda x: x["date"])
    start = ordered[0]["date"]
    end = ordered[-1]["date"]
    if isinstance(start, str):
        start = date.fromisoformat(start[:10])
    if isinstance(end, str):
        end = date.fromisoformat(end[:10])
    by_date: dict[date, dict] = {}
    for row in ordered:
        d = row["date"]
        if isinstance(d, str):
            d = date.fromisoformat(d[:10])
        by_date[d] = {**row, "date": d}

    dense: list[dict] = []
    d = start
    last: dict | None = None
    while d <= end:
        if d in by_date:
            last = dict(by_date[d])
            dense.append(last)
        elif last is not None:
            dense.append(
                {
                    "date": d,
                    "sales": 0,
                    "revenue": 0.0,
                    "stock": int(last.get("stock") or 0),
                    "frozen": float(last.get("frozen") or 0),
                    "has_warehouse_snapshot": False,
                }
            )
        d += timedelta(days=1)
    return dense


def _finalize_warehouse_history_series(rows: list[dict]) -> list[dict]:
    rows = _impute_spurious_zero_snapshots(rows)
    return _densify_daily_history(rows)


async def get_brand_sales_history(brand_id: int, days: int = 90) -> list[dict]:
    """
    Історія по бренду: залишок/заморожено з product_sales_history;
    продажі та виручка — успішні замовлення CRM по даті order_date.
    """
    from bot.services.procurement_crm_sales import (
        cosmy_product_ids_for_brand,
        get_crm_daily_sales_revenue_totals,
    )

    cutoff = date.today() - timedelta(days=days)
    async with AsyncProductsSessionLocal() as session:
        rows = await _aggregate_history_rows(session, cutoff, brand_id=brand_id)
    cosmy_ids = await cosmy_product_ids_for_brand(brand_id)
    crm_by_day = await get_crm_daily_sales_revenue_totals(
        cutoff, cosmy_product_ids=cosmy_ids
    )
    merged = _finalize_warehouse_history_series(
        _merge_crm_daily_sales_into_history(rows, crm_by_day)
    )
    return [
        {
            "date": h["date"],
            "sales": h["sales"],
            "stock": h["stock"],
            "revenue": h["revenue"],
            "frozen": h.get("frozen", 0.0),
        }
        for h in merged
    ]


async def get_warehouse_sales_history(days: int = 90) -> list[dict]:
    """
    Історія по складу: залишок/заморожено з щоденного зрізу (фід);
    продажі (шт) і виручка (₴) — успішні замовлення CRM по календарній даті.
    """
    from bot.services.procurement_crm_sales import get_crm_daily_sales_revenue_totals

    cutoff = date.today() - timedelta(days=days)
    async with AsyncProductsSessionLocal() as session:
        rows = await _aggregate_history_rows(session, cutoff)
    crm_by_day = await get_crm_daily_sales_revenue_totals(cutoff)
    return _finalize_warehouse_history_series(
        _merge_crm_daily_sales_into_history(rows, crm_by_day)
    )


async def collect_daily_sales_snapshot() -> None:
    """
    Збирає щоденний зріз продажів для всіх товарів.
    Викликається щоденно (наприклад, після синхронізації фіда о 01:35).

    Товари з опціями (has_options=1) пишуться лише на рівні варіацій —
    інакше залишок/продажі дублюються на графіках динаміки.
    """
    logger.info("📊 Початок збору щоденного зрізу продажів...")
    today = date.today()

    async with AsyncProductsSessionLocal() as session:
        products_result = await session.execute(
            select(Product).where(
                Product.product_data_mode != "aggregate_by_options",
                Product.has_options.is_(False),
            )
        )
        products = products_result.scalars().all()

        records_added = 0

        for product in products:
            existing = await session.execute(
                select(ProductSalesHistory).where(
                    and_(
                        ProductSalesHistory.product_id == product.id,
                        ProductSalesHistory.date == today,
                        ProductSalesHistory.product_option_id.is_(None),
                    )
                )
            )
            if existing.scalar_one_or_none():
                continue

            sales_qty = product.sales_qty_1d or 0
            revenue = None
            price = product.selling_price_catalog or product.selling_price_order
            if sales_qty and price:
                revenue = sales_qty * price

            history_record = ProductSalesHistory(
                product_id=product.id,
                product_option_id=None,
                date=today,
                stock_qty=product.stock_qty,
                sales_qty=sales_qty,
                revenue=revenue,
                purchase_price=product.purchase_price,
                selling_price=price,
            )
            session.add(history_record)
            records_added += 1

        options_result = await session.execute(
            select(ProductOption).join(Product, Product.id == ProductOption.product_id)
        )
        options = options_result.scalars().all()

        for option in options:
            existing = await session.execute(
                select(ProductSalesHistory).where(
                    and_(
                        ProductSalesHistory.product_option_id == option.id,
                        ProductSalesHistory.date == today,
                    )
                )
            )
            if existing.scalar_one_or_none():
                continue

            sales_qty = option.sales_qty_1d or 0
            revenue = None
            price = option.selling_price_catalog or option.selling_price_order
            if sales_qty and price:
                revenue = sales_qty * price

            history_record = ProductSalesHistory(
                product_id=option.product_id,
                product_option_id=option.id,
                date=today,
                stock_qty=option.stock_qty,
                sales_qty=sales_qty,
                revenue=revenue,
                purchase_price=option.purchase_price,
                selling_price=price,
            )
            session.add(history_record)
            records_added += 1

        await session.commit()
        logger.info("✅ Збір щоденного зрізу завершено: додано %d записів", records_added)


async def get_sales_history(
    product_id: int,
    product_option_id: Optional[int] = None,
    days: int = 30,
) -> list[ProductSalesHistory]:
    """
    Отримує історію продажів для конкретного товару/варіації за вказаний період.
    """
    async with AsyncProductsSessionLocal() as session:
        if product_option_id:
            query = select(ProductSalesHistory).where(
                ProductSalesHistory.product_option_id == product_option_id
            )
        else:
            query = select(ProductSalesHistory).where(
                and_(
                    ProductSalesHistory.product_id == product_id,
                    ProductSalesHistory.product_option_id.is_(None),
                )
            )

        query = query.order_by(ProductSalesHistory.date.desc()).limit(days)
        result = await session.execute(query)
        return result.scalars().all()
