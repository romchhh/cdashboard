# Обробники для перегляду та підтвердження транзакцій

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from datetime import datetime, timedelta
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.engine import AsyncSessionLocal
from bot.database.models import Transaction, TransactionStatus, Category, FOP, TransactionType, TransactionSource
from bot.services.classifier import TransactionClassifier
from bot.services.fop_filter import (
    decode_fop_filter_token,
    load_fop_filter_options,
    transaction_fop_scope_condition,
)
from bot.keyboards.inline import (
    get_fop_selector,
    get_back_to_tx_keyboard,
    get_transactions_pagination_keyboard,
)
from bot.services.balance_sync import format_balance_display, format_amount_signed

router = Router()

TX_CTX = "tx_context"
TX_FOP_IDS = "tx_selected_fop_ids"
TX_ALL_SELECTED = "tx_all_selected"  # True = галочки скрізь, False = тільки по selected
TX_REPORT_PERIOD = "tx_report_period"  # today | week | month — для пагінації
TX_REPORT_FOP_IDS = "tx_report_fop_ids"
PER_PAGE = 10


async def format_transaction(tx: Transaction, session: AsyncSession) -> str:
    """Форматування транзакції для виводу"""
    # Категорія
    cat_name = "—"
    if tx.category_id:
        cat_result = await session.execute(select(Category).where(Category.id == tx.category_id))
        cat = cat_result.scalar_one_or_none()
        if cat:
            cat_name = cat.name

    # ФОП
    fop_name = "—"
    if tx.fop_id:
        fop_result = await session.execute(select(FOP).where(FOP.id == tx.fop_id))
        fop = fop_result.scalar_one_or_none()
        if fop:
            fop_name = fop.name

    # Знак суми
    sign = "+" if tx.transaction_type == TransactionType.INCOME else "-"
    
    # Статус
    status_icons = {
        TransactionStatus.CONFIRMED: "✅",
        TransactionStatus.PENDING: "⏳",
        TransactionStatus.ERROR: "❌",
        TransactionStatus.WAITING: "⏸",
    }
    status_icon = status_icons.get(tx.status, "")

    text = (
        f"{status_icon} <b>{sign}{format_balance_display(float(tx.amount))} грн</b> | {tx.date.strftime('%d.%m.%Y %H:%M')}\n"
        f"ФОП: {fop_name} | {tx.source.value.capitalize()}\n"
        f"Категорія: {cat_name}\n"
    )

    if tx.counterparty:
        text += f"Контрагент: {tx.counterparty}\n"
    if tx.description:
        desc = tx.description[:80] + "..." if len(tx.description) > 80 else tx.description
        text += f"Коментар: {desc}\n"

    return text


# --- Інлайн: вибір періоду → FOP selector ---

@router.callback_query(F.data.startswith("tx_") & ~F.data.contains("_fop_"))
async def cb_tx_choice(callback: CallbackQuery, state: FSMContext):
    """Обрано період (today, week, month, pending) — показуємо вибір ФОПів"""
    prefix = callback.data
    await state.update_data(**{TX_CTX: prefix, TX_FOP_IDS: [], TX_ALL_SELECTED: True})

    async with AsyncSessionLocal() as session:
        filter_options = await load_fop_filter_options(session)

    if not filter_options:
        await callback.answer("Немає ФОПів у БД", show_alert=True)
        return

    title, keyboard = get_fop_selector(filter_options, [], prefix, title="Оберіть ФОПи для перегляду транзакцій:", all_selected=True)
    await callback.message.edit_text(f"💳 <b>Транзакції</b>\n\n{title}", reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("tx_") & F.data.contains("_fop_"))
async def cb_tx_fop(callback: CallbackQuery, state: FSMContext):
    """Вибір ФОПів для транзакцій: all / toggle / apply"""
    parts = callback.data.split("_")
    prefix = f"{parts[0]}_{parts[1]}"  # tx_today, tx_week, ...
    action = parts[3]  # all, t, apply

    data = await state.get_data()
    selected = list(data.get(TX_FOP_IDS) or [])
    all_sel = data.get(TX_ALL_SELECTED, True)

    if action == "all":
        all_sel = not all_sel
        selected = []
    elif action == "t" and len(parts) > 4:
        all_sel = False
        token = parts[4]
        async with AsyncSessionLocal() as session:
            filter_options = await load_fop_filter_options(session)
        if token.isdigit():
            idx = int(token)
            if idx < 0 or idx >= len(filter_options):
                await callback.answer()
                return
            value = filter_options[idx]["value"]
        else:
            value = decode_fop_filter_token(token)
        if value in selected:
            selected.remove(value)
        else:
            selected.append(value)
    elif action == "apply":
        fop_filter_values = None if (all_sel or not selected) else selected
        period = parts[1]
        if period == "pending":
            await state.clear()
            await _show_pending(callback.message, fop_filter_values)
        else:
            await state.update_data(**{TX_REPORT_PERIOD: period, TX_REPORT_FOP_IDS: fop_filter_values})
            date_from, date_to, title = _period_to_dates_and_title(period)
            await _show_transactions(callback.message, date_from, date_to, title, fop_filter_values, page=0)
        await callback.answer("✅ Готово")
        return

    await state.update_data(**{TX_FOP_IDS: selected, TX_ALL_SELECTED: all_sel})

    async with AsyncSessionLocal() as session:
        filter_options = await load_fop_filter_options(session)
    title, keyboard = get_fop_selector(filter_options, selected, prefix, title="Оберіть ФОПи для перегляду транзакцій:", all_selected=all_sel)
    await callback.message.edit_text(f"💳 <b>Транзакції</b>\n\n{title}", reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("txscroll_"))
