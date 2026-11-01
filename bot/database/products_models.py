from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime,
    Boolean,
    ForeignKey,
    Text,
    Date,
    Index,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship
import enum


ProductsBase = declarative_base()


class ProductStatus(enum.Enum):
    """Статус товару для аналітики закупок."""
    FAST_MOVER = "fast_mover"      # Швидкообіговий товар
    MEDIUM_MOVER = "medium_mover"  # Середній оборот
    SLOW_MOVER = "slow_mover"      # Повільнообіговий товар
    DEAD_STOCK = "dead_stock"      # Неліквід
    NEW = "new"                    # Новий товар (недостатньо даних)
    OUT_OF_STOCK = "out_of_stock"  # Немає в наявності


class ProcurementAction(enum.Enum):
    """Рекомендована дія по закупках."""
    BUY_URGENT = "buy_urgent"  # Закупити терміново (критичний залишок)
    BUY_NORMAL = "buy_normal"  # Закупити (за планом)
    BUY_CAREFUL = "buy_careful"  # Закупити обережно (повільний оборот)
    REDUCE = "reduce"  # Зменшити закуп (надлишок)
    STOP = "stop"  # Не закуповувати (великий залишок)
    SELL_OFF = "sell_off"  # Розпродаж (неліквід)


class ProductCategory(ProductsBase):
    """Категорія товарів з XML-фіда Cosmy."""

    __tablename__ = "product_categories"

    id = Column(Integer, primary_key=True)
    external_id = Column(Integer, unique=True, nullable=False, index=True)
    parent_external_id = Column(Integer, nullable=True, index=True)

    name = Column(String(255), nullable=False)
    name_xml = Column(String(255), nullable=True)
    url = Column(String(500), nullable=True)
    products_count = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    products = relationship("Product", back_populates="category")


class ProductBrand(ProductsBase):
    """Бренд товарів з XML-фіда Cosmy."""

    __tablename__ = "product_brands"

    id = Column(Integer, primary_key=True)
    external_id = Column(Integer, unique=True, nullable=False, index=True)

    name = Column(String(255), nullable=False)
    url = Column(String(500), nullable=True)
    products_count = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    products = relationship("Product", back_populates="brand")


