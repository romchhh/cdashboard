# Генерація звітів

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from collections import defaultdict

from bot.database.models import Transaction, Category, FOP, TransactionType, TransactionStatus, TransactionSource

# Категорії, які не рахуються як витрати (переміщення коштів)
# Враховуємо обидва варіанти написання «Зняття Готівки» на всяк випадок
CASH_MOVEMENT_CATEGORY_NAMES = ("Готівка", "Зняття Готівки", "Зняття готівки")


def _source_filter_for_fops(
    fop_ids: list[int],
    *,
    include_cash: bool,
):
    """Фільтр джерел: [] — лише готівка; інакше обрані ФОПи; include_cash додає готівку до ФОПів."""
    if len(fop_ids) == 0:
        return Transaction.source == TransactionSource.CASH
    if include_cash:
        return or_(
            Transaction.fop_id.in_(fop_ids),
            Transaction.source == TransactionSource.CASH,
        )
    return Transaction.fop_id.in_(fop_ids)


def _append_fop_scope_filter(
    conditions: list,
    *,
    fop_ids: list[int] | None,
    include_cash: bool = True,
    fop_filter_values: list[str] | None = None,
) -> None:
    if fop_filter_values is not None:
        from bot.services.fop_filter import transaction_fop_scope_condition

        scope = transaction_fop_scope_condition(fop_filter_values)
        if scope is not None:
            conditions.append(scope)
    elif fop_ids is not None:
        conditions.append(_source_filter_for_fops(fop_ids, include_cash=include_cash))


def _append_flow_filter(conditions: list, flow: str) -> None:
    """Обмежити звіт лише доходами або витратами (flow: all | income | expense)."""
    if flow == "income":
        conditions.append(Transaction.transaction_type == TransactionType.INCOME)
    elif flow == "expense":
        conditions.append(Transaction.transaction_type == TransactionType.EXPENSE)


async def get_cashflow(
    session: AsyncSession,
    date_from: datetime,
    date_to: datetime,
    fop_ids: list[int] | None = None,
    include_cash: bool = True,
    only_confirmed: bool = True,
    flow: str = "all",
    fop_filter_values: list[str] | None = None,
) -> dict:
    """Cashflow за період. fop_ids=None — усі джерела. fop_ids=[] — лише готівка. Інакше — обрані ФОПи; include_cash додає готівку до ФОПів."""
    conditions = [
        Transaction.date >= date_from,
        Transaction.date <= date_to,
    ]
    if only_confirmed:
        conditions.append(Transaction.status == TransactionStatus.CONFIRMED)
    _append_fop_scope_filter(
        conditions,
        fop_ids=fop_ids,
        include_cash=include_cash,
        fop_filter_values=fop_filter_values,
    )
    _append_flow_filter(conditions, flow)
    result = await session.execute(
        select(Transaction, Category)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(and_(*conditions))
    )
    rows = result.all()

    income = 0
    expense = 0
    daily = defaultdict(lambda: {"income": 0, "expense": 0})
    
    for tx, cat in rows:
        # Пропускаємо переміщення коштів (категорія "Готівка" або "Зняття Готівки")
        if cat and cat.name in CASH_MOVEMENT_CATEGORY_NAMES:
            continue
            
        if tx.transaction_type == TransactionType.INCOME:
            income += tx.amount
            daily[tx.date.strftime("%Y-%m-%d")]["income"] += tx.amount
        else:
            expense += tx.amount
            daily[tx.date.strftime("%Y-%m-%d")]["expense"] += tx.amount

    net = income - expense

    return {
        "income": income,
        "expense": expense,
        "net": net,
        "daily": dict(daily),
    }