async def cb_tx_scroll(callback: CallbackQuery, state: FSMContext):
    """Прокрутка сторінок списку транзакцій (◀️ Назад / ▶️ Далі)."""
    if callback.data == "txscroll_noop":
        await callback.answer()
        return
    try:
        page = int(callback.data.replace("txscroll_", ""))
    except ValueError:
        await callback.answer()
        return
    data = await state.get_data()
    period = data.get(TX_REPORT_PERIOD) or "month"
    fop_filter_values = data.get(TX_REPORT_FOP_IDS)
    date_from, date_to, title = _period_to_dates_and_title(period)
    async with AsyncSessionLocal() as session:
        text, keyboard = await _build_transactions_page(session, date_from, date_to, title, fop_filter_values, page)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


def _period_to_dates_and_title(period: str) -> tuple[datetime, datetime, str]:
    """Повертає (date_from, date_to, title) для періоду today / yesterday / week / month."""
    now = datetime.now()
    if period == "today":
        date_from = now.replace(hour=0, minute=0, second=0, microsecond=0)
        date_to = now
        title = "СЬОГОДНІ"
    elif period == "yesterday":
        yesterday = now - timedelta(days=1)
        date_from = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
        date_to = yesterday.replace(hour=23, minute=59, second=59, microsecond=999999)
        title = "ВЧОРА"
    elif period == "week":
        date_to = now
        date_from = now - timedelta(days=7)
        title = "ЗА ТИЖДЕНЬ"
    else:
        date_to = now
        date_from = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        title = "ЗА МІСЯЦЬ"
    return date_from, date_to, title


async def _build_transactions_page(
    session: AsyncSession,
    date_from: datetime,
    date_to: datetime,
    title: str,
    fop_filter_values: list[str] | None,
    page: int,
) -> tuple[str, InlineKeyboardMarkup]:
    """Збирає текст і клавіатуру для однієї сторінки списку транзакцій (пагінація)."""
    conditions = [
        Transaction.date >= date_from,
        Transaction.date <= date_to,
    ]
    fop_scope = transaction_fop_scope_condition(fop_filter_values)
    if fop_scope is not None:
        conditions.append(fop_scope)
    result = await session.execute(
        select(Transaction)
        .where(and_(*conditions))
        .order_by(Transaction.date.desc())
        .limit(50)
    )
    transactions = result.scalars().all()
    total = len(transactions)
    income = sum(tx.amount for tx in transactions if tx.transaction_type == TransactionType.INCOME)
    expense = sum(tx.amount for tx in transactions if tx.transaction_type == TransactionType.EXPENSE)
    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * PER_PAGE
    chunk = transactions[start : start + PER_PAGE]

    text = f"💳 <b>{title}</b>\n\n"
    text += f"Доходи: <b>+{format_balance_display(float(income))} грн</b>\n"
    text += f"Витрати: <b>-{format_balance_display(float(expense))} грн</b>\n"
    text += f"Прибуток: <b>{format_amount_signed(income - expense)} грн</b>\n"
    text += f"\nТранзакцій: {total}\n"
    text += "━━━━━━━━━━━━━━━━━━━━\n\n"
    for tx in chunk:
        text += await format_transaction(tx, session)
        text += "\n"
    if total > PER_PAGE:
        text += f"\n📄 Сторінка {page + 1} з {total_pages}"
    keyboard = get_transactions_pagination_keyboard(page, total_pages)
    return text, keyboard


