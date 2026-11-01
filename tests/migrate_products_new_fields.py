"""
Міграція: нові поля у products, product_options та product_analytics.

Додає:
- products: dilovod_id, date_expired, stock_status_id, stock_status,
            sales_qty_60d, sales_qty_90d, sales_qty_180d, sales_qty_360d,
            sales_avg_per_day_60d, sales_avg_per_day_90d, sales_avg_per_day_180d, sales_avg_per_day_360d
- product_options: date_expired, stock_status_id, stock_status,
                   sales_qty_60d, sales_qty_90d, sales_qty_180d, sales_qty_360d,
                   sales_avg_per_day_60d, sales_avg_per_day_90d, sales_avg_per_day_180d, sales_avg_per_day_360d
- product_analytics: days_until_expiry

Безпечно: ігнорує "duplicate column name".
"""

import asyncio
from sqlalchemy import text
from bot.database.products_engine import products_engine


MIGRATIONS: list[tuple[str, str]] = [
    # --- products ---
    ("products", "ALTER TABLE products ADD COLUMN dilovod_id VARCHAR(50) NULL"),
    ("products", "ALTER TABLE products ADD COLUMN date_expired DATE NULL"),
    ("products", "ALTER TABLE products ADD COLUMN stock_status_id INTEGER NULL"),
    ("products", "ALTER TABLE products ADD COLUMN stock_status VARCHAR(100) NULL"),
    ("products", "ALTER TABLE products ADD COLUMN sales_qty_60d INTEGER NULL"),
    ("products", "ALTER TABLE products ADD COLUMN sales_qty_90d INTEGER NULL"),
    ("products", "ALTER TABLE products ADD COLUMN sales_qty_180d INTEGER NULL"),
    ("products", "ALTER TABLE products ADD COLUMN sales_qty_360d INTEGER NULL"),
    ("products", "ALTER TABLE products ADD COLUMN sales_avg_per_day_60d REAL NULL"),
    ("products", "ALTER TABLE products ADD COLUMN sales_avg_per_day_90d REAL NULL"),
    ("products", "ALTER TABLE products ADD COLUMN sales_avg_per_day_180d REAL NULL"),
    ("products", "ALTER TABLE products ADD COLUMN sales_avg_per_day_360d REAL NULL"),

    # --- product_options ---
    ("product_options", "ALTER TABLE product_options ADD COLUMN date_expired DATE NULL"),
    ("product_options", "ALTER TABLE product_options ADD COLUMN stock_status_id INTEGER NULL"),
    ("product_options", "ALTER TABLE product_options ADD COLUMN stock_status VARCHAR(100) NULL"),
    ("product_options", "ALTER TABLE product_options ADD COLUMN sales_qty_60d INTEGER NULL"),
    ("product_options", "ALTER TABLE product_options ADD COLUMN sales_qty_90d INTEGER NULL"),
    ("product_options", "ALTER TABLE product_options ADD COLUMN sales_qty_180d INTEGER NULL"),
    ("product_options", "ALTER TABLE product_options ADD COLUMN sales_qty_360d INTEGER NULL"),
    ("product_options", "ALTER TABLE product_options ADD COLUMN sales_avg_per_day_60d REAL NULL"),
    ("product_options", "ALTER TABLE product_options ADD COLUMN sales_avg_per_day_90d REAL NULL"),
    ("product_options", "ALTER TABLE product_options ADD COLUMN sales_avg_per_day_180d REAL NULL"),
    ("product_options", "ALTER TABLE product_options ADD COLUMN sales_avg_per_day_360d REAL NULL"),

    # --- product_analytics ---
    ("product_analytics", "ALTER TABLE product_analytics ADD COLUMN days_until_expiry INTEGER NULL"),
]


async def migrate() -> None:
    print("📁 Використовується БД продуктів")

    async with products_engine.begin() as conn:
        ok = 0
        skipped = 0
        for table, sql in MIGRATIONS:
            try:
                await conn.execute(text(sql))
                col = sql.split("ADD COLUMN")[1].strip().split()[0]
                print(f"  ✅ {table}.{col}")
                ok += 1
            except Exception as e:
                msg = str(e).lower()
                if "duplicate column name" in msg or "already exists" in msg:
                    col = sql.split("ADD COLUMN")[1].strip().split()[0]
                    print(f"  ⏭  {table}.{col} — вже є")
                    skipped += 1
                else:
                    print(f"  ❌ {sql[:60]}: {e}")

    print(f"\n📊 Результат: {ok} додано, {skipped} пропущено.")


if __name__ == "__main__":
    print("🔄 Міграція products (нові поля фіду)...")
    asyncio.run(migrate())
    print("✅ Міграція завершена!")