async def get_daily_totals(
    session: AsyncSession,
    date_from: datetime,
    date_to: datetime,
    fop_ids: list[int] | None = None,
    include_cash: bool = True,
    only_confirmed: bool = True,
    flow: str = "all",
    fop_filter_values: list[str] | None = None,
) -> list[dict]:
    """Щоденні підсумки для графіка. fop_ids=None — усі джерела; інакше як у get_cashflow."""
    conditions = [
        Transaction.date >= date_from,
        Transaction.date <= date_to,
    ]
    if only_confirmed:
        conditions.append(Transaction.status == TransactionStatus.CONFIRMED)
    _append_fop_scope_filter(
        conditions,
        fop_ids=fop_ids,
        include_cash=include_cash,
        fop_filter_values=fop_filter_values,
    )
    _append_flow_filter(conditions, flow)
    result = await session.execute(
        select(Transaction, Category)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(and_(*conditions))
    )
    rows = result.all()

    daily = defaultdict(lambda: {"income": 0.0, "expense": 0.0})
    for tx, cat in rows:
        # Пропускаємо переміщення коштів ("Готівка", "Зняття Готівки") та переведення коштів (категорія "Богдан")
        if cat and (cat.name in CASH_MOVEMENT_CATEGORY_NAMES or cat.name == "Богдан"):
            continue
            
        day = tx.date.strftime("%Y-%m-%d")
        if tx.transaction_type == TransactionType.INCOME:
            daily[day]["income"] += tx.amount
        else:
            daily[day]["expense"] += abs(float(tx.amount))

    out = []
    # Нормалізуємо дати до початку дня для коректного порівняння
    date_from_normalized = date_from.replace(hour=0, minute=0, second=0, microsecond=0)
    # Для date_to беремо дату і додаємо 1 день, щоб включити весь останній день
    date_to_date_only = date_to.replace(hour=0, minute=0, second=0, microsecond=0)
    date_to_normalized = date_to_date_only + timedelta(days=1)
    
    current = date_from_normalized
    while current < date_to_normalized:
        d_str = current.strftime("%Y-%m-%d")
        inc = daily[d_str]["income"]
        exp = daily[d_str]["expense"]
        out.append({
            "date": d_str,
            "label": current.strftime("%d.%m"),
            "income": float(round(inc, 2)),
            "expense": float(round(exp, 2)),
        })
        current += timedelta(days=1)
    return out


