"""Procurement / products routes."""

from web.deps import *  # noqa: F403, F401
from web.deps import _procurement_unread_alerts_count

import asyncio
from fastapi import APIRouter

router = APIRouter()

PRODUCTS_PER_PAGE = 50


def _product_search_query(q: str | None) -> str:
    from web.procurement_table_filters import normalize_product_search_query

    return normalize_product_search_query(q)


def _filter_sku_catalog_by_product_query(
    items: list,
    q: str | None,
    horizon_days: int,
    purchase_horizon_days: int,
) -> list:
    from web.procurement_table_filters import filter_catalog_by_product_query

    return filter_catalog_by_product_query(items, q, horizon_days, purchase_horizon_days)


def _referer_redirect(referer: str | None, fallback: str, query: str) -> RedirectResponse:
    base = (referer or fallback).split("?")[0] or fallback
    return RedirectResponse(url=f"{base}?{query}", status_code=302)


async def _run_products_full_sync_and_clear_report_caches() -> None:
    """Фід + зріз продажів + аналітика + ABC; скидає in-memory кеші звітів закупівель."""
    from bot.services.products_sync import sync_and_recalculate
    from bot.services.abc_analysis import abc_analysis_service, reports_service

    await sync_and_recalculate()
    abc_analysis_service.clear_cache()
    reports_service.clear_cache()


async def _run_products_analytics_only() -> None:
    from bot.services.analytics import analytics_service as _analytics_service
    from bot.services.abc_analysis import abc_analysis_service, reports_service

    logger.info("📊 [1/2] Розрахунок аналітики (ручний запуск)...")
    await _analytics_service.calculate_all_analytics()
    logger.info("✅ [1/2] Аналітику розраховано")

    logger.info("🎯 [2/2] ABC-аналіз...")
    await abc_analysis_service.calculate_abc_analysis(days=30)
    logger.info("✅ [2/2] ABC-аналіз виконано")

    abc_analysis_service.clear_cache()
    reports_service.clear_cache()


@router.post("/products/sync")
async def products_sync_post(
    request: Request,
    user: str = Depends(require_admin),
    _csrf: None = Depends(verify_csrf_dependency),
):
    """Ручна синхронізація продуктів Cosmy з фіда + повний цикл аналітики + скидання кешу звітів."""
    referer = request.headers.get("referer") or "/products"

    async def run_products_sync() -> None:
        try:
            await _run_products_full_sync_and_clear_report_caches()
        except Exception as exc:
            logger.error("❌ Помилка синхронізації продуктів: %s", exc, exc_info=True)

    asyncio.create_task(run_products_sync())
    return _referer_redirect(referer, "/products", "synced=1")


@router.post("/products/calculate-analytics")
async def products_calculate_analytics_post(
    request: Request,
    user: str = Depends(require_admin),
    _csrf: None = Depends(verify_csrf_dependency),
):
    """Тільки розрахунок аналітики + ABC (без синхронізації фіду)."""
    referer = request.headers.get("referer") or "/sync"

    async def run_analytics() -> None:
        try:
            await _run_products_analytics_only()
        except Exception as exc:
            logger.error("❌ Помилка розрахунку аналітики: %s", exc, exc_info=True)

    asyncio.create_task(run_analytics())
    return _referer_redirect(referer, "/sync", "analytics_calculated=1")


@router.post("/procurement/calculate-analytics")
async def calculate_analytics_post(
    request: Request,
    user: str = Depends(require_admin),
    _csrf: None = Depends(verify_csrf_dependency),
):
    """Застарілий URL: той самий повний цикл, що «Оновити продукти зараз» на /sync."""
    async def run_products_sync() -> None:
        try:
            await _run_products_full_sync_and_clear_report_caches()
        except Exception as exc:
            logger.error("❌ Помилка розрахунку аналітики: %s", exc, exc_info=True)

    asyncio.create_task(run_products_sync())
    return RedirectResponse(url="/procurement/dashboard?calculated=1", status_code=302)



@router.get("/products", response_class=HTMLResponse)
async def products_page(request: Request, user: str = Depends(get_current_user)):
    """Сторінка перегляду продуктів Cosmy для адміна та менеджерів."""
    # Фільтри
    try:
        page = max(1, int(request.query_params.get("page", "1")))
    except ValueError:
        page = 1
    q = (request.query_params.get("q") or "").strip()
    brand_q = (request.query_params.get("brand_q") or "").strip()

    # Множинний вибір брендів: кілька brand_id у query-параметрах
    brand_ids_raw = request.query_params.getlist("brand_id")
    selected_brand_ids: list[int] = []
    for v in brand_ids_raw:
        try:
            bid = int(v)
        except ValueError:
            continue
        if bid not in selected_brand_ids:
            selected_brand_ids.append(bid)

    async with AsyncProductsSessionLocal() as session:
        # Бренди для фільтра (лише з ненульовою кількістю товарів)
        brand_result = await session.execute(
            select(ProductBrand).where(
                ProductBrand.products_count > 0
            ).order_by(ProductBrand.name)
        )
        brands_all = brand_result.scalars().all()
        if brand_q:
            lower_bq = brand_q.lower()
            brands = [
                b
                for b in brands_all
                if lower_bq in (b.name or "").lower()
            ]
        else:
            brands = brands_all

        # Базовий запит по товарах
        conditions = []
        if q:
            like_q = f"%{q}%"
            conditions.append(
                or_(
                    Product.name.ilike(like_q),
                    Product.sku.ilike(like_q),
                    Product.model.ilike(like_q),
                    Product.model_internal.ilike(like_q),
                    Product.brand_name.ilike(like_q),
                )
            )
        if selected_brand_ids:
            conditions.append(Product.brand_external_id.in_(selected_brand_ids))

        where_clause = and_(*conditions) if conditions else None

        # Підрахунок кількості для пагінації
        count_stmt = select(func.count(Product.id))
        if where_clause is not None:
            count_stmt = count_stmt.where(where_clause)
        total_count_res = await session.execute(count_stmt)
        total_count = total_count_res.scalar() or 0

        offset = (page - 1) * PRODUCTS_PER_PAGE
        stmt = (
            select(Product)
            .order_by(Product.updated_at.desc(), Product.id.desc())
            .offset(offset)
            .limit(PRODUCTS_PER_PAGE)
        )
        if where_clause is not None:
            stmt = stmt.where(where_clause)
        products_result = await session.execute(stmt)
        products = products_result.scalars().all()

        # Підтягнути варіації для відображення (по всім product.id на цій сторінці)
        product_ids = [p.id for p in products]
        options_by_product: dict[int, list[ProductOption]] = {}
        if product_ids:
            opts_res = await session.execute(
                select(ProductOption).where(ProductOption.product_id.in_(product_ids))
            )
            for opt in opts_res.scalars().all():
                options_by_product.setdefault(opt.product_id, []).append(opt)

    total_pages = (total_count + PRODUCTS_PER_PAGE - 1) // PRODUCTS_PER_PAGE if total_count else 1

    def pagination_url(p: int) -> str:
        params = [f"page={p}"]
        if q:
            params.append(f"q={q}")
        if brand_q:
            params.append(f"brand_q={brand_q}")
        for bid in selected_brand_ids:
            params.append(f"brand_id={bid}")
        return "/products?" + "&".join(params)

    return templates.TemplateResponse(
        "products.html",
        {
            "request": request,
            "brands": brands,
            "products": products,
            "options_by_product": options_by_product,
            "page": page,
            "total_pages": total_pages,
            "total_count": total_count,
            "per_page": PRODUCTS_PER_PAGE,
            "search_query": q,
            "brand_search_query": brand_q,
            "selected_brand_ids": selected_brand_ids,
            "pagination_url": pagination_url,
            "synced": request.query_params.get("synced"),
            "error": request.query_params.get("error"),
        },
    )


# --- Procurement Copilot: Аналітика закупок ---



