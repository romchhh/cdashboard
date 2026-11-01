# Адміністративні команди для налаштування бота

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from datetime import datetime

router = Router()


@router.message(Command("init_db"))
async def cmd_init_db(message: Message):
    """Перекласифікувати транзакції (без створення категорій)."""
    status_msg = await message.answer(
        "🔄 <b>ІНІЦІАЛІЗАЦІЯ БД</b>\n\n"
        "⏳ Перекласифікую транзакції без категорій...",
        parse_mode="HTML"
    )
    
    try:
        from bot.database.init_data import init_database
        await init_database()
        
        # Показати результат
        from bot.database.engine import AsyncSessionLocal
        from sqlalchemy import select, func
        from bot.database.models import Category, Transaction, TransactionStatus
        
        async with AsyncSessionLocal() as session:
            # Підрахувати категорії
            result = await session.execute(select(func.count(Category.id)))
            cat_count = result.scalar()
            
            # Підрахувати класифіковані транзакції
            result = await session.execute(
                select(func.count(Transaction.id)).where(Transaction.category_id.is_not(None))
            )
            classified = result.scalar()
            
            # Підрахувати pending
            result = await session.execute(
                select(func.count(Transaction.id)).where(Transaction.status == TransactionStatus.PENDING)
            )
            pending = result.scalar()
        
        text = (
            "✅ <b>БАЗА ДАНИХ ІНІЦІАЛІЗОВАНА</b>\n\n"
            f"📂 Категорій у базі: <b>{cat_count}</b>\n"
            f"✅ Транзакцій класифіковано: <b>{classified}</b>\n"
            f"⏳ Потребують підтвердження: <b>{pending}</b>\n\n"
            f"<i>Завершено: {datetime.now().strftime('%d.%m.%Y %H:%M')}</i>\n\n"
            "Тепер звіти працюватимуть коректно!\n"
            "Використайте /reports для перегляду."
        )
        
        await status_msg.edit_text(text, parse_mode="HTML")
        
    except Exception as e:
        await status_msg.edit_text(
            f"❌ <b>ПОМИЛКА</b>\n\n{str(e)}",
            parse_mode="HTML"
        )


@router.message(Command("db_stats"))
async def cmd_db_stats(message: Message):
    """Статистика бази даних"""
    from bot.database.engine import AsyncSessionLocal
    from sqlalchemy import select, func
    from bot.database.models import (
        FOP, Category, Transaction, ClassificationRule,
        Budget, AccountBalance
    )
    
    async with AsyncSessionLocal() as session:
        # Підрахунки
        fop_count = (await session.execute(select(func.count(FOP.id)))).scalar()
        cat_count = (await session.execute(select(func.count(Category.id)))).scalar()
        tx_count = (await session.execute(select(func.count(Transaction.id)))).scalar()
        rule_count = (await session.execute(select(func.count(ClassificationRule.id)))).scalar()
        budget_count = (await session.execute(select(func.count(Budget.id)))).scalar()
        balance_count = (await session.execute(select(func.count(AccountBalance.id)))).scalar()
        
        # Транзакції з категоріями
        classified = (await session.execute(
            select(func.count(Transaction.id)).where(Transaction.category_id.is_not(None))
        )).scalar()
        
        # Pending
        from bot.database.models import TransactionStatus
        pending = (await session.execute(
            select(func.count(Transaction.id)).where(Transaction.status == TransactionStatus.PENDING)
        )).scalar()
    
    text = (
        "📊 <b>СТАТИСТИКА БАЗИ ДАНИХ</b>\n\n"
        f"👥 ФОПи: <b>{fop_count}</b>\n"
        f"📂 Категорії: <b>{cat_count}</b>\n"
        f"📋 Транзакції: <b>{tx_count}</b>\n"
        f"  └ Класифіковано: <b>{classified}</b> ({classified/tx_count*100 if tx_count else 0:.1f}%)\n"
        f"  └ Потребують підтвердження: <b>{pending}</b>\n"
        f"🎯 Правила класифікації: <b>{rule_count}</b>\n"
        f"💰 Бюджети: <b>{budget_count}</b>\n"
        f"📈 Знімки балансів: <b>{balance_count}</b>\n\n"
        f"<i>Оновлено: {datetime.now().strftime('%d.%m.%Y %H:%M')}</i>"
    )
    
    await message.answer(text, parse_mode="HTML")


