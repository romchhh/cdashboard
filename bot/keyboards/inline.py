# Інлайн-клавіатури та вибір ФОПів

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import FOP


def get_back_to_reports_keyboard() -> InlineKeyboardMarkup:
    """Кнопка «Назад» під результатом звіту — у меню звітів."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_reports"))
    return builder.as_markup()


def get_back_to_tx_keyboard() -> InlineKeyboardMarkup:
    """Кнопка «Назад» під результатом транзакцій — у меню транзакцій."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_tx"))
    return builder.as_markup()


def get_transactions_pagination_keyboard(page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Клавіатура прокрутки транзакцій: ◀️ Назад | Сторінка X/Y | ▶️ Далі та ⬅️ Назад у меню."""
    builder = InlineKeyboardBuilder()
    if total_pages > 1:
        row = []
        if page > 0:
            row.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"txscroll_{page - 1}"))
        row.append(
            InlineKeyboardButton(
                text=f"Сторінка {page + 1}/{total_pages}",
                callback_data="txscroll_noop",
            )
        )
        if page < total_pages - 1:
            row.append(InlineKeyboardButton(text="▶️ Далі", callback_data=f"txscroll_{page + 1}"))
        builder.row(*row)
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_tx"))
    return builder.as_markup()


def get_back_to_cash_keyboard() -> InlineKeyboardMarkup:
    """Кнопка «Назад» — у меню готівки."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_cash"))
    return builder.as_markup()


def get_cash_menu() -> InlineKeyboardMarkup:
    """Меню готівки: перегляд, внесення в касу, винесення з каси."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📊 Перегляд готівки", callback_data="cash_view"),
    )
    builder.row(
        InlineKeyboardButton(text="➕ Внесення в касу", callback_data="cash_in"),
        InlineKeyboardButton(text="➖ Винесення з каси", callback_data="cash_out"),
    )
    return builder.as_markup()


def get_reports_menu() -> InlineKeyboardMarkup:
    """Меню звітів: Cashflow, P&L, Breakdown, Compare"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="💰 Грошовий потік", callback_data="report_cf"),
        InlineKeyboardButton(text="📊 P&L", callback_data="report_pl"),
    )
    builder.row(
        InlineKeyboardButton(text="📈 По категоріях", callback_data="report_bd"),
        InlineKeyboardButton(text="📉 Порівняння", callback_data="report_cmp"),
    )
    return builder.as_markup()


def get_reports_period_menu(report_type: str) -> InlineKeyboardMarkup:
    """Вибір періоду для звіту: сьогодні, вчора, тиждень, місяць"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📅 Сьогодні", callback_data=f"{report_type}_period_today"),
        InlineKeyboardButton(text="📅 Вчора", callback_data=f"{report_type}_period_yesterday"),
    )
    builder.row(
        InlineKeyboardButton(text="📆 Тиждень", callback_data=f"{report_type}_period_week"),
        InlineKeyboardButton(text="📆 Місяць", callback_data=f"{report_type}_period_month"),
    )
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_reports"))
    return builder.as_markup()


def get_transactions_menu() -> InlineKeyboardMarkup:
    """Меню транзакцій: сьогодні, вчора, тиждень, місяць, непідтверджені"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📅 Сьогодні", callback_data="tx_today"),
        InlineKeyboardButton(text="📅 Вчора", callback_data="tx_yesterday"),
    )
    builder.row(
        InlineKeyboardButton(text="📆 Тиждень", callback_data="tx_week"),
        InlineKeyboardButton(text="📆 Місяць", callback_data="tx_month"),
    )
    builder.row(
        InlineKeyboardButton(text="⏳ Непідтверджені", callback_data="tx_pending"),
    )
    builder.row(
        InlineKeyboardButton(text="🔄 Оновити транзакції", callback_data="menu_sync_tx"),
    )
    return builder.as_markup()