@router.get("/procurement/dashboard", response_class=HTMLResponse)
async def procurement_dashboard(
    request: Request,
    user: str = Depends(get_current_user),
    purchase_horizon_days: int = 30,
    horizon_days: int = 30,
):
    """Головний дашборд аналітики складу для Procurement Copilot."""
    from bot.services.abc_analysis import (
        reports_service,
        normalize_horizon_days,
        recommendation_row_horizon_view,
    )
    from bot.database.products_models import ProductOption

    purchase_horizon_days = normalize_horizon_days(purchase_horizon_days)
    horizon_days = normalize_horizon_days(horizon_days)
    
    async with AsyncProductsSessionLocal() as session:
        # Загальна аналітика складу
        warehouse_analytics = await reports_service.get_warehouse_analytics()
        # Динамічний розподіл статусів за обраним горизонтом
        horizon_stats = await reports_service.get_status_distribution_for_horizon(horizon_days)
        
        # Критичні товари (ризик закінчення)
        critical_items_raw = await reports_service.get_critical_stock_items()
        critical_items_pairs = critical_items_raw[:10]
        
        # Бестселери
        bestsellers_pairs = await reports_service.get_bestsellers(limit=10)
        
        # Неліквід
        dead_stock_raw = await reports_service.get_dead_stock_items()
        dead_stock_pairs = dead_stock_raw[:10]

        # Для всіх цих товарів разом визначаємо, чи є опції з dilovod_id
        all_products = (
            [p for p, _opt, _a in critical_items_pairs]
            + [p for p, _opt, _a in bestsellers_pairs]
            + [p for p, _opt, _a in dead_stock_pairs]
        )
        product_ids = {p.id for p in all_products}
        has_dilovod_by_product: dict[int, bool] = {}
        if product_ids:
            opts_res = await session.execute(
                select(ProductOption.product_id).where(
                    ProductOption.product_id.in_(product_ids),
                    ProductOption.dilovod_id.isnot(None),
                    ProductOption.dilovod_id != "",
                )
            )
            for pid, in opts_res.all():
                has_dilovod_by_product[pid] = True

        # Формуємо трійки (product, analytics, has_dilovod) — option ігноруємо в шаблоні (є назва товару)
        critical_items = [
            (
                product,
                analytics,
                has_dilovod_by_product.get(product.id, False),
                recommendation_row_horizon_view(
                    product, None, analytics, purchase_horizon_days
                ),
            )
            for product, _opt, analytics in critical_items_pairs
        ]
        bestsellers = [
            (product, analytics, has_dilovod_by_product.get(product.id, False))
            for product, _opt, analytics in bestsellers_pairs
        ]
        dead_stock = [
            (product, analytics, has_dilovod_by_product.get(product.id, False))
            for product, _opt, analytics in dead_stock_pairs
        ]
        
    unread_alerts_count = await _procurement_unread_alerts_count()

    return templates.TemplateResponse(
        "procurement_dashboard.html",
        {
            "request": request,
            "warehouse_analytics": warehouse_analytics,
            "critical_items": critical_items,
            "bestsellers": bestsellers,
            "dead_stock": dead_stock,
            "unread_alerts_count": unread_alerts_count,
            "frozen_by_status": warehouse_analytics.get("frozen_by_status", {}),
            "turnover_ranges": warehouse_analytics.get("turnover_ranges", {}),
            "sales_comparison": warehouse_analytics.get("sales_comparison", {}),
            "top_brands_by_revenue": warehouse_analytics.get("top_brands_by_revenue", []),
            "top_brands_by_frozen_capital": warehouse_analytics.get("top_brands_by_frozen_capital", []),
            "top_brands_by_speed": warehouse_analytics.get("top_brands_by_speed", []),
            "purchase_horizon_days": purchase_horizon_days,
            "horizon_days": horizon_days,
            "horizon_stats": horizon_stats,
        },
    )



@router.get("/procurement/top-products", response_class=HTMLResponse)
async def procurement_top_products(
    request: Request,
    user: str = Depends(get_current_user),
    sort_by: str = "revenue",
    sort_dir: str = "desc",
    page: int = 1,
    horizon_days: int = 7,
    purchase_horizon_days: int = 7,
    brand_id: str | None = None,
    q: str | None = None,
):
    """Топ продуктів за різними критеріями."""
    from bot.services.abc_analysis import reports_service, normalize_horizon_days
    from web.procurement_table_sort import (
        CATALOG_SORT_KEYS,
        make_sort_url_factory,
        normalize_sort_dir,
    )

    per_page = 50
    horizon_days = normalize_horizon_days(horizon_days)
    purchase_horizon_days = normalize_horizon_days(purchase_horizon_days)
    if sort_by not in CATALOG_SORT_KEYS:
        sort_by = "revenue"
    sort_dir = normalize_sort_dir(sort_dir, sort_by=sort_by)

    brand_ext_id: int | None = None
    if brand_id:
        try:
            brand_ext_id = int(brand_id)
        except ValueError:
            brand_ext_id = None

    async with AsyncProductsSessionLocal() as session:
        top_products = await reports_service.get_top_products(
            sort_by=sort_by,
            horizon_days=horizon_days,
            brand_external_id=brand_ext_id,
            sort_dir=sort_dir,
            purchase_horizon_days=purchase_horizon_days,
        )
        product_search = _product_search_query(q)
        top_products = _filter_sku_catalog_by_product_query(
            top_products, product_search, horizon_days, purchase_horizon_days
        )

        total_count = len(top_products)
        if page < 1:
            page = 1
        offset = (page - 1) * per_page
        page_items = top_products[offset : offset + per_page]
        total_pages = (total_count + per_page - 1) // per_page if total_count else 1

        brands_result = await session.execute(
            select(ProductBrand).where(
                ProductBrand.products_count.is_(None)
                | (ProductBrand.products_count > 0)
            ).order_by(ProductBrand.name)
        )
        brands = brands_result.scalars().all()

    unread_alerts_count = await _procurement_unread_alerts_count()

    def _page_extra(page_num: int | None = None) -> dict:
        extra = {
            k: v
            for k, v in {
                "sort_by": sort_by if sort_by != "revenue" else None,
                "sort_dir": sort_dir if sort_dir != "desc" or sort_by != "revenue" else None,
                "horizon_days": horizon_days if horizon_days != 7 else None,
                "purchase_horizon_days": purchase_horizon_days if purchase_horizon_days != 7 else None,
                "brand_id": str(brand_ext_id) if brand_ext_id else None,
                "q": product_search or None,
            }.items()
            if v is not None and v != ""
        }
        if page_num and page_num > 1:
            extra["page"] = page_num
        return extra

    def pagination_url(p: int) -> str:
        from urllib.parse import urlencode
        qs = urlencode(_page_extra(p))
        return "/procurement/top-products" + (f"?{qs}" if qs else "")

    def horizon_url(h: int, ph: int | None = None) -> str:
        from urllib.parse import urlencode
        extra = _page_extra()
        extra["horizon_days"] = h
        if ph is not None:
            extra["purchase_horizon_days"] = ph
        qs = urlencode(extra)
        return "/procurement/top-products" + (f"?{qs}" if qs else "")

    sort_url = make_sort_url_factory(
        "/procurement/top-products",
        sort_by=sort_by,
        sort_dir=sort_dir,
        extra=_page_extra(),
        default_sort_by="revenue",
        default_sort_dir="desc",
    )

    return templates.TemplateResponse(
        "procurement_top_products.html",
        {
            "request": request,
            "top_products": page_items,
            "brands": brands,
            "sort_by": sort_by,
            "sort_dir": sort_dir,
            "horizon_days": horizon_days,
            "purchase_horizon_days": purchase_horizon_days,
            "page": page,
            "total_pages": total_pages,
            "total_count": total_count,
            "per_page": per_page,
            "pagination_url": pagination_url,
            "horizon_url": horizon_url,
            "sort_url": sort_url,
            "selected_brand_id": brand_ext_id,
            "product_search": product_search,
            "unread_alerts_count": unread_alerts_count,
        },
    )



@router.get("/procurement/category-turnover", response_class=HTMLResponse)
async def procurement_category_turnover(
    request: Request,
    user: str = Depends(get_current_user),
    turnover_days: int | None = None,
):
    """Оборотність по брендах (раніше — по категоріях)."""
    from bot.services.abc_analysis import reports_service
    from collections import defaultdict
    from bot.database.products_models import Product, ProductAnalytics, ProductOption
    
    # Агреговані дані по брендах (завжди на базі поточної аналітики)
    category_data = await reports_service.get_category_turnover()

    # Якщо обрано фільтр по оборотності (днів) – відфільтровуємо бренди
    if turnover_days:
        category_data = [
            cat for cat in category_data
            if (cat.get("avg_turnover_days") or 0) <= turnover_days
        ]

    # Підтягуємо товари по кожному бренду (топ за оборотністю) + ознаку складу
    products_by_category: dict[int, list[tuple[Product, ProductAnalytics, bool]]] = defaultdict(list)
    
    async with AsyncProductsSessionLocal() as session:
        for cat in category_data:
            ext_id = cat["brand_id"]
            result = await session.execute(
                select(Product, ProductAnalytics)
                .join(ProductAnalytics, Product.id == ProductAnalytics.product_id)
                .where(
                    Product.brand_external_id == ext_id,
                    ProductAnalytics.product_option_id.is_(None),
                    Product.has_options == 0,
                )
                .order_by(
                    ProductAnalytics.turnover_days.asc().nullslast()
                )
            )
            rows = result.all()

            # Фільтр по оборотності для товарів усередині категорії (якщо задано)
            if turnover_days:
                rows = [
                    (product, analytics)
                    for product, analytics in rows
                    if (analytics.turnover_days or 0) <= turnover_days
                ]

            # Для цих товарів перевіряємо, чи є опції з dilovod_id
            product_ids = [p.id for p, _ in rows]
            has_dilovod_by_product: dict[int, bool] = {}
            if product_ids:
                opts_res = await session.execute(
                    select(ProductOption.product_id).where(
                        ProductOption.product_id.in_(product_ids),
                        ProductOption.dilovod_id.isnot(None),
                        ProductOption.dilovod_id != "",
                    )
                )
                for pid, in opts_res.all():
                    has_dilovod_by_product[pid] = True

            products_by_category[ext_id] = [
                (product, analytics, has_dilovod_by_product.get(product.id, False))
                for product, analytics in rows
            ]

    unread_alerts_count = await _procurement_unread_alerts_count()

    return templates.TemplateResponse(
        "procurement_category_turnover.html",
        {
            "request": request,
            "brands": category_data,
            "products_by_category": products_by_category,
            "selected_turnover_days": turnover_days,
            "unread_alerts_count": unread_alerts_count,
        },
    )



