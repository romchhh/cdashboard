"""
Міграція для додавання поля dilovod_id у таблицю product_options.

Потрібно виконати один раз після оновлення моделі ProductOption.
"""

import asyncio

from sqlalchemy import text

from bot.database.products_engine import products_engine


async def migrate() -> None:
    """Додати колонку dilovod_id у product_options (якщо її ще немає)."""
    print("📁 Використовується БД продуктів (PRODUCTS_DATABASE_URL)")

    async with products_engine.begin() as conn:
        try:
            await conn.execute(
                text("ALTER TABLE product_options ADD COLUMN dilovod_id VARCHAR(50) NULL")
            )
            print("✅ Додано колонку dilovod_id у product_options")
        except Exception as e:
            msg = str(e).lower()
            if "duplicate column name" in msg:
                print("⚠️  Колонка dilovod_id вже існує у product_options")
            else:
                print(f"❌ Помилка додавання dilovod_id: {e}")


if __name__ == "__main__":
    print("🔄 Початок міграції product_options (dilovod_id)...")
    asyncio.run(migrate())
    print("✅ Міграція product_options завершена!")