async def get_pl(
    session: AsyncSession,
    date_from: datetime,
    date_to: datetime,
    fop_ids: list[int] | None = None,
    category_id: int | None = None,
    include_cash: bool = True,
    only_confirmed: bool = True,
    flow: str = "all",
    fop_filter_values: list[str] | None = None,
) -> dict:
    """P&L за період. fop_ids=None — усі джерела; fop_ids=[] — лише готівка; інакше ФОПи та опційно готівка (include_cash).
    fop_filter_values — сирий список з UI (cash, id, id:novapay); має пріоритет над fop_ids."""
    conditions = [
        Transaction.date >= date_from,
        Transaction.date <= date_to,
    ]
    if only_confirmed:
        conditions.append(Transaction.status == TransactionStatus.CONFIRMED)
    _append_fop_scope_filter(
        conditions,
        fop_ids=fop_ids,
        include_cash=include_cash,
        fop_filter_values=fop_filter_values,
    )
    if category_id:
        conditions.append(Transaction.category_id == category_id)
    _append_flow_filter(conditions, flow)
    result = await session.execute(
        select(Transaction, Category, FOP)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .outerjoin(FOP, Transaction.fop_id == FOP.id)
        .where(and_(*conditions))
    )
    rows = result.all()

    # Структура: {fop_id: {"name": "", "total": 0.0, "sources": {"novapay": 0.0, "monobank": 0.0}}}
    income_by_fop = defaultdict(lambda: {"name": "", "total": 0.0, "sources": defaultdict(float)})
    expense_by_category = defaultdict(float)
    expense_cat_detail: dict[int, dict[str, Any]] = {}
    total_income = 0
    total_expense = 0
    cash_expense_total = 0  # Окремі витрати з готівки

    for tx, cat, fop in rows:
        # Пропускаємо переміщення та «Богдан», якщо не фільтруємо явно по категорії
        if not category_id and cat and (
            cat.name in CASH_MOVEMENT_CATEGORY_NAMES or cat.name == "Богдан"
        ):
            continue
            
        if tx.transaction_type == TransactionType.INCOME:
            total_income += tx.amount
            if tx.source == TransactionSource.CASH:
                # Використовуємо -1 як спеціальний fop_id для готівки
                income_by_fop[-1]["name"] = "Готівка"
                income_by_fop[-1]["total"] += tx.amount
                income_by_fop[-1]["sources"]["cash"] += tx.amount
            elif fop:
                income_by_fop[fop.id]["name"] = fop.name
                income_by_fop[fop.id]["total"] += tx.amount
                # Визначаємо джерело транзакції
                if tx.source == TransactionSource.NOVAPAY:
                    income_by_fop[fop.id]["sources"]["novapay"] += tx.amount
                elif tx.source == TransactionSource.MONOBANK:
                    income_by_fop[fop.id]["sources"]["monobank"] += tx.amount
        else:
            total_expense += tx.amount
            # Витрати з готівки та категорії "Богдан" не додаємо до категорій витрат
            if tx.source == TransactionSource.CASH or (cat and cat.name == "Богдан"):
                # Витрати з готівки та "Богдан" рахуємо окремо
                if tx.source == TransactionSource.CASH:
                    cash_expense_total += tx.amount
            else:
                # Тільки витрати НЕ з готівки та НЕ "Богдан" додаємо до категорій
                if cat:
                    expense_by_category[cat.name] += tx.amount
                    bucket = expense_cat_detail.setdefault(
                        cat.id, {"id": cat.id, "name": cat.name, "amount": 0.0}
                    )
                    bucket["amount"] += tx.amount
                else:
                    expense_by_category["Без категорії"] += tx.amount
                    bucket = expense_cat_detail.setdefault(
                        0, {"id": 0, "name": "Без категорії", "amount": 0.0}
                    )
                    bucket["amount"] += tx.amount

    profit = total_income - total_expense
    margin = (profit / total_income * 100) if total_income > 0 else 0

    # Спочатку формуємо список окремих ФОПів
    fop_items = []
    for fid, data in income_by_fop.items():
        if data["total"] > 0:
            # Конвертуємо defaultdict в звичайний dict для sources
            sources_dict = dict(data["sources"])
            fop_items.append({
                "fop_id": fid,
                "name": data["name"],
                "total": data["total"],
                "sources": sources_dict,
            })
    
    # Список ФОПів за прізвищем, відсортований за сумою
    from bot.services.fop_filter import fop_surname

    grouped: dict[str, dict[str, Any]] = {}
    cash_item = None
    for fop_item in fop_items:
        if fop_item.get("fop_id") == -1:
            cash_item = fop_item
            continue
        surname = fop_surname(fop_item["name"])
        bucket = grouped.setdefault(
            surname,
            {"fop_id": fop_item["fop_id"], "name": surname, "total": 0.0, "sources": defaultdict(float)},
        )
        bucket["total"] += fop_item["total"]
        for src, amt in (fop_item.get("sources") or {}).items():
            bucket["sources"][src] += amt

    merged_items = list(grouped.values())
    if cash_item:
        merged_items.append(cash_item)
    merged_items.sort(key=lambda x: x["total"], reverse=True)

    income_by_fop_list = []
    for fop_item in merged_items:
        sources_dict = dict(fop_item.get("sources") or {})
        income_by_fop_list.append({
            "fop_id": fop_item["fop_id"],
            "name": fop_item["name"],
            "total": fop_item["total"],
            "sources": sources_dict,
            "novapay": float(sources_dict.get("novapay") or 0),
            "monobank": float(sources_dict.get("monobank") or 0),
            "cash": float(sources_dict.get("cash") or 0),
        })

    # Окремо рахуємо зняття готівки та категорію "Богдан" для окремого відображення
    cash_withdrawal_total = 0.0
    bogdan_total = 0.0
    
    # Перераховуємо для окремих категорій
    # Зняття готівки може бути з карток (MONOBANK/NOVAPAY) або з готівки (CASH)
    for tx, cat, fop in rows:
        if tx.transaction_type == TransactionType.EXPENSE:
            # Для перенесення коштів у готівку враховуємо лише категорію «Готівка».
            # «Зняття готівки» (та варіанти написання) не збільшують касу, але й надалі
            # не враховуються у витратах через CASH_MOVEMENT_CATEGORY_NAMES вище.
            if cat and cat.name == "Готівка":
                cash_withdrawal_total += tx.amount
            elif cat and cat.name == "Богдан":
                bogdan_total += tx.amount
    
    expense_categories = sorted(
        expense_cat_detail.values(),
        key=lambda x: x["amount"],
        reverse=True,
    )

    return {
        "income": total_income,
        "income_by_fop": income_by_fop_list,
        "expense": total_expense,
        "expense_by_category": dict(expense_by_category),
        "expense_categories": expense_categories,
        "cash_expense_total": cash_expense_total,  # Окремі витрати з готівки (не переміщення)
        "cash_withdrawal_total": cash_withdrawal_total,  # Зняття готівки (переміщення коштів)
        "bogdan_total": bogdan_total,  # Категорія "Богдан" (переведення коштів)
        "profit": profit,
        "margin": margin,
    }