def get_settings_menu() -> InlineKeyboardMarkup:
    """Меню налаштувань"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📊 Статистика БД", callback_data="set_db_stats"),
        InlineKeyboardButton(text="📂 Ініціалізація БД", callback_data="set_init_db"),
    )
    builder.row(
        InlineKeyboardButton(text="📂 Категорії", callback_data="set_categories"),
    )
    builder.row(
        InlineKeyboardButton(text="🔄 Повна синхронізація", callback_data="menu_sync_all"),
        InlineKeyboardButton(text="💰 Баланси з API", callback_data="menu_balance"),
    )

    return builder.as_markup()


def get_balance_menu() -> InlineKeyboardMarkup:
    """Меню балансів"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔄 Оновити з API", callback_data="menu_balance_refresh"),
        InlineKeyboardButton(text="📈 Історія", callback_data="set_balance_hist"),
    )
    return builder.as_markup()


def get_fop_selector(
    filter_options: list,
    selected_values: list[str],
    prefix: str,
    title: str = "Оберіть ФОПи для аналітики:",
    all_selected: bool = True,
) -> tuple[str, InlineKeyboardMarkup]:
    """
    Клавіатура вибору ФОПів з галочками (toggle).
    filter_options — з build_fop_filter_options (один рядок на прізвище).
    all_selected=True: «Всі ФОПи» увімкнено — галочки скрізь. Повторний клік по «Всі ФОПи» вимикає.
    all_selected=False: галочки тільки у обраних окремо пунктів.
    """
    from bot.services.fop_filter import fop_filter_option_label

    builder = InlineKeyboardBuilder()

    all_text = "✅ Всі ФОПи" if all_selected else "☐ Всі ФОПи"
    builder.row(
        InlineKeyboardButton(text=all_text, callback_data=f"{prefix}_fop_all"),
    )

    for idx, opt in enumerate(filter_options):
        value = opt["value"] if isinstance(opt, dict) else opt.value
        display = (
            fop_filter_option_label(opt)
            if isinstance(opt, dict)
            else str(opt.get("label") if hasattr(opt, "get") else opt)
        )
        is_sel = all_selected or (value in selected_values)
        btn_text = f"✅ {display}" if is_sel else f"☐ {display}"
        builder.row(
            InlineKeyboardButton(text=btn_text, callback_data=f"{prefix}_fop_t_{idx}"),
        )

    builder.row(
        InlineKeyboardButton(text="▶️ Застосувати", callback_data=f"{prefix}_fop_apply"),
    )
    back_cb = "menu_reports" if prefix.startswith("report_") else "menu_tx"
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=back_cb),
    )

    return title, builder.as_markup()


async def get_fop_list(session: AsyncSession) -> list:
    """ФОПи для синхронізації та балансів: лише is_active=True."""
    from config import INACTIVE_FOP_NAMES

    result = await session.execute(
        select(FOP).where(FOP.is_active == True).order_by(FOP.name)
    )
    return [
        f
        for f in result.scalars().all()
        if f.name not in INACTIVE_FOP_NAMES
    ]


async def get_fops_for_filters(session: AsyncSession) -> list:
    """
    ФОПи для фільтрів звітів і транзакцій:
    активні + архівні, у яких є підтверджені транзакції (історія лишається в P&L).
    """
    from config import INACTIVE_FOP_NAMES
    from bot.database.models import Transaction, TransactionStatus

    active = await get_fop_list(session)
    active_ids = {f.id for f in active}

    archived_result = await session.execute(
        select(FOP)
        .join(Transaction, Transaction.fop_id == FOP.id)
        .where(
            FOP.is_active.is_(False),
            Transaction.status == TransactionStatus.CONFIRMED,
        )
        .group_by(FOP.id)
        .order_by(FOP.name)
    )
    archived = [
        f
        for f in archived_result.scalars().all()
        if f.id not in active_ids and f.name not in INACTIVE_FOP_NAMES
    ]
    return active + archived
