"""
Сервіс для ABC-аналізу продуктів та звітності.
"""

import logging
from datetime import datetime, date, timedelta
from typing import List, Dict, Optional, Tuple

from sqlalchemy import select, and_, func, desc, or_, case
from sqlalchemy.orm import joinedload

from bot.database.products_engine import AsyncProductsSessionLocal
from bot.database.products_models import (
    Product,
    ProductOption,
    ProductAnalytics,
    ProductSalesHistory,
    ProductCategory,
    ProductBrand,
    ProcurementSettings,
)

logger = logging.getLogger(__name__)


SUPPORTED_HORIZON_DAYS = {1, 7, 14, 30, 60, 90, 180, 360}


def normalize_horizon_days(days: Optional[int]) -> int:
    try:
        d = int(days or 30)
    except (TypeError, ValueError):
        d = 30
    return d if d in SUPPORTED_HORIZON_DAYS else 30


def _sales_qty_for_days(item, days: int) -> int:
    d = normalize_horizon_days(days)
    if d == 14:
        # У фіді немає окремого 14д: оцінка 2×7д, але не більше ніж продано за 30д
        # (інакше при 7д=1 і 30д=1 виходило б «2 шт за 14д» > «1 шт за 30д»).
        s7 = int(getattr(item, "sales_qty_7d", 0) or 0)
        doubled = s7 * 2
        s30 = getattr(item, "sales_qty_30d", None)
        if s30 is not None:
            return min(doubled, int(s30 or 0))
        return doubled
    field = f"sales_qty_{d}d"
    return int(getattr(item, field, 0) or 0)


def _avg_sales_for_days(item, days: int) -> float:
    d = normalize_horizon_days(days)
    if d == 14:
        # Узгоджено з фактичною кількістю за 14д (після cap до 30д).
        qty = _sales_qty_for_days(item, 14)
        return float(qty) / 14.0 if qty else 0.0
    field = f"sales_avg_per_day_{d}d"
    return float(getattr(item, field, 0) or 0)


def _effective_frozen_capital(item, analytics: ProductAnalytics) -> float:
    """Заморожений капітал: з аналітики або stock × purchase_price."""
    frozen = float(analytics.frozen_capital or 0)
    if frozen > 0:
        return frozen
    stock = int(getattr(item, "stock_qty", 0) or 0)
    price = float(getattr(item, "purchase_price", 0) or 0)
    if stock > 0 and price > 0:
        return float(stock * price)
    return 0.0


def _frozen_capital_sql(stock_col, price_col, frozen_col):
    """SQL-вираз для суми замороженого капіталу з fallback."""
    return case(
        (and_(frozen_col.isnot(None), frozen_col > 0), frozen_col),
        else_=func.coalesce(stock_col, 0) * func.coalesce(price_col, 0),
    )


def recommended_purchase_qty_for_horizon(analytics: ProductAnalytics, days: int) -> int:
    """Рекомендована закупівля на N днів (узгоджено з топ товарами)."""
    d = normalize_horizon_days(days)
    if d == 1:
        return int(analytics.recommended_purchase_1d or 0)
    if d == 7:
        return int(analytics.recommended_purchase_7d or 0)
    if d == 14:
        r7 = int(analytics.recommended_purchase_7d or 0)
        r30 = int(analytics.recommended_purchase_30d or analytics.recommended_purchase_qty or 0)
        return min(r7 * 2, r30)
    if d == 30:
        return int(analytics.recommended_purchase_30d or analytics.recommended_purchase_qty or 0)
    if d == 60:
        return int(analytics.recommended_purchase_60d or 0)
    if d == 90:
        return int(analytics.recommended_purchase_90d or 0)
    if d == 180:
        return int(analytics.recommended_purchase_180d or 0)
    return int(analytics.recommended_purchase_360d or 0)


def forecast_qty_for_horizon(avg_daily: float, horizon_days: int, analytics: ProductAnalytics) -> float:
    """Прогноз продажів на N днів: середній продаж/день × N, з тією ж корекцією за трендом, що в аналітиці."""
    h = normalize_horizon_days(horizon_days)
    if avg_daily <= 0:
        return 0.0
    forecast = avg_daily * float(h)
    t = analytics.trend_7d_vs_30d
    if t is not None and abs(float(t)) > 10:
        forecast *= 1.0 + float(t) / 100.0
    return forecast


def recommended_purchase_with_action(analytics: ProductAnalytics, days: int) -> int:
    """
    Рекомендована закупівля з урахуванням procurement_action.

    - stop / sell_off        → 0 (не закуповувати)
    - reduce                 → 0 (зменшити; надлишок показується окремо через surplus)
    - buy_careful / buy_normal / buy_urgent → значення з БД
    """
    action = analytics.procurement_action or ""
    if action in ("stop", "sell_off", "reduce"):
        return 0
    return recommended_purchase_qty_for_horizon(analytics, days)


def surplus_qty_for_horizon(analytics: ProductAnalytics, days: int) -> int:
    """
    Надлишок закупки для дій 'stop' / 'reduce':
    скільки шт зайве щодо горизонту (від'ємне "закупити").
    Повертає 0 для інших дій.
    """
    action = analytics.procurement_action or ""
    if action not in ("stop", "sell_off", "reduce"):
        return 0
    return recommended_purchase_qty_for_horizon(analytics, days)


def recommendation_row_horizon_view(
    product: Product,
    option: Optional[ProductOption],
    analytics: ProductAnalytics,
    horizon_days: int,
    purchase_horizon_days: Optional[int] = None,
) -> dict:
    """
    Метрики рядка рекомендацій (як у топ товарів):
    - horizon_days — горизонт аналітики: продаж/день, оборотність, прогноз;
    - purchase_horizon_days — горизонт закупки: «Закупити» та surplus; якщо None — той самий, що й horizon_days.
    """
    ha = normalize_horizon_days(horizon_days)
    hp = normalize_horizon_days(purchase_horizon_days) if purchase_horizon_days is not None else ha
    item = option or product
    avg = _avg_sales_for_days(item, ha)
    if option is not None and option.stock_qty is not None:
        stock = int(option.stock_qty or 0)
    else:
        stock = int(product.stock_qty or 0)
    turnover = round(stock / avg, 1) if avg > 0 else None
    return {
        "horizon_days": ha,
        "purchase_horizon_days": hp,
        "avg_daily_sales": avg,
        "turnover_days_display": turnover,
        "recommended_purchase": recommended_purchase_with_action(analytics, hp),
        "surplus_qty": surplus_qty_for_horizon(analytics, hp),
        "forecast_qty": forecast_qty_for_horizon(avg, ha, analytics),
    }


def actual_revenue_for_days(product: Product, option: Optional[ProductOption], days: int) -> float:
    """
    Фактична виручка за N днів: продано (шт) × ціна з каталогу на сайті (selling_price_catalog).
    Для варіації: ціна опції, інакше товару; без підстановки середньої з замовлень.
    """
    item = option or product
    qty = float(_sales_qty_for_days(item, days))
    if option:
        price = option.selling_price_catalog or product.selling_price_catalog
    else:
        price = product.selling_price_catalog
    if qty and price:
        return qty * float(price)
    return 0.0


def actual_revenue_30d(product: Product, option: Optional[ProductOption]) -> float:
    """
    Фактична виручка за останні 30 днів: продано (шт) × каталогова ціна на сайті.
    """
    return actual_revenue_for_days(product, option, 30)


def _sku_model_text(product: Product, option: Optional[ProductOption]) -> str:
    if option and option.option_model:
        return option.option_model
    if option and option.dilovod_model:
        return option.dilovod_model
    return product.model or ""


def _sku_stock_qty(product: Product, option: Optional[ProductOption]) -> int:
    if option is not None and option.stock_qty is not None:
        return int(option.stock_qty or 0)
    return int(product.stock_qty or 0)


def _sku_site_price(product: Product, option: Optional[ProductOption]) -> float:
    item = option or product
    price = getattr(item, "selling_price_catalog", None)
    if price is None and option:
        price = product.selling_price_catalog
    return float(price or 0)


def _sku_purchase_price(product: Product, option: Optional[ProductOption]) -> float:
    if option is not None:
        if option.purchase_price is not None:
            return float(option.purchase_price)
        if product.purchase_price is not None:
            return float(product.purchase_price)
        return 0.0
    if product.purchase_price is not None:
        return float(product.purchase_price)
    return 0.0


def sku_margin_pct(product: Product, option: Optional[ProductOption] = None) -> Optional[float]:
    """Маржа SKU: (ціна продажу − закупівельна) / ціна продажу × 100."""
    if option is not None:
        sell = option.selling_price_catalog if option.selling_price_catalog is not None else product.selling_price_catalog
        buy = option.purchase_price if option.purchase_price is not None else product.purchase_price
    else:
        sell = product.selling_price_catalog
        buy = product.purchase_price
    if sell is None or buy is None:
        return None
    sell_f = float(sell)
    buy_f = float(buy)
    if sell_f <= 0 or buy_f <= 1:
        return None
    return (sell_f - buy_f) / sell_f * 100.0


def _dynamics_trend_pct(item) -> float:
    s30 = float(getattr(item, "sales_qty_30d", None) or 0)
    s60 = float(getattr(item, "sales_qty_60d", None) or 0)
    prev30 = s60 - s30
    if prev30 > 0:
        return (s30 - prev30) / prev30 * 100.0
    return 100.0 if s30 > 0 else 0.0


_STATUS_SORT_ORDER = {
    "fast_mover": 1,
    "medium_mover": 2,
    "slow_mover": 3,
    "new": 4,
    "out_of_stock": 5,
    "dead_stock": 6,
}

_ACTION_SORT_ORDER = {
    "buy_urgent": 1,
    "buy_normal": 2,
    "buy_careful": 3,
    "reduce": 4,
    "stop": 5,
    "sell_off": 6,
}


