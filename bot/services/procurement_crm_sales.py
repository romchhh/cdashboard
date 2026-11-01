"""
Продажі для Procurement Copilot з CRM (лише успішні замовлення).

Замінює агрегати sales_qty_* / sales_avg_per_day_* у products.db після синхронізації
фіду товарів — щоб рекомендації до закупівлі не враховували скасування, відмови тощо.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Iterable

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.clients_engine import AsyncSessionLocalClients
from bot.database.clients_models import ClientOrder, ClientOrderLine
from bot.database.products_engine import AsyncProductsSessionLocal
from bot.database.products_models import Product, ProductOption
from config import CLIENTS_EXPORT_EXCLUDED_CUSTOMER_IDS
from mono.api import now_kyiv
from web.client_marketing import _clients_orders_exclude_clause, _sql_outcome_is_success

logger = logging.getLogger(__name__)

SALES_PERIODS: tuple[int, ...] = (1, 7, 30, 60, 90, 180, 360)


def _distribute_qty(total: int, weights: list[int]) -> list[int]:
    """Розподілити total між позиціями пропорційно weights (або порівну, якщо всі 0)."""
    n = len(weights)
    if n == 0:
        return []
    total = max(0, int(total or 0))
    if total == 0:
        return [0] * n
    wsum = sum(max(0, int(w or 0)) for w in weights)
    if wsum <= 0:
        base, rem = divmod(total, n)
        return [base + (1 if i < rem else 0) for i in range(n)]
    parts = [total * max(0, int(w or 0)) // wsum for w in weights]
    assigned = sum(parts)
    remainder = total - assigned
    order = sorted(range(n), key=lambda i: weights[i], reverse=True)
    for i in order[:remainder]:
        parts[i] += 1
    return parts


def _metrics_from_period_qty(period_qty: dict[int, int]) -> dict[str, int | float | None]:
    """Обчислити sales_qty_* та sales_avg_per_day_* з кількостей за періоди."""
    out: dict[str, int | float | None] = {}
    for days in SALES_PERIODS:
        qty = int(period_qty.get(days, 0) or 0)
        out[f"sales_qty_{days}d"] = qty
        out[f"sales_avg_per_day_{days}d"] = round(qty / days, 4) if qty > 0 else 0.0
    return out


async def _fetch_success_sales_by_cosmy_product_id(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> dict[int, dict[int, int]]:
    """
  Повертає {cosmy_product_id: {period_days: qty}} для успішних замовлень.
  Періоди: 1, 7, 30, 60, 90, 180, 360 днів від now (Київ).
    """
    ref = now or now_kyiv()
    since_by_period = {d: ref - timedelta(days=d) for d in SALES_PERIODS}
    succ = _sql_outcome_is_success()
    qty = func.coalesce(ClientOrderLine.quantity, 0)

    period_cols = {
        d: func.sum(case((ClientOrder.order_date >= since_by_period[d], qty), else_=0)).label(f"s{d}")
        for d in SALES_PERIODS
    }

    stmt = (
        select(ClientOrderLine.product_id, *period_cols.values())
        .join(ClientOrder, ClientOrder.id == ClientOrderLine.order_row_id)
        .where(
            ClientOrderLine.product_id.isnot(None),
            ClientOrder.order_date.isnot(None),
            succ,
        )
        .group_by(ClientOrderLine.product_id)
    )

    deny = _clients_orders_exclude_clause()
    if deny is not None:
        stmt = stmt.where(deny)
    if CLIENTS_EXPORT_EXCLUDED_CUSTOMER_IDS:
        stmt = stmt.where(
            ClientOrder.client_customer_id.notin_(CLIENTS_EXPORT_EXCLUDED_CUSTOMER_IDS)
        )

    rows = (await session.execute(stmt)).all()
    out: dict[int, dict[int, int]] = {}
    for row in rows:
        cosmy_pid = int(row[0])
        out[cosmy_pid] = {d: int(row[i + 1] or 0) for i, d in enumerate(SALES_PERIODS)}
    return out


async def _fetch_last_success_sale_dates(
    session: AsyncSession,
) -> dict[int, date]:
    """Остання дата успішного продажу по cosmy product_id."""
    succ = _sql_outcome_is_success()
    stmt = (
        select(
            ClientOrderLine.product_id,
            func.max(func.date(ClientOrder.order_date)).label("last_dt"),
        )
        .join(ClientOrder, ClientOrder.id == ClientOrderLine.order_row_id)
        .where(
            ClientOrderLine.product_id.isnot(None),
            ClientOrder.order_date.isnot(None),
            ClientOrderLine.quantity.isnot(None),
            ClientOrderLine.quantity > 0,
            succ,
        )
        .group_by(ClientOrderLine.product_id)
    )
    deny = _clients_orders_exclude_clause()
    if deny is not None:
        stmt = stmt.where(deny)
    if CLIENTS_EXPORT_EXCLUDED_CUSTOMER_IDS:
        stmt = stmt.where(
            ClientOrder.client_customer_id.notin_(CLIENTS_EXPORT_EXCLUDED_CUSTOMER_IDS)
        )

    rows = (await session.execute(stmt)).all()
    result: dict[int, date] = {}
    for cosmy_pid, last_dt in rows:
        if cosmy_pid is None or last_dt is None:
            continue
        if isinstance(last_dt, str):
            result[int(cosmy_pid)] = date.fromisoformat(last_dt[:10])
        else:
            result[int(cosmy_pid)] = last_dt
    return result


def _apply_metrics_to_item(item: Product | ProductOption, metrics: dict[str, int | float | None], last_sale: date | None) -> None:
    for key, value in metrics.items():
        setattr(item, key, value)
    if last_sale is not None:
        item.last_sale_date = last_sale.isoformat()
    elif hasattr(item, "last_sale_date"):
        item.last_sale_date = None


def _zero_metrics() -> dict[str, int | float | None]:
    return _metrics_from_period_qty({d: 0 for d in SALES_PERIODS})


async def get_crm_daily_sales_revenue_totals(
    cutoff: date,
    *,
    cosmy_product_ids: Iterable[int] | None = None,
) -> dict[date, dict[str, float | int]]:
    """
    Продажі (шт) і виручка (₴) по календарних днях order_date — лише успішні замовлення CRM.
    Виручка = сума quantity × price по рядках (факт з замовлення, не каталог).
    """
    ids_filter: set[int] | None = None
    if cosmy_product_ids is not None:
        ids_filter = {int(x) for x in cosmy_product_ids if x is not None}
        if not ids_filter:
            return {}

    qty = func.coalesce(ClientOrderLine.quantity, 0)
    line_revenue = qty * func.coalesce(ClientOrderLine.price, 0)
    day_col = func.date(ClientOrder.order_date).label("day")
    succ = _sql_outcome_is_success()

    stmt = (
        select(
            day_col,
            func.sum(qty).label("sales"),
            func.coalesce(func.sum(line_revenue), 0).label("revenue"),
        )
        .select_from(ClientOrderLine)
        .join(ClientOrder, ClientOrder.id == ClientOrderLine.order_row_id)
        .where(
            ClientOrder.order_date.isnot(None),
            func.date(ClientOrder.order_date) >= cutoff,
            succ,
        )
        .group_by(day_col)
        .order_by(day_col)
    )
    if ids_filter is not None:
        stmt = stmt.where(ClientOrderLine.product_id.in_(ids_filter))
    deny = _clients_orders_exclude_clause()
    if deny is not None:
        stmt = stmt.where(deny)
    if CLIENTS_EXPORT_EXCLUDED_CUSTOMER_IDS:
        stmt = stmt.where(
            ClientOrder.client_customer_id.notin_(CLIENTS_EXPORT_EXCLUDED_CUSTOMER_IDS)
        )

    async with AsyncSessionLocalClients() as session:
        rows = (await session.execute(stmt)).all()

    out: dict[date, dict[str, float | int]] = {}
    for row in rows:
        day = row.day
        if isinstance(day, str):
            day = date.fromisoformat(day[:10])
        out[day] = {
            "sales": int(row.sales or 0),
            "revenue": round(float(row.revenue or 0), 2),
        }
    return out


async def cosmy_product_ids_for_brand(brand_id: int) -> set[int]:
    """Cosmy product_id (рядки CRM) для внутрішнього brand_id у products.db."""
    async with AsyncProductsSessionLocal() as session:
        result = await session.execute(
            select(Product.product_id).where(Product.brand_id == brand_id)
        )
        return {int(pid) for pid in result.scalars().all() if pid is not None}


async def apply_success_crm_sales_to_products() -> dict[str, int]:
    """
    Перезаписує sales_qty_* / sales_avg_per_day_* у products.db з CRM (успішні замовлення).
    Викликати після sync_cosmy_products та sync_clients_from_feed.
    """
    now = now_kyiv()
    async with AsyncSessionLocalClients() as cs:
        sales_by_pid = await _fetch_success_sales_by_cosmy_product_id(cs, now=now)
        last_sale_by_pid = await _fetch_last_success_sale_dates(cs)

    products_updated = 0
    options_updated = 0
    zero_period = {d: 0 for d in SALES_PERIODS}

    async with AsyncProductsSessionLocal() as ps:
        products = (await ps.execute(select(Product))).scalars().all()
        options = (await ps.execute(select(ProductOption))).scalars().all()
        products_by_id = {p.id: p for p in products}
        options_by_product: dict[int, list[ProductOption]] = {}
        for opt in options:
            options_by_product.setdefault(opt.product_id, []).append(opt)

        handled_option_ids: set[int] = set()

        for product in products:
            cosmy_pid = int(product.product_id)
            period_qty = sales_by_pid.get(cosmy_pid, zero_period)
            last_sale = last_sale_by_pid.get(cosmy_pid)
            product_opts = options_by_product.get(product.id, [])

            if product.has_options and product_opts:
                feed_weights = [int(o.sales_qty_30d or 0) for o in product_opts]
                per_option_period = [
                    {d: 0 for d in SALES_PERIODS} for _ in product_opts
                ]
                for days in SALES_PERIODS:
                    distributed = _distribute_qty(
                        int(period_qty.get(days, 0) or 0), feed_weights
                    )
                    for i, qty in enumerate(distributed):
                        per_option_period[i][days] = qty
                for opt, opt_period_qty in zip(product_opts, per_option_period):
                    _apply_metrics_to_item(
                        opt,
                        _metrics_from_period_qty(opt_period_qty),
                        last_sale,
                    )
                    handled_option_ids.add(opt.id)
                    options_updated += 1
                _apply_metrics_to_item(
                    product, _metrics_from_period_qty(zero_period), None
                )
            else:
                _apply_metrics_to_item(
                    product,
                    _metrics_from_period_qty(period_qty),
                    last_sale,
                )
            products_updated += 1

        for opt in options:
            if opt.id in handled_option_ids:
                continue
            parent = products_by_id.get(opt.product_id)
            cosmy_pid = int(parent.product_id) if parent else None
            if cosmy_pid is None:
                _apply_metrics_to_item(opt, _zero_metrics(), None)
            else:
                period_qty = sales_by_pid.get(cosmy_pid, zero_period)
                _apply_metrics_to_item(
                    opt,
                    _metrics_from_period_qty(period_qty),
                    last_sale_by_pid.get(cosmy_pid),
                )
            options_updated += 1

        await ps.commit()

    logger.info(
        "✅ CRM-продажі (успішні): %d cosmy product_id, оновлено %d товарів / %d варіацій",
        len(sales_by_pid),
        products_updated,
        options_updated,
    )
    return {
        "crm_product_ids": len(sales_by_pid),
        "products_updated": products_updated,
        "options_updated": options_updated,
    }
