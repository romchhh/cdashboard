# Команди для роботи з балансами

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from datetime import datetime, timedelta

from bot.database.engine import AsyncSessionLocal
from bot.services.balance_sync import get_latest_balances, get_balance_history, format_balance_display
from bot.keyboards.inline import get_balance_menu

router = Router()


@router.message(Command("balances_db"))
async def cmd_balances_from_db(message: Message):
    """Показати останні збережені баланси з БД (без виклику API)"""
    async with AsyncSessionLocal() as session:
        balances = await get_latest_balances(session)
    
    if not balances:
        await message.answer("📭 Немає збережених балансів в БД")
        return
    
    text = "💾 <b>БАЛАНСИ З БД (останні збережені)</b>\n\n"
    
    current_source = None
    for bal in balances:
        # Заголовок джерела
        if bal["source"] != current_source:
            current_source = bal["source"]
            icon = "🏦" if bal["source"] == "monobank" else "💳"
            text += f"\n<b>{icon} {bal['source'].capitalize()}:</b>\n"
        
        # Баланс
        recorded_time = bal["recorded_at"].strftime("%d.%m.%Y %H:%M")
        text += f"• {bal['fop_name']}: <b>{format_balance_display(float(bal['balance']))} {bal['currency']}</b>\n"
        text += f"  Оновлено: {recorded_time}\n"
    
    await message.answer(text, parse_mode="HTML")


@router.message(Command("balance_history"))
async def cmd_balance_history(message: Message):
    """Історія балансів за останні 7 днів"""
    async with AsyncSessionLocal() as session:
        history = await get_balance_history(session, days=7)
    
    if not history:
        await message.answer("📭 Немає історії балансів")
        return
    
    text = "📊 <b>ІСТОРІЯ БАЛАНСІВ (останні 7 днів)</b>\n\n"
    
    # Групуємо по ФОП + джерело
    from collections import defaultdict
    grouped = defaultdict(list)
    
    for item in history:
        key = f"{item['fop_name']} ({item['source']})"
        grouped[key].append(item)
    
    # Виводимо по кожному рахунку
    for account_key, records in grouped.items():
        text += f"<b>{account_key}</b>\n"
        
        # Беремо перші 5 записів
        for rec in records[:5]:
            date_str = rec["recorded_at"].strftime("%d.%m %H:%M")
            text += f"  {date_str}: {format_balance_display(float(rec['balance']))} {rec['currency']}\n"
        
        if len(records) > 5:
            text += f"  ... та ще {len(records) - 5} записів\n"
        
        text += "\n"
    
    await message.answer(text, parse_mode="HTML")


# --- Інлайн: баланси ---