def sort_sku_catalog_items(
    items: List[Tuple[Product, Optional[ProductOption], ProductAnalytics]],
    sort_by: str,
    horizon_days: int,
    sort_dir: str = "desc",
    purchase_horizon_days: int = 7,
) -> None:
    """In-place сортування каталогу SKU по всьому списку (до пагінації)."""
    normalized_days = normalize_horizon_days(horizon_days)
    purchase_days = normalize_horizon_days(purchase_horizon_days)
    reverse = (sort_dir or "desc").lower() == "desc"

    def sort_key(item: tuple[Product, Optional[ProductOption], ProductAnalytics]):
        product, option, analytics = item
        item_for_sales = option or product
        if sort_by == "frozen_capital":
            return _effective_frozen_capital(item_for_sales, analytics)
        if sort_by == "sales":
            return _sales_qty_for_days(item_for_sales, normalized_days)
        if sort_by == "sales_avg":
            return _avg_sales_for_days(item_for_sales, normalized_days)
        if sort_by in ("turnover", "turnover_slow"):
            stock = _sku_stock_qty(product, option)
            avg = _avg_sales_for_days(item_for_sales, normalized_days)
            if avg <= 0 or stock <= 0:
                return 999999.0
            turnover = stock / avg
            return 999999.0 if turnover < 0.05 else turnover
        if sort_by in ("stock", "stock_low", "stock_high"):
            return _sku_stock_qty(product, option)
        if sort_by == "name":
            return (product.name or "").lower()
        if sort_by == "brand":
            return (product.brand_name or "").lower()
        if sort_by == "model":
            return _sku_model_text(product, option).lower()
        if sort_by == "price":
            return _sku_site_price(product, option)
        if sort_by == "purchase_price":
            buy = _sku_purchase_price(product, option)
            return buy if buy > 1 else -1.0
        if sort_by == "margin":
            m = sku_margin_pct(product, option)
            return m if m is not None else -1.0
        if sort_by == "dynamics":
            return _dynamics_trend_pct(item_for_sales)
        if sort_by == "buy":
            return float(recommended_purchase_with_action(analytics, purchase_days))
        if sort_by == "forecast":
            avg = _avg_sales_for_days(item_for_sales, normalized_days)
            return forecast_qty_for_horizon(avg, normalized_days, analytics)
        if sort_by == "status":
            return _STATUS_SORT_ORDER.get(analytics.status or "", 99)
        return actual_revenue_for_days(product, option, normalized_days)

    if sort_by == "turnover_slow":
        reverse = not reverse
    if sort_by == "stock_low":
        reverse = not reverse
    items.sort(key=sort_key, reverse=reverse)


def sort_recommendation_rows(
    rows: List[Tuple[Product, Optional[ProductOption], ProductAnalytics, dict]],
    sort_by: str,
    sort_dir: str = "desc",
) -> None:
    """In-place сортування рядків рекомендацій (кортеж з hv dict)."""
    reverse = (sort_dir or "desc").lower() == "desc"

    def sort_key(row: tuple[Product, Optional[ProductOption], ProductAnalytics, dict]):
        product, option, analytics, hv = row
        item = option or product
        if sort_by == "name":
            return (product.name or "").lower()
        if sort_by == "model":
            return _sku_model_text(product, option).lower()
        if sort_by == "stock":
            return _sku_stock_qty(product, option)
        if sort_by == "price":
            return _sku_site_price(product, option)
        if sort_by == "purchase_price":
            buy = _sku_purchase_price(product, option)
            return buy if buy > 1 else -1.0
        if sort_by == "turnover":
            t = hv.get("turnover_days_display")
            return float(t) if t is not None else 999999.0
        if sort_by == "sales_avg":
            return float(hv.get("avg_daily_sales") or 0)
        if sort_by == "dynamics":
            return _dynamics_trend_pct(item)
        if sort_by == "buy":
            action = analytics.procurement_action or ""
            if action == "reduce":
                return float(-(hv.get("surplus_qty") or 0))
            if action in ("stop", "sell_off"):
                return 0.0
            return float(hv.get("recommended_purchase") or 0)
        if sort_by == "forecast":
            return float(hv.get("forecast_qty") or 0)
        if sort_by == "status":
            return _STATUS_SORT_ORDER.get(analytics.status or "", 99)
        if sort_by == "action":
            return _ACTION_SORT_ORDER.get(analytics.procurement_action or "", 99)
        return float(hv.get("recommended_purchase") or 0)

    rows.sort(key=sort_key, reverse=reverse)


class ABCAnalysisService:
    """Сервіс для ABC-аналізу продуктів."""

    def __init__(self) -> None:
        # Простий in-memory кеш з TTL
        self._cache: dict[str, tuple[datetime, dict]] = {}
        self._cache_ttl = timedelta(minutes=5)

    def clear_cache(self) -> None:
        """Скидає кеш ABC-аналізу."""
        self._cache.clear()
    
    async def calculate_abc_analysis(self, days: int = 30) -> None:
        """
        Розраховує ABC-аналіз для всіх товарів на основі виручки.
        
        ABC-аналіз:
        - A: 50% виручки (топові товари)
        - B: наступні 30% виручки (середні товари, до 80% суми)
        - C: останні 20% виручки (довгий хвіст)
        
        Args:
            days: Період для аналізу (за замовчуванням 30 днів)
        """
        logger.info("📊 Початок ABC-аналізу...")
        cutoff_date = date.today() - timedelta(days=days)

        async with AsyncProductsSessionLocal() as session:
            # 1. Виручка по товарах
            product_revenue_query = (
                select(
                    ProductSalesHistory.product_id,
                    func.sum(ProductSalesHistory.revenue).label("revenue"),
                )
                .where(ProductSalesHistory.date >= cutoff_date)
                .group_by(ProductSalesHistory.product_id)
            )
            product_revenue_result = await session.execute(product_revenue_query)
            product_revenue_map = {
                row.product_id: row.revenue or 0 for row in product_revenue_result.all()
            }

            # 2. Виручка по опціях
            option_revenue_query = (
                select(
                    ProductSalesHistory.product_option_id,
                    func.sum(ProductSalesHistory.revenue).label("revenue"),
                )
                .where(
                    and_(
                        ProductSalesHistory.date >= cutoff_date,
                        ProductSalesHistory.product_option_id.isnot(None),
                    )
                )
                .group_by(ProductSalesHistory.product_option_id)
            )
            option_revenue_result = await session.execute(option_revenue_query)
            option_revenue_map = {
                row.product_option_id: row.revenue or 0
                for row in option_revenue_result.all()
            }

            # 3. Збираємо всі елементи (товари + опції) з їх аналітикою
            revenue_data: list[tuple[int, Optional[int], float, Optional[ProductAnalytics]]] = []

            products_result = await session.execute(
                select(Product, ProductAnalytics).join(
                    ProductAnalytics,
                    Product.id == ProductAnalytics.product_id,
                    isouter=True,
                )
            )
            for product, analytics in products_result.all():
                revenue = product_revenue_map.get(product.id, 0.0)
                revenue_data.append((product.id, None, revenue, analytics))

            options_result = await session.execute(
                select(ProductOption, ProductAnalytics).join(
                    ProductAnalytics,
                    ProductOption.id == ProductAnalytics.product_option_id,
                    isouter=True,
                )
            )
            for option, analytics in options_result.all():
                revenue = option_revenue_map.get(option.id, 0.0)
                revenue_data.append((option.product_id, option.id, revenue, analytics))

            # 4. Фільтруємо елементи без виручки
            revenue_data = [row for row in revenue_data if row[2] > 0]

            # Сортуємо за виручкою (від більшої до меншої)
            revenue_data.sort(key=lambda x: x[2], reverse=True)

            total_revenue = sum(x[2] for x in revenue_data)
            if total_revenue == 0:
                logger.warning("⚠️ Загальна виручка = 0, ABC-аналіз не можливий")
                return

            # 5. Розподіл по категоріях A, B, C (50 / 30 / 20)
            cumulative_revenue = 0.0
            a_threshold = total_revenue * 0.50
            b_threshold = total_revenue * 0.80

            for product_id, option_id, revenue, analytics in revenue_data:
                cumulative_revenue += revenue
                revenue_pct = (revenue / total_revenue) * 100

                if cumulative_revenue <= a_threshold:
                    category = "A"
                elif cumulative_revenue <= b_threshold:
                    category = "B"
                else:
                    category = "C"

                if analytics:
                    analytics.abc_category = category
                    analytics.revenue_contribution_pct = revenue_pct
                    analytics.updated_at = datetime.utcnow()

            await session.commit()
            logger.info("✅ ABC-аналіз завершено")
    
    async def get_abc_summary(self) -> Dict[str, any]:
        """
        Отримує зведення по ABC-аналізу.
        
        Returns:
            Словник з кількістю товарів та виручкою по категоріях A, B, C
        """
        # Ключ кешу для ABC-зведення один, бо параметрів немає
        cache_key = "abc_summary"
        now = datetime.utcnow()
        cached = self._cache.get(cache_key)
        if cached:
            expires_at, value = cached
            if now < expires_at:
                return value

        async with AsyncProductsSessionLocal() as session:
            # Фактична виручка = sales_qty_Nd × selling_price_catalog
            # Товари без варіацій (product-level analytics, has_options=0)
            _p = func.coalesce(Product.selling_price_catalog, 0)
            q_prod = (
                select(
                    ProductAnalytics.abc_category,
                    func.count(ProductAnalytics.id).label("count"),
                    func.sum(ProductAnalytics.frozen_capital).label("frozen_capital"),
                    func.sum(ProductAnalytics.potential_revenue_7d).label("rev_forecast_7d"),
                    func.sum(func.coalesce(Product.sales_qty_7d, 0) * _p).label("rev_7d"),
                    func.sum(func.coalesce(Product.sales_qty_30d, 0) * _p).label("rev_30d"),
                    func.sum(func.coalesce(Product.sales_qty_90d, 0) * _p).label("rev_90d"),
                    func.sum(func.coalesce(Product.sales_qty_180d, 0) * _p).label("rev_180d"),
                    func.sum(func.coalesce(Product.sales_qty_360d, 0) * _p).label("rev_360d"),
                )
                .join(Product, Product.id == ProductAnalytics.product_id)
                .where(
                    ProductAnalytics.product_option_id.is_(None),
                    Product.has_options == 0,
                )
                .group_by(ProductAnalytics.abc_category)
            )
            # Варіації (option-level analytics)
            _o = func.coalesce(ProductOption.selling_price_catalog, Product.selling_price_catalog, 0)
            q_opt = (
                select(
                    ProductAnalytics.abc_category,
                    func.count(ProductAnalytics.id).label("count"),
                    func.sum(ProductAnalytics.frozen_capital).label("frozen_capital"),
                    func.sum(ProductAnalytics.potential_revenue_7d).label("rev_forecast_7d"),
                    func.sum(func.coalesce(ProductOption.sales_qty_7d, 0) * _o).label("rev_7d"),
                    func.sum(func.coalesce(ProductOption.sales_qty_30d, 0) * _o).label("rev_30d"),
                    func.sum(func.coalesce(ProductOption.sales_qty_90d, 0) * _o).label("rev_90d"),
                    func.sum(func.coalesce(ProductOption.sales_qty_180d, 0) * _o).label("rev_180d"),
                    func.sum(func.coalesce(ProductOption.sales_qty_360d, 0) * _o).label("rev_360d"),
                )
                .join(ProductOption, ProductOption.id == ProductAnalytics.product_option_id)
                .join(Product, Product.id == ProductOption.product_id)
                .where(ProductAnalytics.product_option_id.isnot(None))
                .group_by(ProductAnalytics.abc_category)
            )
            prod_rows = (await session.execute(q_prod)).all()
            opt_rows = (await session.execute(q_opt)).all()

            _empty = lambda: {
                "count": 0, "frozen_capital": 0,
                "rev_forecast_7d": 0,
                "rev_7d": 0, "rev_30d": 0, "rev_90d": 0,
                "rev_180d": 0, "rev_360d": 0,
            }
            summary: Dict[str, dict] = {"A": _empty(), "B": _empty(), "C": _empty()}

            def _merge(rows):
                for row in rows:
                    cat = row.abc_category or "C"
                    s = summary.get(cat) or summary["C"]
                    s["count"]         += int(row.count or 0)
                    s["frozen_capital"] += float(row.frozen_capital or 0)
                    s["rev_forecast_7d"] += float(row.rev_forecast_7d or 0)
                    s["rev_7d"]   += float(row.rev_7d or 0)
                    s["rev_30d"]  += float(row.rev_30d or 0)
                    s["rev_90d"]  += float(row.rev_90d or 0)
                    s["rev_180d"] += float(row.rev_180d or 0)
                    s["rev_360d"] += float(row.rev_360d or 0)

            _merge(prod_rows)
            _merge(opt_rows)

            # Кладемо в кеш
            self._cache[cache_key] = (now + self._cache_ttl, summary)
            return summary