async def get_breakdown(
    session: AsyncSession,
    date_from: datetime,
    date_to: datetime,
    fop_ids: list[int] | None = None,
    include_cash: bool = True,
    only_confirmed: bool = True,
    flow: str = "all",
    fop_filter_values: list[str] | None = None,
) -> dict:
    """Розбивка витрат по категоріях — узгоджена з get_pl (той самий total_expense)."""
    breakdown_flow = flow if flow in ("all", "expense") else "expense"
    pl = await get_pl(
        session,
        date_from,
        date_to,
        fop_ids,
        include_cash=include_cash,
        only_confirmed=only_confirmed,
        flow=breakdown_flow,
        fop_filter_values=fop_filter_values,
    )
    categories = [
        {
            "id": int(item["id"]),
            "name": str(item["name"]),
            "amount": float(item["amount"]),
        }
        for item in pl.get("expense_categories") or []
    ]
    cash_exp = float(pl.get("cash_expense_total") or 0)
    if cash_exp > 0:
        categories.append({"id": 0, "name": "Готівка (витрати)", "amount": cash_exp})

    total_expense = float(pl["expense"])
    for cat in categories:
        cat["percentage"] = (cat["amount"] / total_expense * 100) if total_expense > 0 else 0

    return {
        "total_expense": total_expense,
        "categories": categories,
    }


async def compare_periods(
    session: AsyncSession,
    period1_from: datetime,
    period1_to: datetime,
    period2_from: datetime,
    period2_to: datetime,
    fop_ids: list[int] | None = None,
    include_cash: bool = True,
    flow: str = "all",
    fop_filter_values: list[str] | None = None,
) -> dict:
    """Порівняння двох періодів. Фільтр джерел як у get_pl."""
    p1 = await get_pl(
        session,
        period1_from,
        period1_to,
        fop_ids,
        include_cash=include_cash,
        flow=flow,
        fop_filter_values=fop_filter_values,
    )
    p2 = await get_pl(
        session,
        period2_from,
        period2_to,
        fop_ids,
        include_cash=include_cash,
        flow=flow,
        fop_filter_values=fop_filter_values,
    )

    def calc_change(old, new):
        if old == 0:
            return 0
        return ((new - old) / old) * 100

    return {
        "period1": p1,
        "period2": p2,
        "income_change": calc_change(p2["income"], p1["income"]),
        "expense_change": calc_change(p2["expense"], p1["expense"]),
        "profit_change": calc_change(p2["profit"], p1["profit"]),
    }
