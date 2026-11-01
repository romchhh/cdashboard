# Меню «Клієнти» — посилання на веб та коротка статистика з БД

from aiogram import Router, F
from aiogram.types import Message
from sqlalchemy import func, select

from bot.database.clients_engine import AsyncSessionLocalClients
from bot.database.clients_models import Client
from config import WEB_PUBLIC_BASE_URL

router = Router()


@router.message(F.text == "👥 Клієнти")
async def menu_clients(message: Message):
    async with AsyncSessionLocalClients() as session:
        r = await session.execute(select(func.count()).select_from(Client))
        n = int(r.scalar() or 0)

    lines = [
        "👥 <b>Клієнти Cosmy</b>",
        "",
        f"У базі: <b>{n}</b> записів (після синхронізації з фіду).",
        "",
        "Повний список (по 50 на сторінку) та картка клієнта з замовленнями — у веб-панелі.",
    ]
    if WEB_PUBLIC_BASE_URL:
        url = f"{WEB_PUBLIC_BASE_URL}/clients"
        lines.append(f'<a href="{url}">Відкрити розділ «Клієнти»</a>')
    else:
        lines.append(
            "Щоб отримати посилання з Telegram, додайте у <code>.env</code> змінну "
            "<code>WEB_PUBLIC_BASE_URL</code> (наприклад, <code>https://your-domain.com</code>)."
        )

    await message.answer("\n".join(lines), parse_mode="HTML")