@router.callback_query(F.data == "menu_balance")
async def cb_balance_menu(callback: CallbackQuery):
    """Меню балансів — інлайн"""
    await callback.message.edit_text(
        "💰 <b>БАЛАНСИ</b>\n\nОберіть дію:",
        reply_markup=get_balance_menu(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "menu_balance_refresh")
async def cb_balance_refresh(callback: CallbackQuery):
    """Оновити баланси з API по інлайн-кнопці"""
    await callback.answer("🔄 Оновлюю...")
    from bot.services.balance import get_all_balances
    from bot.services.balance_sync import filter_api_balances, sync_balances_to_db

    balances = await get_all_balances()
    async with AsyncSessionLocal() as session:
        balances = await filter_api_balances(session, balances)
        await sync_balances_to_db(session)
    mono_list = balances.get("monobank") or []
    nova_list = balances.get("novapay") or []
    total_mono_uah = sum(float(acc["balance"]) for acc in mono_list if (acc.get("currency") or "UAH").upper() == "UAH")
    total_nova_uah = sum(float(acc["balance"]) for acc in nova_list if (acc.get("currency") or "UAH").upper() == "UAH")
    total_uah = total_mono_uah + total_nova_uah
    text = "💰 <b>ПОТОЧНІ БАЛАНСИ (оновлено з API)</b>\n\n"
    if mono_list:
        text += "<b>🏦 Monobank:</b>\n"
        for acc in mono_list:
            text += f"• {acc['fop']}: {format_balance_display(float(acc['balance']))} {acc['currency']}\n"
        text += f"  <i>Разом Monobank: {format_balance_display(total_mono_uah)} UAH</i>\n\n"
    if nova_list:
        text += "<b>💳 NovaPay:</b>\n"
        for acc in nova_list:
            text += f"• {acc['fop']}: {format_balance_display(float(acc['balance']))} {acc['currency']}\n"
        text += f"  <i>Разом NovaPay: {format_balance_display(total_nova_uah)} UAH</i>\n\n"
    if not mono_list and not nova_list:
        text += "📭 Немає доступних рахунків\n"
    else:
        text += f"<b>💵 Баланс разом:</b> <b>{format_balance_display(total_uah)} UAH</b>\n\n"
    text += f"<i>Оновлено: {datetime.now().strftime('%d.%m.%Y %H:%M')}</i>"
    await callback.message.answer(text, parse_mode="HTML")


@router.callback_query(F.data == "set_balance_hist")
async def cb_balance_hist(callback: CallbackQuery):
    """Історія балансів по інлайн-кнопці"""
    await callback.answer()
    async with AsyncSessionLocal() as session:
        history = await get_balance_history(session, days=7)
    if not history:
        await callback.message.answer("📭 Немає історії балансів", parse_mode="HTML")
        return
    from collections import defaultdict
    grouped = defaultdict(list)
    for item in history:
        key = f"{item['fop_name']} ({item['source']})"
        grouped[key].append(item)
    text = "📊 <b>ІСТОРІЯ БАЛАНСІВ (7 днів)</b>\n\n"
    for account_key, records in grouped.items():
        text += f"<b>{account_key}</b>\n"
        for rec in records[:5]:
            date_str = rec["recorded_at"].strftime("%d.%m %H:%M")
            text += f"  {date_str}: {format_balance_display(float(rec['balance']))} {rec['currency']}\n"
        if len(records) > 5:
            text += f"  ... та ще {len(records) - 5}\n"
        text += "\n"
    await callback.message.answer(text, parse_mode="HTML")


@router.message(Command("balance_trend"))
async def cmd_balance_trend(message: Message):
    """Тренд балансів: порівняння зараз vs тиждень тому"""
    async with AsyncSessionLocal() as session:
        # Поточні баланси
        current = await get_latest_balances(session)
        
        # Баланси тиждень тому
        history = await get_balance_history(session, days=7)
    
    if not current:
        await message.answer("📭 Немає даних про баланси")
        return
    
    text = "📈 <b>ТРЕНД БАЛАНСІВ</b>\n\n"
    
    for curr_bal in current:
        # Знайти запис ~7 днів тому
        week_ago = datetime.utcnow() - timedelta(days=7)
        
        old_bal = None
        for hist in history:
            if (hist["fop_name"] == curr_bal["fop_name"] and 
                hist["source"] == curr_bal["source"] and
                hist["recorded_at"] <= week_ago):
                old_bal = hist
                break
        
        # Показати зміну
        fop_source = f"{curr_bal['fop_name']} ({curr_bal['source']})"
        curr_amount = curr_bal["balance"]
        
        text += f"<b>{fop_source}</b>\n"
        text += f"Зараз: {format_balance_display(float(curr_amount))} {curr_bal['currency']}\n"
        
        if old_bal:
            old_amount = old_bal["balance"]
            change = curr_amount - old_amount
            change_pct = (change / old_amount * 100) if old_amount != 0 else 0
            
            arrow = "📈" if change > 0 else "📉" if change < 0 else "➡️"
            sign = "+" if change > 0 else ""
            
            text += f"Тиждень тому: {format_balance_display(float(old_amount))}\n"
            text += f"{arrow} Зміна: {sign}{format_balance_display(abs(change))} ({sign}{change_pct:.1f}%)\n"
        else:
            text += "Тиждень тому: немає даних\n"
        
        text += "\n"
    
    await message.answer(text, parse_mode="HTML")