async def _show_transactions(
    message_or_callback_message,
    date_from: datetime,
    date_to: datetime,
    title: str,
    fop_filter_values: list[str] | None,
    page: int = 0,
):
    """Показати транзакції за період з фільтром по ФОПах (з пагінацією)."""
    async with AsyncSessionLocal() as session:
        conditions = [
            Transaction.date >= date_from,
            Transaction.date <= date_to,
        ]
        fop_scope = transaction_fop_scope_condition(fop_filter_values)
        if fop_scope is not None:
            conditions.append(fop_scope)
        result = await session.execute(
            select(Transaction)
            .where(and_(*conditions))
            .order_by(Transaction.date.desc())
            .limit(50)
        )
        transactions = result.scalars().all()

        if not transactions:
            await message_or_callback_message.answer(
                f"📭 Немає транзакцій за цей період",
                parse_mode="HTML",
                reply_markup=get_back_to_tx_keyboard(),
            )
            return

        text, keyboard = await _build_transactions_page(session, date_from, date_to, title, fop_filter_values, page)
        await message_or_callback_message.answer(
            text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )


async def _show_pending(message_or_callback_message, fop_filter_values: list[str] | None):
    """Показати непідтверджені транзакції з фільтром по ФОПах."""
    async with AsyncSessionLocal() as session:
        conditions = [Transaction.status == TransactionStatus.PENDING]
        fop_scope = transaction_fop_scope_condition(fop_filter_values)
        if fop_scope is not None:
            conditions.append(fop_scope)
        result = await session.execute(
            select(Transaction)
            .where(and_(*conditions))
            .order_by(Transaction.date.desc())
            .limit(20)
        )
        transactions = result.scalars().all()

        if not transactions:
            await message_or_callback_message.answer(
                "✅ Всі транзакції підтверджено!",
                parse_mode="HTML",
                reply_markup=get_back_to_tx_keyboard(),
            )
            return

        await message_or_callback_message.answer(
            f"⏳ <b>ПОТРЕБУЮТЬ ПІДТВЕРДЖЕННЯ</b>\n\nВсього: {len(transactions)}",
            parse_mode="HTML",
            reply_markup=get_back_to_tx_keyboard(),
        )
        for tx in transactions:
            await send_transaction_for_confirmation(message_or_callback_message, tx, session)


@router.message(Command("today"))
async def cmd_today(message: Message, state: FSMContext):
    """Транзакції за сьогодні — показуємо FOP selector"""
    await state.update_data(**{TX_CTX: "tx_today", TX_FOP_IDS: [], TX_ALL_SELECTED: True})
    async with AsyncSessionLocal() as session:
        filter_options = await load_fop_filter_options(session)
    if not filter_options:
        await message.answer("Немає ФОПів у БД", parse_mode="HTML")
        return
    title, keyboard = get_fop_selector(filter_options, [], "tx_today", title="Оберіть ФОПи для перегляду транзакцій:", all_selected=True)
    await message.answer(f"💳 <b>Транзакції — сьогодні</b>\n\n{title}", reply_markup=keyboard, parse_mode="HTML")


@router.message(Command("week"))
async def cmd_week(message: Message, state: FSMContext):
    await state.update_data(**{TX_CTX: "tx_week", TX_FOP_IDS: [], TX_ALL_SELECTED: True})
    async with AsyncSessionLocal() as session:
        filter_options = await load_fop_filter_options(session)
    if not filter_options:
        await message.answer("Немає ФОПів у БД", parse_mode="HTML")
        return
    title, keyboard = get_fop_selector(filter_options, [], "tx_week", title="Оберіть ФОПи для перегляду транзакцій:", all_selected=True)
    await message.answer(f"💳 <b>Транзакції — тиждень</b>\n\n{title}", reply_markup=keyboard, parse_mode="HTML")


@router.message(Command("month"))
async def cmd_month(message: Message, state: FSMContext):
    await state.update_data(**{TX_CTX: "tx_month", TX_FOP_IDS: [], TX_ALL_SELECTED: True})
    async with AsyncSessionLocal() as session:
        filter_options = await load_fop_filter_options(session)
    if not filter_options:
        await message.answer("Немає ФОПів у БД", parse_mode="HTML")
        return
    title, keyboard = get_fop_selector(filter_options, [], "tx_month", title="Оберіть ФОПи для перегляду транзакцій:", all_selected=True)
    await message.answer(f"💳 <b>Транзакції — місяць</b>\n\n{title}", reply_markup=keyboard, parse_mode="HTML")


@router.message(Command("pending"))
async def cmd_pending(message: Message, state: FSMContext):
    await state.update_data(**{TX_CTX: "tx_pending", TX_FOP_IDS: [], TX_ALL_SELECTED: True})
    async with AsyncSessionLocal() as session:
        filter_options = await load_fop_filter_options(session)
    if not filter_options:
        await message.answer("Немає ФОПів у БД", parse_mode="HTML")
        return
    title, keyboard = get_fop_selector(filter_options, [], "tx_pending", title="Оберіть ФОПи для перегляду непідтверджених:", all_selected=True)
    await message.answer(f"💳 <b>Непідтверджені транзакції</b>\n\n{title}", reply_markup=keyboard, parse_mode="HTML")