@router.get("/procurement/abc-analysis")
async def procurement_abc_analysis_redirect():
    """Старий URL ABC — перенаправлення на аналітику за статусами руху."""
    return RedirectResponse(url="/procurement/by-status", status_code=301)



@router.get("/procurement/by-status", response_class=HTMLResponse)
async def procurement_by_status(
    request: Request,
    user: str = Depends(get_current_user),
    movement_status: str | None = None,
    sort_by: str = "revenue",
    sort_dir: str = "desc",
    page: int = 1,
    horizon_days: int = 7,
    purchase_horizon_days: int = 7,
    brand_id: str | None = None,
    q: str | None = None,
):
    """Каталог SKU за статусом руху: ті самі фільтри/таблиця, що ТОП товарів."""
    from urllib.parse import urlencode

    from bot.services.abc_analysis import (
        normalize_horizon_days,
        reports_service,
        sort_sku_catalog_items,
    )
    from web.procurement_table_sort import (
        CATALOG_SORT_KEYS,
        make_sort_url_factory,
        normalize_sort_dir,
    )

    per_page = 50
    horizon_days = normalize_horizon_days(horizon_days)
    purchase_horizon_days = normalize_horizon_days(purchase_horizon_days)
    if sort_by not in CATALOG_SORT_KEYS:
        sort_by = "revenue"
    sort_dir = normalize_sort_dir(sort_dir, sort_by=sort_by)

    brand_ext_id: int | None = None
    if brand_id:
        try:
            brand_ext_id = int(brand_id)
        except ValueError:
            brand_ext_id = None

    valid_statuses = {
        "fast_mover",
        "medium_mover",
        "slow_mover",
        "dead_stock",
        "new",
        "out_of_stock",
    }
    if movement_status and movement_status not in valid_statuses:
        movement_status = None

    product_search = _product_search_query(q)

    items = await reports_service.enumerate_sku_catalog_items(
        brand_external_id=brand_ext_id,
    )

    # Динамічний розподіл статусів за обраним горизонтом
    horizon_stats = await reports_service.get_status_distribution_for_horizon(horizon_days)

    if movement_status:
        # Класифікуємо кожен item динамічно за поточним horizon_days,
        # щоб фільтр "Fast Movers" відповідав тому ж горизонту що й чіпи вгорі
        from bot.services.abc_analysis import _avg_sales_for_days, _sales_qty_for_days
        from bot.database.products_models import ProcurementSettings as _PS

        async with AsyncProductsSessionLocal() as _session:
            _sr = await _session.execute(select(_PS).where(_PS.category_id.is_(None)))
            _s = _sr.scalar_one_or_none()
            _fast = float((_s and _s.fast_mover_threshold) or 0.25)
            _med = float((_s and getattr(_s, "medium_mover_threshold", None)) or 0.07)
            _dead = int((_s and _s.dead_stock_days) or 90)
            _slow = int((_s and _s.slow_mover_days) or 14)

        def _dynamic_status(product, option, analytics) -> str:
            item = option or product
            stock = int((option.stock_qty if option else product.stock_qty) or 0)
            if stock == 0:
                return "out_of_stock"
            avg = _avg_sales_for_days(item, horizon_days)
            if avg >= _fast:
                return "fast_mover"
            if avg >= _med:
                return "medium_mover"
            if avg > 0:
                return "slow_mover"
            # avg == 0: класифікуємо за днями без продажів
            dws = int(analytics.days_without_sales or 0)
            if dws >= _dead:
                return "dead_stock"
            if analytics.avg_daily_sales is None and analytics.avg_daily_sales_7d is None:
                return "new"
            return "slow_mover"

        items = [
            it for it in items
            if _dynamic_status(it[0], it[1], it[2]) == movement_status
        ]

    items = _filter_sku_catalog_by_product_query(
        items, product_search, horizon_days, purchase_horizon_days
    )

    sort_sku_catalog_items(
        items,
        sort_by,
        horizon_days,
        sort_dir=sort_dir,
        purchase_horizon_days=purchase_horizon_days,
    )

    total_count = len(items)
    if page < 1:
        page = 1
    offset = (page - 1) * per_page
    total_pages = (total_count + per_page - 1) // per_page if total_count else 1
    if page > total_pages and total_pages >= 1:
        page = total_pages
        offset = (page - 1) * per_page
    page_items = items[offset : offset + per_page]

    async with AsyncProductsSessionLocal() as session:
        brands_result = await session.execute(
            select(ProductBrand).where(
                ProductBrand.products_count.is_(None)
                | (ProductBrand.products_count > 0)
            ).order_by(ProductBrand.name)
        )
        brands = brands_result.scalars().all()

    def _qs(extra: dict | None = None) -> str:
        base_extra = {
            k: v
            for k, v in {
                "movement_status": movement_status or None,
                "sort_by": sort_by if sort_by != "revenue" else None,
                "sort_dir": sort_dir if sort_dir != "desc" or sort_by != "revenue" else None,
                "horizon_days": horizon_days if horizon_days != 7 else None,
                "purchase_horizon_days": purchase_horizon_days if purchase_horizon_days != 7 else None,
                "brand_id": str(brand_ext_id) if brand_ext_id else None,
                "q": product_search or None,
            }.items()
            if v is not None and v != ""
        }
        if extra:
            for k, v in extra.items():
                if v is None:
                    base_extra.pop(k, None)
                else:
                    base_extra[k] = v
        return urlencode(base_extra)

    def pagination_url(p: int) -> str:
        extra = {} if p <= 1 else {"page": p}
        qstr = _qs(extra)
        return "/procurement/by-status" + (("?" + qstr) if qstr else "")

    def by_status_link(movement_status_key: str | None = None) -> str:
        parts: dict = {}
        if movement_status_key:
            parts["movement_status"] = movement_status_key
        if sort_by != "revenue":
            parts["sort_by"] = sort_by
        if sort_dir != "desc" or sort_by != "revenue":
            parts["sort_dir"] = sort_dir
        if horizon_days != 7:
            parts["horizon_days"] = horizon_days
        if purchase_horizon_days != 7:
            parts["purchase_horizon_days"] = purchase_horizon_days
        if brand_ext_id:
            parts["brand_id"] = str(brand_ext_id)
        if product_search:
            parts["q"] = product_search
        qstr = urlencode(parts)
        return "/procurement/by-status" + (("?" + qstr) if qstr else "")

    def horizon_url(h: int, ph: int | None = None) -> str:
        extra: dict = {}
        if movement_status:
            extra["movement_status"] = movement_status
        if sort_by != "revenue":
            extra["sort_by"] = sort_by
        if sort_dir != "desc" or sort_by != "revenue":
            extra["sort_dir"] = sort_dir
        extra["horizon_days"] = h
        if ph is not None:
            extra["purchase_horizon_days"] = ph
        if brand_ext_id:
            extra["brand_id"] = str(brand_ext_id)
        if product_search:
            extra["q"] = product_search
        qstr = urlencode(extra)
        return "/procurement/by-status" + (("?" + qstr) if qstr else "")

    sort_url = make_sort_url_factory(
        "/procurement/by-status",
        sort_by=sort_by,
        sort_dir=sort_dir,
        extra={
            k: v
            for k, v in {
                "movement_status": movement_status,
                "horizon_days": horizon_days if horizon_days != 7 else None,
                "purchase_horizon_days": purchase_horizon_days if purchase_horizon_days != 7 else None,
                "brand_id": str(brand_ext_id) if brand_ext_id else None,
                "q": product_search or None,
            }.items()
            if v is not None and v != ""
        },
        default_sort_by="revenue",
        default_sort_dir="desc",
    )

    warehouse_analytics = await reports_service.get_warehouse_analytics()
    # Чіпи зверху — динамічний розподіл за horizon_days (вже розраховано вище)
    status_distribution = horizon_stats["status_distribution"]
    frozen_by_status = horizon_stats["frozen_by_status"]

    unread_alerts_count = await _procurement_unread_alerts_count()

    return templates.TemplateResponse(
        "procurement_by_status.html",
        {
            "request": request,
            "top_products": page_items,
            "movement_status": movement_status,
            "brands": brands,
            "selected_brand_id": brand_ext_id,
            "sort_by": sort_by,
            "sort_dir": sort_dir,
            "horizon_days": horizon_days,
            "purchase_horizon_days": purchase_horizon_days,
            "page": page,
            "per_page": per_page,
            "total_count": total_count,
            "total_pages": total_pages,
            "pagination_url": pagination_url,
            "by_status_link": by_status_link,
            "horizon_url": horizon_url,
            "sort_url": sort_url,
            "status_distribution": status_distribution,
            "frozen_by_status": frozen_by_status,
            "horizon_stats": horizon_stats,
            "warehouse_analytics": warehouse_analytics,
            "product_search": product_search,
            "unread_alerts_count": unread_alerts_count,
        },
    )