class Product(ProductsBase):
    """Товар з фіда Cosmy (один запис на товар, варіації зберігаються окремо)."""

    __tablename__ = "products"

    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, unique=True, nullable=False, index=True)

    sku = Column(String(100), nullable=True)
    model = Column(String(100), nullable=True)
    model_internal = Column(String(100), nullable=True)
    name = Column(String(500), nullable=False)
    image_url = Column(String(500), nullable=True)

    # Бренд
    brand_id = Column(Integer, ForeignKey("product_brands.id"), nullable=True, index=True)
    brand_external_id = Column(Integer, nullable=True, index=True)
    brand_name = Column(String(255), nullable=True)

    category_id = Column(Integer, ForeignKey("product_categories.id"), nullable=True)
    category_name = Column(String(255), nullable=True)
    subcategory_id = Column(Integer, nullable=True)
    subcategory_name = Column(String(255), nullable=True)
    leaf_category_id = Column(Integer, nullable=True)
    leaf_category_name = Column(String(255), nullable=True)

    category_path_ids = Column(String(255), nullable=True)
    category_path = Column(String(500), nullable=True)

    stock_qty = Column(Integer, nullable=True)
    stock_qty_opencart = Column(Integer, nullable=True)

    # Ідентифікатор в Діловоді (для товарів без варіацій)
    dilovod_id = Column(String(50), nullable=True, index=True)

    # Термін придатності (дата у форматі YYYY-MM-DD)
    date_expired = Column(Date, nullable=True, index=True)

    # Статус наявності
    stock_status_id = Column(Integer, nullable=True)
    stock_status = Column(String(100), nullable=True)

    has_options = Column(Boolean, default=False)
    product_data_mode = Column(String(50), nullable=True)

    sales_qty_1d = Column(Integer, nullable=True)
    sales_qty_7d = Column(Integer, nullable=True)
    sales_qty_30d = Column(Integer, nullable=True)
    sales_qty_60d = Column(Integer, nullable=True)
    sales_qty_90d = Column(Integer, nullable=True)
    sales_qty_180d = Column(Integer, nullable=True)
    sales_qty_360d = Column(Integer, nullable=True)
    sales_avg_per_day_1d = Column(Float, nullable=True)
    sales_avg_per_day_7d = Column(Float, nullable=True)
    sales_avg_per_day_30d = Column(Float, nullable=True)
    sales_avg_per_day_60d = Column(Float, nullable=True)
    sales_avg_per_day_90d = Column(Float, nullable=True)
    sales_avg_per_day_180d = Column(Float, nullable=True)
    sales_avg_per_day_360d = Column(Float, nullable=True)
    last_sale_date = Column(String(50), nullable=True)

    purchase_price = Column(Float, nullable=True)
    selling_price_order = Column(Float, nullable=True)
    selling_price_order_avg_30d = Column(Float, nullable=True)
    selling_price_catalog = Column(Float, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    category = relationship("ProductCategory", back_populates="products")
    brand = relationship("ProductBrand", back_populates="products")
    options = relationship("ProductOption", back_populates="product", cascade="all, delete-orphan")


class ProductOption(ProductsBase):
    """Варіація товару (наприклад, різні об’єми)."""

    __tablename__ = "product_options"

    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)

    product_option_value_id = Column(Integer, nullable=True)
    option_id = Column(Integer, nullable=True)
    option_name = Column(String(255), nullable=True)
    option_value_id = Column(Integer, nullable=True)
    option_value_name = Column(String(255), nullable=True)
    option_model = Column(String(100), nullable=True)
    option_image_url = Column(String(500), nullable=True)

    stock_qty = Column(Integer, nullable=True)
    stock_qty_opencart = Column(Integer, nullable=True)

    # Термін придатності (для варіацій, якщо відрізняється від основного товару)
    date_expired = Column(Date, nullable=True, index=True)

    # Статус наявності варіації
    stock_status_id = Column(Integer, nullable=True)
    stock_status = Column(String(100), nullable=True)

    sales_qty_1d = Column(Integer, nullable=True)
    sales_qty_7d = Column(Integer, nullable=True)
    sales_qty_30d = Column(Integer, nullable=True)
    sales_qty_60d = Column(Integer, nullable=True)
    sales_qty_90d = Column(Integer, nullable=True)
    sales_qty_180d = Column(Integer, nullable=True)
    sales_qty_360d = Column(Integer, nullable=True)
    sales_avg_per_day_1d = Column(Float, nullable=True)
    sales_avg_per_day_7d = Column(Float, nullable=True)
    sales_avg_per_day_30d = Column(Float, nullable=True)
    sales_avg_per_day_60d = Column(Float, nullable=True)
    sales_avg_per_day_90d = Column(Float, nullable=True)
    sales_avg_per_day_180d = Column(Float, nullable=True)
    sales_avg_per_day_360d = Column(Float, nullable=True)
    last_sale_date = Column(String(50), nullable=True)

    purchase_price = Column(Float, nullable=True)
    selling_price_order = Column(Float, nullable=True)
    selling_price_order_avg_30d = Column(Float, nullable=True)
    selling_price_catalog = Column(Float, nullable=True)

    # Ідентифікатор позиції в Діловоді (якщо є зв'язок)
    dilovod_id = Column(String(50), nullable=True, index=True)
    dilovod_model = Column(String(100), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    product = relationship("Product", back_populates="options")


class ProductSalesHistory(ProductsBase):
    """Історія продажів продукту (щоденний зріз для аналітики та прогнозування)."""

    __tablename__ = "product_sales_history"
    __table_args__ = (
        Index('idx_sales_history_product_date', 'product_id', 'date'),
        Index('idx_sales_history_option_date', 'product_option_id', 'date'),
    )

    id = Column(Integer, primary_key=True)
    
    # Посилання на товар або варіацію
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    product_option_id = Column(Integer, ForeignKey("product_options.id", ondelete="CASCADE"), nullable=True, index=True)
    
    # Дата запису (щоденний зріз)
    date = Column(Date, nullable=False, index=True)
    
    # Дані на кінець дня
    stock_qty = Column(Integer, nullable=True)  # Залишок
    sales_qty = Column(Integer, nullable=True)  # Кількість проданих одиниць за день
    revenue = Column(Float, nullable=True)  # Виручка за день
    purchase_price = Column(Float, nullable=True)  # Закупівельна ціна на момент
    selling_price = Column(Float, nullable=True)  # Ціна продажу на момент
    
    created_at = Column(DateTime, default=datetime.utcnow)


class ProductAnalytics(ProductsBase):
    """Аналітичні дані по продукту (розраховуються щоденно)."""

    __tablename__ = "product_analytics"
    __table_args__ = (
        # Унікальність по парі: один рядок на товар (без варіації) або на варіацію
        Index('idx_analytics_product_option_unique', 'product_id', 'product_option_id', unique=True),
        Index('idx_analytics_status', 'status'),
        Index('idx_analytics_action', 'procurement_action'),
        Index('idx_analytics_abc', 'abc_category'),
    )

    id = Column(Integer, primary_key=True)
    
    # Посилання на товар або варіацію
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    product_option_id = Column(Integer, ForeignKey("product_options.id", ondelete="CASCADE"), nullable=True, unique=True, index=True)
    
    # Статус товару
    status = Column(String(50), nullable=True)  # fast_mover, slow_mover, dead_stock, new, out_of_stock
    
    # Оборотність та швидкість продажів
    turnover_days = Column(Integer, nullable=True)  # Кількість днів для повного обороту поточного залишку
    avg_daily_sales = Column(Float, nullable=True)  # Середній продаж на день (за 30 днів)
    avg_daily_sales_7d = Column(Float, nullable=True)  # Середній продаж на день (за 7 днів)
    avg_daily_sales_90d = Column(Float, nullable=True)  # Середній продаж на день (за 90 днів)
    
    # Тренд попиту
    trend_7d_vs_30d = Column(Float, nullable=True)  # Зміна попиту: (7d / 30d) * 100 - 100
    trend_30d_vs_90d = Column(Float, nullable=True)  # Зміна попиту: (30d / 90d) * 100 - 100
    trend_mom = Column(Float, nullable=True)  # Month-over-Month: зміна відносно попереднього місяця
    trend_yoy = Column(Float, nullable=True)  # Year-over-Year: зміна відносно минулого року
    
    # Прогноз попиту
    forecasted_sales_7d = Column(Float, nullable=True)  # Прогноз продажів на 7 днів
    forecasted_sales_14d = Column(Float, nullable=True)  # Прогноз на 14 днів
    forecasted_sales_30d = Column(Float, nullable=True)  # Прогноз на 30 днів
    
    # Рекомендації по закупках
    recommended_purchase_qty = Column(Integer, nullable=True)  # Рекомендована кількість до закупки (базово: 30 днів)
    recommended_purchase_1d = Column(Integer, nullable=True)
    recommended_purchase_7d = Column(Integer, nullable=True)
    recommended_purchase_30d = Column(Integer, nullable=True)
    recommended_purchase_60d = Column(Integer, nullable=True)
    recommended_purchase_90d = Column(Integer, nullable=True)
    recommended_purchase_180d = Column(Integer, nullable=True)
    recommended_purchase_360d = Column(Integer, nullable=True)
    safety_stock_days = Column(Integer, default=14)  # Страховий запас (днів)
    procurement_action = Column(String(50), nullable=True)  # buy_urgent, buy_normal, reduce, stop, sell_off
    
    # Фінансові метрики
    frozen_capital = Column(Float, nullable=True)  # Заморожений капітал (залишок × закупівельна ціна)
    potential_revenue_7d = Column(Float, nullable=True)  # Потенційна виручка за 7 днів
    
    # ABC-аналіз
    abc_category = Column(String(1), nullable=True)  # A, B, C
    revenue_contribution_pct = Column(Float, nullable=True)  # % внеску в загальну виручку
    
    # Дні без продажів
    days_without_sales = Column(Integer, nullable=True)  # Скільки днів немає продажів
    last_sale_date = Column(Date, nullable=True)  # Дата останнього продажу
    
    # Ризики
    risk_out_of_stock_7d = Column(Boolean, default=False)  # Ризик закінчення запасу протягом 7 днів
    is_overstocked = Column(Boolean, default=False)  # Надлишковий запас (>60 днів обороту)

    # Термін придатності
    days_until_expiry = Column(Integer, nullable=True)  # Кількість днів до закінчення терміну придатності (NULL = немає дати)
    
    # Дати оновлення
    calculated_at = Column(DateTime, default=datetime.utcnow)  # Коли розраховано
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ProcurementAlert(ProductsBase):
    """Алерти для системи закупок."""

    __tablename__ = "procurement_alerts"

    id = Column(Integer, primary_key=True)
    
    # Посилання на товар
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    product_option_id = Column(Integer, ForeignKey("product_options.id", ondelete="CASCADE"), nullable=True, index=True)
    
    # Тип алерта
    alert_type = Column(String(50), nullable=False, index=True)  # critical, daily, weekly
    alert_category = Column(String(50), nullable=False)  # out_of_stock, bestseller_low, no_sales_30d, demand_spike, demand_drop, overstock
    
    # Повідомлення
    message = Column(Text, nullable=False)
    severity = Column(String(20), default="info")  # critical, warning, info
    
    # Статус
    is_sent = Column(Boolean, default=False)  # Чи відправлено
    is_read = Column(Boolean, default=False)  # Чи прочитано
    sent_at = Column(DateTime, nullable=True)  # Коли відправлено
    
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class ProductExpiryProfile(ProductsBase):
    """
    Профіль розподілу строків придатності для товару/опції (стабільний ключ — cosmy product_id з фіду).
    Після ручного редагування use_manual_lots=True — синхронізація лише коригує кількість (FEFO).
    """

    __tablename__ = "product_expiry_profiles"
    __table_args__ = (
        UniqueConstraint("cosmy_product_id", "product_option_value_id", name="uq_expiry_profile_sku"),
    )

    id = Column(Integer, primary_key=True)
    cosmy_product_id = Column(Integer, nullable=False, index=True)
    product_option_value_id = Column(Integer, nullable=True, index=True)
    use_manual_lots = Column(Boolean, default=False, nullable=False)
    last_known_stock = Column(Integer, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ProductExpiryLot(ProductsBase):
    """Партія товару з кількістю та (опційно) датою придатності."""

    __tablename__ = "product_expiry_lots"
    __table_args__ = (
        Index("ix_expiry_lots_product_option", "cosmy_product_id", "product_option_value_id"),
    )

    id = Column(Integer, primary_key=True)
    cosmy_product_id = Column(Integer, nullable=False, index=True)
    product_option_value_id = Column(Integer, nullable=True, index=True)
    expiry_date = Column(Date, nullable=True, index=True)
    quantity = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ProcurementSettings(ProductsBase):
    """Налаштування системи закупок."""

    __tablename__ = "procurement_settings"

    id = Column(Integer, primary_key=True)
    
    # Налаштування для категорії або загальні
    category_id = Column(Integer, ForeignKey("product_categories.id", ondelete="CASCADE"), nullable=True, index=True, unique=True)
    
    # Пороги для класифікації товарів
    fast_mover_threshold = Column(Float, default=0.25)    # Продажів/день для Fast Mover
    medium_mover_threshold = Column(Float, default=0.07)  # Продажів/день для Medium Mover (мінімум)
    slow_mover_days = Column(Integer, default=14)         # Днів без продажів для Slow Mover
    dead_stock_days = Column(Integer, default=90)         # Днів без продажів для Dead Stock
    
    # Пороги для рекомендацій
    safety_stock_days = Column(Integer, default=14)  # Страховий запас за замовчуванням
    critical_stock_days = Column(Integer, default=7)  # Критичний рівень залишку
    overstock_days = Column(Integer, default=60)  # Надлишковий запас
    
    # Сезонні коефіцієнти (JSON)
    seasonal_multipliers = Column(Text, nullable=True)  # {"1": 0.8, "6": 1.5} - місяці року
    
    # Пороги для алертів по трендах
    trend_alert_threshold = Column(Float, default=20.0)  # Відсоток зміни для генерації алерту (|trend| >= N%)

    # Пріоритет категорії
    priority = Column(Integer, default=0)  # Вищий пріоритет = важливіша категорія
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