@router.message(Command("categories"))
async def cmd_categories(message: Message):
    """Переглянути всі категорії"""
    from bot.database.engine import AsyncSessionLocal
    from sqlalchemy import select, func
    from bot.database.models import Category, ClassificationRule
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(
                Category.id,
                Category.name,
                func.count(ClassificationRule.id).label("rules_count")
            )
            .outerjoin(ClassificationRule, ClassificationRule.category_id == Category.id)
            .group_by(Category.id, Category.name)
            .order_by(Category.name)
        )
        
        categories = result.all()
    
    if not categories:
        await message.answer(
            "❌ <b>Категорії не знайдено</b>\n\n"
            "Використайте /init_db для ініціалізації",
            parse_mode="HTML"
        )
        return
    
    text = f"📂 <b>КАТЕГОРІЇ ВИТРАТ</b>\n\n"
    text += f"Всього: <b>{len(categories)}</b>\n\n"
    
    for cat_id, cat_name, rules_count in categories:
        text += f"• <b>{cat_name}</b>\n"
        text += f"  └ Правил: {rules_count}\n"
    
    await message.answer(text, parse_mode="HTML")


# --- Інлайн-кнопки налаштувань ---

@router.callback_query(F.data == "set_db_stats")
async def cb_db_stats(callback: CallbackQuery):
    """Статистика БД по інлайн-кнопці"""
    await callback.answer()
    from bot.database.engine import AsyncSessionLocal
    from sqlalchemy import select, func
    from bot.database.models import (
        FOP, Category, Transaction, ClassificationRule,
        Budget, AccountBalance, TransactionStatus
    )
    async with AsyncSessionLocal() as session:
        fop_count = (await session.execute(select(func.count(FOP.id)))).scalar()
        cat_count = (await session.execute(select(func.count(Category.id)))).scalar()
        tx_count = (await session.execute(select(func.count(Transaction.id)))).scalar()
        rule_count = (await session.execute(select(func.count(ClassificationRule.id)))).scalar()
        budget_count = (await session.execute(select(func.count(Budget.id)))).scalar()
        balance_count = (await session.execute(select(func.count(AccountBalance.id)))).scalar()
        classified = (await session.execute(
            select(func.count(Transaction.id)).where(Transaction.category_id.is_not(None))
        )).scalar()
        pending = (await session.execute(
            select(func.count(Transaction.id)).where(Transaction.status == TransactionStatus.PENDING)
        )).scalar()
    text = (
        "📊 <b>СТАТИСТИКА БАЗИ ДАНИХ</b>\n\n"
        f"👥 ФОПи: <b>{fop_count}</b>\n"
        f"📂 Категорії: <b>{cat_count}</b>\n"
        f"📋 Транзакції: <b>{tx_count}</b>\n"
        f"  └ Класифіковано: <b>{classified}</b> ({classified/tx_count*100 if tx_count else 0:.1f}%)\n"
        f"  └ Потребують підтвердження: <b>{pending}</b>\n"
        f"🎯 Правила класифікації: <b>{rule_count}</b>\n"
        f"💰 Бюджети: <b>{budget_count}</b>\n"
        f"📈 Знімки балансів: <b>{balance_count}</b>\n\n"
        f"<i>Оновлено: {datetime.now().strftime('%d.%m.%Y %H:%M')}</i>"
    )
    await callback.message.answer(text, parse_mode="HTML")