@router.get("/procurement/recommendations", response_class=HTMLResponse)
async def procurement_recommendations(
    request: Request,
    user: str = Depends(get_current_user),
    action: str = None,
    brand_id: int | None = None,
    horizon_days: int = 7,
    purchase_horizon_days: int = 7,
    page: int = 1,
    sort_by: str = "buy",
    sort_dir: str = "desc",
    q: str | None = None,
):
    """Рекомендації по закупках."""
    from urllib.parse import urlencode

    from bot.services.abc_analysis import (
        reports_service,
        normalize_horizon_days,
        recommendation_row_horizon_view,
        sort_recommendation_rows,
    )
    from web.procurement_table_sort import (
        RECOMMENDATION_SORT_KEYS,
        make_sort_url_factory,
        normalize_sort_dir,
    )

    horizon_days = normalize_horizon_days(horizon_days)
    purchase_horizon_days = normalize_horizon_days(purchase_horizon_days)
    if sort_by not in RECOMMENDATION_SORT_KEYS:
        sort_by = "buy"
    sort_dir = normalize_sort_dir(sort_dir, sort_by=sort_by)
    per_page = 50
    if page < 1:
        page = 1
    product_search = _product_search_query(q)

    async with AsyncProductsSessionLocal() as session:
        raw = await reports_service.get_procurement_recommendations(
            action=action,
            brand_id=brand_id,
            limit=0,
        )
        _buy_actions = {"buy_urgent", "buy_normal", "buy_careful", None}
        _filter_nonzero = action in _buy_actions
        recommendations = [
            (p, o, a, hv)
            for p, o, a in raw
            for hv in [
                recommendation_row_horizon_view(
                    p, o, a, horizon_days, purchase_horizon_days
                )
            ]
            if not _filter_nonzero or hv.get("recommended_purchase", 0) > 0
        ]
        if product_search:
            from web.procurement_table_filters import SkuTableFilters, filter_recommendation_rows

            recommendations = filter_recommendation_rows(
                recommendations, SkuTableFilters(q=product_search)
            )
        sort_recommendation_rows(recommendations, sort_by, sort_dir)
        total_count = len(recommendations)
        total_pages = (total_count + per_page - 1) // per_page if total_count else 1
        if page > total_pages:
            page = total_pages
        start = (page - 1) * per_page
        recommendations = recommendations[start : start + per_page]

        brands_result = await session.execute(
            select(ProductBrand).where(
                ProductBrand.products_count.isnot(None),
                ProductBrand.products_count > 0,
            ).order_by(ProductBrand.name)
        )
        brands = brands_result.scalars().all()

    unread_alerts_count = await _procurement_unread_alerts_count()

    def _rec_extra(page_num: int | None = None) -> dict:
        extra = {
            k: v
            for k, v in {
                "action": action or None,
                "brand_id": str(brand_id) if brand_id else None,
                "horizon_days": horizon_days if horizon_days != 7 else None,
                "purchase_horizon_days": purchase_horizon_days if purchase_horizon_days != 7 else None,
                "sort_by": sort_by if sort_by != "buy" else None,
                "sort_dir": sort_dir if sort_dir != "desc" or sort_by != "buy" else None,
                "q": product_search or None,
            }.items()
            if v is not None and v != ""
        }
        if page_num and page_num > 1:
            extra["page"] = page_num
        return extra

    def pagination_url(p: int) -> str:
        qs = urlencode(_rec_extra(p))
        return "/procurement/recommendations" + (f"?{qs}" if qs else "")

    def horizon_url(h: int, ph: int | None = None) -> str:
        extra = _rec_extra()
        extra["horizon_days"] = h
        if ph is not None:
            extra["purchase_horizon_days"] = ph
        qs = urlencode(extra)
        return "/procurement/recommendations" + (f"?{qs}" if qs else "")

    sort_url = make_sort_url_factory(
        "/procurement/recommendations",
        sort_by=sort_by,
        sort_dir=sort_dir,
        extra=_rec_extra(),
        default_sort_by="buy",
        default_sort_dir="desc",
    )

    return templates.TemplateResponse(
        "procurement_recommendations.html",
        {
            "request": request,
            "recommendations": recommendations,
            "selected_action": action,
            "brands": brands,
            "selected_brand_id": brand_id,
            "horizon_days": horizon_days,
            "purchase_horizon_days": purchase_horizon_days,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "total_count": total_count,
            "sort_by": sort_by,
            "sort_dir": sort_dir,
            "sort_url": sort_url,
            "pagination_url": pagination_url,
            "horizon_url": horizon_url,
            "product_search": product_search,
            "unread_alerts_count": unread_alerts_count,
        },
    )



async def _save_product_expiry_lots(
    session,
    *,
    product_id: int,
    option_id: int | None,
    worst_expiry_date: str,
    worst_expiry_qty: str,
) -> tuple[dict | None, str | None]:
    """Зберегти найгірший строк. Повертає (payload для UI, текст помилки)."""
    from datetime import date as date_type

    from bot.services.expiry_lots import LotDraft, save_manual_lots

    product_result = await session.execute(select(Product).where(Product.id == product_id))
    product = product_result.scalar_one_or_none()
    if not product:
        return None, "Товар не знайдено"

    active_option = None
    pov_id = None
    if option_id:
        opt_res = await session.execute(
            select(ProductOption).where(
                and_(ProductOption.id == option_id, ProductOption.product_id == product_id)
            )
        )
        active_option = opt_res.scalar_one_or_none()
        if active_option:
            pov_id = active_option.product_option_value_id

    stock_qty = (
        int(active_option.stock_qty or 0)
        if active_option and active_option.stock_qty is not None
        else int(product.stock_qty or 0)
    )

    q_s = str(worst_expiry_qty or "").strip()
    try:
        qty = int(q_s) if q_s else 0
    except ValueError:
        return None, "Некоректна кількість"

    d_s = str(worst_expiry_date or "").strip()
    exp: date_type | None = None
    if d_s:
        try:
            exp = date_type.fromisoformat(d_s[:10])
        except ValueError:
            return None, "Некоректна дата"

    drafts: list[LotDraft] = []
    if qty > 0:
        drafts.append(LotDraft(expiry_date=exp, quantity=qty))

    err = await save_manual_lots(
        session,
        cosmy_product_id=product.product_id,
        product_option_value_id=pov_id,
        stock_qty=stock_qty,
        drafts=drafts,
    )
    if err:
        return None, err

    eff_date = exp if qty > 0 else None
    days_left = (eff_date - date_type.today()).days if eff_date else None
    return {
        "product_id": product_id,
        "option_id": option_id,
        "worst_date": eff_date.isoformat() if eff_date else "",
        "worst_date_display": eff_date.strftime("%d.%m.%Y") if eff_date else "",
        "worst_qty": qty,
        "other_qty": max(0, stock_qty - qty),
        "stock_qty": stock_qty,
        "days_until_expiry": days_left,
        "use_manual_lots": True,
    }, None


