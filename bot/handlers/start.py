# Головне меню та команда /start

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext

from bot.database.engine import AsyncSessionLocal
from bot.handlers.cash import get_cash_menu_text
from bot.keyboards.inline import (
    get_reports_menu,
    get_transactions_menu,
    get_settings_menu,
    get_cash_menu,
)

router = Router()


def get_main_menu_keyboard():
    """Головне меню бота"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Звіти"), KeyboardButton(text="💳 Транзакції")],
            [KeyboardButton(text="💰 Баланс"), KeyboardButton(text="💵 Готівка")],
            [KeyboardButton(text="👥 Клієнти"), KeyboardButton(text="⚙️ Налаштування")],
            [KeyboardButton(text="🔄 Оновити всі дані")],
        ],
        resize_keyboard=True
    )
    return keyboard


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    """Команда /start — головне меню з балансами (без IBAN у привітанні)"""
    await state.clear()

    from bot.database.engine import AsyncSessionLocal
    from bot.services.balance_sync import get_latest_balances, format_balance_display

    text = "👋 <b>Сosmy Management Agent</b>\n\n💰 <b>ПОТОЧНІ БАЛАНСИ</b>\n\n"

    async with AsyncSessionLocal() as session:
        balances = await get_latest_balances(session)

    if not balances:
        text += "📭 Немає збережених балансів\n"
        text += "Використайте «💰 Баланс» або «🔄 Оновити всі дані» для оновлення\n\n"
    else:
        mono_balances = [b for b in balances if b["source"] == "monobank"]
        nova_balances = [b for b in balances if b["source"] == "novapay"]
        total_mono_uah = sum(float(acc["balance"]) for acc in mono_balances if (acc.get("currency") or "UAH").upper() == "UAH")
        total_nova_uah = sum(float(acc["balance"]) for acc in nova_balances if (acc.get("currency") or "UAH").upper() == "UAH")
        total_uah = total_mono_uah + total_nova_uah

        if mono_balances:
            text += "<b>🏦 Monobank:</b>\n"
            for acc in mono_balances:
                text += f"• {acc['fop_name']}: {format_balance_display(float(acc['balance']))} {acc['currency']}\n"
            text += f"  <i>Разом Monobank: {format_balance_display(total_mono_uah)} UAH</i>\n\n"

        if nova_balances:
            text += "<b>💳 NovaPay:</b>\n"
            for acc in nova_balances:
                text += f"• {acc['fop_name']}: {format_balance_display(float(acc['balance']))} {acc['currency']}\n"
            text += f"  <i>Разом NovaPay: {format_balance_display(total_nova_uah)} UAH</i>\n\n"

        text += f"<b>💵 Баланс разом:</b> <b>{format_balance_display(total_uah)} UAH</b>\n\n"
        latest_update = max(b["recorded_at"] for b in balances)
        text += f"<i>Оновлено: {latest_update.strftime('%d.%m.%Y %H:%M')}</i>\n"
        text += "<i>Баланси оновлюються автоматично кожні 30 хв</i>\n\n"

    text += "━━━━━━━━━━━━━━━━━━━━\nОберіть дію:"

    await message.answer(
        text,
        reply_markup=get_main_menu_keyboard(),
        parse_mode="HTML"
    )


async def _format_balance_full(session) -> str:
    """Текст з усіма рахунками та балансом разом (для кнопки Баланс)."""
    from bot.services.balance_sync import get_latest_balances, format_balance_display

    balances = await get_latest_balances(session)
    if not balances:
        return "📭 Немає збережених балансів.\nВикористайте «🔄 Оновити всі дані» або /balance для оновлення з API."

    mono_balances = [b for b in balances if b["source"] == "monobank"]
    nova_balances = [b for b in balances if b["source"] == "novapay"]
    total_mono_uah = sum(float(acc["balance"]) for acc in mono_balances if (acc.get("currency") or "UAH").upper() == "UAH")
    total_nova_uah = sum(float(acc["balance"]) for acc in nova_balances if (acc.get("currency") or "UAH").upper() == "UAH")
    total_uah = total_mono_uah + total_nova_uah

    text = "💰 <b>ПОТОЧНІ БАЛАНСИ</b>\n\n"

    if mono_balances:
        text += "<b>🏦 Monobank:</b>\n"
        for acc in mono_balances:
            text += f"• {acc['fop_name']}: {format_balance_display(float(acc['balance']))} {acc['currency']}\n"
            if acc.get("iban"):
                text += f"  {acc['iban']}\n"
        text += f"  <i>Разом Monobank: {format_balance_display(total_mono_uah)} UAH</i>\n\n"

    if nova_balances:
        text += "<b>💳 NovaPay:</b>\n"
        for acc in nova_balances:
            text += f"• {acc['fop_name']}: {format_balance_display(float(acc['balance']))} {acc['currency']}\n"
            if acc.get("iban"):
                text += f"  {acc['iban']}\n"
        text += f"  <i>Разом NovaPay: {format_balance_display(total_nova_uah)} UAH</i>\n\n"

    latest_update = max(b["recorded_at"] for b in balances)
    time_str = latest_update.strftime("%d.%m.%Y %H:%M")
    text += f"<b>💵 Баланс разом:</b> <b>{format_balance_display(total_uah)} UAH</b>\n\n"
    text += f"<i>Оновлено: {time_str}</i>\n"
    text += "<i>Баланси оновлюються автоматично кожні 30 хв</i>"
    return text


@router.message(F.text == "💰 Баланс")
async def menu_balance(message: Message):
    """Кнопка Баланс — усі рахунки та баланс разом"""
    from bot.database.engine import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        text = await _format_balance_full(session)
    await message.answer(text, parse_mode="HTML")


@router.callback_query(F.data == "menu_reports")
async def cb_menu_reports(callback: CallbackQuery):
    """Меню звітів — інлайн"""
    await callback.message.edit_text(
        "📊 <b>ЗВІТИ</b>\n\nОберіть тип звіту:",
        reply_markup=get_reports_menu(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "menu_tx")
async def cb_menu_tx(callback: CallbackQuery):
    """Меню транзакцій — інлайн"""
    await callback.message.edit_text(
        "💳 <b>ТРАНЗАКЦІЇ</b>\n\nОберіть період або дію:",
        reply_markup=get_transactions_menu(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "menu_settings")
async def cb_menu_settings(callback: CallbackQuery):
    """Меню налаштувань — інлайн"""
    await callback.message.edit_text(
        "⚙️ <b>НАЛАШТУВАННЯ</b>\n\nОберіть дію:",
        reply_markup=get_settings_menu(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(F.text == "📊 Звіти")
async def menu_reports(message: Message):
    """Меню звітів — показуємо інлайн кнопки"""
    await message.answer(
        "📊 <b>ЗВІТИ</b>\n\nОберіть тип звіту:",
        reply_markup=get_reports_menu(),
        parse_mode="HTML"
    )


@router.message(F.text == "💳 Транзакції")
async def menu_transactions(message: Message):
    """Меню транзакцій — інлайн кнопки"""
    await message.answer(
        "💳 <b>ТРАНЗАКЦІЇ</b>\n\nОберіть період або дію:",
        reply_markup=get_transactions_menu(),
        parse_mode="HTML"
    )


@router.message(F.text == "⚙️ Налаштування")
async def menu_settings(message: Message):
    """Меню налаштувань — інлайн кнопки"""
    await message.answer(
        "⚙️ <b>НАЛАШТУВАННЯ</b>\n\nОберіть дію:",
        reply_markup=get_settings_menu(),
        parse_mode="HTML"
    )


@router.message(F.text == "💵 Готівка")
async def menu_cash(message: Message):
    """Меню готівки — баланс, останні внесення/винесення з коментарями, кнопки."""
    async with AsyncSessionLocal() as session:
        text = await get_cash_menu_text(session)
    await message.answer(
        text,
        reply_markup=get_cash_menu(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "menu_sync_all")
async def cb_sync_all(callback: CallbackQuery):
    """Інлайн кнопка — повна синхронізація"""
    await callback.answer("🔄 Запускаю синхронізацію...")
    # Викликаємо ту ж логіку, що й по кнопці
    await _run_full_sync(callback.message)


@router.message(F.text == "🔄 Оновити всі дані")
async def update_all_data(message: Message):
    """Вручну запустити всі cron-задачі (повна синхронізація)"""
    await _run_full_sync(message)


async def _run_full_sync(message_or_callback_message):
    """Спільна логіка повної синхронізації (повідомлення або callback.message)."""
    status_msg = await message_or_callback_message.answer(
        "🔄 <b>Оновлення всіх даних...</b>\n\n"
        "⏳ Це може зайняти 1-2 хвилини\n"
        "Зачекайте, будь ласка...",
        parse_mode="HTML"
    )
    
    results = []
    
    # 1. Синхронізація транзакцій
    try:
        await status_msg.edit_text(
            "🔄 <b>Оновлення всіх даних...</b>\n\n"
            "1️⃣ Синхронізація транзакцій... ⏳\n"
            "2️⃣ Синхронізація балансів...\n"
            "3️⃣ Оновлення токенів NovaPay...\n"
            "4️⃣ Синхронізація клієнтів Cosmy...",
            parse_mode="HTML"
        )
        
        from bot.scheduler.tasks import sync_all_transactions
        await sync_all_transactions()
        results.append("✅ Транзакції синхронізовано")
    except Exception as e:
        results.append(f"❌ Транзакції: {str(e)[:50]}")
    
    # 2. Синхронізація балансів
    try:
        await status_msg.edit_text(
            "🔄 <b>Оновлення всіх даних...</b>\n\n"
            "1️⃣ Синхронізація транзакцій... ✅\n"
            "2️⃣ Синхронізація балансів... ⏳\n"
            "3️⃣ Оновлення токенів NovaPay...\n"
            "4️⃣ Синхронізація клієнтів Cosmy...",
            parse_mode="HTML"
        )
        
        from bot.scheduler.tasks import sync_balances
        await sync_balances()
        results.append("✅ Баланси синхронізовано")
    except Exception as e:
        results.append(f"❌ Баланси: {str(e)[:50]}")
    
    # 3. Оновлення токенів NovaPay
    try:
        await status_msg.edit_text(
            "🔄 <b>Оновлення всіх даних...</b>\n\n"
            "1️⃣ Синхронізація транзакцій... ✅\n"
            "2️⃣ Синхронізація балансів... ✅\n"
            "3️⃣ Оновлення токенів NovaPay... ⏳\n"
            "4️⃣ Синхронізація клієнтів Cosmy...",
            parse_mode="HTML"
        )
        
        from bot.scheduler.tasks import refresh_novapay_tokens
        await refresh_novapay_tokens()
        results.append("✅ Токени NovaPay оновлено")
    except Exception as e:
        results.append(f"❌ Токени: {str(e)[:50]}")
    
    # 4. Синхронізація клієнтів Cosmy (фід analytics_customer.xml)
    try:
        await status_msg.edit_text(
            "🔄 <b>Оновлення всіх даних...</b>\n\n"
            "1️⃣ Синхронізація транзакцій... ✅\n"
            "2️⃣ Синхронізація балансів... ✅\n"
            "3️⃣ Оновлення токенів NovaPay... ✅\n"
            "4️⃣ Синхронізація клієнтів Cosmy... ⏳",
            parse_mode="HTML"
        )
        from bot.services.clients_sync import sync_clients_from_feed as sync_clients_xml
        await sync_clients_xml()
        results.append("✅ Клієнти Cosmy синхронізовано")
    except Exception as e:
        results.append(f"❌ Клієнти Cosmy: {str(e)[:50]}")
    
    # Фінальний результат
    from datetime import datetime
    final_text = (
        "✅ <b>ОНОВЛЕННЯ ЗАВЕРШЕНО</b>\n\n"
        + "\n".join(results) +
        f"\n\n<i>Оновлено: {datetime.now().strftime('%d.%m.%Y %H:%M')}</i>"
    )
    
    await status_msg.edit_text(final_text, parse_mode="HTML")
    
    # Показуємо оновлені баланси
    from bot.database.engine import AsyncSessionLocal
    from bot.services.balance_sync import get_latest_balances
    
    async with AsyncSessionLocal() as session:
        balances = await get_latest_balances(session)
    
    if balances:
        from bot.services.balance_sync import format_balance_display
        balance_text = "\n💰 <b>ПОТОЧНІ БАЛАНСИ:</b>\n\n"
        mono_balances = [b for b in balances if b["source"] == "monobank"]
        nova_balances = [b for b in balances if b["source"] == "novapay"]
        total_mono_uah = sum(float(acc["balance"]) for acc in mono_balances if (acc.get("currency") or "UAH").upper() == "UAH")
        total_nova_uah = sum(float(acc["balance"]) for acc in nova_balances if (acc.get("currency") or "UAH").upper() == "UAH")
        total_uah = total_mono_uah + total_nova_uah
        if mono_balances:
            balance_text += "<b>🏦 Monobank:</b>\n"
            for acc in mono_balances:
                balance_text += f"• {acc['fop_name']}: {format_balance_display(float(acc['balance']))} {acc['currency']}\n"
            balance_text += f"  <i>Разом Monobank: {format_balance_display(total_mono_uah)} UAH</i>\n\n"
        if nova_balances:
            balance_text += "<b>💳 NovaPay:</b>\n"
            for acc in nova_balances:
                balance_text += f"• {acc['fop_name']}: {format_balance_display(float(acc['balance']))} {acc['currency']}\n"
            balance_text += f"  <i>Разом NovaPay: {format_balance_display(total_nova_uah)} UAH</i>\n\n"
        balance_text += f"<b>💵 Баланс разом:</b> <b>{format_balance_display(total_uah)} UAH</b>\n\n"
        await message_or_callback_message.answer(balance_text, parse_mode="HTML")


@router.message(Command("balance"))
async def cmd_balance(message: Message):
    """Оновити баланси з API в реальному часі"""
    await message.answer("🔄 Оновлюю баланси з API...", parse_mode="HTML")
    
    # Отримуємо баланси через API
    from bot.services.balance import get_all_balances
    from bot.database.engine import AsyncSessionLocal
    from bot.services.balance_sync import filter_api_balances, sync_balances_to_db

    async with AsyncSessionLocal() as session:
        balances = await filter_api_balances(session, await get_all_balances())
        await sync_balances_to_db(session)
    
    from bot.services.balance_sync import format_balance_display
    from datetime import datetime

    text = "💰 <b>ПОТОЧНІ БАЛАНСИ (оновлено з API)</b>\n\n"
    mono_list = balances.get("monobank") or []
    nova_list = balances.get("novapay") or []
    total_mono_uah = sum(float(acc["balance"]) for acc in mono_list if (acc.get("currency") or "UAH").upper() == "UAH")
    total_nova_uah = sum(float(acc["balance"]) for acc in nova_list if (acc.get("currency") or "UAH").upper() == "UAH")
    total_uah = total_mono_uah + total_nova_uah

    if mono_list:
        text += "<b>🏦 Monobank:</b>\n"
        for acc in mono_list:
            text += f"• {acc['fop']}: {format_balance_display(float(acc['balance']))} {acc['currency']}\n"
            text += f"  {acc['iban']}\n"
        text += f"  <i>Разом Monobank: {format_balance_display(total_mono_uah)} UAH</i>\n\n"
    if nova_list:
        text += "<b>💳 NovaPay:</b>\n"
        for acc in nova_list:
            text += f"• {acc['fop']}: {format_balance_display(float(acc['balance']))} {acc['currency']}\n"
            text += f"  {acc['iban']}\n"
        text += f"  <i>Разом NovaPay: {format_balance_display(total_nova_uah)} UAH</i>\n\n"
    if not mono_list and not nova_list:
        text += "📭 Немає доступних рахунків\n\n"
    else:
        text += f"<b>💵 Баланс разом:</b> <b>{format_balance_display(total_uah)} UAH</b>\n\n"
    text += f"<i>Оновлено: {datetime.now().strftime('%d.%m.%Y %H:%M')}</i>\n"
    text += "<i>✅ Збережено в БД</i>"
    
    await message.answer(text, parse_mode="HTML")
