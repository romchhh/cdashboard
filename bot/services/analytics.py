"""
Сервіс аналітики продуктів для Procurement Copilot.
Розраховує метрики, тренди, прогнози та рекомендації по закупках.
"""

import logging
from datetime import datetime, date, timedelta
from typing import Optional, Tuple

from sqlalchemy import select, and_, func, desc
from sqlalchemy.dialects.sqlite import insert

from bot.database.products_engine import AsyncProductsSessionLocal
from bot.database.products_models import (
    Product,
    ProductOption,
    ProductSalesHistory,
    ProductAnalytics,
    ProcurementAlert,
    ProcurementSettings,
)

logger = logging.getLogger(__name__)


class ProductAnalyticsService:
    """Сервіс для розрахунку аналітики продуктів."""

    # Пороги за замовчуванням (переписуються з БД через _load_settings)
    FAST_MOVER_AVG_DAILY_SALES = 0.25
    MEDIUM_MOVER_AVG_DAILY_SALES = 0.07  # Мінімум для Medium (нижче — Slow)
    SLOW_MOVER_DAYS_WITHOUT_SALES = 14
    DEAD_STOCK_DAYS_WITHOUT_SALES = 90
    SAFETY_STOCK_DAYS_DEFAULT = 14
    CRITICAL_STOCK_DAYS = 7
    OVERSTOCK_DAYS = 60
    TREND_ALERT_THRESHOLD = 20.0  # |trend| >= 20% → алерт

    def __init__(self):
        self.session = None

    async def _load_settings(self) -> None:
        """Завантажує глобальні налаштування з ProcurementSettings (запис без category_id)."""
        try:
            result = await self.session.execute(
                select(ProcurementSettings).where(
                    ProcurementSettings.category_id.is_(None)
                )
            )
            settings = result.scalar_one_or_none()
            if settings:
                self.FAST_MOVER_AVG_DAILY_SALES = (
                    settings.fast_mover_threshold
                    if settings.fast_mover_threshold is not None
                    else 0.25
                )
                self.MEDIUM_MOVER_AVG_DAILY_SALES = (
                    settings.medium_mover_threshold
                    if getattr(settings, "medium_mover_threshold", None) is not None
                    else 0.07
                )
                self.SLOW_MOVER_DAYS_WITHOUT_SALES = settings.slow_mover_days or 14
                self.DEAD_STOCK_DAYS_WITHOUT_SALES = settings.dead_stock_days or 90
                self.SAFETY_STOCK_DAYS_DEFAULT = settings.safety_stock_days or 14
                self.CRITICAL_STOCK_DAYS = settings.critical_stock_days or 7
                self.OVERSTOCK_DAYS = settings.overstock_days or 60
                self.TREND_ALERT_THRESHOLD = settings.trend_alert_threshold or 20.0
                logger.info("✅ Налаштування аналітики завантажено з БД")
            else:
                logger.info("ℹ️ Глобальні налаштування не знайдені — використовуються дефолти")
        except Exception as e:
            logger.warning("⚠️ Не вдалося завантажити налаштування: %s", e)

    async def calculate_all_analytics(self) -> None:
        """
        Розраховує аналітику для всіх товарів і варіацій.
        Викликається щоденно після синхронізації фіда.
        """
        logger.info("🔍 Початок розрахунку аналітики для всіх товарів...")

        async with AsyncProductsSessionLocal() as session:
            self.session = session

            # Завантажуємо налаштування з БД
            await self._load_settings()

            # 1. Розраховуємо для основних продуктів (без варіацій)
            products_result = await session.execute(select(Product))
            products = products_result.scalars().all()

            analytics_updated = 0
            for product in products:
                await self._calculate_product_analytics(product.id, None)
                analytics_updated += 1

            # 2. Розраховуємо для варіацій (ProductOption)
            options_result = await session.execute(
                select(ProductOption).where(
                    (ProductOption.sales_avg_per_day_30d.isnot(None))
                    | (ProductOption.stock_qty.isnot(None))
                )
            )
            options = options_result.scalars().all()

            options_updated = 0
            for option in options:
                await self._calculate_product_analytics(option.product_id, option.id)
                options_updated += 1

            await session.commit()
            logger.info(
                "✅ Аналітика розрахована: %d товарів, %d варіацій",
                analytics_updated,
                options_updated,
            )

            # Після розрахунку аналітики — генеруємо алерти
            await self._generate_alerts()

    async def _calculate_product_analytics(
        self,
        product_id: int,
        product_option_id: Optional[int] = None,
    ) -> None:
        """Розраховує аналітику для конкретного товару/варіації."""

        if product_option_id:
            result = await self.session.execute(
                select(ProductOption).where(ProductOption.id == product_option_id)
            )
            item = result.scalar_one_or_none()
        else:
            result = await self.session.execute(
                select(Product).where(Product.id == product_id)
            )
            item = result.scalar_one_or_none()

        if not item:
            return

        # Отримуємо історію продажів
        history = await self._get_sales_history(product_id, product_option_id, days=90)
        history_30d_ago = await self._get_sales_history_range(
            product_id, product_option_id, days_from=60, days_to=30
        )
        history_1y_ago = await self._get_sales_history_range(
            product_id, product_option_id, days_from=395, days_to=365
        )

        # Основні метрики
        stock_qty = item.stock_qty or 0
        avg_daily_sales = item.sales_avg_per_day_30d or 0
        avg_daily_sales_7d = item.sales_avg_per_day_7d or 0
        # Якщо в фіді є 90-денна статистика — беремо звідти, інакше рахуємо з історії
        avg_daily_sales_90d = (
            getattr(item, "sales_avg_per_day_90d", None)
            or self._calculate_avg_sales(history, days=90)
        )
        avg_daily_sales_prev_month = self._calculate_avg_sales(history_30d_ago, days=30)
        avg_daily_sales_last_year = self._calculate_avg_sales(history_1y_ago, days=30)

        # Оборотність
        turnover_days = None
        if avg_daily_sales > 0:
            turnover_days = int(stock_qty / avg_daily_sales)

        # Тренди
        trend_7d_vs_30d = None
        if avg_daily_sales > 0 and avg_daily_sales_7d is not None:
            trend_7d_vs_30d = ((avg_daily_sales_7d / avg_daily_sales) - 1) * 100

        trend_30d_vs_90d = None
        if avg_daily_sales_90d > 0 and avg_daily_sales > 0:
            trend_30d_vs_90d = ((avg_daily_sales / avg_daily_sales_90d) - 1) * 100

        trend_mom = None
        if avg_daily_sales_prev_month > 0 and avg_daily_sales > 0:
            trend_mom = ((avg_daily_sales / avg_daily_sales_prev_month) - 1) * 100
        # Якщо денної історії за 30–60 днів тому немає (короткий зріз БД), беремо MoM
        # з тих самих агрегатів фіду, що й колонка «Динаміка»: 30д vs попередні 30д.
        if trend_mom is None and avg_daily_sales > 0:
            s30 = getattr(item, "sales_qty_30d", None)
            s60 = getattr(item, "sales_qty_60d", None)
            if s30 is not None and s60 is not None:
                prev30_qty = s60 - s30
                if prev30_qty > 0:
                    trend_mom = ((s30 - prev30_qty) / prev30_qty) * 100

        trend_yoy = None
        if avg_daily_sales_last_year > 0 and avg_daily_sales > 0:
            trend_yoy = ((avg_daily_sales / avg_daily_sales_last_year) - 1) * 100
        # Аналогічно: 180д vs попередні 180д з фіду (як на сторінці бренду).
        if trend_yoy is None and avg_daily_sales > 0:
            s180 = getattr(item, "sales_qty_180d", None)
            s360 = getattr(item, "sales_qty_360d", None)
            if s180 is not None and s360 is not None:
                prev180_qty = s360 - s180
                if prev180_qty > 0:
                    trend_yoy = ((s180 - prev180_qty) / prev180_qty) * 100

        # Прогноз попиту
        forecasted_sales_7d = avg_daily_sales * 7 if avg_daily_sales else 0
        forecasted_sales_14d = avg_daily_sales * 14 if avg_daily_sales else 0
        forecasted_sales_30d = avg_daily_sales * 30 if avg_daily_sales else 0

        # Корекція прогнозу за трендом
        if trend_7d_vs_30d and abs(trend_7d_vs_30d) > 10:
            trend_multiplier = 1 + (trend_7d_vs_30d / 100)
            forecasted_sales_7d *= trend_multiplier
            forecasted_sales_14d *= trend_multiplier
            forecasted_sales_30d *= trend_multiplier

        # Рекомендовані обсяги закупки
        safety_stock_days = self.SAFETY_STOCK_DAYS_DEFAULT
        recommended_purchase_qty = 0
        recommended_1d = recommended_7d = recommended_30d = 0
        recommended_60d = recommended_90d = recommended_180d = recommended_360d = 0

        if avg_daily_sales > 0:
            def _need_for(days: int) -> int:
                return max(0, int(avg_daily_sales * days - stock_qty))

            recommended_1d   = _need_for(1)
            recommended_7d   = _need_for(7)
            recommended_30d  = _need_for(30)
            recommended_60d  = _need_for(60)
            recommended_90d  = _need_for(90)
            recommended_180d = _need_for(180)
            recommended_360d = _need_for(360)
            recommended_purchase_qty = recommended_30d

        # Статус товару
        status = await self._determine_product_status(
            item, avg_daily_sales, turnover_days, history
        )

        # Рекомендована дія по закупках
        procurement_action = await self._determine_procurement_action(
            stock_qty, avg_daily_sales, turnover_days, status
        )

        # Фінансові метрики
        frozen_capital = None
        if stock_qty and item.purchase_price:
            frozen_capital = stock_qty * item.purchase_price

        potential_revenue_7d = None
        # Для аналітики використовуємо поточну ціну з сайту (selling_price_catalog),
        # а не історичну selling_price_order.
        selling_price = getattr(item, "selling_price_catalog", None) or getattr(
            item, "selling_price_order", None
        )
        if forecasted_sales_7d and selling_price:
            potential_revenue_7d = forecasted_sales_7d * selling_price

        days_without_sales = await self._calculate_days_without_sales(history)
        last_sale_date = self._get_last_sale_date(history)

        # Уточнюємо days_without_sales через агрегати фіду якщо history коротка/порожня
        if days_without_sales == 999 or len(history) < self.DEAD_STOCK_DAYS_WITHOUT_SALES:
            sales_30d = getattr(item, "sales_qty_30d", None)
            if sales_30d is not None and sales_30d == 0:
                last_sale_raw = getattr(item, "last_sale_date", None)
                if last_sale_raw:
                    try:
                        from datetime import date as _d
                        ls = _d.fromisoformat(str(last_sale_raw)[:10]) if isinstance(last_sale_raw, str) else last_sale_raw
                        days_from_feed = (date.today() - ls).days
                        if days_from_feed > days_without_sales:
                            days_without_sales = days_from_feed
                            if last_sale_date is None:
                                last_sale_date = ls
                    except (ValueError, TypeError):
                        days_without_sales = max(days_without_sales, self.DEAD_STOCK_DAYS_WITHOUT_SALES)
                else:
                    days_without_sales = max(days_without_sales, self.DEAD_STOCK_DAYS_WITHOUT_SALES)
            sales_60d = getattr(item, "sales_qty_60d", None)
            if sales_60d is not None and sales_60d == 0:
                days_without_sales = max(days_without_sales, 60)
            sales_90d = getattr(item, "sales_qty_90d", None)
            if sales_90d is not None and sales_90d == 0:
                days_without_sales = max(
                    days_without_sales, self.DEAD_STOCK_DAYS_WITHOUT_SALES
                )

        risk_out_of_stock_7d = bool(
            turnover_days and turnover_days < 7 and avg_daily_sales > 0
        )
        is_overstocked = bool(turnover_days and turnover_days > self.OVERSTOCK_DAYS)

        # Термін придатності
        days_until_expiry = None
        item_date_expired = getattr(item, "date_expired", None)
        if item_date_expired:
            days_until_expiry = (item_date_expired - date.today()).days

        # Якщо термін придатності скоро закінчується — пріоритет на розпродаж
        if days_until_expiry is not None and days_until_expiry <= 180 and stock_qty > 0:
            procurement_action = "sell_off"

        # Знаходимо існуючий запис (по парі product_id + product_option_id)
        if product_option_id:
            existing = await self.session.execute(
                select(ProductAnalytics).where(
                    ProductAnalytics.product_option_id == product_option_id
                )
            )
        else:
            existing = await self.session.execute(
                select(ProductAnalytics).where(
                    and_(
                        ProductAnalytics.product_id == product_id,
                        ProductAnalytics.product_option_id.is_(None),
                    )
                )
            )

        analytics = existing.scalar_one_or_none()

        now = datetime.utcnow()
        update_fields = dict(
            status=status,
            turnover_days=turnover_days,
            avg_daily_sales=avg_daily_sales,
            avg_daily_sales_7d=avg_daily_sales_7d,
            avg_daily_sales_90d=avg_daily_sales_90d,
            trend_7d_vs_30d=trend_7d_vs_30d,
            trend_30d_vs_90d=trend_30d_vs_90d,
            trend_mom=trend_mom,
            trend_yoy=trend_yoy,
            forecasted_sales_7d=forecasted_sales_7d,
            forecasted_sales_14d=forecasted_sales_14d,
            forecasted_sales_30d=forecasted_sales_30d,
            recommended_purchase_qty=recommended_purchase_qty,
            recommended_purchase_1d=recommended_1d,
            recommended_purchase_7d=recommended_7d,
            recommended_purchase_30d=recommended_30d,
            recommended_purchase_60d=recommended_60d,
            recommended_purchase_90d=recommended_90d,
            recommended_purchase_180d=recommended_180d,
            recommended_purchase_360d=recommended_360d,
            safety_stock_days=safety_stock_days,
            procurement_action=procurement_action,
            frozen_capital=frozen_capital,
            potential_revenue_7d=potential_revenue_7d,
            days_without_sales=days_without_sales,
            last_sale_date=last_sale_date,
            risk_out_of_stock_7d=risk_out_of_stock_7d,
            is_overstocked=is_overstocked,
            days_until_expiry=days_until_expiry,
            calculated_at=now,
            updated_at=now,
        )

        if analytics:
            for k, v in update_fields.items():
                setattr(analytics, k, v)
        else:
            self.session.add(
                ProductAnalytics(
                    product_id=product_id,
                    product_option_id=product_option_id,
                    **update_fields,
                )
            )

    async def _get_sales_history(
        self,
        product_id: int,
        product_option_id: Optional[int],
        days: int = 30,
    ) -> list[ProductSalesHistory]:
        """Отримує історію продажів за вказаний період."""
        if product_option_id:
            query = select(ProductSalesHistory).where(
                ProductSalesHistory.product_option_id == product_option_id
            )
        else:
            query = select(ProductSalesHistory).where(
                and_(
                    ProductSalesHistory.product_id == product_id,
                    ProductSalesHistory.product_option_id.is_(None),
                )
            )

        cutoff_date = date.today() - timedelta(days=days)
        query = query.where(ProductSalesHistory.date >= cutoff_date)
        query = query.order_by(ProductSalesHistory.date.desc())

        result = await self.session.execute(query)
        return result.scalars().all()

    async def _get_sales_history_range(
        self,
        product_id: int,
        product_option_id: Optional[int],
        days_from: int = 60,
        days_to: int = 30,
    ) -> list[ProductSalesHistory]:
        """Отримує історію продажів за діапазон днів назад."""
        if product_option_id:
            query = select(ProductSalesHistory).where(
                ProductSalesHistory.product_option_id == product_option_id
            )
        else:
            query = select(ProductSalesHistory).where(
                and_(
                    ProductSalesHistory.product_id == product_id,
                    ProductSalesHistory.product_option_id.is_(None),
                )
            )

        date_from = date.today() - timedelta(days=days_from)
        date_to = date.today() - timedelta(days=days_to)
        query = query.where(
            and_(
                ProductSalesHistory.date >= date_from,
                ProductSalesHistory.date <= date_to,
            )
        )
        query = query.order_by(ProductSalesHistory.date.desc())

        result = await self.session.execute(query)
        return result.scalars().all()

    def _calculate_avg_sales(
        self, history: list[ProductSalesHistory], days: int = 30
    ) -> float:
        """Розраховує середній продаж на день за історією."""
        if not history:
            return 0.0

        total_sales = sum(h.sales_qty or 0 for h in history)
        period_days = min(len(history), days)

        if period_days == 0:
            return 0.0

        return total_sales / period_days

    async def _determine_product_status(
        self,
        item,
        avg_daily_sales: float,
        turnover_days: Optional[int],
        history: list[ProductSalesHistory],
    ) -> str:
        """Визначає статус товару (Fast/Slow/Dead/New/Out of Stock).

        Пріоритет:
        1. out_of_stock — немає залишку
        2. dead_stock   — 90+ днів без продажів (незалежно від довжини history)
        3. new          — менше 7 записів в history І немає чітких ознак dead/slow
        4. slow_mover / fast_mover — за швидкістю продажів
        """
        stock_qty = item.stock_qty or 0

        if stock_qty == 0:
            return "out_of_stock"

        # Рахуємо кількість днів без продажів.
        # Спочатку пробуємо через history (детальніший підрахунок),
        # якщо history коротша ніж поріг — використовуємо агрегати з фіду.
        days_without_sales = await self._calculate_days_without_sales(history)

        # Якщо history коротка або відсутня — оцінюємо через дані самого фіду:
        # sales_qty_30d == 0 означає що за 30 днів продажів не було (слабший сигнал).
        if days_without_sales == 999 or len(history) < self.DEAD_STOCK_DAYS_WITHOUT_SALES:
            sales_30d = getattr(item, "sales_qty_30d", None)
            if sales_30d is not None and sales_30d == 0:
                # Немає продажів за 30 днів → перевіряємо last_sale_date з фіду
                last_sale_raw = getattr(item, "last_sale_date", None)
                if last_sale_raw:
                    try:
                        from datetime import date as _date
                        if isinstance(last_sale_raw, str):
                            last_sale = _date.fromisoformat(last_sale_raw[:10])
                        else:
                            last_sale = last_sale_raw
                        days_since_last = (date.today() - last_sale).days
                        days_without_sales = max(days_without_sales, days_since_last)
                    except (ValueError, TypeError):
                        # Якщо дата не парситься — беремо мінімум dead_stock поріг
                        days_without_sales = max(days_without_sales, self.DEAD_STOCK_DAYS_WITHOUT_SALES)
                else:
                    # Нема взагалі дати останнього продажу → вважаємо dead_stock поріг+
                    days_without_sales = max(days_without_sales, self.DEAD_STOCK_DAYS_WITHOUT_SALES)

            # Також перевіряємо 60d / 90d агрегати фіду
            sales_60d = getattr(item, "sales_qty_60d", None)
            if sales_60d is not None and sales_60d == 0:
                days_without_sales = max(days_without_sales, 60)
            sales_90d = getattr(item, "sales_qty_90d", None)
            if sales_90d is not None and sales_90d == 0:
                days_without_sales = max(
                    days_without_sales, self.DEAD_STOCK_DAYS_WITHOUT_SALES
                )

        if days_without_sales >= self.DEAD_STOCK_DAYS_WITHOUT_SALES:
            return "dead_stock"

        if days_without_sales >= self.SLOW_MOVER_DAYS_WITHOUT_SALES:
            return "slow_mover"

        # Тільки після перевірки dead/slow — перевіряємо чи є достатньо history
        if len(history) < 7:
            return "new"

        if avg_daily_sales >= self.FAST_MOVER_AVG_DAILY_SALES:
            return "fast_mover"

        if avg_daily_sales >= self.MEDIUM_MOVER_AVG_DAILY_SALES:
            return "medium_mover"

        return "slow_mover"

    async def _determine_procurement_action(
        self,
        stock_qty: int,
        avg_daily_sales: float,
        turnover_days: Optional[int],
        status: str,
    ) -> str:
        """Визначає рекомендовану дію по закупках."""

        if status == "dead_stock":
            return "sell_off"

        if status == "out_of_stock" and avg_daily_sales > 0:
            return "buy_urgent"

        if turnover_days and turnover_days < self.CRITICAL_STOCK_DAYS and avg_daily_sales > 0:
            return "buy_urgent"

        if turnover_days and turnover_days > self.OVERSTOCK_DAYS:
            return "stop"

        if status == "slow_mover" and turnover_days and turnover_days > 30:
            return "reduce"

        if status == "slow_mover":
            return "buy_careful"

        if status == "medium_mover" and turnover_days and turnover_days > self.OVERSTOCK_DAYS:
            return "stop"

        if status in ("fast_mover", "medium_mover"):
            return "buy_normal"

        return "buy_normal"

    async def _calculate_days_without_sales(
        self, history: list[ProductSalesHistory]
    ) -> int:
        """Розраховує кількість днів підряд без продажів.

        Рахує підряд-нулі з кінця history (найновіші спочатку).
        Якщо history порожня → повертає 999 (сигнал для caller-а, що треба
        дивитись в агрегати фіду).
        """
        if not history:
            return 999

        days_count = 0
        for record in sorted(history, key=lambda x: x.date, reverse=True):
            if record.sales_qty and record.sales_qty > 0:
                break
            days_count += 1

        return days_count

    def _get_last_sale_date(
        self, history: list[ProductSalesHistory]
    ) -> Optional[date]:
        """Повертає дату останнього продажу."""
        for record in sorted(history, key=lambda x: x.date, reverse=True):
            if record.sales_qty and record.sales_qty > 0:
                return record.date
        return None

    async def _generate_alerts(self) -> None:
        """Генерує алерти на основі аналітики."""
        logger.info("🔔 Генерація алертів...")

        await self._generate_critical_alerts()
        await self._generate_daily_alerts()
        await self._generate_weekly_alerts()
        await self._generate_trend_alerts()

        await self.session.commit()
        logger.info("✅ Алерти згенеровано")

    async def _generate_critical_alerts(self) -> None:
        """Генерує критичні алерти (бестселери, що закінчились). Охоплює варіації та товари без варіацій."""
        sku_rows: list[tuple] = []

        q_prod = select(Product, ProductAnalytics).join(
            ProductAnalytics, Product.id == ProductAnalytics.product_id
        ).where(
            ProductAnalytics.product_option_id.is_(None),
            Product.has_options == 0,
            ProductAnalytics.status == "out_of_stock",
            ProductAnalytics.avg_daily_sales > 0.5,
        )
        for product, analytics in await self.session.execute(q_prod):
            sku_rows.append((product, None, analytics, None))

        q_opt = (
            select(Product, ProductOption, ProductAnalytics)
            .join(ProductOption, Product.id == ProductOption.product_id)
            .join(ProductAnalytics, ProductAnalytics.product_option_id == ProductOption.id)
            .where(
                ProductAnalytics.status == "out_of_stock",
                ProductAnalytics.avg_daily_sales > 0.5,
            )
        )
        for product, option, analytics in await self.session.execute(q_opt):
            sku_rows.append((product, option, analytics, option.id))

        for product, option, analytics, option_id in sku_rows:
            opt_suffix = f" ({option.option_name}: {option.option_value_name})" if option else ""
            existing_q = select(ProcurementAlert).where(
                and_(
                    ProcurementAlert.product_id == product.id,
                    ProcurementAlert.alert_category == "out_of_stock",
                    ProcurementAlert.is_sent == False,
                )
            )
            if option_id:
                existing_q = existing_q.where(ProcurementAlert.product_option_id == option_id)
            else:
                existing_q = existing_q.where(ProcurementAlert.product_option_id.is_(None))
            if (await self.session.execute(existing_q)).scalar_one_or_none():
                continue

            self.session.add(
                ProcurementAlert(
                    product_id=product.id,
                    product_option_id=option_id,
                    alert_type="critical",
                    alert_category="out_of_stock",
                    message=f"⚠️ КРИТИЧНО: Бестселер '{product.name}{opt_suffix}' закінчився на складі!",
                    severity="critical",
                )
            )

    async def _generate_daily_alerts(self) -> None:
        """Генерує щоденні алерти (товари, що скоро закінчаться). Охоплює варіації та товари без варіацій."""
        today = datetime.utcnow().date()
        sku_rows: list[tuple] = []

        q_prod = select(Product, ProductAnalytics).join(
            ProductAnalytics, Product.id == ProductAnalytics.product_id
        ).where(
            ProductAnalytics.product_option_id.is_(None),
            Product.has_options == 0,
            ProductAnalytics.risk_out_of_stock_7d == True,
        )
        for product, analytics in await self.session.execute(q_prod):
            sku_rows.append((product, None, analytics, None))

        q_opt = (
            select(Product, ProductOption, ProductAnalytics)
            .join(ProductOption, Product.id == ProductOption.product_id)
            .join(ProductAnalytics, ProductAnalytics.product_option_id == ProductOption.id)
            .where(ProductAnalytics.risk_out_of_stock_7d == True)
        )
        for product, option, analytics in await self.session.execute(q_opt):
            sku_rows.append((product, option, analytics, option.id))

        for product, option, analytics, option_id in sku_rows:
            opt_suffix = f" ({option.option_name}: {option.option_value_name})" if option else ""
            existing_q = select(ProcurementAlert).where(
                and_(
                    ProcurementAlert.product_id == product.id,
                    ProcurementAlert.alert_category == "bestseller_low",
                    func.date(ProcurementAlert.created_at) == today,
                )
            )
            if option_id:
                existing_q = existing_q.where(ProcurementAlert.product_option_id == option_id)
            else:
                existing_q = existing_q.where(ProcurementAlert.product_option_id.is_(None))
            if (await self.session.execute(existing_q)).scalar_one_or_none():
                continue

            self.session.add(
                ProcurementAlert(
                    product_id=product.id,
                    product_option_id=option_id,
                    alert_type="daily",
                    alert_category="bestseller_low",
                    message=f"📉 '{product.name}{opt_suffix}' закінчиться через ~{analytics.turnover_days} днів",
                    severity="warning",
                )
            )

    async def _generate_weekly_alerts(self) -> None:
        """Генерує щотижневі алерти (неліквід, надлишок). Охоплює варіації та товари без варіацій."""
        today = datetime.utcnow().date()
        week_start = today - timedelta(days=today.weekday())

        async def _collect_sku(status_filter) -> list[tuple]:
            rows: list[tuple] = []
            q_p = (
                select(Product, ProductAnalytics)
                .join(ProductAnalytics, Product.id == ProductAnalytics.product_id)
                .where(
                    ProductAnalytics.product_option_id.is_(None),
                    Product.has_options == 0,
                    *status_filter,
                )
            )
            for product, analytics in await self.session.execute(q_p):
                rows.append((product, None, analytics, None))
            q_o = (
                select(Product, ProductOption, ProductAnalytics)
                .join(ProductOption, Product.id == ProductOption.product_id)
                .join(ProductAnalytics, ProductAnalytics.product_option_id == ProductOption.id)
                .where(*status_filter)
            )
            for product, option, analytics in await self.session.execute(q_o):
                rows.append((product, option, analytics, option.id))
            return rows

        # Dead stock
        for product, option, analytics, option_id in await _collect_sku([ProductAnalytics.status == "dead_stock"]):
            opt_suffix = f" ({option.option_name}: {option.option_value_name})" if option else ""
            existing_q = select(ProcurementAlert).where(
                and_(
                    ProcurementAlert.product_id == product.id,
                    ProcurementAlert.alert_category == "no_sales_30d",
                    ProcurementAlert.created_at >= week_start,
                )
            )
            existing_q = existing_q.where(
                ProcurementAlert.product_option_id == option_id
                if option_id else ProcurementAlert.product_option_id.is_(None)
            )
            if (await self.session.execute(existing_q)).scalar_one_or_none():
                continue
            self.session.add(
                ProcurementAlert(
                    product_id=product.id,
                    product_option_id=option_id,
                    alert_type="weekly",
                    alert_category="no_sales_30d",
                    message=(
                        f"❗️ Неліквід: '{product.name}{opt_suffix}' — "
                        f"{analytics.days_without_sales} днів без продажів"
                    ),
                    severity="info",
                )
            )

        # Надлишкові залишки
        for product, option, analytics, option_id in await _collect_sku([ProductAnalytics.is_overstocked == True]):
            opt_suffix = f" ({option.option_name}: {option.option_value_name})" if option else ""
            existing_q = select(ProcurementAlert).where(
                and_(
                    ProcurementAlert.product_id == product.id,
                    ProcurementAlert.alert_category == "overstock",
                    ProcurementAlert.created_at >= week_start,
                )
            )
            existing_q = existing_q.where(
                ProcurementAlert.product_option_id == option_id
                if option_id else ProcurementAlert.product_option_id.is_(None)
            )
            if (await self.session.execute(existing_q)).scalar_one_or_none():
                continue
            frozen = analytics.frozen_capital or 0
            self.session.add(
                ProcurementAlert(
                    product_id=product.id,
                    product_option_id=option_id,
                    alert_type="weekly",
                    alert_category="overstock",
                    message=(
                        f"📦 Надлишок: '{product.name}{opt_suffix}' — "
                        f"{analytics.turnover_days} днів запасу "
                        f"({frozen:.0f} грн заморожено)"
                    ),
                    severity="info",
                )
            )

    async def _generate_trend_alerts(self) -> None:
        """
        Генерує щотижневі алерти по суттєвих змінах попиту.
        Порогом є TREND_ALERT_THRESHOLD (за замовчуванням 20%).
        Аналізує trend_mom та trend_7d_vs_30d.
        Покриває і товари без варіацій, і окремі варіації.
        """
        today = datetime.utcnow().date()
        week_start = today - timedelta(days=today.weekday())
        threshold = self.TREND_ALERT_THRESHOLD

        # Збираємо SKU-рядки: (product, option_or_none, analytics, option_id_or_none)
        sku_rows: list[tuple] = []

        # 1) Товари без варіацій
        q_prod = select(Product, ProductAnalytics).join(
            ProductAnalytics, Product.id == ProductAnalytics.product_id
        ).where(
            ProductAnalytics.product_option_id.is_(None),
            Product.has_options == 0,
        )
        for product, analytics in await self.session.execute(q_prod):
            sku_rows.append((product, None, analytics, None))

        # 2) Варіації (option-level)
        q_opt = (
            select(Product, ProductOption, ProductAnalytics)
            .join(ProductOption, Product.id == ProductOption.product_id)
            .join(ProductAnalytics, ProductAnalytics.product_option_id == ProductOption.id)
        )
        for product, option, analytics in await self.session.execute(q_opt):
            sku_rows.append((product, option, analytics, option.id))

        for product, option, analytics, option_id in sku_rows:
            # Визначаємо найрелевантніший тренд (MoM пріоритетний, fallback — 7d vs 30d)
            trend_value = analytics.trend_mom
            trend_label = "MoM"
            if trend_value is None:
                trend_value = analytics.trend_7d_vs_30d
                trend_label = "7д vs 30д"

            if trend_value is None or abs(trend_value) < threshold:
                continue

            # Формуємо суфікс варіації для повідомлення
            opt_suffix = ""
            if option:
                opt_suffix = f" ({option.option_name}: {option.option_value_name})"

            if trend_value >= threshold:
                category = "demand_growth"
                message = (
                    f"📈 Ріст попиту ({trend_label}): '{product.name}{opt_suffix}' "
                    f"+{trend_value:.0f}% — розгляньте збільшення закупки"
                )
                severity = "info"
            else:
                category = "demand_drop"
                message = (
                    f"📉 Падіння попиту ({trend_label}): '{product.name}{opt_suffix}' "
                    f"{trend_value:.0f}% — зменшіть закупку або зробіть розпродаж"
                )
                severity = "warning"

            existing_q = select(ProcurementAlert).where(
                and_(
                    ProcurementAlert.product_id == product.id,
                    ProcurementAlert.alert_category == category,
                    ProcurementAlert.created_at >= week_start,
                )
            )
            if option_id:
                existing_q = existing_q.where(
                    ProcurementAlert.product_option_id == option_id
                )
            else:
                existing_q = existing_q.where(
                    ProcurementAlert.product_option_id.is_(None)
                )

            if (await self.session.execute(existing_q)).scalar_one_or_none():
                continue

            self.session.add(
                ProcurementAlert(
                    product_id=product.id,
                    product_option_id=option_id,
                    alert_type="weekly",
                    alert_category=category,
                    message=message,
                    severity=severity,
                )
            )


# Глобальний екземпляр сервісу
analytics_service = ProductAnalyticsService()