@router.get("/procurement/clearance", response_class=HTMLResponse)
async def procurement_clearance(
    request: Request,
    user: str = Depends(get_current_user),
    days: int = 90,
    brand_id: str | None = None,
    expired: bool = False,
):
    """Розпродаж — товари з терміном придатності, що закінчується або вже сплив."""
    from bot.services.abc_analysis import reports_service
    from bot.services.expiry_lots import load_expiry_contexts_batch

    # Доступні горизонти для фільтра (без дрібних: 1/7/14)
    valid_days = [30, 60, 90, 180, 365]
    if days not in valid_days:
        days = 90

    # Безпечний парсинг brand_id: дозволяємо порожній рядок / відсутній параметр
    brand_id_int: int | None
    if brand_id is None or brand_id == "":
        brand_id_int = None
    else:
        try:
            brand_id_int = int(brand_id)
        except ValueError:
            brand_id_int = None

    async with AsyncProductsSessionLocal() as session:
        products = await reports_service.get_expiring_products(
            days_threshold=days,
            brand_id=brand_id_int,
            expired_only=expired,
        )

        # Прибираємо товари з нульовим залишком (товар та опція)
        products = [
            (product, option, analytics)
            for product, option, analytics in products
            if (
                (option and (option.stock_qty or 0) > 0)
                or (not option and (product.stock_qty or 0) > 0)
            )
        ]

        expiry_rows = []
        for product, option, _analytics in products:
            item = option if option else product
            stock = (
                int(option.stock_qty or 0)
                if option and option.stock_qty is not None
                else int(product.stock_qty or 0)
            )
            pov_id = option.product_option_value_id if option else None
            feed_date = getattr(item, "date_expired", None)
            expiry_rows.append((product.product_id, pov_id, stock, feed_date))

        expiry_map = await load_expiry_contexts_batch(session, expiry_rows)

        products_enriched = []
        critical_count = 0
        high_count = 0
        from datetime import date as date_type

        for product, option, analytics in products:
            pov_id = option.product_option_value_id if option else None
            expiry_ui = dict(expiry_map.get((product.product_id, pov_id), {}) or {})
            eff = expiry_ui.get("earliest_expiry")
            if eff is not None:
                display_days = (eff - date_type.today()).days
            elif analytics and analytics.days_until_expiry is not None:
                display_days = analytics.days_until_expiry
            else:
                display_days = None
            expiry_ui["display_days_until"] = display_days
            if display_days is not None and display_days <= 30:
                critical_count += 1
            elif display_days is not None and display_days <= 60:
                high_count += 1
            products_enriched.append((product, option, analytics, expiry_ui))

        brands_result = await session.execute(
            select(ProductBrand).where(
                ProductBrand.products_count.isnot(None),
                ProductBrand.products_count > 0,
            ).order_by(ProductBrand.name)
        )
        brands = brands_result.scalars().all()

    role = getattr(request.state, "role", ROLE_ADMIN)
    is_admin = role == ROLE_ADMIN
    unread_alerts_count = await _procurement_unread_alerts_count()

    return templates.TemplateResponse(
        "procurement_clearance.html",
        {
            "request": request,
            "products": products_enriched,
            "selected_days": days,
            "valid_days": valid_days,
            "expired": expired,
            "brands": brands,
            "selected_brand_id": brand_id_int,
            "total_count": len(products_enriched),
            "critical_count": critical_count,
            "high_count": high_count,
            "unread_alerts_count": unread_alerts_count,
            "is_admin": is_admin,
        },
    )



@router.get("/procurement/export/clearance")
async def export_clearance(
    user: str = Depends(get_current_user),
    days: int = 90,
    format: str = "xlsx",
):
    """Експорт розпродажу в Excel/CSV."""
    from bot.services.abc_analysis import reports_service
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from fastapi.responses import StreamingResponse

    valid_days = [1, 7, 14, 30, 60, 90, 180, 365]
    if days not in valid_days:
        days = 90

    products = await reports_service.get_expiring_products(days_threshold=days)

    if format == "xlsx":
        wb = Workbook()
        ws = wb.active
        ws.title = "Розпродаж"

        # Оновлена структура: мінус 3 колонки, "Каталогова ціна" → "Ціна на сайті"
        headers = [
            "Товар", "Категорія", "Термін придатності", "Днів залишилось",
            "Залишок", "Статус наявності", "Продаж/день (30д)", "Продаж/день (90д)",
            "Продажів за 30д", "Закупівельна ціна", "Ціна продажу", "Ціна на сайті", "Заморожений капітал",
        ]
        ws.append(headers)

        header_fill = PatternFill(start_color="EF4444", end_color="EF4444", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF")
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")

        total_frozen = 0.0

        for product, option, analytics in products:
            item = option if option else product
            stock = (option.stock_qty if option and option.stock_qty is not None else product.stock_qty) or 0
            date_exp = getattr(item, "date_expired", None) if stock > 0 else None
            days_left = analytics.days_until_expiry if analytics else None
            frozen_value = round(analytics.frozen_capital or 0, 2) if analytics else 0
            total_frozen += frozen_value

            ws.append([
                product.name + (f" [{option.option_value_name}]" if option and option.option_value_name else ""),
                product.category_name or "—",
                date_exp.strftime("%d.%m.%Y") if date_exp else "—",
                days_left if days_left is not None else "—",
                stock,
                getattr(item, "stock_status", None) or "—",
                round(getattr(item, "sales_avg_per_day_30d", None) or 0, 4),
                round(getattr(item, "sales_avg_per_day_90d", None) or 0, 4),
                getattr(item, "sales_qty_30d", None) or 0,
                round(getattr(item, "purchase_price", None) or 0, 2),
                round(getattr(item, "selling_price_catalog", None) or 0, 2),
                frozen_value,
            ])

        # Рядок підсумку (тотал) по замороженому капіталу
        ws.append(
            ["Разом", *[""] * (len(headers) - 2), round(total_frozen, 2)]
        )

        for column in ws.columns:
            max_length = max((len(str(cell.value or "")) for cell in column), default=10)
            ws.column_dimensions[column[0].column_letter].width = min(max_length + 4, 50)

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        filename = f"clearance_{days}d.xlsx"
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    else:
        import csv
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Товар", "Категорія", "Термін придатності", "Днів залишилось",
            "Залишок", "Статус наявності", "Продаж/день (30д)", "Продаж/день (90д)",
            "Закупівельна ціна", "Ціна продажу", "Заморожений капітал",
        ])

        total_frozen = 0.0

        for product, option, analytics in products:
            item = option if option else product
            stock = (option.stock_qty if option and option.stock_qty is not None else product.stock_qty) or 0
            date_exp = getattr(item, "date_expired", None) if stock > 0 else None
            days_left = analytics.days_until_expiry if analytics else None
            frozen_value = round(analytics.frozen_capital or 0, 2) if analytics else 0
            total_frozen += frozen_value

            writer.writerow([
                product.name + (f" [{option.option_value_name}]" if option and option.option_value_name else ""),
                product.category_name or "—",
                date_exp.strftime("%d.%m.%Y") if date_exp else "—",
                days_left if days_left is not None else "—",
                stock,
                getattr(item, "stock_status", None) or "—",
                round(getattr(item, "sales_avg_per_day_30d", None) or 0, 4),
                round(getattr(item, "sales_avg_per_day_90d", None) or 0, 4),
                round(getattr(item, "purchase_price", None) or 0, 2),
                round(getattr(item, "selling_price_catalog", None) or 0, 2),
                frozen_value,
            ])

        # Тотал по замороженому капіталу в CSV
        writer.writerow(
            ["Разом", *[""] * 8, round(total_frozen, 2)]
        )

        filename = f"clearance_{days}d.csv"
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode("utf-8-sig")),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )



@router.get("/procurement/alerts", response_class=HTMLResponse)
async def procurement_alerts(
    request: Request,
    user: str = Depends(get_current_user),
    alert_type: str = None,
):
    """Список алертів."""
    async with AsyncProductsSessionLocal() as session:
        query = select(ProcurementAlert).order_by(desc(ProcurementAlert.created_at))
        
        if alert_type:
            query = query.where(ProcurementAlert.alert_type == alert_type)
        
        query = query.limit(100)
        result = await session.execute(query)
        alerts = result.scalars().all()

        # Підтягуємо товари для алертів
        product_ids = {a.product_id for a in alerts if a.product_id}
        option_ids = {a.product_option_id for a in alerts if a.product_option_id}
        products_by_id: dict[int, Product] = {}
        options_by_id: dict[int, ProductOption] = {}
        has_dilovod_by_product: dict[int, bool] = {}

        if product_ids:
            products_res = await session.execute(
                select(Product).where(Product.id.in_(product_ids))
            )
            for p in products_res.scalars().all():
                products_by_id[p.id] = p

            # Чи є хоч одна опція з dilovod_id для продукту
            opts_res = await session.execute(
                select(ProductOption.product_id).where(
                    ProductOption.product_id.in_(product_ids),
                    ProductOption.dilovod_id.isnot(None),
                    ProductOption.dilovod_id != "",
                )
            )
            for pid, in opts_res.all():
                has_dilovod_by_product[pid] = True

        if option_ids:
            opt_res = await session.execute(
                select(ProductOption).where(ProductOption.id.in_(option_ids))
            )
            for o in opt_res.scalars().all():
                options_by_id[o.id] = o

        unread_alerts_count = await _procurement_unread_alerts_count()

    return templates.TemplateResponse(
        "procurement_alerts.html",
        {
            "request": request,
            "alerts": alerts,
            "products_by_id": products_by_id,
            "options_by_id": options_by_id,
            "has_dilovod_by_product": has_dilovod_by_product,
            "selected_alert_type": alert_type,
            "unread_alerts_count": unread_alerts_count,
        },
    )



