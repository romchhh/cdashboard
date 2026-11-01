# Обробники готівки: внесення в касу та винесення з каси

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from mono.api import now_kyiv
from bot.database.engine import AsyncSessionLocal
from bot.database.models import (
    Transaction,
    TransactionType,
    TransactionSource,
    TransactionStatus,
    Category,
)
from bot.keyboards.inline import get_cash_menu, get_back_to_cash_keyboard
from bot.services.balance_sync import format_balance_display, format_amount_signed

router = Router()


class CashInStates(StatesGroup):
    amount = State()
    description = State()


class CashOutStates(StatesGroup):
    amount = State()
    category_id = State()
    description = State()


def _parse_amount(text: str) -> float | None:
    """Парсинг суми з тексту (дозволяє кому та пробіли)."""
    try:
        cleaned = text.replace(",", ".").replace(" ", "").strip()
        if not cleaned:
            return None
        value = float(cleaned)
        return value if value > 0 else None
    except ValueError:
        return None


from bot.services.category_lookup import get_sales_category_id


# --- Текст меню готівки (баланс + останні операції) ---

LAST_IN_COUNT = 5
LAST_OUT_COUNT = 5


async def _get_last_cash_transactions(
    session: AsyncSession,
    limit_in: int = LAST_IN_COUNT,
    limit_out: int = LAST_OUT_COUNT,
) -> tuple[list, list]:
    """Повертає (список останніх внесень, список останніх винесень). Кожен елемент: {amount, date, description, category_name (тільки для out)}."""
    income_result = await session.execute(
        select(Transaction.amount, Transaction.date, Transaction.description)
        .where(
            and_(
                Transaction.source == TransactionSource.CASH,
                Transaction.transaction_type == TransactionType.INCOME,
            )
        )
        .order_by(Transaction.date.desc())
        .limit(limit_in)
    )
    ins = [
        {"amount": r[0], "date": r[1], "description": r[2]}
        for r in income_result.all()
    ]
    expense_result = await session.execute(
        select(Transaction.amount, Transaction.date, Transaction.description, Category.name)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(
            and_(
                Transaction.source == TransactionSource.CASH,
                Transaction.transaction_type == TransactionType.EXPENSE,
            )
        )
        .order_by(Transaction.date.desc())
        .limit(limit_out)
    )
    outs = [
        {"amount": r[0], "date": r[1], "description": r[2], "category_name": r[3] or "—"}
        for r in expense_result.all()
    ]
    return ins, outs


async def get_cash_menu_text(session: AsyncSession) -> str:
    """Збирає текст для екрану «Готівка / Каса»: баланс, останні внесення та винесення з коментарями, кінцевий баланс."""
    summary = await _get_cash_summary(session)
    ins, outs = await _get_last_cash_transactions(session)
    balance = summary["balance"]
    lines = [
        "💵 <b>ГОТІВКА / КАСА</b>\n",
        f"💰 <b>Баланс готівки:</b> <b>{format_amount_signed(balance)} грн</b>\n",
    ]
    if ins:
        lines.append("➕ <b>Останні внесення:</b>")
        for t in ins:
            dt = t["date"].strftime("%d.%m.%Y %H:%M")
            lines.append(f"• +{format_balance_display(float(t['amount']))} грн | {dt}")
            if t.get("description"):
                lines.append(f"  <i>{t['description']}</i>")
        lines.append("")
    if outs:
        lines.append("➖ <b>Останні винесення:</b>")
        for t in outs:
            dt = t["date"].strftime("%d.%m.%Y %H:%M")
            lines.append(f"• -{format_balance_display(float(t['amount']))} грн · {t['category_name']} | {dt}")
            if t.get("description"):
                lines.append(f"  <i>{t['description']}</i>")
        lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"💰 <b>Кінцевий баланс:</b> <b>{format_amount_signed(balance)} грн</b>\n")
    lines.append("Оберіть дію:")
    return "\n".join(lines)


# --- Перегляд готівки (внесення, винесення, баланс) ---

