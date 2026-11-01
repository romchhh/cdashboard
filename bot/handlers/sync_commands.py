# Команди для ручної синхронізації даних

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from datetime import datetime

from bot.services.balance_sync import format_balance_display

router = Router()


@router.message(Command("sync_all"))
async def cmd_sync_all(message: Message):
    """Повна синхронізація всіх даних (альтернативна команда)"""
    status_msg = await message.answer(
        "🔄 <b>ПОВНА СИНХРОНІЗАЦІЯ</b>\n\n"
        "Запускаю всі cron-задачі вручну...",
        parse_mode="HTML"
    )
    
    results = []
    
    # 1. Синхронізація транзакцій
    try:
        from bot.scheduler.tasks import sync_all_transactions
        await sync_all_transactions()
        results.append("✅ Транзакції синхронізовано")
    except Exception as e:
        results.append(f"❌ Транзакції: {str(e)[:50]}")
    
    # 2. Синхронізація балансів
    try:
        from bot.scheduler.tasks import sync_balances
        await sync_balances()
        results.append("✅ Баланси синхронізовано")
    except Exception as e:
        results.append(f"❌ Баланси: {str(e)[:50]}")
    
    # 3. Оновлення токенів NovaPay
    try:
        from bot.scheduler.tasks import refresh_novapay_tokens
        await refresh_novapay_tokens()
        results.append("✅ Токени NovaPay оновлено")
    except Exception as e:
        results.append(f"❌ Токени: {str(e)[:50]}")
    
    # Результат
    final_text = (
        "✅ <b>СИНХРОНІЗАЦІЯ ЗАВЕРШЕНА</b>\n\n"
        + "\n".join(results) +
        f"\n\n<i>Завершено: {datetime.now().strftime('%d.%m.%Y %H:%M')}</i>\n\n"
        "Використайте /start для перегляду оновлених даних"
    )
    
    await status_msg.edit_text(final_text, parse_mode="HTML")


@router.callback_query(F.data == "menu_sync_tx")
async def cb_sync_transactions(callback: CallbackQuery):
    """Оновити транзакції по інлайн-кнопці"""
    await callback.answer("🔄 Запускаю синхронізацію...")
    status_msg = await callback.message.answer("🔄 Синхронізація транзакцій...", parse_mode="HTML")
    try:
        from bot.scheduler.tasks import sync_all_transactions
        await sync_all_transactions()
        await status_msg.edit_text(
            f"✅ <b>Транзакції синхронізовано</b>\n\n<i>{datetime.now().strftime('%d.%m.%Y %H:%M')}</i>",
            parse_mode="HTML"
        )
    except Exception as e:
        await status_msg.edit_text(f"❌ Помилка: {str(e)}", parse_mode="HTML")


@router.message(Command("sync_transactions"))
async def cmd_sync_transactions(message: Message):
    """Синхронізувати тільки транзакції"""
    await message.answer("🔄 Синхронізація транзакцій...", parse_mode="HTML")
    
    try:
        from bot.scheduler.tasks import sync_all_transactions
        await sync_all_transactions()
        await message.answer(
            "✅ <b>Транзакції синхронізовано</b>\n\n"
            f"Оновлено: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
            parse_mode="HTML"
        )
    except Exception as e:
        await message.answer(f"❌ Помилка: {str(e)}", parse_mode="HTML")


@router.message(Command("sync_balances"))
async def cmd_sync_balances(message: Message):
    """Синхронізувати тільки баланси"""
    await message.answer("🔄 Синхронізація балансів...", parse_mode="HTML")
    
    try:
        from bot.scheduler.tasks import sync_balances
        await sync_balances()
        
        # Показуємо оновлені баланси
        from bot.database.engine import AsyncSessionLocal
        from bot.services.balance_sync import get_latest_balances
        
        async with AsyncSessionLocal() as session:
            balances = await get_latest_balances(session)
        
        text = "✅ <b>Баланси синхронізовано</b>\n\n"
        
        if balances:
            mono_balances = [b for b in balances if b["source"] == "monobank"]
            nova_balances = [b for b in balances if b["source"] == "novapay"]
            
            if mono_balances:
                text += "<b>🏦 Monobank:</b>\n"
                for acc in mono_balances:
                    text += f"• {acc['fop_name']}: <b>{format_balance_display(float(acc['balance']))} {acc['currency']}</b>\n"
                text += "\n"
            
            if nova_balances:
                text += "<b>💳 NovaPay:</b>\n"
                for acc in nova_balances:
                    text += f"• {acc['fop_name']}: <b>{format_balance_display(float(acc['balance']))} {acc['currency']}</b>\n"
        
        text += f"\n<i>Оновлено: {datetime.now().strftime('%d.%m.%Y %H:%M')}</i>"
        
        await message.answer(text, parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ Помилка: {str(e)}", parse_mode="HTML")


@router.message(Command("sync_tokens"))
async def cmd_sync_tokens(message: Message):
    """Оновити токени NovaPay"""
    await message.answer("🔄 Оновлення токенів NovaPay...", parse_mode="HTML")
    
    try:
        from bot.scheduler.tasks import refresh_novapay_tokens
        await refresh_novapay_tokens()
        await message.answer(
            "✅ <b>Токени NovaPay оновлено</b>\n\n"
            f"Оновлено: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
            parse_mode="HTML"
        )
    except Exception as e:
        await message.answer(f"❌ Помилка: {str(e)}", parse_mode="HTML")