@router.post("/procurement/alerts/{alert_id}/mark-read")
async def mark_alert_read(
    alert_id: int,
    request: Request,
    user: str = Depends(get_current_user),
    alert_type: str | None = Query(None),
    _csrf: None = Depends(verify_csrf_dependency),
):
    """Позначити алерт як прочитаний. З Accept: application/json — без перезавантаження сторінки."""
    updated = False
    async with AsyncProductsSessionLocal() as session:
        alert_query = select(ProcurementAlert).where(ProcurementAlert.id == alert_id)
        result = await session.execute(alert_query)
        alert = result.scalar_one_or_none()

        if alert:
            alert.is_read = True
            await session.commit()
            updated = True

    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        if updated:
            return JSONResponse({"ok": True})
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)

    allowed = {"critical", "daily", "weekly"}
    if alert_type and alert_type in allowed:
        redirect_url = f"/procurement/alerts?alert_type={alert_type}"
    else:
        redirect_url = "/procurement/alerts"
    return RedirectResponse(url=redirect_url, status_code=302)



@router.get("/procurement/settings", response_class=HTMLResponse)
async def procurement_settings_get(
    request: Request,
    user: str = Depends(get_current_user),
):
    """Сторінка налаштувань системи закупок."""
    from bot.database.products_models import ProcurementSettings as ProcurementSettingsModel

    async with AsyncProductsSessionLocal() as session:
        result = await session.execute(
            select(ProcurementSettingsModel).where(
                ProcurementSettingsModel.category_id.is_(None)
            )
        )
        settings = result.scalar_one_or_none()

        # Якщо глобальних налаштувань ще немає — створюємо запис з дефолтами
        if not settings:
            settings = ProcurementSettingsModel()
            session.add(settings)
            await session.commit()
            await session.refresh(settings)

    saved = request.query_params.get("saved")
    unread_alerts_count = await _procurement_unread_alerts_count()
    return templates.TemplateResponse(
        "procurement_settings.html",
        {
            "request": request,
            "settings": settings,
            "saved": saved,
            "unread_alerts_count": unread_alerts_count,
        },
    )



@router.post("/procurement/settings")
async def procurement_settings_post(
    request: Request,
    user: str = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_dependency),
):
    """Збереження налаштувань системи закупок."""
    from bot.database.products_models import ProcurementSettings as ProcurementSettingsModel

    form = await request.form()

    def _f(key: str, default: float) -> float:
        try:
            return float(form.get(key, default))
        except (TypeError, ValueError):
            return default

    def _i(key: str, default: int) -> int:
        try:
            return int(float(form.get(key, default)))
        except (TypeError, ValueError):
            return default

    async with AsyncProductsSessionLocal() as session:
        result = await session.execute(
            select(ProcurementSettingsModel).where(
                ProcurementSettingsModel.category_id.is_(None)
            )
        )
        settings = result.scalar_one_or_none()

        if not settings:
            settings = ProcurementSettingsModel()
            session.add(settings)

        settings.fast_mover_threshold = _f("fast_mover_threshold", 0.25)
        settings.medium_mover_threshold = _f("medium_mover_threshold", 0.07)
        settings.slow_mover_days = _i("slow_mover_days", 14)
        settings.dead_stock_days = _i("dead_stock_days", 90)
        settings.safety_stock_days = _i("safety_stock_days", 14)
        settings.critical_stock_days = _i("critical_stock_days", 7)
        settings.overstock_days = _i("overstock_days", 60)
        settings.trend_alert_threshold = _f("trend_alert_threshold", 20.0)
        settings.updated_at = datetime.now()

        await session.commit()

    return RedirectResponse(url="/procurement/settings?saved=1", status_code=302)



@router.get("/procurement/export/recommendations")
async def export_recommendations(
    user: str = Depends(get_current_user),
    action: str = None,
    format: str = "xlsx",
    horizon_days: int = 7,
    purchase_horizon_days: int = 7,
):
    """Експорт рекомендацій у Excel/CSV."""
    from bot.services.abc_analysis import (
        reports_service,
        normalize_horizon_days,
        recommendation_row_horizon_view,
    )
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from fastapi.responses import StreamingResponse

    horizon_days = normalize_horizon_days(horizon_days)
    purchase_horizon_days = normalize_horizon_days(purchase_horizon_days)
    ha = horizon_days
    hp = purchase_horizon_days

    async with AsyncProductsSessionLocal() as session:
        raw_recs = await reports_service.get_procurement_recommendations(
            action=action,
            limit=0,
        )
        _buy_actions = {"buy_urgent", "buy_normal", "buy_careful", None}
        _filter_nonzero = action in _buy_actions
        recommendations = [
            (p, o, a)
            for p, o, a in raw_recs
            if not _filter_nonzero
            or recommendation_row_horizon_view(
                p, o, a, horizon_days, purchase_horizon_days
            ).get("recommended_purchase", 0)
            > 0
        ]
        recommendations.sort(
            key=lambda item: recommendation_row_horizon_view(
                item[0], item[1], item[2], horizon_days, purchase_horizon_days
            ).get("recommended_purchase", 0),
            reverse=True,
        )

    if format == "xlsx":
        wb = Workbook()
        ws = wb.active
        ws.title = "Рекомендації"

        headers = [
            "Товар",
            "Варіант",
            "Категорія",
            "Залишок",
            f"Оборотність ({ha}д)",
            f"Продаж/день ({ha}д)",
            f"Закупити ({hp}д)",
            f"Прогноз ({ha}д)",
            "Закупити (1д)",
            "Закупити (7д)",
            "Закупити (30д)",
            "Закупити (60д)",
            "Закупити (90д)",
            "Закупити (180д)",
            "Закупити (360д)",
            "Динаміка 30д MoM %",
            "Динаміка 30д vs 90д %",
            "Динаміка 180д YoY %",
            "Статус",
            "Дія",
            "Заморожений капітал",
        ]
        ws.append(headers)
        
        header_fill = PatternFill(start_color="3B82F6", end_color="3B82F6", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF")
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
        
        for product, option, analytics in recommendations:
            hv = recommendation_row_horizon_view(
                product, option, analytics, horizon_days, purchase_horizon_days
            )
            if option and option.stock_qty is not None:
                stock = int(option.stock_qty or 0)
            else:
                stock = int(product.stock_qty or 0)
            variant = (
                f"{option.option_name or ''}: {option.option_value_name or ''}".strip(": ")
                if option
                else "—"
            )
            ws.append([
                product.name or "—",
                variant,
                product.category_name or "—",
                stock,
                hv["turnover_days_display"] if hv["turnover_days_display"] is not None else "—",
                round(hv["avg_daily_sales"], 2),
                hv["recommended_purchase"],
                round(hv["forecast_qty"], 1),
                analytics.recommended_purchase_1d or 0,
                analytics.recommended_purchase_7d or 0,
                analytics.recommended_purchase_30d or analytics.recommended_purchase_qty or 0,
                analytics.recommended_purchase_60d or 0,
                analytics.recommended_purchase_90d or 0,
                analytics.recommended_purchase_180d or 0,
                analytics.recommended_purchase_360d or 0,
                round(analytics.trend_mom, 1) if analytics.trend_mom is not None else "—",
                round(analytics.trend_30d_vs_90d, 1) if analytics.trend_30d_vs_90d is not None else "—",
                round(analytics.trend_yoy, 1) if analytics.trend_yoy is not None else "—",
                analytics.status or "—",
                analytics.procurement_action or "—",
                round(analytics.frozen_capital or 0, 2),
            ])
        
        for column in ws.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                if cell.value:
                    max_length = max(max_length, len(str(cell.value)))
            ws.column_dimensions[column_letter].width = min(max_length + 2, 50)
        
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        
        filename = f"recommendations_{action or 'all'}_{datetime.now().strftime('%Y%m%d')}.xlsx"
        
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    
    else:
        import csv
        
        output = io.StringIO()
        writer = csv.writer(output)
        
        writer.writerow([
            "Товар",
            "Варіант",
            "Категорія",
            "Залишок",
            f"Оборотність ({ha}д)",
            f"Продаж/день ({ha}д)",
            f"Закупити ({hp}д)",
            f"Прогноз ({ha}д)",
            "Закупити (1д)",
            "Закупити (7д)",
            "Закупити (30д)",
            "Закупити (60д)",
            "Закупити (90д)",
            "Закупити (180д)",
            "Закупити (360д)",
            "Динаміка 30д MoM %",
            "Динаміка 30д vs 90д %",
            "Динаміка 180д YoY %",
            "Статус",
            "Дія",
            "Заморожений капітал",
        ])

        for product, option, analytics in recommendations:
            hv = recommendation_row_horizon_view(
                product, option, analytics, horizon_days, purchase_horizon_days
            )
            if option and option.stock_qty is not None:
                stock = int(option.stock_qty or 0)
            else:
                stock = int(product.stock_qty or 0)
            variant = (
                f"{option.option_name or ''}: {option.option_value_name or ''}".strip(": ")
                if option
                else "—"
            )
            writer.writerow([
                product.name or "—",
                variant,
                product.category_name or "—",
                stock,
                hv["turnover_days_display"] if hv["turnover_days_display"] is not None else "—",
                round(hv["avg_daily_sales"], 2),
                hv["recommended_purchase"],
                round(hv["forecast_qty"], 1),
                analytics.recommended_purchase_1d or 0,
                analytics.recommended_purchase_7d or 0,
                analytics.recommended_purchase_30d or analytics.recommended_purchase_qty or 0,
                analytics.recommended_purchase_60d or 0,
                analytics.recommended_purchase_90d or 0,
                analytics.recommended_purchase_180d or 0,
                analytics.recommended_purchase_360d or 0,
                round(analytics.trend_mom, 1) if analytics.trend_mom is not None else "—",
                round(analytics.trend_30d_vs_90d, 1) if analytics.trend_30d_vs_90d is not None else "—",
                round(analytics.trend_yoy, 1) if analytics.trend_yoy is not None else "—",
                analytics.status or "—",
                analytics.procurement_action or "—",
                round(analytics.frozen_capital or 0, 2),
            ])
        
        output.seek(0)
        filename = f"recommendations_{action or 'all'}_{datetime.now().strftime('%Y%m%d')}.csv"
        
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode('utf-8-sig')),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )



@router.get("/procurement/export/top-products")
async def export_top_products(
    user: str = Depends(get_current_user),
    sort_by: str = "revenue",
    format: str = "xlsx",
):
    """Експорт топ товарів у Excel/CSV."""
    from bot.services.abc_analysis import reports_service, actual_revenue_30d
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from fastapi.responses import StreamingResponse
    
    async with AsyncProductsSessionLocal() as session:
        top_products = await reports_service.get_top_products(
            sort_by=sort_by,
        )
    
    if format == "xlsx":
        wb = Workbook()
        ws = wb.active
        ws.title = "ТОП товарів"
        
        headers = [
            "Товар", "Категорія", "Залишок", "Оборотність (дні)",
            "Продаж/день", "Виручка факт (30д)", "Прогноз (30д)",
            "Заморожений капітал", "Статус руху",
        ]
        ws.append(headers)
        
        header_fill = PatternFill(start_color="10B981", end_color="10B981", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF")
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
        
        for product, option, analytics in top_products:
            ws.append([
                f"{product.name}" + (f" — {option.option_name}: {option.option_value_name}" if option else ""),
                product.category_name or "—",
                (option.stock_qty if option and option.stock_qty is not None else (product.stock_qty or 0)),
                analytics.turnover_days or 0,
                round(analytics.avg_daily_sales or 0, 2),
                round(actual_revenue_30d(product, option), 2),
                round(analytics.potential_revenue_7d or 0, 2),
                round(analytics.frozen_capital or 0, 2),
                analytics.status or "—",
            ])
        
        for column in ws.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                if cell.value:
                    max_length = max(max_length, len(str(cell.value)))
            ws.column_dimensions[column_letter].width = min(max_length + 2, 50)
        
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        
        filename = f"top_products_{sort_by}_{datetime.now().strftime('%Y%m%d')}.xlsx"
        
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    
    else:
        import csv
        
        output = io.StringIO()
        writer = csv.writer(output)
        
        writer.writerow([
            "Товар", "Категорія", "Залишок", "Оборотність (дні)",
            "Продаж/день", "Виручка факт (30д)", "Прогноз (30д)",
            "Заморожений капітал", "Статус руху",
        ])
        
        for product, option, analytics in top_products:
            writer.writerow([
                f"{product.name}" + (f" — {option.option_name}: {option.option_value_name}" if option else ""),
                product.category_name or "—",
                (option.stock_qty if option and option.stock_qty is not None else (product.stock_qty or 0)),
                analytics.turnover_days or 0,
                round(analytics.avg_daily_sales or 0, 2),
                round(actual_revenue_30d(product, option), 2),
                round(analytics.potential_revenue_7d or 0, 2),
                round(analytics.frozen_capital or 0, 2),
                analytics.status or "—",
            ])
        
        output.seek(0)
        filename = f"top_products_{sort_by}_{datetime.now().strftime('%Y%m%d')}.csv"
        
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode('utf-8-sig')),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )



@router.get("/procurement/brands", response_class=HTMLResponse)
async def procurement_brands(
    request: Request,
    user: str = Depends(get_current_user),
    history_days: int = Query(90, ge=1),
    history_granularity: str = Query("auto"),
):
    """Сторінка всіх брендів з аналітикою."""
    from bot.services.abc_analysis import reports_service
    from bot.services.sales_history import (
        aggregate_history_granularity,
        get_warehouse_sales_history,
        resolve_history_granularity,
    )

    warehouse_analytics = await reports_service.get_warehouse_analytics()
    all_brands = warehouse_analytics.get("all_brands", [])

    def _sum_brands(key: str) -> float:
        return float(sum(b.get(key, 0) or 0 for b in all_brands))

    total_frozen = _sum_brands("frozen_capital")

    history_days_allowed = (90, 180, 360)
    history_days_selected = history_days if history_days in history_days_allowed else 90
    history_granularity_selected = resolve_history_granularity(
        history_days_selected, history_granularity
    )
    history_raw = await get_warehouse_sales_history(days=history_days_selected)
    history_raw = aggregate_history_granularity(history_raw, history_granularity_selected)
    history_data = [
        {
            "date": (h.get("date_label") or h["date"].strftime("%d.%m")),
            "sales": h["sales"],
            "stock": h["stock"],
            "revenue": round(h["revenue"], 2),
            "frozen": round(h.get("frozen", 0), 2),
        }
        for h in history_raw
    ]

    unread_alerts_count = await _procurement_unread_alerts_count()

    return templates.TemplateResponse(
        "procurement_brands.html",
        {
            "request": request,
            "all_brands": all_brands,
            "warehouse_analytics": warehouse_analytics,
            "total_frozen": total_frozen,
            "brands_count": len(all_brands),
            "history_data": history_data,
            "history_days_selected": history_days_selected,
            "history_days_allowed": history_days_allowed,
            "history_granularity_selected": history_granularity_selected,
            "unread_alerts_count": unread_alerts_count,
        },
    )


@router.get("/procurement/brand-margins", response_class=HTMLResponse)
async def procurement_brand_margins(
    request: Request,
    user: str = Depends(get_current_user),
):
    """Таблиця середньої маржі по брендах."""
    from bot.services.abc_analysis import reports_service

    warehouse_analytics = await reports_service.get_warehouse_analytics()
    all_brands = warehouse_analytics.get("all_brands", [])
    brands = sorted(
        all_brands,
        key=lambda b: (b.get("avg_margin_pct") is None, -(b.get("avg_margin_pct") or 0)),
    )

    unread_alerts_count = await _procurement_unread_alerts_count()
    return templates.TemplateResponse(
        "procurement_brand_margins.html",
        {
            "request": request,
            "brands": brands,
            "unread_alerts_count": unread_alerts_count,
        },
    )



@router.get("/procurement/brand/{brand_id}", response_class=HTMLResponse)
async def procurement_brand_detail(
    brand_id: int,
    request: Request,
    user: str = Depends(get_current_user),
    history_days: int = Query(90, ge=1),
    history_granularity: str = Query("auto"),
):
    """Сторінка бренду з аналітикою, графіками та списком товарів."""
    from bot.services.abc_analysis import reports_service
    from bot.services.sales_history import (
        aggregate_history_granularity,
        get_brand_sales_history,
        resolve_history_granularity,
    )

    brand_data = await reports_service.get_brand_detail(brand_id)
    if not brand_data:
        raise HTTPException(status_code=404, detail="Бренд не знайдено")

    history_days_allowed = (90, 180, 360)
    history_days_selected = history_days if history_days in history_days_allowed else 90

    history_granularity_selected = resolve_history_granularity(
        history_days_selected, history_granularity
    )
    history_raw = await get_brand_sales_history(brand_id, days=history_days_selected)
    history_raw = aggregate_history_granularity(history_raw, history_granularity_selected)
    history_data = [
        {
            "date": (h.get("date_label") or h["date"].strftime("%d.%m")),
            "sales": h["sales"],
            "stock": h["stock"],
            "revenue": h["revenue"],
        }
        for h in history_raw
    ]

    # Динаміка попиту: середні продажі та тренди
    s7 = brand_data.get("sales_qty_7d") or 0
    s30 = brand_data.get("sales_qty_30d") or 0
    s90 = brand_data.get("sales_qty_90d") or 0
    s60 = (brand_data.get("sales_qty_60d") or 0)
    s180 = brand_data.get("sales_qty_180d") or 0
    s360 = brand_data.get("sales_qty_360d") or 0
    prev30 = s60 - s30 if s60 else None
    prev90 = s180 - s90 if s180 else None
    prev180 = s360 - s180 if s360 else None

    def _pct(cur, prev):
        if not prev or prev <= 0:
            return None
        return ((cur - prev) / prev) * 100

    brand_analytics = {
        "avg_daily_sales_7d": s7 / 7 if s7 else 0,
        "avg_daily_sales_30d": s30 / 30 if s30 else 0,
        "avg_daily_sales_90d": s90 / 90 if s90 else 0,
        "trend_7d_vs_30d": _pct(s7 / 7, s30 / 30) if s30 else None,
        "trend_30d_vs_90d": _pct(s30 / 30, s90 / 90) if s90 else None,
        "trend_mom": _pct(s30, prev30) if prev30 is not None else None,
        "trend_yoy": _pct(s180, prev180) if prev180 is not None else None,
    }

    unread_alerts_count = await _procurement_unread_alerts_count()
    return templates.TemplateResponse(
        "procurement_brand_detail.html",
        {
            "request": request,
            "brand_data": brand_data,
            "history_data": history_data,
            "history_days_selected": history_days_selected,
            "history_days_allowed": history_days_allowed,
            "history_granularity_selected": history_granularity_selected,
            "brand_analytics": brand_analytics,
            "unread_alerts_count": unread_alerts_count,
        },
    )



