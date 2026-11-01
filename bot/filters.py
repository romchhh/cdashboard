# Фільтри бота

from aiogram.filters import BaseFilter
from aiogram.types import Message, CallbackQuery

from config import administrators


class IsAdmin(BaseFilter):
    """Доступ тільки для адміністраторів (список administrators з config)."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user_id = getattr(event.from_user, "id", None)
        if user_id is None:
            return False
        if administrators and user_id == administrators[0]:
            return True
        return user_id in administrators