@router.callback_query(F.data == "set_init_db")
async def cb_init_db(callback: CallbackQuery):
    """Ініціалізація БД по інлайн-кнопці"""
    await callback.answer("⏳ Запускаю...")
    status_msg = await callback.message.answer(
        "🔄 <b>ІНІЦІАЛІЗАЦІЯ БД</b>\n\n⏳ Перекласифікую транзакції без категорій...",
        parse_mode="HTML"
    )
    try:
        from bot.database.init_data import init_database
        from bot.database.engine import AsyncSessionLocal
        from sqlalchemy import select, func
        from bot.database.models import Category, Transaction, TransactionStatus
        await init_database()
        async with AsyncSessionLocal() as session:
            cat_count = (await session.execute(select(func.count(Category.id)))).scalar()
            classified = (await session.execute(
                select(func.count(Transaction.id)).where(Transaction.category_id.is_not(None))
            )).scalar()
            pending = (await session.execute(
                select(func.count(Transaction.id)).where(Transaction.status == TransactionStatus.PENDING)
            )).scalar()
        text = (
            "✅ <b>БАЗА ДАНИХ ІНІЦІАЛІЗОВАНА</b>\n\n"
            f"📂 Категорій: <b>{cat_count}</b>\n"
            f"✅ Класифіковано транзакцій: <b>{classified}</b>\n"
            f"⏳ Потребують підтвердження: <b>{pending}</b>\n\n"
            f"<i>{datetime.now().strftime('%d.%m.%Y %H:%M')}</i>"
        )
        await status_msg.edit_text(text, parse_mode="HTML")
    except Exception as e:
        await status_msg.edit_text(f"❌ <b>ПОМИЛКА</b>\n\n{str(e)}", parse_mode="HTML")


@router.callback_query(F.data == "set_categories")
async def cb_categories(callback: CallbackQuery):
    """Категорії по інлайн-кнопці"""
    await callback.answer()
    from bot.database.engine import AsyncSessionLocal
    from sqlalchemy import select, func
    from bot.database.models import Category, ClassificationRule
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Category.id, Category.name, func.count(ClassificationRule.id).label("rules_count"))
            .outerjoin(ClassificationRule, ClassificationRule.category_id == Category.id)
            .group_by(Category.id, Category.name)
            .order_by(Category.name)
        )
        categories = result.all()
    if not categories:
        await callback.message.answer("❌ Категорії не знайдено. Використайте Ініціалізацію БД.", parse_mode="HTML")
        return
    text = f"📂 <b>КАТЕГОРІЇ ВИТРАТ</b>\n\nВсього: <b>{len(categories)}</b>\n\n"
    for cat_id, cat_name, rules_count in categories:
        text += f"• <b>{cat_name}</b> — правил: {rules_count}\n"
    await callback.message.answer(text, parse_mode="HTML")


@router.callback_query(F.data.in_({"set_notify", "set_test_notify"}))
async def cb_notify_placeholder(callback: CallbackQuery):
    """Заглушка сповіщень"""
    await callback.answer("Функція в розробці", show_alert=True)


@router.message(Command("backup_db"))
async def cmd_backup_db(message: Message):
    """Ручна резервна копія БД (локально + усім адмінам у Telegram)."""
    status_msg = await message.answer(
        "💾 <b>Резервна копія БД</b>\n\n⏳ Створюю архів і надсилаю адмінам…",
        parse_mode="HTML",
    )
    try:
        from bot.services.db_backup import run_database_backup

        result = await run_database_backup(notify_telegram=True)
        if not result.ok:
            await status_msg.edit_text(
                "❌ <b>Не вдалося створити бекап</b>\n\n" + "\n".join(result.errors),
                parse_mode="HTML",
            )
            return
        lines = [
            "✅ <b>Резервна копія готова</b>",
            "",
            f"📁 <code>{result.archive_path.name}</code>",
        ]
        if result.telegram_delivered_to:
            who = ", ".join(str(x) for x in result.telegram_delivered_to)
            if result.telegram_parts > 1:
                lines.append(f"📤 Надіслано: <code>{who}</code> ({result.telegram_parts} частин)")
            else:
                lines.append(f"📤 Надіслано: <code>{who}</code>")
        if result.telegram_failed:
            lines.append("⚠️ Не вдалося: " + "; ".join(result.telegram_failed))
        elif not result.telegram_sent and result.errors:
            lines.append("⚠️ Telegram: " + "; ".join(result.errors))
        if result.skipped:
            lines.append("")
            lines.append("<b>Пропущено:</b>")
            lines.extend(f"• {s}" for s in result.skipped)
        lines.append(f"\n<i>{datetime.now().strftime('%d.%m.%Y %H:%M')}</i>")
        await status_msg.edit_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        await status_msg.edit_text(f"❌ <b>Помилка</b>\n\n{str(e)}", parse_mode="HTML")
