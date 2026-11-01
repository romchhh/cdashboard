"""
Найгірший строк придатності: адмін вказує дату та кількість одиниць із критичним строком.
Решта залишку не відстежується. При зменшенні залишку з фіду (продаж) лічильник
найгіршого строку зменшується разом із залишком (припущення FEFO).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional
from xml.etree import ElementTree as ET

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.products_models import Product, ProductExpiryLot, ProductExpiryProfile, ProductOption, ProductAnalytics


@dataclass
class LotDraft:
    expiry_date: date | None
    quantity: int


def _lot_sort_worst_first(expiry_date: date | None) -> tuple:
    """Для списання: спочатку найгірші (найраніші) дати; без дати — в кінці."""
    if expiry_date is None:
        return (1, date.max.toordinal())
    return (0, expiry_date.toordinal())


def worst_lot_from_feed_element(elem: Optional[ET.Element], stock_qty: int | None) -> list[LotDraft]:
    """З фіду — лише найгірший строк: найраніша дата та кількість одиниць з нею."""
    from bot.services.products_sync import _dates_with_gaps

    if stock_qty is None or stock_qty <= 0:
        return []

    gaps = _dates_with_gaps(elem)[:stock_qty]
    dated = [d for d in gaps if d is not None]
    if not dated:
        return []

    earliest = min(dated)
    qty = sum(1 for d in gaps if d == earliest)
    return [LotDraft(expiry_date=earliest, quantity=qty)]


def normalize_worst_lot_drafts(drafts: list[LotDraft]) -> list[LotDraft]:
    """Залишити один запис — найгірший (найраніший) строк і його кількість."""
    positive = [d for d in drafts if d.quantity > 0]
    if not positive:
        return []

    dated = [d for d in positive if d.expiry_date is not None]
    if not dated:
        return [LotDraft(expiry_date=None, quantity=sum(d.quantity for d in positive))]

    earliest = min(d.expiry_date for d in dated)
    qty = sum(d.quantity for d in dated if d.expiry_date == earliest)
    return [LotDraft(expiry_date=earliest, quantity=qty)]


def earliest_expiry_from_drafts(drafts: list[LotDraft]) -> date | None:
    dated = [d.expiry_date for d in drafts if d.expiry_date is not None and d.quantity > 0]
    return min(dated) if dated else None


def validate_lot_drafts(drafts: list[LotDraft], stock_qty: int) -> str | None:
    if stock_qty < 0:
        return "Некоректний залишок на складі."
    worst = normalize_worst_lot_drafts(drafts)
    if not worst:
        return None
    if len(worst) > 1:
        return "Дозволено лише один найгірший строк."
    w = worst[0]
    if w.quantity < 0:
        return "Кількість не може бути від'ємною."
    if w.quantity > stock_qty:
        return (
            f"Кількість із найгіршим строком ({w.quantity} шт) "
            f"не може перевищувати залишок ({stock_qty} шт)."
        )
    if w.quantity > 0 and w.expiry_date is None:
        return "Вкажіть дату найгіршого строку придатності."
    return None


def reconcile_lot_drafts(
    drafts: list[LotDraft],
    old_stock: int,
    new_stock: int,
) -> list[LotDraft]:
    """
    Коригує лічильник найгіршого строку при зміні залишку з фіду.
    Продаж (−залишок) зменшує і залишок, і кількість із найгіршим строком (FEFO).
    Надходження (+залишок) не збільшує лічильник найгіршого строку.
    """
    if new_stock == old_stock:
        return normalize_worst_lot_drafts(drafts)

    worst = normalize_worst_lot_drafts(drafts)
    if not worst:
        return []

    w = worst[0]
    if new_stock < old_stock:
        sold = old_stock - new_stock
        new_qty = max(0, w.quantity - sold)
        if new_qty <= 0:
            return []
        return [LotDraft(expiry_date=w.expiry_date, quantity=new_qty)]

    return [LotDraft(expiry_date=w.expiry_date, quantity=w.quantity)]


async def _get_or_create_profile(
    session: AsyncSession,
    cosmy_product_id: int,
    product_option_value_id: int | None,
) -> ProductExpiryProfile:
    stmt = select(ProductExpiryProfile).where(
        ProductExpiryProfile.cosmy_product_id == cosmy_product_id,
        ProductExpiryProfile.product_option_value_id == product_option_value_id
        if product_option_value_id is not None
        else ProductExpiryProfile.product_option_value_id.is_(None),
    )
    profile = (await session.execute(stmt)).scalar_one_or_none()
    if profile:
        return profile
    profile = ProductExpiryProfile(
        cosmy_product_id=cosmy_product_id,
        product_option_value_id=product_option_value_id,
        use_manual_lots=False,
        last_known_stock=None,
    )
    session.add(profile)
    await session.flush()
    return profile


async def load_lot_drafts(
    session: AsyncSession,
    cosmy_product_id: int,
    product_option_value_id: int | None,
) -> list[LotDraft]:
    stmt = (
        select(ProductExpiryLot)
        .where(
            ProductExpiryLot.cosmy_product_id == cosmy_product_id,
            ProductExpiryLot.product_option_value_id == product_option_value_id
            if product_option_value_id is not None
            else ProductExpiryLot.product_option_value_id.is_(None),
        )
    )
    rows = list((await session.execute(stmt)).scalars().all())
    rows.sort(key=lambda r: _lot_sort_worst_first(r.expiry_date))
    drafts = [LotDraft(expiry_date=r.expiry_date, quantity=int(r.quantity or 0)) for r in rows]
    return normalize_worst_lot_drafts(drafts)


async def _replace_lots(
    session: AsyncSession,
    cosmy_product_id: int,
    product_option_value_id: int | None,
    drafts: list[LotDraft],
) -> None:
    await session.execute(
        delete(ProductExpiryLot).where(
            ProductExpiryLot.cosmy_product_id == cosmy_product_id,
            ProductExpiryLot.product_option_value_id == product_option_value_id
            if product_option_value_id is not None
            else ProductExpiryLot.product_option_value_id.is_(None),
        )
    )
    now = datetime.utcnow()
    for d in drafts:
        if d.quantity <= 0:
            continue
        session.add(
            ProductExpiryLot(
                cosmy_product_id=cosmy_product_id,
                product_option_value_id=product_option_value_id,
                expiry_date=d.expiry_date,
                quantity=d.quantity,
                updated_at=now,
            )
        )


async def apply_effective_expiry_to_db_item(
    session: AsyncSession,
    *,
    cosmy_product_id: int,
    product_option_value_id: int | None,
    drafts: list[LotDraft],
) -> None:
    """Оновити date_expired на Product / ProductOption і days_until_expiry в аналітиці."""
    eff = earliest_expiry_from_drafts(drafts)
    days_until_expiry = (eff - date.today()).days if eff else None

    product_db_id: int | None = None
    option_db_id: int | None = None
    stock_qty = 0

    if product_option_value_id is not None:
        res = await session.execute(
            select(ProductOption)
            .join(Product, Product.id == ProductOption.product_id)
            .where(
                Product.product_id == cosmy_product_id,
                ProductOption.product_option_value_id == product_option_value_id,
            )
        )
        opt = res.scalar_one_or_none()
        if opt:
            opt.date_expired = eff
            option_db_id = opt.id
            product_db_id = opt.product_id
            stock_qty = int(opt.stock_qty or 0)
    else:
        res = await session.execute(select(Product).where(Product.product_id == cosmy_product_id))
        prod = res.scalar_one_or_none()
        if prod:
            prod.date_expired = eff
            product_db_id = prod.id
            stock_qty = int(prod.stock_qty or 0)

    if option_db_id is not None:
        analytics_res = await session.execute(
            select(ProductAnalytics).where(ProductAnalytics.product_option_id == option_db_id)
        )
    elif product_db_id is not None:
        analytics_res = await session.execute(
            select(ProductAnalytics).where(
                ProductAnalytics.product_id == product_db_id,
                ProductAnalytics.product_option_id.is_(None),
            )
        )
    else:
        return

    analytics = analytics_res.scalar_one_or_none()
    if not analytics:
        return

    analytics.days_until_expiry = days_until_expiry
    if days_until_expiry is not None and days_until_expiry <= 180 and stock_qty > 0:
        analytics.procurement_action = "sell_off"


async def sync_lots_after_feed_import(
    session: AsyncSession,
    *,
    cosmy_product_id: int,
    product_option_value_id: int | None,
    stock_qty: int | None,
    feed_date_elem: Optional[ET.Element],
) -> None:
    """Після імпорту рядка з фіду: ініціалізація або FEFO-корекція кількості."""
    stock = max(0, int(stock_qty or 0))
    profile = await _get_or_create_profile(session, cosmy_product_id, product_option_value_id)
    feed_drafts = worst_lot_from_feed_element(feed_date_elem, stock)

    if not profile.use_manual_lots:
        drafts = feed_drafts
        profile.last_known_stock = stock
        await _replace_lots(session, cosmy_product_id, product_option_value_id, drafts)
        await apply_effective_expiry_to_db_item(
            session,
            cosmy_product_id=cosmy_product_id,
            product_option_value_id=product_option_value_id,
            drafts=drafts,
        )
        return

    current = await load_lot_drafts(session, cosmy_product_id, product_option_value_id)
    old_stock = profile.last_known_stock if profile.last_known_stock is not None else sum(d.quantity for d in current)
    if not current and feed_drafts:
        current = feed_drafts
    drafts = reconcile_lot_drafts(current, old_stock, stock)
    profile.last_known_stock = stock
    await _replace_lots(session, cosmy_product_id, product_option_value_id, drafts)
    await apply_effective_expiry_to_db_item(
        session,
        cosmy_product_id=cosmy_product_id,
        product_option_value_id=product_option_value_id,
        drafts=drafts,
    )


async def save_manual_lots(
    session: AsyncSession,
    *,
    cosmy_product_id: int,
    product_option_value_id: int | None,
    stock_qty: int,
    drafts: list[LotDraft],
) -> str | None:
    """Зберегти ручний розподіл. Повертає текст помилки або None."""
    err = validate_lot_drafts(drafts, stock_qty)
    if err:
        return err
    normalized = normalize_worst_lot_drafts(
        [LotDraft(expiry_date=d.expiry_date, quantity=d.quantity) for d in drafts if d.quantity > 0]
    )

    profile = await _get_or_create_profile(session, cosmy_product_id, product_option_value_id)
    profile.use_manual_lots = True
    profile.last_known_stock = stock_qty
    await _replace_lots(session, cosmy_product_id, product_option_value_id, normalized)
    await apply_effective_expiry_to_db_item(
        session,
        cosmy_product_id=cosmy_product_id,
        product_option_value_id=product_option_value_id,
        drafts=normalized,
    )
    return None


ExpiryRowKey = tuple[int, int | None]


async def load_expiry_contexts_batch(
    session: AsyncSession,
    rows: list[tuple[int, int | None, int, date | None]],
) -> dict[ExpiryRowKey, dict[str, Any]]:
    """
    Завантажити expiry_ui для списку рядків (cosmy_product_id, pov_id, stock, feed_date).
    Один batch-запит замість N окремих.
    """
    if not rows:
        return {}

    keys: set[ExpiryRowKey] = {(cid, pov) for cid, pov, _, _ in rows}
    cosmy_ids = {k[0] for k in keys}

    lot_rows = list(
        (
            await session.execute(
                select(ProductExpiryLot).where(ProductExpiryLot.cosmy_product_id.in_(cosmy_ids))
            )
        ).scalars().all()
    )
    profile_rows = list(
        (
            await session.execute(
                select(ProductExpiryProfile).where(
                    ProductExpiryProfile.cosmy_product_id.in_(cosmy_ids)
                )
            )
        ).scalars().all()
    )

    lots_by_key: dict[ExpiryRowKey, list[LotDraft]] = {}
    for lot in lot_rows:
        key: ExpiryRowKey = (lot.cosmy_product_id, lot.product_option_value_id)
        if key not in keys:
            continue
        lots_by_key.setdefault(key, []).append(
            LotDraft(expiry_date=lot.expiry_date, quantity=int(lot.quantity or 0))
        )

    profile_by_key = {
        (p.cosmy_product_id, p.product_option_value_id): p for p in profile_rows
    }

    result: dict[ExpiryRowKey, dict[str, Any]] = {}
    for cosmy_id, pov_id, stock_qty, feed_single_date in rows:
        key = (cosmy_id, pov_id)
        drafts = normalize_worst_lot_drafts(lots_by_key.get(key, []))
        profile = profile_by_key.get(key)
        result[key] = expiry_context_for_ui(
            drafts,
            stock_qty,
            use_manual=bool(profile and profile.use_manual_lots),
            feed_single_date=feed_single_date,
        )
    return result


def expiry_context_for_ui(
    drafts: list[LotDraft],
    stock_qty: int,
    *,
    use_manual: bool,
    feed_single_date: date | None,
) -> dict[str, Any]:
    worst = normalize_worst_lot_drafts(drafts)
    worst_qty = worst[0].quantity if worst else 0
    worst_date = worst[0].expiry_date if worst else None
    other_qty = max(0, stock_qty - worst_qty)
    rows = []
    if worst and worst_qty > 0:
        d = worst[0]
        rows.append(
            {
                "expiry_date": d.expiry_date,
                "expiry_date_str": d.expiry_date.isoformat() if d.expiry_date else "",
                "quantity": d.quantity,
                "display_date": d.expiry_date.strftime("%d.%m.%Y") if d.expiry_date else "Без дати",
            }
        )
    return {
        "lots": rows,
        "worst_qty": worst_qty,
        "worst_date": worst_date,
        "other_qty": other_qty,
        "assigned_qty": worst_qty,
        "unassigned_qty": other_qty,
        "stock_qty": stock_qty,
        "use_manual_lots": use_manual,
        "feed_single_date": feed_single_date,
        "earliest_expiry": worst_date,
        "can_assign_more": worst_qty < stock_qty,
    }