async def _get_cash_summary(session: AsyncSession) -> dict:
    """Повертає total_in, total_out, balance по транзакціях з source=CASH."""
    income_row = await session.execute(
        select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            and_(
                Transaction.source == TransactionSource.CASH,
                Transaction.transaction_type == TransactionType.INCOME,
            )
        )
    )
    expense_row = await session.execute(
        select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            and_(
                Transaction.source == TransactionSource.CASH,
                Transaction.transaction_type == TransactionType.EXPENSE,
            )
        )
    )
    total_in = float(income_row.scalar() or 0)
    total_out = float(expense_row.scalar() or 0)
    balance = total_in - total_out
    return {"total_in": total_in, "total_out": total_out, "balance": balance}


@router.callback_query(F.data == "cash_view")
async def cb_cash_view(callback: CallbackQuery):
    """Показати підсумок готівки: внесення, винесення, загальний баланс."""
    async with AsyncSessionLocal() as session:
        summary = await _get_cash_summary(session)
    text = (
        "💵 <b>ПЕРЕГЛЯД ГОТІВКИ</b>\n\n"
        f"➕ Внесення в касу: <b>+{format_balance_display(summary['total_in'])} грн</b>\n"
        f"➖ Винесення з каси: <b>-{format_balance_display(summary['total_out'])} грн</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Баланс готівки:</b> <b>{format_amount_signed(summary['balance'])} грн</b>"
    )
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=get_back_to_cash_keyboard(),
    )
    await callback.answer()


# --- Меню готівки (callback «Назад») ---

@router.callback_query(F.data == "menu_cash")
async def cb_menu_cash(callback: CallbackQuery, state: FSMContext):
    """Повернутися в меню готівки — показуємо баланс та останні операції."""
    await state.clear()
    async with AsyncSessionLocal() as session:
        text = await get_cash_menu_text(session)
    await callback.message.edit_text(
        text,
        reply_markup=get_cash_menu(),
        parse_mode="HTML",
    )
    await callback.answer()


# --- Внесення в касу ---

@router.callback_query(F.data == "cash_in")
async def cb_cash_in(callback: CallbackQuery, state: FSMContext):
    """Початок внесення в касу: запит суми."""
    await state.clear()
    await state.set_state(CashInStates.amount)
    await callback.message.edit_text(
        "➕ <b>Внесення в касу</b>\n\n"
        "Завжди враховується як <b>оплата за товар</b> (категорія «Продаж»).\n\n"
        "Введіть суму в гривнях:",
        parse_mode="HTML",
        reply_markup=get_back_to_cash_keyboard(),
    )
    await callback.answer()


@router.message(CashInStates.amount, F.text)
async def cash_in_amount(message: Message, state: FSMContext):
    """Отримано суму внесення — запитуємо коментар або пропуск."""
    amount = _parse_amount(message.text)
    if amount is None:
        await message.answer("❌ Введіть коректну суму числом (наприклад: 1500 або 1 500,50)")
        return
    await state.update_data(cash_in_amount=amount)
    await state.set_state(CashInStates.description)
    await message.answer(
        "📝 Введіть коментар (або натисніть /skip щоб пропустити):",
        reply_markup=get_back_to_cash_keyboard(),
    )


@router.message(CashInStates.description, F.text)
async def cash_in_description(message: Message, state: FSMContext):
    """Отримано коментар або /skip — зберігаємо транзакцію внесення."""
    description = None if message.text and message.text.strip().lower() == "/skip" else (message.text or "").strip() or None
    data = await state.get_data()
    amount = data.get("cash_in_amount")
    if amount is None:
        await state.clear()
        await message.answer("❌ Сесію скинуто. Почніть знову з меню «💵 Готівка».", reply_markup=get_cash_menu())
        return

    async with AsyncSessionLocal() as session:
        sales_cat_id = await get_sales_category_id(session)
        if not sales_cat_id:
            await message.answer(
                "❌ Категорію «Продаж» не знайдено в БД. Додайте категорії через налаштування.",
                reply_markup=get_back_to_cash_keyboard(),
            )
            await state.clear()
            return

        tx = Transaction(
            amount=amount,
            transaction_type=TransactionType.INCOME,
            date=now_kyiv(),
            source=TransactionSource.CASH,
            fop_id=None,
            category_id=sales_cat_id,
            status=TransactionStatus.CONFIRMED,
            description=description,
            confirmed_by_user=message.from_user.id,
            confirmed_at=now_kyiv(),
        )
        session.add(tx)
        await session.commit()

    await state.clear()
    await message.answer(
        f"✅ <b>Внесення в касу збережено</b>\n\n"
        f"+{format_balance_display(amount)} грн · Оплата за товар (Продаж)\n"
        f"{'Коментар: ' + description if description else ''}",
        parse_mode="HTML",
        reply_markup=get_back_to_cash_keyboard(),
    )


