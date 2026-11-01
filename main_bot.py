# Точка входу: Telegram-бот (доступ тільки для адміністраторів з ADMINISTRATORS у .env)

import asyncio
import logging
from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Message, CallbackQuery

from config import BOT_TOKEN
from bot.database.engine import init_db
from bot.database.clients_engine import init_clients_db
from bot.filters import IsAdmin
from bot.handlers import start, transactions, reports, balance_commands, sync_commands, admin_commands, cash, clients

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _register_admin_filters(*routers: Router) -> None:
    """Додає IsAdmin фільтр для message/callback у кожний роутер."""
    for router in routers:
        router.message.filter(IsAdmin())
        router.callback_query.filter(IsAdmin())


def _include_routers(dp: Dispatcher, *routers: Router) -> None:
    """Підключає роутери до диспетчера в заданому порядку."""
    for router in routers:
        dp.include_router(router)


async def run_bot():
    """Запуск Telegram-бота."""
    logger.info("=" * 60)
    logger.info("Ініціалізація бази даних...")
    await init_db()
    logger.info("✅ Структура БД створена/перевірена")
    await init_clients_db()
    logger.info("✅ Структура БД клієнтів Cosmy (окремий файл) створена/перевірена")
    logger.info("=" * 60)

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    admin_routers = (
        start.router,
        transactions.router,
        reports.router,
        balance_commands.router,
        sync_commands.router,
        admin_commands.router,
        cash.router,
        clients.router,
    )
    _register_admin_filters(*admin_routers)
    _include_routers(dp, *admin_routers)

    # Останнім: будь-який користувач не зі списку адмінів отримує відмову (бот тільки по ID адміна)
    access_denied_router = Router()

    @access_denied_router.message()
    async def deny_message(message: Message):
        await message.answer(
            "⛔ У вас немає доступу до цього бота. Доступ мають лише адміністратори."
        )

    @access_denied_router.callback_query()
    async def deny_callback(callback: CallbackQuery):
        await callback.answer("Немає доступу.", show_alert=True)

    dp.include_router(access_denied_router)

    logger.info("Бот запущено! (Крон-задачі запускаються разом з веб-апкою.)")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(run_bot())