class ProcurementReportsService:
    """Сервіс для генерації звітів по закупках."""

    def __init__(self) -> None:
        # Простий in-memory кеш з TTL
        self._cache: dict[tuple, tuple[datetime, any]] = {}
        self._cache_ttl = timedelta(minutes=5)

    def clear_cache(self) -> None:
        """Скидає кеш всіх звітів."""
        self._cache.clear()
    
    async def get_warehouse_analytics(self) -> Dict[str, any]:
        """
        Отримує загальну аналітику складу.

        Returns:
            Словник з метриками складу, включаючи розбивку по статусах
            та діапазони оборотності.
        """
        cache_key: tuple = ("warehouse_analytics",)
        now = datetime.utcnow()
        cached = self._cache.get(cache_key)
        if cached:
            expires_at, value = cached
            if now < expires_at:
                return value

        async with AsyncProductsSessionLocal() as session:
            # Для кількості товарів та оборотності — product-level записи (один на товар).
            # Для frozen_capital — треба розрізняти:
            #   - товари БЕЗ опцій: беремо з product-level
            #   - товари З опціями: беремо суму з option-level (там реальні залишки)
            product_level = ProductAnalytics.product_option_id.is_(None)
            option_level = ProductAnalytics.product_option_id.isnot(None)

            frozen_expr_prod = _frozen_capital_sql(
                Product.stock_qty, Product.purchase_price, ProductAnalytics.frozen_capital
            )
            frozen_expr_opt = _frozen_capital_sql(
                ProductOption.stock_qty, ProductOption.purchase_price, ProductAnalytics.frozen_capital
            )

            # 1а. Заморожений капітал товарів БЕЗ опцій (product-level, has_options=0)
            frozen_no_options_result = await session.execute(
                select(func.sum(frozen_expr_prod))
                .join(Product, Product.id == ProductAnalytics.product_id)
                .where(product_level, Product.has_options == 0)
            )
            frozen_no_options = frozen_no_options_result.scalar() or 0

            # 1б. Заморожений капітал товарів З опціями (option-level)
            frozen_with_options_result = await session.execute(
                select(func.sum(frozen_expr_opt))
                .join(ProductOption, ProductOption.id == ProductAnalytics.product_option_id)
                .where(option_level)
            )
            frozen_with_options = frozen_with_options_result.scalar() or 0

            total_frozen_capital = frozen_no_options + frozen_with_options

            # 2. Кількість SKU по статусах: без варіацій (product-level) + варіації (option-level)
            status_distribution: Dict[str, int] = {}
            st_no = await session.execute(
                select(ProductAnalytics.status, func.count(ProductAnalytics.id).label("count"))
                .join(Product, Product.id == ProductAnalytics.product_id)
                .where(product_level, Product.has_options == 0)
                .group_by(ProductAnalytics.status)
            )
            for status, cnt in st_no.all():
                status_distribution[status] = int(cnt or 0)
            st_opt = await session.execute(
                select(ProductAnalytics.status, func.count(ProductAnalytics.id).label("count"))
                .join(ProductOption, ProductOption.id == ProductAnalytics.product_option_id)
                .where(option_level)
                .group_by(ProductAnalytics.status)
            )
            for status, cnt in st_opt.all():
                status_distribution[status] = status_distribution.get(status, 0) + int(cnt or 0)

            # 2в. Підрахунок унікальних ТОВАРІВ (product_id) зі статусом out_of_stock
            # - Товари без опцій: рядок у product-level analytics з status=out_of_stock
            oos_no_options_result = await session.execute(
                select(func.count(ProductAnalytics.product_id))
                .join(Product, Product.id == ProductAnalytics.product_id)
                .where(product_level, Product.has_options == 0, ProductAnalytics.status == "out_of_stock")
            )
            oos_no_options = int(oos_no_options_result.scalar() or 0)
            # - Товари з опціями: унікальні product_id де ВСІ варіації мають status=out_of_stock
            # (тобто товар повністю відсутній) — через підзапит із case/sum
            oos_with_options_subq = (
                select(
                    ProductAnalytics.product_id,
                    func.count(ProductAnalytics.id).label("total_sku"),
                    func.sum(
                        case((ProductAnalytics.status == "out_of_stock", 1), else_=0)
                    ).label("oos_sku"),
                )
                .join(Product, Product.id == ProductAnalytics.product_id)
                .where(option_level, Product.has_options == 1)
                .group_by(ProductAnalytics.product_id)
                .subquery()
            )
            oos_with_options_result = await session.execute(
                select(func.count(oos_with_options_subq.c.product_id)).where(
                    oos_with_options_subq.c.total_sku == oos_with_options_subq.c.oos_sku
                )
            )
            oos_with_options = int(oos_with_options_result.scalar() or 0)

            # Товари, у яких ХОЧА Б ОДНА варіація відсутня (але є хоча б одна в наявності)
            oos_partial_result = await session.execute(
                select(func.count(oos_with_options_subq.c.product_id)).where(
                    oos_with_options_subq.c.oos_sku > 0,
                    oos_with_options_subq.c.total_sku > oos_with_options_subq.c.oos_sku,
                )
            )
            oos_partial = int(oos_partial_result.scalar() or 0)

            out_of_stock_products = oos_no_options + oos_with_options

            # 2г. Кількість унікальних товарів (не SKU) по статусах (product-level, без опцій)
            # + унікальні product_id через option-level (за домінуючим статусом)
            products_no_options_result = await session.execute(
                select(func.count(Product.id))
                .where(Product.has_options == 0)
            )
            products_no_options = int(products_no_options_result.scalar() or 0)
            products_with_options_result = await session.execute(
                select(func.count(func.distinct(ProductOption.product_id)))
                .select_from(ProductOption)
                .join(Product, Product.id == ProductOption.product_id)
                .where(Product.has_options == 1)
            )
            products_with_options = int(products_with_options_result.scalar() or 0)
            total_unique_products = products_no_options + products_with_options

            stock_no_opts_result = await session.execute(
                select(func.coalesce(func.sum(Product.stock_qty), 0)).where(Product.has_options == 0)
            )
            stock_with_opts_result = await session.execute(
                select(func.coalesce(func.sum(ProductOption.stock_qty), 0))
                .select_from(ProductOption)
                .join(Product, Product.id == ProductOption.product_id)
                .where(Product.has_options == 1)
            )
            total_stock_qty = int(stock_no_opts_result.scalar() or 0) + int(
                stock_with_opts_result.scalar() or 0
            )

            # 2б. Заморожений капітал по статусах (теж коректно: no_options product-level + options option-level)
            frozen_by_status: Dict[str, float] = {}
            # Для товарів без опцій
            fbs_no_opts = await session.execute(
                select(ProductAnalytics.status, func.sum(frozen_expr_prod))
                .join(Product, Product.id == ProductAnalytics.product_id)
                .where(product_level, Product.has_options == 0)
                .group_by(ProductAnalytics.status)
            )
            for status, frozen in fbs_no_opts.all():
                frozen_by_status[status] = float(frozen or 0)
            # Для товарів з опціями — беремо статус з option-level
            fbs_opts = await session.execute(
                select(ProductAnalytics.status, func.sum(frozen_expr_opt))
                .join(ProductOption, ProductOption.id == ProductAnalytics.product_option_id)
                .where(option_level)
                .group_by(ProductAnalytics.status)
            )
            for status, frozen in fbs_opts.all():
                frozen_by_status[status] = frozen_by_status.get(status, 0) + float(frozen or 0)

            # 3. Середня оборотність по SKU (product без варіацій + option-level)
            avg_no = await session.execute(
                select(
                    func.avg(ProductAnalytics.turnover_days),
                    func.count(ProductAnalytics.id),
                )
                .join(Product, Product.id == ProductAnalytics.product_id)
                .where(
                    product_level,
                    Product.has_options == 0,
                    ProductAnalytics.turnover_days.isnot(None),
                )
            )
            avg_opt = await session.execute(
                select(
                    func.avg(ProductAnalytics.turnover_days),
                    func.count(ProductAnalytics.id),
                ).where(
                    option_level,
                    ProductAnalytics.turnover_days.isnot(None),
                )
            )
            row_no, row_opt = avg_no.one(), avg_opt.one()
            a1, n1 = float(row_no[0] or 0), int(row_no[1] or 0)
            a2, n2 = float(row_opt[0] or 0), int(row_opt[1] or 0)
            avg_turnover = (
                (a1 * n1 + a2 * n2) / (n1 + n2) if (n1 + n2) > 0 else 0.0
            )

            # 4. Розбиття SKU за діапазонами оборотності
            t_no = await session.execute(
                select(ProductAnalytics.turnover_days)
                .join(Product, Product.id == ProductAnalytics.product_id)
                .where(
                    product_level,
                    Product.has_options == 0,
                    ProductAnalytics.turnover_days.isnot(None),
                )
            )
            t_opt = await session.execute(
                select(ProductAnalytics.turnover_days).where(
                    option_level,
                    ProductAnalytics.turnover_days.isnot(None),
                )
            )
            all_turnover = [r[0] for r in t_no.all()] + [r[0] for r in t_opt.all()]

            turnover_ranges = {
                "lt7": sum(1 for d in all_turnover if d < 7),
                "7_30": sum(1 for d in all_turnover if 7 <= d < 30),
                "30_60": sum(1 for d in all_turnover if 30 <= d < 60),
                "gt60": sum(1 for d in all_turnover if d >= 60),
            }

            # 5. Агреговані продажі по асортименту для порівняльної аналітики
            sales_agg_result = await session.execute(
                select(
                    func.sum(Product.sales_qty_30d).label("s30"),
                    func.sum(Product.sales_qty_60d).label("s60"),
                    func.sum(Product.sales_qty_90d).label("s90"),
                    func.sum(Product.sales_qty_180d).label("s180"),
                    func.sum(Product.sales_qty_360d).label("s360"),
                )
            )
            sa = sales_agg_result.one()
            s30  = int(sa.s30  or 0)
            s60  = int(sa.s60  or 0)
            s90  = int(sa.s90  or 0)
            s180 = int(sa.s180 or 0)
            s360 = int(sa.s360 or 0)

            # Попередні блоки = різниця між більшим і меншим горизонтом
            prev30  = s60  - s30   # попередні 30д (31-60)
            prev90  = s180 - s90   # попередні 90д (91-180)
            prev180 = s360 - s180  # попередні 180д (181-360)

            def _pct(cur: int, prev: int) -> float | None:
                if prev > 0:
                    return round((cur - prev) / prev * 100, 1)
                return None

            sales_comparison = {
                "cur30":  s30,  "prev30":  prev30,  "pct30":  _pct(s30, prev30),
                "cur90":  s90,  "prev90":  prev90,  "pct90":  _pct(s90, prev90),
                "cur180": s180, "prev180": prev180, "pct180": _pct(s180, prev180),
            }

            # 6. ЄДИНА ЛОГІКА: продажі (шт) + виручка (продажі × selling_price_catalog; опція → товар)
            # Товари без опцій (has_options=0): беруємо з Product
            # Товари з опціями (has_options=1): беруємо з ProductOption (без Product — щоб не дублювати)
            _sq = lambda c: func.coalesce(c, 0)
            brand_data: Dict[str, Dict] = {}

            def _add_brand(
                brand_id: int,
                name: str,
                *,
                s1: int,
                s7: int,
                s30: int,
                s60: int,
                s90: int,
                s180: int,
                s360: int,
                r1: float,
                r7: float,
                r30: float,
                r60: float,
                r90: float,
                r180: float,
                r360: float,
            ) -> None:
                if name not in brand_data:
                    brand_data[name] = {
                        "brand_id": brand_id,
                        "sales_qty_1d": 0,
                        "sales_qty_7d": 0,
                        "sales_qty_30d": 0,
                        "sales_qty_60d": 0,
                        "sales_qty_90d": 0,
                        "sales_qty_180d": 0,
                        "sales_qty_360d": 0,
                        "revenue_1d": 0.0,
                        "revenue_7d": 0.0,
                        "revenue_30d": 0.0,
                        "revenue_60d": 0.0,
                        "revenue_90d": 0.0,
                        "revenue_180d": 0.0,
                        "revenue_360d": 0.0,
                    }
                b = brand_data[name]
                b["sales_qty_1d"] += s1
                b["sales_qty_7d"] += s7
                b["sales_qty_30d"] += s30
                b["sales_qty_60d"] += s60
                b["sales_qty_90d"] += s90
                b["sales_qty_180d"] += s180
                b["sales_qty_360d"] += s360
                b["revenue_1d"] += r1
                b["revenue_7d"] += r7
                b["revenue_30d"] += r30
                b["revenue_60d"] += r60
                b["revenue_90d"] += r90
                b["revenue_180d"] += r180
                b["revenue_360d"] += r360

            # Товари без опцій: Product.sales_qty_Xd × каталог. Важливо: has_options=1 сюди не потрапляє —
            # для таких товарів рахуємо лише опції нижче (інакше був би подвійний облік з батьком).
            prod_price = func.coalesce(Product.selling_price_catalog, 0)
            prod_rows = await session.execute(
                select(
                    ProductBrand.id,
                    ProductBrand.name,
                    func.sum(_sq(Product.sales_qty_1d)).label("s1"),
                    func.sum(_sq(Product.sales_qty_7d)).label("s7"),
                    func.sum(_sq(Product.sales_qty_30d)).label("s30"),
                    func.sum(_sq(Product.sales_qty_60d)).label("s60"),
                    func.sum(_sq(Product.sales_qty_90d)).label("s90"),
                    func.sum(_sq(Product.sales_qty_180d)).label("s180"),
                    func.sum(_sq(Product.sales_qty_360d)).label("s360"),
                    (func.sum(_sq(Product.sales_qty_1d) * prod_price)).label("r1"),
                    (func.sum(_sq(Product.sales_qty_7d) * prod_price)).label("r7"),
                    (func.sum(_sq(Product.sales_qty_30d) * prod_price)).label("r30"),
                    (func.sum(_sq(Product.sales_qty_60d) * prod_price)).label("r60"),
                    (func.sum(_sq(Product.sales_qty_90d) * prod_price)).label("r90"),
                    (func.sum(_sq(Product.sales_qty_180d) * prod_price)).label("r180"),
                    (func.sum(_sq(Product.sales_qty_360d) * prod_price)).label("r360"),
                )
                .select_from(Product)
                .join(ProductBrand, ProductBrand.id == Product.brand_id)
                .where(and_(Product.has_options == 0, Product.brand_id.isnot(None)))
                .group_by(ProductBrand.id)
            )
            for row in prod_rows.all():
                _add_brand(
                    row.id,
                    row.name,
                    s1=int(row.s1 or 0),
                    s7=int(row.s7 or 0),
                    s30=int(row.s30 or 0),
                    s60=int(row.s60 or 0),
                    s90=int(row.s90 or 0),
                    s180=int(row.s180 or 0),
                    s360=int(row.s360 or 0),
                    r1=float(row.r1 or 0),
                    r7=float(row.r7 or 0),
                    r30=float(row.r30 or 0),
                    r60=float(row.r60 or 0),
                    r90=float(row.r90 or 0),
                    r180=float(row.r180 or 0),
                    r360=float(row.r360 or 0),
                )

            # Товари з опціями: лише ProductOption (батьківський Product.sales_qty_* не додаємо).
            opt_price = func.coalesce(
                ProductOption.selling_price_catalog,
                Product.selling_price_catalog,
                0,
            )
            opt_rows = await session.execute(
                select(
                    ProductBrand.id,
                    ProductBrand.name,
                    func.sum(_sq(ProductOption.sales_qty_1d)).label("s1"),
                    func.sum(_sq(ProductOption.sales_qty_7d)).label("s7"),
                    func.sum(_sq(ProductOption.sales_qty_30d)).label("s30"),
                    func.sum(_sq(ProductOption.sales_qty_60d)).label("s60"),
                    func.sum(_sq(ProductOption.sales_qty_90d)).label("s90"),
                    func.sum(_sq(ProductOption.sales_qty_180d)).label("s180"),
                    func.sum(_sq(ProductOption.sales_qty_360d)).label("s360"),
                    (func.sum(_sq(ProductOption.sales_qty_1d) * opt_price)).label("r1"),
                    (func.sum(_sq(ProductOption.sales_qty_7d) * opt_price)).label("r7"),
                    (func.sum(_sq(ProductOption.sales_qty_30d) * opt_price)).label("r30"),
                    (func.sum(_sq(ProductOption.sales_qty_60d) * opt_price)).label("r60"),
                    (func.sum(_sq(ProductOption.sales_qty_90d) * opt_price)).label("r90"),
                    (func.sum(_sq(ProductOption.sales_qty_180d) * opt_price)).label("r180"),
                    (func.sum(_sq(ProductOption.sales_qty_360d) * opt_price)).label("r360"),
                )
                .select_from(ProductOption)
                .join(Product, Product.id == ProductOption.product_id)
                .join(ProductBrand, ProductBrand.id == Product.brand_id)
                .where(Product.has_options == 1)
                .group_by(ProductBrand.id)
            )
            for row in opt_rows.all():
                _add_brand(
                    row.id,
                    row.name,
                    s1=int(row.s1 or 0),
                    s7=int(row.s7 or 0),
                    s30=int(row.s30 or 0),
                    s60=int(row.s60 or 0),
                    s90=int(row.s90 or 0),
                    s180=int(row.s180 or 0),
                    s360=int(row.s360 or 0),
                    r1=float(row.r1 or 0),
                    r7=float(row.r7 or 0),
                    r30=float(row.r30 or 0),
                    r60=float(row.r60 or 0),
                    r90=float(row.r90 or 0),
                    r180=float(row.r180 or 0),
                    r360=float(row.r360 or 0),
                )

            # 7. Кількість товарів по брендах
            products_count_by_brand: Dict[str, int] = {}
            pc_result = await session.execute(
                select(ProductBrand.name, func.count(Product.id).label("cnt"))
                .select_from(ProductBrand)
                .join(Product, Product.brand_id == ProductBrand.id)
                .group_by(ProductBrand.id)
            )
            for row in pc_result.all():
                products_count_by_brand[row.name] = int(row.cnt or 0)

            # 7б. SKU по брендах: товари без опцій (1 = 1 SKU) + кожна опція для has_options=1
            sku_count_by_brand: Dict[str, int] = {}
            sku_no_rows = await session.execute(
                select(ProductBrand.name, func.count(Product.id).label("cnt"))
                .select_from(ProductBrand)
                .join(Product, Product.brand_id == ProductBrand.id)
                .where(Product.has_options == 0)
                .group_by(ProductBrand.id)
            )
            for row in sku_no_rows.all():
                sku_count_by_brand[row.name] = int(row.cnt or 0)
            sku_opt_rows = await session.execute(
                select(ProductBrand.name, func.count(ProductOption.id).label("cnt"))
                .select_from(ProductOption)
                .join(Product, Product.id == ProductOption.product_id)
                .join(ProductBrand, ProductBrand.id == Product.brand_id)
                .where(Product.has_options == 1)
                .group_by(ProductBrand.id)
            )
            for row in sku_opt_rows.all():
                sku_count_by_brand[row.name] = sku_count_by_brand.get(row.name, 0) + int(row.cnt or 0)

            # 7в. Залишок (шт) по брендах — та сама логіка, що для total_stock_qty
            stock_qty_by_brand: Dict[str, int] = {}
            stock_no_rows = await session.execute(
                select(ProductBrand.name, func.coalesce(func.sum(Product.stock_qty), 0).label("qty"))
                .select_from(ProductBrand)
                .join(Product, Product.brand_id == ProductBrand.id)
                .where(Product.has_options == 0)
                .group_by(ProductBrand.id)
            )
            for row in stock_no_rows.all():
                stock_qty_by_brand[row.name] = int(row.qty or 0)
            stock_opt_rows = await session.execute(
                select(ProductBrand.name, func.coalesce(func.sum(ProductOption.stock_qty), 0).label("qty"))
                .select_from(ProductOption)
                .join(Product, Product.id == ProductOption.product_id)
                .join(ProductBrand, ProductBrand.id == Product.brand_id)
                .where(Product.has_options == 1)
                .group_by(ProductBrand.id)
            )
            for row in stock_opt_rows.all():
                stock_qty_by_brand[row.name] = stock_qty_by_brand.get(row.name, 0) + int(row.qty or 0)

            sku_in_stock_by_brand: Dict[str, int] = {}
            products_in_stock_by_brand: Dict[str, int] = {}
            sku_no_stock_rows = await session.execute(
                select(ProductBrand.name, func.count(Product.id).label("cnt"))
                .select_from(ProductBrand)
                .join(Product, Product.brand_id == ProductBrand.id)
                .where(Product.has_options == 0, func.coalesce(Product.stock_qty, 0) > 0)
                .group_by(ProductBrand.id)
            )
            for row in sku_no_stock_rows.all():
                sku_in_stock_by_brand[row.name] = int(row.cnt or 0)
                products_in_stock_by_brand[row.name] = int(row.cnt or 0)
            sku_opt_stock_rows = await session.execute(
                select(ProductBrand.name, func.count(ProductOption.id).label("cnt"))
                .select_from(ProductOption)
                .join(Product, Product.id == ProductOption.product_id)
                .join(ProductBrand, ProductBrand.id == Product.brand_id)
                .where(Product.has_options == 1, func.coalesce(ProductOption.stock_qty, 0) > 0)
                .group_by(ProductBrand.id)
            )
            for row in sku_opt_stock_rows.all():
                sku_in_stock_by_brand[row.name] = sku_in_stock_by_brand.get(row.name, 0) + int(row.cnt or 0)
            prod_opt_stock_rows = await session.execute(
                select(ProductBrand.name, func.count(func.distinct(Product.id)).label("cnt"))
                .select_from(ProductOption)
                .join(Product, Product.id == ProductOption.product_id)
                .join(ProductBrand, ProductBrand.id == Product.brand_id)
                .where(Product.has_options == 1, func.coalesce(ProductOption.stock_qty, 0) > 0)
                .group_by(ProductBrand.id)
            )
            for row in prod_opt_stock_rows.all():
                products_in_stock_by_brand[row.name] = products_in_stock_by_brand.get(row.name, 0) + int(row.cnt or 0)

            # Середня маржа по брендах (унікальний товар: для опцій — середнє по варіантах)
            margin_stats_by_brand = await self._compute_margin_stats_by_brand(session)

            # 8. Заморожений капітал та avg_daily_sales — з ProductAnalytics (product-level + option-level)
            # Як для total_frozen_capital: товари без опцій — product-level; з опціями — тільки option-level
            # (батьківський product-level рядок не додаємо, щоб не дублювати залишок).
            frozen_by_brand: Dict[str, float] = {}
            avg_sales_by_brand: Dict[str, float] = {}

            # Product-level analytics (лише товари без варіацій)
            frozen_prod = await session.execute(
                select(
                    ProductBrand.name,
                    func.sum(ProductAnalytics.frozen_capital).label("frozen"),
                    func.avg(ProductAnalytics.avg_daily_sales).label("avg_sales"),
                )
                .select_from(ProductAnalytics)
                .join(Product, Product.id == ProductAnalytics.product_id)
                .join(ProductBrand, ProductBrand.id == Product.brand_id)
                .where(
                    ProductAnalytics.product_option_id.is_(None),
                    Product.has_options == 0,
                )
                .group_by(ProductBrand.id)
            )
            for row in frozen_prod.all():
                frozen_by_brand[row.name] = float(row.frozen or 0)
                avg_sales_by_brand[row.name] = float(row.avg_sales or 0)

            # Option-level analytics
            frozen_opt = await session.execute(
                select(
                    ProductBrand.name,
                    func.sum(ProductAnalytics.frozen_capital).label("frozen"),
                    func.avg(ProductAnalytics.avg_daily_sales).label("avg_sales"),
                )
                .select_from(ProductAnalytics)
                .join(ProductOption, ProductOption.id == ProductAnalytics.product_option_id)
                .join(Product, Product.id == ProductOption.product_id)
                .join(ProductBrand, ProductBrand.id == Product.brand_id)
                .where(ProductAnalytics.product_option_id.isnot(None))
                .group_by(ProductBrand.id)
            )
            for row in frozen_opt.all():
                frozen_by_brand[row.name] = frozen_by_brand.get(row.name, 0) + float(row.frozen or 0)
                opt_avg = float(row.avg_sales or 0)
                if row.name in avg_sales_by_brand:
                    avg_sales_by_brand[row.name] = (avg_sales_by_brand[row.name] + opt_avg) / 2
                else:
                    avg_sales_by_brand[row.name] = opt_avg

            # Збираємо brand_agg (усі бренди з продуктами)
            brand_agg: List[Dict] = []
            for name, data in brand_data.items():
                s30 = data["sales_qty_30d"]
                r30 = data["revenue_30d"]
                frozen = frozen_by_brand.get(name, 0.0)
                margin_info = margin_stats_by_brand.get(name, {})
                brand_agg.append({
                        "brand_id": data.get("brand_id"),
                        "name": name,
                        "products_count": products_count_by_brand.get(name, 0),
                        "sku_count": sku_count_by_brand.get(name, 0),
                        "avg_margin_pct": margin_info.get("avg_margin_pct"),
                        "margin_products_count": margin_info.get("margin_products_count", 0),
                        "stock_qty": stock_qty_by_brand.get(name, 0),
                        "sku_in_stock": sku_in_stock_by_brand.get(name, 0),
                        "products_in_stock": products_in_stock_by_brand.get(name, 0),
                        "sales_qty_1d": data["sales_qty_1d"],
                        "sales_qty_7d": data["sales_qty_7d"],
                        "sales_qty_30d": data["sales_qty_30d"],
                        "sales_qty_60d": data["sales_qty_60d"],
                        "sales_qty_90d": data["sales_qty_90d"],
                        "sales_qty_180d": data["sales_qty_180d"],
                        "sales_qty_360d": data["sales_qty_360d"],
                        "revenue_1d": data["revenue_1d"],
                        "revenue_7d": data["revenue_7d"],
                        "revenue_30d": data["revenue_30d"],
                        "revenue_60d": data["revenue_60d"],
                        "revenue_90d": data["revenue_90d"],
                        "revenue_180d": data["revenue_180d"],
                        "revenue_360d": data["revenue_360d"],
                        "frozen_capital": frozen,
                        "avg_daily_sales": avg_sales_by_brand.get(name, 0.0),
                    })

            # Для сторінки брендів — повний список, відсортований за виручкою 30д
            all_brands = sorted(brand_agg, key=lambda x: x.get("revenue_30d", 0), reverse=True)

            # ТОП за виручкою: тільки бренди з виручкою > 0, об'єднання топ-10 по кожному періоду
            def _top_n_by_period(agg: List[Dict], key: str, n: int = 10) -> set:
                return {b["name"] for b in sorted(agg, key=lambda x: x.get(key, 0), reverse=True)[:n]}
            brands_with_revenue = [b for b in brand_agg if any((
                b.get("revenue_1d", 0) > 0,
                b.get("revenue_7d", 0) > 0,
                b.get("revenue_30d", 0) > 0,
                b.get("revenue_60d", 0) > 0,
                b.get("revenue_90d", 0) > 0,
                b.get("revenue_180d", 0) > 0,
                b.get("revenue_360d", 0) > 0,
            ))]
            revenue_names = (
                _top_n_by_period(brands_with_revenue, "revenue_1d")
                | _top_n_by_period(brands_with_revenue, "revenue_7d")
                | _top_n_by_period(brands_with_revenue, "revenue_30d")
                | _top_n_by_period(brands_with_revenue, "revenue_60d")
                | _top_n_by_period(brands_with_revenue, "revenue_90d")
                | _top_n_by_period(brands_with_revenue, "revenue_180d")
                | _top_n_by_period(brands_with_revenue, "revenue_360d")
            )
            top_brands_by_revenue = sorted(
                [b for b in brand_agg if b["name"] in revenue_names],
                key=lambda x: x["revenue_7d"],  # початковий порядок для табу 7д
                reverse=True,
            )[:30]

            # ТОП за продажами: об'єднання топ-10 по кожному періоду
            sales_names = (
                _top_n_by_period(brand_agg, "sales_qty_7d")
                | _top_n_by_period(brand_agg, "sales_qty_30d")
                | _top_n_by_period(brand_agg, "sales_qty_90d")
                | _top_n_by_period(brand_agg, "sales_qty_180d")
                | _top_n_by_period(brand_agg, "sales_qty_360d")
            )
            top_brands_by_speed = sorted(
                [b for b in brand_agg if b["name"] in sales_names],
                key=lambda x: x["sales_qty_7d"],  # початковий порядок для табу 7д
                reverse=True,
            )[:30]

            top_brands_by_frozen = sorted(
                brand_agg, key=lambda x: x["frozen_capital"], reverse=True
            )[:10]

            ps_result = await session.execute(
                select(ProcurementSettings).where(ProcurementSettings.category_id.is_(None))
            )
            ps = ps_result.scalar_one_or_none()
            status_rules = {
                "avg_sales_basis_days": 30,
                "fast_mover_threshold": float(ps.fast_mover_threshold)
                if ps and ps.fast_mover_threshold is not None
                else 0.25,
                "medium_mover_threshold": float(getattr(ps, "medium_mover_threshold", None) or 0.07)
                if ps
                else 0.07,
                "slow_mover_days_without_sales": int(ps.slow_mover_days)
                if ps and ps.slow_mover_days is not None
                else 14,
                "dead_stock_days_without_sales": int(ps.dead_stock_days)
                if ps and ps.dead_stock_days is not None
                else 90,
                "new_max_history_records": 7,
                "sales_history_window_days": 90,
            }

            result = {
                "total_frozen_capital": total_frozen_capital,
                "status_distribution": status_distribution,
                "status_rules": status_rules,
                "frozen_by_status": frozen_by_status,
                "avg_turnover_days": avg_turnover,
                "turnover_ranges": turnover_ranges,
                # total_products = унікальні товари (не SKU/варіації)
                "total_products": total_unique_products,
                "total_sku": sum(status_distribution.values()),
                "total_stock_qty": total_stock_qty,
                # out_of_stock: скільки унікальних товарів повністю без залишку
                "out_of_stock_products": out_of_stock_products,
                # скільки товарів з опціями мають лише ЧАСТИНУ варіацій відсутніх
                "out_of_stock_partial_products": oos_partial,
                "sales_comparison": sales_comparison,
                "top_brands_by_revenue": top_brands_by_revenue,
                "top_brands_by_frozen_capital": top_brands_by_frozen,
                "top_brands_by_speed": top_brands_by_speed,
                "all_brands": all_brands,
            }

            # Кешуємо результат
            self._cache[cache_key] = (now + self._cache_ttl, result)
            return result

    async def _compute_margin_stats_by_brand(self, session) -> Dict[str, Dict[str, float | int | None]]:
        """
        Середня маржа бренду = середнє арифметичне маржі унікальних товарів у наявності.

        У розрахунок потрапляють лише SKU з залишком > 0.
        Для товару з опціями: середнє по варіантах у наявності; якщо всі 0 шт — товар не рахується.
        """
        from collections import defaultdict

        product_margins: Dict[int, list[float]] = defaultdict(list)
        product_brand: Dict[int, str] = {}

        opt_rows = await session.execute(
            select(
                ProductBrand.name,
                Product.id,
                ProductOption.stock_qty,
                ProductOption.selling_price_catalog,
                ProductOption.purchase_price,
                Product.selling_price_catalog,
                Product.purchase_price,
            )
            .select_from(ProductOption)
            .join(Product, Product.id == ProductOption.product_id)
            .join(ProductBrand, ProductBrand.id == Product.brand_id)
            .where(Product.has_options == 1)
        )
        for brand_name, product_id, stock_qty, o_sell, o_buy, p_sell, p_buy in opt_rows.all():
            if int(stock_qty or 0) <= 0:
                continue
            sell = o_sell if o_sell is not None else p_sell
            buy = o_buy if o_buy is not None else p_buy
            if sell is None or buy is None or float(sell) <= 0 or float(buy) <= 1:
                continue
            margin = (float(sell) - float(buy)) / float(sell) * 100.0
            product_margins[product_id].append(margin)
            product_brand[product_id] = brand_name

        prod_rows = await session.execute(
            select(
                ProductBrand.name,
                Product.id,
                Product.stock_qty,
                Product.selling_price_catalog,
                Product.purchase_price,
            )
            .select_from(Product)
            .join(ProductBrand, ProductBrand.id == Product.brand_id)
            .where(Product.has_options == 0)
        )
        for brand_name, product_id, stock_qty, sell, buy in prod_rows.all():
            if int(stock_qty or 0) <= 0:
                continue
            if sell is None or buy is None or float(sell) <= 0 or float(buy) <= 1:
                continue
            margin = (float(sell) - float(buy)) / float(sell) * 100.0
            product_margins[product_id] = [margin]
            product_brand[product_id] = brand_name

        brand_values: Dict[str, list[float]] = defaultdict(list)
        for product_id, margins in product_margins.items():
            brand_name = product_brand.get(product_id)
            if not brand_name or not margins:
                continue
            brand_values[brand_name].append(sum(margins) / len(margins))

        result: Dict[str, Dict[str, float | int | None]] = {}
        for brand_name, values in brand_values.items():
            result[brand_name] = {
                "avg_margin_pct": sum(values) / len(values),
                "margin_products_count": len(values),
            }
        return result

    async def get_brand_detail(self, brand_id: int) -> Optional[Dict[str, any]]:
        """
        Отримує детальну аналітику по бренду.
        Returns:
            Словник з метриками бренду або None якщо бренд не знайдено
        """
        async with AsyncProductsSessionLocal() as session:
            # Перевірка існування бренду
            brand_result = await session.execute(
                select(ProductBrand).where(ProductBrand.id == brand_id)
            )
            brand = brand_result.scalar_one_or_none()
            if not brand:
                return None

            _sq = lambda c: func.coalesce(c, 0)

            # Кількість унікальних товарів (один рядок на товар, незалежно від опцій)
            products_count = await session.execute(
                select(func.count(Product.id)).where(Product.brand_id == brand_id)
            )
            products_count = products_count.scalar() or 0

            # Кількість SKU: товари без опцій + кожна опція окремо
            sku_no_options = await session.execute(
                select(func.count(Product.id)).where(Product.brand_id == brand_id, Product.has_options == 0)
            )
            sku_with_options = await session.execute(
                select(func.count(ProductOption.id))
                .join(Product, Product.id == ProductOption.product_id)
                .where(Product.brand_id == brand_id, Product.has_options == 1)
            )
            sku_count = (sku_no_options.scalar() or 0) + (sku_with_options.scalar() or 0)

            # Продажі (шт) та виручка (шт × каталогова ціна) — Product без опцій
            prod_price = func.coalesce(Product.selling_price_catalog, 0)
            prod_row = await session.execute(
                select(
                    func.sum(_sq(Product.sales_qty_7d)).label("s7"),
                    func.sum(_sq(Product.sales_qty_30d)).label("s30"),
                    func.sum(_sq(Product.sales_qty_60d)).label("s60"),
                    func.sum(_sq(Product.sales_qty_90d)).label("s90"),
                    func.sum(_sq(Product.sales_qty_180d)).label("s180"),
                    func.sum(_sq(Product.sales_qty_360d)).label("s360"),
                    (func.sum(_sq(Product.sales_qty_7d) * prod_price)).label("r7"),
                    (func.sum(_sq(Product.sales_qty_30d) * prod_price)).label("r30"),
                    (func.sum(_sq(Product.sales_qty_60d) * prod_price)).label("r60"),
                    (func.sum(_sq(Product.sales_qty_90d) * prod_price)).label("r90"),
                    (func.sum(_sq(Product.sales_qty_180d) * prod_price)).label("r180"),
                    (func.sum(_sq(Product.sales_qty_360d) * prod_price)).label("r360"),
                )
                .select_from(Product)
                .where(and_(Product.brand_id == brand_id, Product.has_options == 0))
            )
            pr = prod_row.one_or_none()

            # Опції
            opt_price = func.coalesce(
                ProductOption.selling_price_catalog,
                Product.selling_price_catalog,
                0,
            )
            opt_row = await session.execute(
                select(
                    func.sum(_sq(ProductOption.sales_qty_7d)).label("s7"),
                    func.sum(_sq(ProductOption.sales_qty_30d)).label("s30"),
                    func.sum(_sq(ProductOption.sales_qty_60d)).label("s60"),
                    func.sum(_sq(ProductOption.sales_qty_90d)).label("s90"),
                    func.sum(_sq(ProductOption.sales_qty_180d)).label("s180"),
                    func.sum(_sq(ProductOption.sales_qty_360d)).label("s360"),
                    (func.sum(_sq(ProductOption.sales_qty_7d) * opt_price)).label("r7"),
                    (func.sum(_sq(ProductOption.sales_qty_30d) * opt_price)).label("r30"),
                    (func.sum(_sq(ProductOption.sales_qty_60d) * opt_price)).label("r60"),
                    (func.sum(_sq(ProductOption.sales_qty_90d) * opt_price)).label("r90"),
                    (func.sum(_sq(ProductOption.sales_qty_180d) * opt_price)).label("r180"),
                    (func.sum(_sq(ProductOption.sales_qty_360d) * opt_price)).label("r360"),
                )
                .select_from(ProductOption)
                .join(Product, Product.id == ProductOption.product_id)
                .where(Product.brand_id == brand_id, Product.has_options == 1)
            )
            opr = opt_row.one_or_none()

            # Заморожений капітал: product-level лише без варіацій + option-level (див. get_warehouse_analytics)
            frozen_prod = await session.execute(
                select(func.sum(ProductAnalytics.frozen_capital))
                .join(Product, Product.id == ProductAnalytics.product_id)
                .where(
                    Product.brand_id == brand_id,
                    ProductAnalytics.product_option_id.is_(None),
                    Product.has_options == 0,
                )
            )
            frozen_opt = await session.execute(
                select(func.sum(ProductAnalytics.frozen_capital))
                .join(ProductOption, ProductOption.id == ProductAnalytics.product_option_id)
                .join(Product, Product.id == ProductOption.product_id)
                .where(Product.brand_id == brand_id, ProductAnalytics.product_option_id.isnot(None))
            )
            frozen_capital = (frozen_prod.scalar() or 0) + (frozen_opt.scalar() or 0)

            # Товари бренду для таблиці (product-level + option-level)
            products_list: List[Dict] = []
            # Товари без опцій
            prod_items = await session.execute(
                select(Product, ProductAnalytics)
                .outerjoin(ProductAnalytics, and_(
                    ProductAnalytics.product_id == Product.id,
                    ProductAnalytics.product_option_id.is_(None),
                ))
                .where(Product.brand_id == brand_id, Product.has_options == 0)
                .order_by(Product.name)
            )
            def _sales(item) -> dict:
                p = item.get("product")
                o = item.get("option")
                return {
                    "sales_1d": (o.sales_qty_1d if o and o.sales_qty_1d is not None else p.sales_qty_1d) or 0,
                    "sales_7d": (o.sales_qty_7d if o and o.sales_qty_7d is not None else p.sales_qty_7d) or 0,
                    "sales_30d": (o.sales_qty_30d if o and o.sales_qty_30d is not None else p.sales_qty_30d) or 0,
                    "sales_60d": (o.sales_qty_60d if o and o.sales_qty_60d is not None else p.sales_qty_60d) or 0,
                    "sales_90d": (o.sales_qty_90d if o and o.sales_qty_90d is not None else p.sales_qty_90d) or 0,
                    "sales_180d": (o.sales_qty_180d if o and o.sales_qty_180d is not None else p.sales_qty_180d) or 0,
                    "sales_360d": (o.sales_qty_360d if o and o.sales_qty_360d is not None else p.sales_qty_360d) or 0,
                }

            for product, analytics in prod_items.all():
                item = {"product": product, "option": None, "analytics": analytics}
                item["stock"] = _sku_stock_qty(product, None)
                item["frozen"] = (analytics.frozen_capital or 0) if analytics else 0
                item.update(_sales(item))
                products_list.append(item)

            # Товари з опціями
            opt_items = await session.execute(
                select(Product, ProductOption, ProductAnalytics)
                .join(ProductOption, Product.id == ProductOption.product_id)
                .outerjoin(ProductAnalytics, ProductAnalytics.product_option_id == ProductOption.id)
                .where(Product.brand_id == brand_id, Product.has_options == 1)
                .order_by(Product.name, ProductOption.option_value_name)
            )
            for product, option, analytics in opt_items.all():
                item = {"product": product, "option": option, "analytics": analytics}
                item["stock"] = _sku_stock_qty(product, option)
                item["frozen"] = (analytics.frozen_capital or 0) if analytics else 0
                item.update(_sales(item))
                products_list.append(item)

            def _val(r, k):
                return int(getattr(r, k, None) or 0) if r else 0

            def _rev(r, k):
                return float(getattr(r, k, None) or 0) if r else 0

            stock_qty = sum(int(item.get("stock") or 0) for item in products_list)
            sku_in_stock = sum(1 for item in products_list if int(item.get("stock") or 0) > 0)
            products_in_stock = len({
                item["product"].id
                for item in products_list
                if int(item.get("stock") or 0) > 0
            })

            return {
                "brand": brand,
                "products_count": products_count,
                "sku_count": sku_count,
                "stock_qty": stock_qty,
                "sku_in_stock": sku_in_stock,
                "products_in_stock": products_in_stock,
                "sales_qty_7d": _val(pr, "s7") + _val(opr, "s7"),
                "sales_qty_30d": _val(pr, "s30") + _val(opr, "s30"),
                "sales_qty_60d": _val(pr, "s60") + _val(opr, "s60"),
                "sales_qty_90d": _val(pr, "s90") + _val(opr, "s90"),
                "sales_qty_180d": _val(pr, "s180") + _val(opr, "s180"),
                "sales_qty_360d": _val(pr, "s360") + _val(opr, "s360"),
                "revenue_7d": _rev(pr, "r7") + _rev(opr, "r7"),
                "revenue_30d": _rev(pr, "r30") + _rev(opr, "r30"),
                "revenue_60d": _rev(pr, "r60") + _rev(opr, "r60"),
                "revenue_90d": _rev(pr, "r90") + _rev(opr, "r90"),
                "revenue_180d": _rev(pr, "r180") + _rev(opr, "r180"),
                "revenue_360d": _rev(pr, "r360") + _rev(opr, "r360"),
                "frozen_capital": float(frozen_capital),
                "products": products_list,
            }
    
    async def _enumerate_sku_catalog_items(
        self,
        session,
        *,
        category_id: Optional[int] = None,
        brand_external_id: Optional[int] = None,
    ) -> List[Tuple[Product, Optional[ProductOption], ProductAnalytics]]:
        items: list[tuple[Product, Optional[ProductOption], ProductAnalytics]] = []

        base_filter = []
        if category_id:
            base_filter.append(Product.category_id == category_id)
        if brand_external_id:
            base_filter.append(Product.brand_external_id == brand_external_id)

        option_query = (
            select(Product, ProductOption, ProductAnalytics)
            .join(ProductOption, Product.id == ProductOption.product_id)
            .join(ProductAnalytics, ProductOption.id == ProductAnalytics.product_option_id)
        )
        if base_filter:
            option_query = option_query.where(and_(*base_filter))

        option_result = await session.execute(option_query)
        for product, option, analytics in option_result.all():
            items.append((product, option, analytics))

        product_query = (
            select(Product, ProductAnalytics)
            .join(ProductAnalytics, Product.id == ProductAnalytics.product_id)
            .where(
                ProductAnalytics.product_option_id.is_(None),
                or_(
                    Product.has_options.is_(False),
                    Product.has_options.is_(None),
                    Product.product_data_mode.is_(None),
                    Product.product_data_mode != "aggregate_by_options",
                ),
            )
        )
        if base_filter:
            product_query = product_query.where(and_(*base_filter))

        product_result = await session.execute(product_query)
        for product, analytics in product_result.all():
            items.append((product, None, analytics))

        return items

    async def enumerate_sku_catalog_items(
        self,
        *,
        category_id: Optional[int] = None,
        brand_external_id: Optional[int] = None,
    ) -> List[Tuple[Product, Optional[ProductOption], ProductAnalytics]]:
        """Усі SKU з аналітикою (опції окремо + товари без опцій) — той самий набір, що й ТОП."""
        async with AsyncProductsSessionLocal() as session:
            return await self._enumerate_sku_catalog_items(
                session,
                category_id=category_id,
                brand_external_id=brand_external_id,
            )

    async def get_top_products(
        self,
        sort_by: str = "revenue",
        horizon_days: int = 30,
        category_id: Optional[int] = None,
        brand_external_id: Optional[int] = None,
        sort_dir: str = "desc",
        purchase_horizon_days: int = 7,
    ) -> List[Tuple[Product, Optional[ProductOption], ProductAnalytics]]:
        """
        Отримує топові продукти за різними критеріями.
        
        Args:
            limit: Кількість товарів
            sort_by: Критерій сортування ("revenue", "frozen_capital", "sales")
            category_id: Фільтр по категорії (опціонально)
        
        Returns:
            Список кортежів (Product, ProductAnalytics)
        """
        async with AsyncProductsSessionLocal() as session:
            items = await self._enumerate_sku_catalog_items(
                session,
                category_id=category_id,
                brand_external_id=brand_external_id,
            )
            sort_sku_catalog_items(
                items,
                sort_by,
                horizon_days,
                sort_dir=sort_dir,
                purchase_horizon_days=purchase_horizon_days,
            )
            return items
    
    async def get_category_turnover(self) -> List[Dict[str, any]]:
        """
        Отримує оборотність по брендах (раніше — по категоріях).
        
        Returns:
            Список словників з даними по брендах
        """
        async with AsyncProductsSessionLocal() as session:
            pc_result = await session.execute(
                select(
                    ProductBrand.id,
                    ProductBrand.name,
                    ProductBrand.external_id,
                    func.count(Product.id).label("products_count"),
                )
                .select_from(ProductBrand)
                .join(Product, Product.brand_id == ProductBrand.id)
                .group_by(ProductBrand.id)
            )
            brand_info = {r.id: r for r in pc_result.all()}

            prod_m = await session.execute(
                select(
                    ProductBrand.id,
                    func.sum(ProductAnalytics.frozen_capital).label("fz"),
                    func.sum(ProductAnalytics.potential_revenue_7d).label("pr"),
                    func.sum(
                        ProductAnalytics.turnover_days * ProductAnalytics.frozen_capital
                    ).label("tw"),
                )
                .select_from(ProductBrand)
                .join(Product, Product.brand_id == ProductBrand.id)
                .join(
                    ProductAnalytics,
                    and_(
                        ProductAnalytics.product_id == Product.id,
                        ProductAnalytics.product_option_id.is_(None),
                    ),
                )
                .where(Product.has_options == 0)
                .group_by(ProductBrand.id)
            )
            opt_m = await session.execute(
                select(
                    ProductBrand.id,
                    func.sum(ProductAnalytics.frozen_capital).label("fz"),
                    func.sum(ProductAnalytics.potential_revenue_7d).label("pr"),
                    func.sum(
                        ProductAnalytics.turnover_days * ProductAnalytics.frozen_capital
                    ).label("tw"),
                )
                .select_from(ProductBrand)
                .join(Product, Product.brand_id == ProductBrand.id)
                .join(ProductOption, ProductOption.product_id == Product.id)
                .join(
                    ProductAnalytics,
                    ProductAnalytics.product_option_id == ProductOption.id,
                )
                .group_by(ProductBrand.id)
            )

            metrics: Dict[int, Dict[str, float]] = {}
            for row in prod_m.all():
                metrics[row.id] = {
                    "fz": float(row.fz or 0),
                    "pr": float(row.pr or 0),
                    "tw": float(row.tw or 0),
                }
            for row in opt_m.all():
                m = metrics.setdefault(row.id, {"fz": 0.0, "pr": 0.0, "tw": 0.0})
                m["fz"] += float(row.fz or 0)
                m["pr"] += float(row.pr or 0)
                m["tw"] += float(row.tw or 0)

            brands: List[Dict[str, any]] = []
            for bid, brow in brand_info.items():
                m = metrics.get(bid, {"fz": 0.0, "pr": 0.0, "tw": 0.0})
                fz = m["fz"]
                avg_turn = (m["tw"] / fz) if fz > 0 else 0.0
                brands.append({
                    "brand_internal_id": brow.id,
                    "brand_name": brow.name,
                    "brand_id": brow.external_id,
                    "products_count": int(brow.products_count or 0),
                    "avg_turnover_days": avg_turn,
                    "total_frozen_capital": fz,
                    "potential_revenue_7d": m["pr"],
                })

            brands.sort(key=lambda x: x["potential_revenue_7d"], reverse=True)
            return brands
    
    async def get_critical_stock_items(self) -> List[Tuple[Product, Optional[ProductOption], ProductAnalytics]]:
        """Отримує список товарів з критичним рівнем запасу (без опцій + варіації)."""
        cache_key: tuple = ("critical_stock_items",)
        now = datetime.utcnow()
        cached = self._cache.get(cache_key)
        if cached:
            expires_at, value = cached
            if now < expires_at:
                return value

        async with AsyncProductsSessionLocal() as session:
            items: list[tuple[Product, Optional[ProductOption], ProductAnalytics]] = []

            # Варіації
            q_opt = (
                select(Product, ProductOption, ProductAnalytics)
                .join(ProductOption, Product.id == ProductOption.product_id)
                .join(ProductAnalytics, ProductAnalytics.product_option_id == ProductOption.id)
                .where(
                    or_(
                        ProductAnalytics.risk_out_of_stock_7d == True,
                        ProductAnalytics.status == "out_of_stock",
                    )
                )
            )
            for product, option, analytics in (await session.execute(q_opt)).all():
                items.append((product, option, analytics))

            # Товари без варіацій
            q_prod = (
                select(Product, ProductAnalytics)
                .join(ProductAnalytics, Product.id == ProductAnalytics.product_id)
                .where(
                    ProductAnalytics.product_option_id.is_(None),
                    Product.has_options == 0,
                    or_(
                        ProductAnalytics.risk_out_of_stock_7d == True,
                        ProductAnalytics.status == "out_of_stock",
                    ),
                )
            )
            for product, analytics in (await session.execute(q_prod)).all():
                items.append((product, None, analytics))

            items.sort(key=lambda x: x[2].avg_daily_sales or 0, reverse=True)
            self._cache[cache_key] = (now + self._cache_ttl, items)
            return items
    
    async def get_dead_stock_items(self) -> List[Tuple[Product, Optional[ProductOption], ProductAnalytics]]:
        """Отримує список неліквідних товарів (без опцій + варіації)."""
        cache_key: tuple = ("dead_stock_items",)
        now = datetime.utcnow()
        cached = self._cache.get(cache_key)
        if cached:
            expires_at, value = cached
            if now < expires_at:
                return value

        async with AsyncProductsSessionLocal() as session:
            items: list[tuple[Product, Optional[ProductOption], ProductAnalytics]] = []

            # Варіації
            q_opt = (
                select(Product, ProductOption, ProductAnalytics)
                .join(ProductOption, Product.id == ProductOption.product_id)
                .join(ProductAnalytics, ProductAnalytics.product_option_id == ProductOption.id)
                .where(ProductAnalytics.status == "dead_stock")
            )
            for product, option, analytics in (await session.execute(q_opt)).all():
                items.append((product, option, analytics))

            # Товари без варіацій
            q_prod = (
                select(Product, ProductAnalytics)
                .join(ProductAnalytics, Product.id == ProductAnalytics.product_id)
                .where(
                    ProductAnalytics.product_option_id.is_(None),
                    Product.has_options == 0,
                    ProductAnalytics.status == "dead_stock",
                )
            )
            for product, analytics in (await session.execute(q_prod)).all():
                items.append((product, None, analytics))

            items.sort(key=lambda x: x[2].frozen_capital or 0, reverse=True)
            self._cache[cache_key] = (now + self._cache_ttl, items)
            return items
    
    async def get_bestsellers(self, limit: int = 20) -> List[Tuple[Product, Optional[ProductOption], ProductAnalytics]]:
        """Отримує список бестселерів (без опцій + варіації)."""
        cache_key: tuple = ("bestsellers", limit)
        now = datetime.utcnow()
        cached = self._cache.get(cache_key)
        if cached:
            expires_at, value = cached
            if now < expires_at:
                return value

        async with AsyncProductsSessionLocal() as session:
            items: list[tuple[Product, Optional[ProductOption], ProductAnalytics]] = []

            # Варіації
            q_opt = (
                select(Product, ProductOption, ProductAnalytics)
                .join(ProductOption, Product.id == ProductOption.product_id)
                .join(ProductAnalytics, ProductAnalytics.product_option_id == ProductOption.id)
                .where(ProductAnalytics.status == "fast_mover")
            )
            for product, option, analytics in (await session.execute(q_opt)).all():
                items.append((product, option, analytics))

            # Товари без варіацій
            q_prod = (
                select(Product, ProductAnalytics)
                .join(ProductAnalytics, Product.id == ProductAnalytics.product_id)
                .where(
                    ProductAnalytics.product_option_id.is_(None),
                    Product.has_options == 0,
                    ProductAnalytics.status == "fast_mover",
                )
            )
            for product, analytics in (await session.execute(q_prod)).all():
                items.append((product, None, analytics))

            items.sort(key=lambda x: x[2].avg_daily_sales or 0, reverse=True)
            items = items[:limit]
            self._cache[cache_key] = (now + self._cache_ttl, items)
            return items
    
    async def get_expiring_products(
        self,
        days_threshold: int = 90,
        category_id: Optional[int] = None,
        brand_id: Optional[int] = None,
        expired_only: bool = False,
    ) -> List[Tuple[Product, Optional[ProductOption], ProductAnalytics]]:
        """
        Отримує товари з терміном придатності менше ніж days_threshold днів.

        Args:
            days_threshold: Максимальна кількість днів до закінчення терміну придатності
            category_id: Фільтр по категорії (опціонально)

        Returns:
            Список кортежів (Product, ProductOption або None, ProductAnalytics)
            відсортованих за зростанням days_until_expiry
        """
        async with AsyncProductsSessionLocal() as session:
            items: list[tuple[Product, Optional[ProductOption], ProductAnalytics]] = []

            # Фільтр по терміну придатності:
            # - expired_only=True  → вже прострочені (days_until_expiry < 0)
            # - expired_only=False → до заданого горизонту (0 <= days_until_expiry <= days_threshold)
            if expired_only:
                # Тільки вже прострочені товари (мінус дні)
                base_product_filter = [
                    ProductAnalytics.days_until_expiry.isnot(None),
                    ProductAnalytics.days_until_expiry < 0,
                ]
            else:
                # Тільки товари, строк яких ще не сплив: 0..days_threshold
                base_product_filter = [
                    ProductAnalytics.days_until_expiry.isnot(None),
                    ProductAnalytics.days_until_expiry >= 0,
                    ProductAnalytics.days_until_expiry <= days_threshold,
                ]
            if category_id:
                base_product_filter.append(Product.category_id == category_id)
            if brand_id:
                base_product_filter.append(Product.brand_id == brand_id)

            # 1) Опції з терміном придатності (власний date_expired варіації)
            option_query = (
                select(Product, ProductOption, ProductAnalytics)
                .join(ProductOption, Product.id == ProductOption.product_id)
                .join(ProductAnalytics, ProductOption.id == ProductAnalytics.product_option_id)
                .where(
                    and_(
                        *base_product_filter,
                        func.coalesce(ProductOption.stock_qty, 0) > 0,
                    )
                )
            )
            option_result = await session.execute(option_query)
            for product, option, analytics in option_result.all():
                items.append((product, option, analytics))

            # 2) Товари без варіацій з терміном придатності
            product_query = (
                select(Product, ProductAnalytics)
                .join(ProductAnalytics, Product.id == ProductAnalytics.product_id)
                .where(
                    # Важливо: якщо у товару є опції — термін придатності має
                    # визначатися на рівні опцій. Інакше можемо отримати "дубль"
                    # рядка на рівні товару (product_option_id IS NULL) і завищити
                    # кількість прострочених.
                    #
                    # Тому "product-level" рядок беремо тільки для товарів без жодної
                    # існуючої опції.
                    ~select(ProductOption.id)
                    .where(ProductOption.product_id == Product.id)
                    .exists(),
                    ProductAnalytics.product_option_id.is_(None),
                    func.coalesce(Product.stock_qty, 0) > 0,
                    *base_product_filter,
                )
            )
            product_result = await session.execute(product_query)
            for product, analytics in product_result.all():
                items.append((product, None, analytics))

            # Сортуємо за кількістю днів до закінчення (найменше — першим)
            items.sort(key=lambda x: x[2].days_until_expiry or 9999)
            return items

    async def get_procurement_recommendations(
        self,
        action: Optional[str] = None,
        category_id: Optional[int] = None,
        brand_id: Optional[int] = None,
        limit: int = 50,
    ) -> List[Tuple[Product, Optional[ProductOption], ProductAnalytics]]:
        """
        Отримує рекомендації по закупках.
        
        Args:
            action: Фільтр по дії ("buy_urgent", "buy_normal", "reduce", "stop", "sell_off")
            limit: Кількість товарів
        
        Returns:
            Список кортежів (Product, ProductAnalytics)
        """
        async with AsyncProductsSessionLocal() as session:
            items: list[tuple[Product, Optional[ProductOption], ProductAnalytics]] = []

            filters = []
            if action:
                filters.append(ProductAnalytics.procurement_action == action)
            if category_id:
                filters.append(Product.category_id == category_id)
            if brand_id:
                filters.append(Product.brand_id == brand_id)

            # 1) Рекомендації по опціях
            option_query = (
                select(Product, ProductOption, ProductAnalytics)
                .join(ProductOption, Product.id == ProductOption.product_id)
                .join(ProductAnalytics, ProductOption.id == ProductAnalytics.product_option_id)
            )
            if filters:
                option_query = option_query.where(and_(*filters))

            option_result = await session.execute(option_query)
            for product, option, analytics in option_result.all():
                items.append((product, option, analytics))

            # 2) Рекомендації по товарах без опцій
            product_query = (
                select(Product, ProductAnalytics)
                .join(ProductAnalytics, Product.id == ProductAnalytics.product_id)
                .where(
                    ProductAnalytics.product_option_id.is_(None),
                    Product.has_options == 0,
                )
            )
            if filters:
                product_query = product_query.where(and_(*filters))

            product_result = await session.execute(product_query)
            for product, analytics in product_result.all():
                items.append((product, None, analytics))

            # 3) Сортуємо за рекомендованою кількістю закупки
            items.sort(
                key=lambda item: (item[2].recommended_purchase_qty or 0),
                reverse=True,
            )
            if limit:
                items = items[:limit]
            return items

    async def get_status_distribution_for_horizon(self, horizon_days: int) -> Dict[str, any]:
        """
        Рахує розподіл статусів SKU (fast/medium/slow/dead/new/out_of_stock) та виручку
        динамічно — на основі sales_avg_per_day_{N}d для обраного горизонту.

        Статуси та виручка перераховуються "на льоту" в Python за тими ж порогами
        що й в analytics.py (завантажуються з ProcurementSettings).
        """
        h = normalize_horizon_days(horizon_days)
        cache_key = ("status_dist_horizon", h)
        now = datetime.utcnow()
        cached = self._cache.get(cache_key)
        if cached:
            expires_at, value = cached
            if now < expires_at:
                return value

        async with AsyncProductsSessionLocal() as session:
            # Завантажуємо пороги з ProcurementSettings
            settings_result = await session.execute(
                select(ProcurementSettings).where(ProcurementSettings.category_id.is_(None))
            )
            settings = settings_result.scalar_one_or_none()
            fast_thr = float((settings and settings.fast_mover_threshold) or 0.25)
            med_thr = float((settings and getattr(settings, "medium_mover_threshold", None)) or 0.07)
            dead_days = int((settings and settings.dead_stock_days) or 90)
            slow_days = int((settings and settings.slow_mover_days) or 14)

            product_level = ProductAnalytics.product_option_id.is_(None)
            option_level = ProductAnalytics.product_option_id.isnot(None)

            # Товари без варіацій (has_options=0)
            no_opts_result = await session.execute(
                select(Product, ProductAnalytics)
                .join(ProductAnalytics, Product.id == ProductAnalytics.product_id)
                .where(product_level, Product.has_options == 0)
            )
            no_opts_rows = no_opts_result.all()

            # Товари з варіаціями (option-level)
            with_opts_result = await session.execute(
                select(ProductOption, ProductAnalytics)
                .join(ProductAnalytics, ProductAnalytics.product_option_id == ProductOption.id)
                .where(option_level)
            )
            with_opts_rows = with_opts_result.all()

            status_distribution: Dict[str, int] = {}
            frozen_by_status: Dict[str, float] = {}
            revenue_by_status: Dict[str, float] = {}

            def _classify(item, analytics, stock_qty: int) -> str:
                if stock_qty == 0:
                    return "out_of_stock"
                avg = _avg_sales_for_days(item, h)
                if avg >= fast_thr:
                    return "fast_mover"
                if avg >= med_thr:
                    return "medium_mover"
                if avg > 0:
                    return "slow_mover"
                # avg == 0: класифікуємо за днями без продажів
                dws = int(analytics.days_without_sales or 0)
                if dws >= dead_days:
                    return "dead_stock"
                if analytics.avg_daily_sales is None and analytics.avg_daily_sales_7d is None:
                    return "new"
                return "slow_mover"

            def _revenue(item, analytics) -> float:
                qty = float(_sales_qty_for_days(item, h))
                price = float(getattr(item, "selling_price_catalog", None) or 0)
                return qty * price

            def _add(status: str, frozen: float, rev: float) -> None:
                status_distribution[status] = status_distribution.get(status, 0) + 1
                frozen_by_status[status] = frozen_by_status.get(status, 0.0) + frozen
                revenue_by_status[status] = revenue_by_status.get(status, 0.0) + rev

            for product, analytics in no_opts_rows:
                stock = int(product.stock_qty or 0)
                status = _classify(product, analytics, stock)
                frozen = _effective_frozen_capital(product, analytics)
                rev = _revenue(product, analytics)
                _add(status, frozen, rev)

            for option, analytics in with_opts_rows:
                stock = int(option.stock_qty or 0)
                status = _classify(option, analytics, stock)
                frozen = _effective_frozen_capital(option, analytics)
                rev = _revenue(option, analytics)
                _add(status, frozen, rev)

        result = {
            "status_distribution": status_distribution,
            "frozen_by_status": frozen_by_status,
            "revenue_by_status": revenue_by_status,
            "horizon_days": h,
        }
        self._cache[cache_key] = (now + self._cache_ttl, result)
        return result


# Глобальні екземпляри сервісів
abc_analysis_service = ABCAnalysisService()
reports_service = ProcurementReportsService()