# --- Винесення з каси ---

@router.callback_query(F.data == "cash_out")
async def cb_cash_out(callback: CallbackQuery, state: FSMContext):
    """Початок винесення з каси: запит суми."""
    await state.clear()
    await state.set_state(CashOutStates.amount)
    await callback.message.edit_text(
        "➖ <b>Винесення з каси</b>\n\n"
        "Введіть суму в гривнях:",
        parse_mode="HTML",
        reply_markup=get_back_to_cash_keyboard(),
    )
    await callback.answer()


@router.message(CashOutStates.amount, F.text)
async def cash_out_amount(message: Message, state: FSMContext):
    """Отримано суму винесення — показуємо вибір категорії."""
    amount = _parse_amount(message.text)
    if amount is None:
        await message.answer("❌ Введіть коректну суму числом (наприклад: 500 або 500,50)")
        return
    await state.update_data(cash_out_amount=amount)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Category)
            .where(Category.is_expense == True)
            .order_by(Category.name)
        )
        categories = result.scalars().all()

    if not categories:
        await message.answer(
            "❌ Немає категорій витрат у БД. Додайте категорії через налаштування.",
            reply_markup=get_back_to_cash_keyboard(),
        )
        await state.clear()
        return

    buttons = []
    row = []
    for cat in categories:
        row.append(InlineKeyboardButton(text=cat.name, callback_data=f"cash_cat_{cat.id}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_cash")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await state.set_state(CashOutStates.category_id)
    await message.answer(
        f"➖ Сума: <b>{format_balance_display(amount)} грн</b>\n\nОберіть категорію витрат:",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.message(CashOutStates.category_id, F.text)
async def cash_out_category_ignore_text(message: Message):
    """У стані вибору категорії — нагадати обрати кнопкою."""
    await message.answer("Оберіть категорію витрат кнопками вище ⬆️")


@router.callback_query(F.data.startswith("cash_cat_"), CashOutStates.category_id)
async def cash_out_category(callback: CallbackQuery, state: FSMContext):
    """Обрано категорію — запитуємо коментар або пропуск."""
    cat_id = int(callback.data.replace("cash_cat_", ""))
    await state.update_data(cash_out_category_id=cat_id)
    await state.set_state(CashOutStates.description)
    await callback.message.answer(
        "📝 Введіть коментар (або натисніть /skip щоб пропустити):",
        reply_markup=get_back_to_cash_keyboard(),
    )
    await callback.answer()


@router.message(CashOutStates.description, F.text)
async def cash_out_description(message: Message, state: FSMContext):
    """Отримано коментар або /skip — зберігаємо транзакцію винесення."""
    description = None if message.text and message.text.strip().lower() == "/skip" else (message.text or "").strip() or None
    data = await state.get_data()
    amount = data.get("cash_out_amount")
    cat_id = data.get("cash_out_category_id")
    if amount is None or cat_id is None:
        await state.clear()
        await message.answer("❌ Сесію скинуто. Почніть знову з меню «💵 Готівка».", reply_markup=get_cash_menu())
        return

    async with AsyncSessionLocal() as session:
        tx = Transaction(
            amount=amount,
            transaction_type=TransactionType.EXPENSE,
            date=now_kyiv(),
            source=TransactionSource.CASH,
            fop_id=None,
            category_id=cat_id,
            status=TransactionStatus.CONFIRMED,
            description=description,
            confirmed_by_user=message.from_user.id,
            confirmed_at=now_kyiv(),
        )
        session.add(tx)
        await session.commit()
        # Ім'я категорії для підсумку
        cat_result = await session.execute(select(Category).where(Category.id == cat_id))
        cat = cat_result.scalar_one_or_none()
        cat_name = cat.name if cat else "—"

    await state.clear()
    await message.answer(
        f"✅ <b>Винесення з каси збережено</b>\n\n"
        f"-{format_balance_display(amount)} грн · {cat_name}\n"
        f"{'Коментар: ' + description if description else ''}",
        parse_mode="HTML",
        reply_markup=get_back_to_cash_keyboard(),
    )
