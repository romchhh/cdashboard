"""Допоміжні запити до довідника категорій."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Category


async def get_sales_category_id(session: AsyncSession) -> int | None:
    """ID категорії «Продаж» (оплата за товар) для внесення в касу."""
    result = await session.execute(
        select(Category).where(Category.name == "Продаж", Category.is_expense.is_(False))
    )
    cat = result.scalar_one_or_none()
    return cat.id if cat else None
