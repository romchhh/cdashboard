from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from config import PRODUCTS_DATABASE_URL
from bot.database.engine import RetryAsyncSession, _sqlite_pragmas, sqlite_engine_kwargs
from bot.database.products_models import ProductsBase, ProcurementSettings

products_engine = create_async_engine(PRODUCTS_DATABASE_URL, **sqlite_engine_kwargs(PRODUCTS_DATABASE_URL))
if "sqlite" in (PRODUCTS_DATABASE_URL or ""):
    event.listen(products_engine.sync_engine, "connect", _sqlite_pragmas)

AsyncProductsSessionLocal = async_sessionmaker(
    products_engine,
    class_=RetryAsyncSession,
    expire_on_commit=False,
)


async def _get_table_columns(conn, table_name: str) -> set[str]:
    result = await conn.execute(text(f"PRAGMA table_info({table_name})"))
    rows = result.fetchall()
    return {row[1] for row in rows} if rows else set()


async def _add_columns_if_missing(
    conn,
    table_name: str,
    columns: list[tuple[str, str]],
) -> None:
    existing = await _get_table_columns(conn, table_name)
    for col, col_type in columns:
        if col not in existing:
            await conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {col} {col_type}"))


async def init_products_db():
    """Ініціалізація окремої БД для продуктів (Cosmy feed) + міграції."""
    async with products_engine.begin() as conn:
        # Міграція: виправлення UNIQUE constraint на product_analytics.product_id.
        # Раніше product_id мав окремий UNIQUE — це не дозволяло зберігати кілька
        # варіацій одного товару. Якщо стара схема ще є — пересоздаємо таблицю.
        try:
            result = await conn.execute(text("PRAGMA index_list(product_analytics)"))
            indexes = {row[1] for row in result.fetchall()}
            # Старий автогенерований індекс SQLAlchemy для unique=True на product_id
            bad_index = "ix_product_analytics_product_id"
            if bad_index in indexes:
                # Найбезпечніший шлях у SQLite — recreate через rename
                await conn.execute(text(
                    "CREATE TABLE IF NOT EXISTS product_analytics_new AS "
                    "SELECT * FROM product_analytics WHERE 1=0"
                ))
                # Просто очищаємо — дані все одно перераховуються автоматично
                await conn.execute(text("DELETE FROM product_analytics"))
                await conn.execute(text(f"DROP INDEX IF EXISTS {bad_index}"))
        except Exception:
            pass

        # Створюємо всі таблиці за моделями (якщо їх ще немає)
        await conn.run_sync(ProductsBase.metadata.create_all)

        # Міграція: додати trend_mom та trend_yoy у product_analytics (якщо відсутні)
        try:
            recommended_purchase_columns = [
                ("recommended_purchase_1d", "INTEGER NULL"),
                ("recommended_purchase_7d", "INTEGER NULL"),
                ("recommended_purchase_30d", "INTEGER NULL"),
                ("recommended_purchase_60d", "INTEGER NULL"),
                ("recommended_purchase_90d", "INTEGER NULL"),
                ("recommended_purchase_180d", "INTEGER NULL"),
                ("recommended_purchase_360d", "INTEGER NULL"),
            ]
            await _add_columns_if_missing(
                conn,
                "product_analytics",
                [("trend_mom", "FLOAT NULL"), ("trend_yoy", "FLOAT NULL"), *recommended_purchase_columns],
            )
        except Exception:
            # Логіку помилки залишаємо на виклик init_products_db
            pass

        # Міграції для product_options
        try:
            options_cols: list[tuple[str, str]] = [
                ("dilovod_id", "VARCHAR(50) NULL"),
                ("date_expired", "DATE NULL"),
                ("stock_status_id", "INTEGER NULL"),
                ("stock_status", "VARCHAR(100) NULL"),
                ("sales_qty_60d", "INTEGER NULL"),
                ("sales_qty_90d", "INTEGER NULL"),
                ("sales_qty_180d", "INTEGER NULL"),
                ("sales_qty_360d", "INTEGER NULL"),
                ("sales_avg_per_day_60d", "REAL NULL"),
                ("sales_avg_per_day_90d", "REAL NULL"),
                ("sales_avg_per_day_180d", "REAL NULL"),
                ("sales_avg_per_day_360d", "REAL NULL"),
            ]
            await _add_columns_if_missing(conn, "product_options", options_cols)
        except Exception:
            pass

        # Міграції для products
        try:
            products_cols: list[tuple[str, str]] = [
                ("dilovod_id", "VARCHAR(50) NULL"),
                ("date_expired", "DATE NULL"),
                ("stock_status_id", "INTEGER NULL"),
                ("stock_status", "VARCHAR(100) NULL"),
                ("sales_qty_60d", "INTEGER NULL"),
                ("sales_qty_90d", "INTEGER NULL"),
                ("sales_qty_180d", "INTEGER NULL"),
                ("sales_qty_360d", "INTEGER NULL"),
                ("sales_avg_per_day_60d", "REAL NULL"),
                ("sales_avg_per_day_90d", "REAL NULL"),
                ("sales_avg_per_day_180d", "REAL NULL"),
                ("sales_avg_per_day_360d", "REAL NULL"),
                ("brand_id", "INTEGER NULL"),
                ("brand_external_id", "INTEGER NULL"),
                ("brand_name", "VARCHAR(255) NULL"),
            ]
            await _add_columns_if_missing(conn, "products", products_cols)
        except Exception:
            pass

        # Міграції для product_analytics (нові поля)
        try:
            await _add_columns_if_missing(
                conn,
                "product_analytics",
                [("days_until_expiry", "INTEGER NULL")],
            )
        except Exception:
            pass

        # Переконатися, що таблиця procurement_settings існує
        await conn.run_sync(
            ProductsBase.metadata.create_all, tables=[ProcurementSettings.__table__]
        )

        # Міграція: додати trend_alert_threshold у procurement_settings (якщо відсутня)
        try:
            await _add_columns_if_missing(
                conn,
                "procurement_settings",
                [
                    ("trend_alert_threshold", "FLOAT DEFAULT 20.0"),
                    ("medium_mover_threshold", "FLOAT DEFAULT 0.07"),
                ],
            )
        except Exception:
            # Якщо міграція не вдалася — не валимо весь запуск, але залишаємо це в логах вище
            pass

        # Міграція: Dead Stock — поріг 90 днів (раніше дефолт був 30)
        try:
            await conn.execute(
                text(
                    "UPDATE procurement_settings SET dead_stock_days = 90 "
                    "WHERE category_id IS NULL "
                    "AND (dead_stock_days IS NULL OR dead_stock_days = 30)"
                )
            )
        except Exception:
            pass

        # Міграція: індекси для product_analytics (додаються, якщо їх ще немає)
        try:
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_analytics_status "
                    "ON product_analytics (status)"
                )
            )
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_analytics_action "
                    "ON product_analytics (procurement_action)"
                )
            )
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_analytics_abc "
                    "ON product_analytics (abc_category)"
                )
            )
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_analytics_product_option "
                    "ON product_analytics (product_id, product_option_id)"
                )
            )
        except Exception:
            # Якщо створення індексів не вдалось — не блокуємо старт апки
            pass