async def show_transactions(message: Message, days: int, title: str, fop_filter_values: list[str] | None = None):
    """Показати транзакції за період (для зворотної сумісності). days: 0=сьогодні, 1=вчора, 7=тиждень, 30=місяць."""
    period = {0: "today", 1: "yesterday", 7: "week", 30: "month"}.get(days, "month")
    date_from, date_to, title = _period_to_dates_and_title(period)
    await _show_transactions(message, date_from, date_to, title, fop_filter_values, page=0)


async def send_transaction_for_confirmation(message: Message, tx: Transaction, session: AsyncSession):
    """Відправити транзакцію для підтвердження з inline-кнопками"""
    text = await format_transaction(tx, session)
    text += "\n<b>Оберіть категорію:</b>"

    # Отримати категорії
    result = await session.execute(
        select(Category)
        .where(Category.is_expense == (tx.transaction_type == TransactionType.EXPENSE))
        .order_by(Category.name)
    )
    categories = result.scalars().all()

    # Створити inline-кнопки (по 2 в ряду)
    buttons = []
    row = []
    for cat in categories:
        row.append(InlineKeyboardButton(text=cat.name, callback_data=f"confirm_{tx.id}_{cat.id}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    # Кнопки дій
    buttons.append([
        InlineKeyboardButton(text="❌ Помилка", callback_data=f"error_{tx.id}"),
        InlineKeyboardButton(text="💬 Коментар", callback_data=f"comment_{tx.id}")
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("confirm_"))
async def callback_confirm_transaction(callback: CallbackQuery):
    """Підтвердження категорії транзакції"""
    _, tx_id, cat_id = callback.data.split("_")
    tx_id = int(tx_id)
    cat_id = int(cat_id)

    async with AsyncSessionLocal() as session:
        # Отримати транзакцію
        result = await session.execute(select(Transaction).where(Transaction.id == tx_id))
        tx = result.scalar_one_or_none()

        if not tx:
            await callback.answer("❌ Транзакцію не знайдено", show_alert=True)
            return

        # Отримати категорію
        cat_result = await session.execute(select(Category).where(Category.id == cat_id))
        cat = cat_result.scalar_one_or_none()
        cat_name = cat.name if cat else "—"
        
        # Оновити транзакцію
        tx.category_id = cat_id
        tx.status = TransactionStatus.CONFIRMED
        tx.confirmed_by_user = callback.from_user.id
        tx.confirmed_at = datetime.now()

        # Якщо вибрана категорія «Готівка» і це витрата по картці, створюємо
        # ОДНУ дзеркальну транзакцію в касі (source=CASH, INCOME), але лише якщо
        # такої ще немає.
        if cat and cat.name == "Готівка" and tx.transaction_type == TransactionType.EXPENSE and tx.source != TransactionSource.CASH:
            existing_cash = await session.execute(
                select(Transaction).where(
                    Transaction.source == TransactionSource.CASH,
                    Transaction.transaction_type == TransactionType.INCOME,
                    Transaction.category_id == cat.id,
                    Transaction.amount == abs(float(tx.amount)),
                    Transaction.date == tx.date,
                )
            )
            if not existing_cash.scalar_one_or_none():
                cash_tx = Transaction(
                    amount=abs(float(tx.amount)),
                    transaction_type=TransactionType.INCOME,
                    date=tx.date,
                    source=TransactionSource.CASH,
                    fop_id=None,
                    category_id=cat.id,
                    status=TransactionStatus.CONFIRMED,
                    description=(tx.description or "") + " (переміщення в готівку)",
                )
                session.add(cash_tx)

        # Навчити класифікатор
        classifier = TransactionClassifier(session)
        await classifier.learn_from_confirmation(tx, cat_id, callback.from_user.id)

        await session.commit()

        await callback.message.edit_text(
            f"✅ <b>Підтверджено</b>\n\n{await format_transaction(tx, session)}\n"
            f"Категорія: <b>{cat_name}</b>",
            parse_mode="HTML"
        )
        await callback.answer("✅ Транзакцію підтверджено")


@router.callback_query(F.data.startswith("error_"))
async def callback_error_transaction(callback: CallbackQuery):
    """Позначити транзакцію як помилку"""
    _, tx_id = callback.data.split("_")
    tx_id = int(tx_id)

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Transaction).where(Transaction.id == tx_id))
        tx = result.scalar_one_or_none()

        if not tx:
            await callback.answer("❌ Транзакцію не знайдено", show_alert=True)
            return

        tx.status = TransactionStatus.ERROR
        await session.commit()

        await callback.message.edit_text(
            f"❌ <b>Позначено як помилка</b>\n\n{await format_transaction(tx, session)}",
            parse_mode="HTML"
        )
        await callback.answer("❌ Позначено як помилку")
