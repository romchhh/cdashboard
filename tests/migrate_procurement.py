"""
Міграція для додавання нових полів у ProductAnalytics та створення ProcurementSettings.

Додає:
- trend_mom та trend_yoy у product_analytics
- Нову таблицю procurement_settings
"""

from bot.database.products_engine import products_engine
from bot.database.products_models import ProductsBase, ProcurementSettings
from sqlalchemy import text


async def migrate():
    """Виконати міграцію."""
    print("📁 Використовується БД: databases/products.db")
    
    async with products_engine.begin() as conn:
        # Додати нові колонки у product_analytics (якщо їх немає)
        try:
            await conn.execute(text(
                "ALTER TABLE product_analytics ADD COLUMN trend_mom FLOAT NULL"
            ))
            print("✅ Додано колонку trend_mom")
        except Exception as e:
            if "duplicate column name" in str(e).lower():
                print("⚠️  Колонка trend_mom вже існує")
            else:
                print(f"❌ Помилка додавання trend_mom: {e}")
        
        try:
            await conn.execute(text(
                "ALTER TABLE product_analytics ADD COLUMN trend_yoy FLOAT NULL"
            ))
            print("✅ Додано колонку trend_yoy")
        except Exception as e:
            if "duplicate column name" in str(e).lower():
                print("⚠️  Колонка trend_yoy вже існує")
            else:
                print(f"❌ Помилка додавання trend_yoy: {e}")
    
    # Створити таблицю procurement_settings (якщо її немає)
    async with products_engine.begin() as conn:
        await conn.run_sync(ProductsBase.metadata.create_all, tables=[ProcurementSettings.__table__])
        print("✅ Таблиця procurement_settings створена/перевірена")


if __name__ == "__main__":
    import asyncio
    print("🔄 Початок міграції...")
    asyncio.run(migrate())
    print("✅ Міграція завершена!")
