# Обробники звітів — інлайн кнопки та вибір ФОПів

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from datetime import datetime, timedelta

from bot.database.engine import AsyncSessionLocal
from bot.services.reports import get_cashflow, get_pl, get_breakdown, compare_periods
from bot.services.balance_sync import format_balance_display, format_amount_signed
from bot.keyboards.inline import (
    get_fop_selector,
    get_back_to_reports_keyboard,
    get_reports_period_menu,
)

router = Router()

# Ключі FSM для вибору ФОПів та періоду
FOP_CTX = "fop_context"
FOP_IDS = "selected_fop_ids"
FOP_ALL_SELECTED = "fop_all_selected"
REPORT_PERIOD = "report_period"  # today | week | month


# --- Вибір типу звіту → показ вибору періоду (день / тиждень / місяць) ---

@router.callback_query(F.data.startswith("report_") & ~F.data.contains("_fop_") & ~F.data.contains("_period_"))
async def cb_report_choice(callback: CallbackQuery, state: FSMContext):
    """Обрано тип звіту (cf, pl, bd, cmp) — показуємо вибір періоду"""
    report_type = callback.data
    await state.update_data(**{FOP_CTX: report_type})
    keyboard = get_reports_period_menu(report_type)
    await callback.message.edit_text(
        "📊 <b>Звіт</b>\n\nОберіть період:",
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


# --- Вибір періоду → показ FOP selector ---

@router.callback_query(F.data.startswith("report_") & F.data.contains("_period_"))
async def cb_report_period(callback: CallbackQuery, state: FSMContext):
    """Обрано період (сьогодні, тиждень, місяць) — показуємо вибір ФОПів"""
    # report_cf_period_today | report_cf_period_week | report_cf_period_month
    parts = callback.data.split("_")
    report_type = f"{parts[0]}_{parts[1]}"
    period = parts[3]  # today, week, month
    await state.update_data(**{FOP_CTX: report_type, REPORT_PERIOD: period, FOP_IDS: [], FOP_ALL_SELECTED: True})

    async with AsyncSessionLocal() as session:
        filter_options = await load_fop_filter_options(session)

    if not filter_options:
        await callback.answer("Немає ФОПів у БД", show_alert=True)
        return

    period_label = {"today": "Сьогодні", "yesterday": "Вчора", "week": "Тиждень", "month": "Місяць"}.get(period, "Місяць")
    title, keyboard = get_fop_selector(
        filter_options, [], report_type, title=f"Період: {period_label}\n\nОберіть ФОПи для звіту:", all_selected=True
    )
    await callback.message.edit_text(f"📊 <b>Звіт</b>\n\n{title}", reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


# --- Toggle ФОПів для звітів ---

@router.callback_query(F.data.startswith("report_") & F.data.contains("_fop_"))
async def cb_report_fop(callback: CallbackQuery, state: FSMContext):
    """Вибір ФОПів для звіту: all / toggle / apply"""
    parts = callback.data.split("_")
    # report_cf_fop_all | report_cf_fop_t_5n | report_cf_fop_apply
    report_type = f"{parts[0]}_{parts[1]}"
    action = parts[3]  # all, t, apply

    data = await state.get_data()
    selected = list(data.get(FOP_IDS) or [])
    all_sel = data.get(FOP_ALL_SELECTED, True)

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
        await _run_report(callback, state, report_type, all_sel, selected)
        return

    await state.update_data(**{FOP_IDS: selected, FOP_ALL_SELECTED: all_sel})

    async with AsyncSessionLocal() as session:
        filter_options = await load_fop_filter_options(session)
    title, keyboard = get_fop_selector(filter_options, selected, report_type, title="Оберіть ФОПи для звіту:", all_selected=all_sel)
    await callback.message.edit_text(f"📊 <b>Звіт</b>\n\n{title}", reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


from bot.services.fop_filter import decode_fop_filter_token, load_fop_filter_options
from bot.services.period_range import period_dates as _period_dates


async def _run_report(callback: CallbackQuery, state: FSMContext, report_type: str, all_selected: bool, selected_values: list[str]):
    """Згенерувати звіт за обраними ФОПами та періодом."""
    data = await state.get_data()
    period = data.get(REPORT_PERIOD) or "month"
    await state.clear()
    fop_filter_values = None if all_selected or not selected_values else selected_values

    date_from, date_to, period_name = _period_dates(period)

    async with AsyncSessionLocal() as session:
        if report_type == "report_cf":
            data = await get_cashflow(session, date_from, date_to, fop_filter_values=fop_filter_values)
            text = f"💰 <b>CASHFLOW за {period_name}</b>\n\n"
            text += f"Надходження: <b>+{format_balance_display(data['income'])} грн</b>\n"
            text += f"Витрати: <b>{format_balance_display(data['expense'])} грн</b>\n"
            text += "━━━━━━━━━━━━━━━━━━━━\n"
            net_emoji = "📈" if data["net"] > 0 else "📉" if data["net"] < 0 else "➖"
            text += f"{net_emoji} <b>Чистий потік: {format_amount_signed(data['net'])} грн</b>\n"

        elif report_type == "report_pl":
            data = await get_pl(session, date_from, date_to, fop_filter_values=fop_filter_values)
            cash_withdrawal_total = data.get("cash_withdrawal_total", 0)
            bogdan_total = data.get("bogdan_total", 0)
            text = f"📊 <b>P&L за {period_name}</b>\n\n"
            text += f"<b>ДОХОДИ: {format_balance_display(data['income'])} грн</b>\n\n"
            if data["income_by_fop"]:
                for item in data["income_by_fop"]:
                    title = item.get("name") or "—"
                    text += f"<b>{title}</b>\n"
                    sources_parts = []
                    sources = item.get("sources") or {}
                    novapay = sources.get("novapay")
                    monobank = sources.get("monobank")
                    cash = sources.get("cash")
                    if novapay:
                        sources_parts.append(f"NП: {format_balance_display(novapay)}")
                    if monobank:
                        sources_parts.append(f"Моно: {format_balance_display(monobank)}")
                    if cash:
                        sources_parts.append(f"Готівка: {format_balance_display(cash)}")
                    if sources_parts:
                        text += f"  {' · '.join(sources_parts)}\n"
                    text += f"  Загальне: <b>{format_balance_display(item['total'])} грн</b>\n\n"
            text += f"<b>ВИТРАТИ: {format_balance_display(data['expense'])} грн</b>\n\n"
            if data["expense_by_category"]:
                for cat_name, amount in sorted(data["expense_by_category"].items(), key=lambda x: x[1], reverse=True)[:10]:
                    pct = (amount / data["expense"] * 100) if data["expense"] > 0 else 0
                    text += f"  • {cat_name}: {format_balance_display(amount)} грн ({pct:.1f}%)\n"
                text += "\n"
            
            # Окремі категорії (переміщення та переведення коштів)
            if cash_withdrawal_total > 0 or bogdan_total > 0:
                text += "━━━━━━━━━━━━━━━━━━━━\n"
                text += "💸 <b>Переміщення та переведення коштів</b>\n"
                text += "<i>(не враховуються в розходах)</i>\n\n"
                if cash_withdrawal_total > 0:
                    text += f"  • Зняття готівки: {format_balance_display(cash_withdrawal_total)} грн\n"
                if bogdan_total > 0:
                    text += f"  • Богдан (переведення на карту): {format_balance_display(bogdan_total)} грн\n"
                text += "\n"
            
            text += "━━━━━━━━━━━━━━━━━━━━\n"
            text += f"<b>Прибуток: {format_amount_signed(data['profit'])} грн</b>\n"
            text += f"Рентабельність: <b>{data['margin']:.1f}%</b>\n"

        elif report_type == "report_bd":
            data = await get_breakdown(session, date_from, date_to, fop_filter_values=fop_filter_values)
            text = f"📈 <b>ВИТРАТИ ЗА КАТЕГОРІЯМИ</b>\nПеріод: {period_name}\n\n"
            if data["categories"]:
                for i, cat in enumerate(data["categories"], 1):
                    text += f"{i}. <b>{cat['name']}</b>\n"
                    text += f"   {format_balance_display(cat['amount'])} грн ({cat['percentage']:.1f}%)\n\n"
            else:
                text += "<i>Немає класифікованих витрат за цей період</i>\n\n"
            text += f"<b>Всього: {format_balance_display(data['total_expense'])} грн</b>"

        elif report_type == "report_cmp":
            if period == "today":
                p2_to = date_from - timedelta(seconds=1)
                p2_from = (date_from - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                p1_name, p2_name = "Сьогодні", "Вчора"
            elif period == "yesterday":
                p2_to = date_from - timedelta(seconds=1)
                p2_from = p2_to.replace(hour=0, minute=0, second=0, microsecond=0)
                p1_name, p2_name = "Вчора", "Передвчора"
            elif period == "week":
                delta = date_to - date_from
                p2_to = date_from - timedelta(seconds=1)
                p2_from = p2_to - delta
                p1_name = f"{date_from.strftime('%d.%m')} – {date_to.strftime('%d.%m')}"
                p2_name = f"{p2_from.strftime('%d.%m')} – {p2_to.strftime('%d.%m')}"
            elif period == "year":
                p2_to = date_from - timedelta(days=1)
                p2_from = p2_to.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
                p1_name = str(date_from.year)
                p2_name = str(p2_from.year)
            elif period == "quarter":
                from bot.services.period_range import (
                    previous_tax_quarter,
                    tax_quarter,
                    tax_quarter_label,
                    tax_quarter_start,
                )

                q = tax_quarter(date_from)
                prev_year, prev_q = previous_tax_quarter(date_from.year, q)
                p2_from = tax_quarter_start(prev_year, prev_q)
                p2_to = date_from - timedelta(seconds=1)
                p1_name = tax_quarter_label(date_from.year, q)
                p2_name = tax_quarter_label(prev_year, prev_q)
            else:
                p2_to = date_from - timedelta(days=1)
                p2_from = p2_to.replace(day=1)
                p1_name = date_from.strftime("%B %Y")
                p2_name = p2_from.strftime("%B %Y")
            data = await compare_periods(session, date_from, date_to, p2_from, p2_to, fop_filter_values=fop_filter_values)
            text = f"📊 <b>ПОРІВНЯННЯ</b>\n\n<b>{p1_name}</b> vs <b>{p2_name}</b>\n\n"
            def fmt(val, ch):
                arrow = "↑" if ch > 0 else "↓" if ch < 0 else "→"
                return f"{format_balance_display(val)} ({arrow} {abs(ch):.1f}%)"
            text += f"Доходи: {fmt(data['period1']['income'], data['income_change'])}\n"
            text += f"Витрати: {fmt(data['period1']['expense'], data['expense_change'])}\n"
            text += f"Прибуток: {fmt(data['period1']['profit'], data['profit_change'])}\n"
        else:
            await callback.answer("Невідомий тип звіту", show_alert=True)
            return

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=get_back_to_reports_keyboard(),
    )
    await callback.answer("✅ Звіт готовий")


# --- Команди /cashflow, /pl тощо — теж показують FOP selector (для зручності) ---

@router.message(F.text.in_({"/cashflow", "/pl", "/breakdown", "/compare"}))
async def cmd_report_via_text(message: Message, state: FSMContext):
    """Команди звітів — показуємо вибір періоду (день / тиждень / місяць)"""
    mapping = {"/cashflow": "report_cf", "/pl": "report_pl", "/breakdown": "report_bd", "/compare": "report_cmp"}
    report_type = mapping.get(message.text, "report_cf")
    await state.update_data(**{FOP_CTX: report_type})
    keyboard = get_reports_period_menu(report_type)
    await message.answer("📊 <b>Звіт</b>\n\nОберіть період:", reply_markup=keyboard, parse_mode="HTML")