@router.get("/procurement/product/{product_id}", response_class=HTMLResponse)
async def product_detail(
    product_id: int,
    request: Request,
    user: str = Depends(get_current_user),
    option_id: int | None = None,
):
    """Детальна сторінка товару з історією та графіками."""
    from bot.services.sales_history import get_sales_history
    
    async with AsyncProductsSessionLocal() as session:
        # Отримуємо товар
        product_result = await session.execute(
            select(Product).where(Product.id == product_id)
        )
        product = product_result.scalar_one_or_none()
        
        if not product:
            raise HTTPException(status_code=404, detail="Товар не знайдено")

        brand = None
        if product.brand_id:
            brand_result = await session.execute(
                select(ProductBrand).where(ProductBrand.id == product.brand_id)
            )
            brand = brand_result.scalar_one_or_none()
        
        # Якщо передано option_id — завантажуємо конкретну опцію (і перевіряємо, що вона належить цьому товару)
        active_option = None
        if option_id:
            opt_res = await session.execute(
                select(ProductOption).where(
                    and_(
                        ProductOption.id == option_id,
                        ProductOption.product_id == product_id,
                    )
                )
            )
            active_option = opt_res.scalar_one_or_none()
            if not active_option:
                # Якщо опція не належить цьому товару — ігноруємо option_id
                option_id = None

        # Отримуємо всі опції / варіації товару (якщо є)
        options_result = await session.execute(
            select(ProductOption)
            .where(ProductOption.product_id == product_id)
            .order_by(ProductOption.option_name, ProductOption.option_value_name)
        )
        options = options_result.scalars().all()

        # Якщо option_id переданий, але не знайшовся (наприклад після ресинку змінились ID)
        # — спробуємо знайти першу опцію цього товару і використати її
        if option_id is None and not active_option and options:
            active_option = options[0]
            option_id = active_option.id

        # Отримуємо аналітику:
        # 1. Якщо є конкретна опція — аналітика по ній
        # 2. Інакше — агрегована по всьому товару (product_option_id IS NULL)
        # 3. Якщо і там немає, але є опції — беремо аналітику першої опції як fallback
        analytics = None
        if option_id:
            analytics_res = await session.execute(
                select(ProductAnalytics).where(
                    and_(
                        ProductAnalytics.product_id == product_id,
                        ProductAnalytics.product_option_id == option_id,
                    )
                )
            )
            analytics = analytics_res.scalar_one_or_none()

        if analytics is None:
            # Шукаємо агреговану по товару
            analytics_res = await session.execute(
                select(ProductAnalytics).where(
                    and_(
                        ProductAnalytics.product_id == product_id,
                        ProductAnalytics.product_option_id.is_(None),
                    )
                )
            )
            analytics = analytics_res.scalar_one_or_none()

        if analytics is None and options:
            # Fallback: аналітика першої опції
            first_opt_id = options[0].id
            analytics_res = await session.execute(
                select(ProductAnalytics).where(
                    ProductAnalytics.product_option_id == first_opt_id
                )
            )
            analytics = analytics_res.scalar_one_or_none()
            if analytics and not active_option:
                active_option = options[0]
        
        # Отримуємо історію продажів за останні 90 днів
        history = await get_sales_history(product_id, product_option_id=option_id, days=90)
        
        # Підготовка даних для графіків
        history_data = []
        for h in reversed(history):
            history_data.append({
                "date": h.date.strftime("%d.%m"),
                "sales": h.sales_qty or 0,
                "stock": h.stock_qty or 0,
                "revenue": h.revenue or 0,
            })

        from bot.services.expiry_lots import expiry_context_for_ui, load_lot_drafts
        from bot.database.products_models import ProductExpiryProfile

        role = getattr(request.state, "role", ROLE_ADMIN)
        is_admin = role == ROLE_ADMIN
        pov_id = active_option.product_option_value_id if active_option else None
        active_stock = (
            int(active_option.stock_qty or 0)
            if active_option and active_option.stock_qty is not None
            else int(product.stock_qty or 0)
        )
        lot_drafts = await load_lot_drafts(session, product.product_id, pov_id)
        prof_stmt = select(ProductExpiryProfile).where(
            ProductExpiryProfile.cosmy_product_id == product.product_id,
            ProductExpiryProfile.product_option_value_id == pov_id
            if pov_id is not None
            else ProductExpiryProfile.product_option_value_id.is_(None),
        )
        profile = (await session.execute(prof_stmt)).scalar_one_or_none()
        feed_single = (
            active_option.date_expired
            if active_option and active_option.date_expired
            else product.date_expired
        )
        expiry_ui = expiry_context_for_ui(
            lot_drafts,
            active_stock,
            use_manual=bool(profile and profile.use_manual_lots),
            feed_single_date=feed_single,
        )

    unread_alerts_count = await _procurement_unread_alerts_count()
    return templates.TemplateResponse(
        "procurement_product_detail.html",
        {
            "request": request,
            "product": product,
            "brand": brand,
            "analytics": analytics,
            "options": options,
            "active_option": active_option,
            "history_data": history_data,
            "unread_alerts_count": unread_alerts_count,
            "is_admin": is_admin,
            "expiry_ui": expiry_ui,
            "expiry_option_value_id": pov_id,
        },
    )


@router.post("/procurement/clearance/expiry-lots")
async def clearance_expiry_lots_save(
    request: Request,
    user: str = Depends(require_admin),
    _csrf: None = Depends(verify_csrf_dependency),
    product_id: int = Form(...),
    option_id: int | None = Form(None),
    worst_expiry_date: str = Form(""),
    worst_expiry_qty: str = Form(""),
):
    """Inline-збереження найгіршого строку зі сторінки розпродажу (JSON)."""
    async with AsyncProductsSessionLocal() as session:
        payload, err = await _save_product_expiry_lots(
            session,
            product_id=product_id,
            option_id=option_id,
            worst_expiry_date=worst_expiry_date,
            worst_expiry_qty=worst_expiry_qty,
        )
        if err:
            return JSONResponse({"ok": False, "error": err}, status_code=400)
        await session.commit()

    from bot.services.abc_analysis import reports_service

    reports_service.clear_cache()
    return JSONResponse({"ok": True, **payload})


@router.post("/procurement/product/{product_id}/expiry-lots")
async def product_expiry_lots_save(
    request: Request,
    product_id: int,
    user: str = Depends(require_admin),
    _csrf: None = Depends(verify_csrf_dependency),
    option_id: int | None = Form(None),
    worst_expiry_date: str = Form(""),
    worst_expiry_qty: str = Form(""),
):
    """Зберегти кількість та дату найгіршого строку придатності."""
    async with AsyncProductsSessionLocal() as session:
        _payload, err = await _save_product_expiry_lots(
            session,
            product_id=product_id,
            option_id=option_id,
            worst_expiry_date=worst_expiry_date,
            worst_expiry_qty=worst_expiry_qty,
        )
        if err:
            from urllib.parse import quote

            return RedirectResponse(
                url=f"/procurement/product/{product_id}?option_id={option_id or ''}&expiry_err={quote(err[:200])}",
                status_code=302,
            )
        await session.commit()

    from bot.services.abc_analysis import reports_service

    reports_service.clear_cache()
    qs = f"option_id={option_id}" if option_id else ""
    sep = "&" if qs else ""
    return RedirectResponse(
        url=f"/procurement/product/{product_id}?{qs}{sep}expiry_ok=1",
        status_code=302,
    )


# --- Ручні витрати поза картами ---


