import logging
from typing import Optional
from xml.etree import ElementTree as ET

import html
import re
from sqlalchemy import delete, select

from bot.database.products_engine import AsyncProductsSessionLocal, init_products_db
from bot.database.products_models import ProductCategory, ProductBrand, Product, ProductOption, ProcurementAlert
from bot.utils.feed_download import stream_url_to_tempfile
from bot.utils.text_normalize import normalize_product_text


logger = logging.getLogger(__name__)

FEED_URL = "https://cosmy.com.ua/price/analytics_bot.xml"

_BROKEN_APOS_ENTITY_RE = re.compile(r"&[аa][рp][оo];", flags=re.IGNORECASE)


def _text(elem: Optional[ET.Element]) -> str:
    if elem is None or elem.text is None:
        return ""
    s = (elem.text or "").strip()
    if not s:
        return ""
    # Централізована нормалізація HTML-ентіті/апострофів.
    return normalize_product_text(s)


def _int(elem: Optional[ET.Element]) -> Optional[int]:
    s = _text(elem)
    if not s:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _float(elem: Optional[ET.Element]) -> Optional[float]:
    s = _text(elem)
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _purchase_price(elem: Optional[ET.Element]) -> Optional[float]:
    """0 або 1 ₴ у фіді = немає реальної закупівельної (placeholder Dilovod)."""
    price = _float(elem)
    if price is None or price <= 1:
        return None
    return price


def _date(elem: Optional[ET.Element]):
    """Парсить дату у форматі YYYY-MM-DD, повертає об'єкт date або None."""
    from datetime import date as date_type
    s = _text(elem)
    if not s:
        return None
    try:
        return date_type.fromisoformat(s[:10])
    except (ValueError, IndexError):
        return None


def _dates(elem: Optional[ET.Element]) -> list:
    """
    Парсить потенційно список дат у одному полі (на кшталт: "YYYY-MM-DD, YYYY-MM-DD").
    Повертає список date (в порядку появи).
    """
    from datetime import date as date_type

    s = _text(elem)
    if not s:
        return []

    parts = [p.strip() for p in s.split(",")]
    out = []
    for p in parts:
        if not p:
            continue
        try:
            out.append(date_type.fromisoformat(p[:10]))
        except (ValueError, IndexError):
            continue
    return out


def _dates_with_gaps(elem: Optional[ET.Element]) -> list:
    """
    Парсить масив дат зі збереженням позицій (порожні значення -> None).
    Це важливо, бо у фіді i-та дата відповідає i-й одиниці товару.
    """
    from datetime import date as date_type

    s = _text(elem)
    if not s:
        return []

    out = []
    for part in s.split(","):
        p = part.strip()
        if not p:
            out.append(None)
            continue
        try:
            out.append(date_type.fromisoformat(p[:10]))
        except (ValueError, IndexError):
            out.append(None)
    return out


def _earliest_date_for_stock(elem: Optional[ET.Element], stock_qty: Optional[int]):
    """
    Повертає найближчу дату придатності для наявного залишку.
    Якщо у фіді прийшов масив дат, беруться перші N елементів (N = stock_qty),
    бо кожна позиція масиву відповідає конкретній одиниці товару.
    """
    parsed_with_gaps = _dates_with_gaps(elem)
    if parsed_with_gaps:
        if stock_qty is not None and stock_qty >= 0:
            relevant = parsed_with_gaps[:stock_qty]
        else:
            relevant = parsed_with_gaps
        parsed = [d for d in relevant if d is not None]
        if parsed:
            return min(parsed)

    # Fallback для старого формату (одна дата без ком)
    parsed = _dates(elem)
    if parsed:
        return min(parsed)
    return _date(elem)


def _local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


_PRODUCT_COMMIT_EVERY = 40


async def _save_product_element(
    session,
    prod_el: ET.Element,
    cat_ids: dict[int, int],
    brand_ids: dict[int, int],
) -> int | None:
    """Записати один товар і його опції. Повертає кількість варіацій."""
    from bot.services.expiry_lots import sync_lots_after_feed_import

    pid = _int(prod_el.find("product_id"))
    if pid is None:
        return None

    sku = _text(prod_el.find("sku")) or None
    model = _text(prod_el.find("model")) or None
    model_internal = _text(prod_el.find("model_internal")) or None
    name = _text(prod_el.find("name")) or f"Товар {pid}"
    image_url = _text(prod_el.find("image_url")) or None

    brand_ext_id = _int(prod_el.find("brand_id"))
    brand_name = _text(prod_el.find("brand")) or None

    category_id_val = _int(prod_el.find("category_id"))
    category_name = _text(prod_el.find("category_name")) or None
    subcategory_id = _int(prod_el.find("subcategory_id"))
    subcategory_name = _text(prod_el.find("subcategory_name")) or None
    leaf_category_id = _int(prod_el.find("leaf_category_id"))
    leaf_category_name = _text(prod_el.find("leaf_category_name")) or None

    category_path_ids = _text(prod_el.find("category_path_ids")) or None
    category_path = _text(prod_el.find("category_path")) or None

    stock_qty = _int(prod_el.find("stock_qty"))
    stock_qty_opencart = _int(prod_el.find("stock_qty_opencart"))

    dilovod_id = _text(prod_el.find("dilovod_id")) or None
    has_options = _int(prod_el.find("has_options")) or 0
    if has_options:
        date_expired = None
    else:
        date_expired = _earliest_date_for_stock(prod_el.find("date_expired"), stock_qty)
    stock_status_id = _int(prod_el.find("stock_status_id"))
    stock_status = _text(prod_el.find("stock_status")) or None

    product_data_mode = _text(prod_el.find("product_data_mode")) or None

    product = Product(
        product_id=pid,
        sku=sku,
        model=model,
        model_internal=model_internal,
        name=name,
        image_url=image_url,
        brand_id=brand_ids.get(brand_ext_id) if brand_ext_id is not None else None,
        brand_external_id=brand_ext_id,
        brand_name=brand_name,
        category_id=cat_ids.get(category_id_val) if category_id_val is not None else None,
        category_name=category_name,
        subcategory_id=subcategory_id,
        subcategory_name=subcategory_name,
        leaf_category_id=leaf_category_id,
        leaf_category_name=leaf_category_name,
        category_path_ids=category_path_ids,
        category_path=category_path,
        stock_qty=stock_qty,
        stock_qty_opencart=stock_qty_opencart,
        dilovod_id=dilovod_id,
        date_expired=date_expired,
        stock_status_id=stock_status_id,
        stock_status=stock_status,
        has_options=bool(has_options),
        product_data_mode=product_data_mode,
        sales_qty_1d=_int(prod_el.find("sales_qty_1d")),
        sales_qty_7d=_int(prod_el.find("sales_qty_7d")),
        sales_qty_30d=_int(prod_el.find("sales_qty_30d")),
        sales_qty_60d=_int(prod_el.find("sales_qty_60d")),
        sales_qty_90d=_int(prod_el.find("sales_qty_90d")),
        sales_qty_180d=_int(prod_el.find("sales_qty_180d")),
        sales_qty_360d=_int(prod_el.find("sales_qty_360d")),
        sales_avg_per_day_1d=_float(prod_el.find("sales_avg_per_day_1d")),
        sales_avg_per_day_7d=_float(prod_el.find("sales_avg_per_day_7d")),
        sales_avg_per_day_30d=_float(prod_el.find("sales_avg_per_day_30d")),
        sales_avg_per_day_60d=_float(prod_el.find("sales_avg_per_day_60d")),
        sales_avg_per_day_90d=_float(prod_el.find("sales_avg_per_day_90d")),
        sales_avg_per_day_180d=_float(prod_el.find("sales_avg_per_day_180d")),
        sales_avg_per_day_360d=_float(prod_el.find("sales_avg_per_day_360d")),
        last_sale_date=_text(prod_el.find("last_sale_date")) or None,
        purchase_price=_purchase_price(prod_el.find("purchase_price")),
        selling_price_order=_float(prod_el.find("selling_price_order")),
        selling_price_order_avg_30d=_float(prod_el.find("selling_price_order_avg_30d")),
        selling_price_catalog=_float(prod_el.find("selling_price_catalog")),
    )
    session.add(product)
    await session.flush()

    options_count = 0
    if not has_options:
        await sync_lots_after_feed_import(
            session,
            cosmy_product_id=pid,
            product_option_value_id=None,
            stock_qty=stock_qty,
            feed_date_elem=prod_el.find("date_expired"),
        )

    options_el = prod_el.find("options")
    if options_el is not None:
        for opt_el in options_el.findall("option"):
            opt_stock_qty = _int(opt_el.find("stock_qty"))
            opt_date_expired = _earliest_date_for_stock(
                opt_el.find("date_expired"),
                opt_stock_qty,
            )
            option = ProductOption(
                product_id=product.id,
                product_option_value_id=_int(opt_el.find("product_option_value_id")),
                option_id=_int(opt_el.find("option_id")),
                option_name=_text(opt_el.find("option_name")) or None,
                option_value_id=_int(opt_el.find("option_value_id")),
                option_value_name=_text(opt_el.find("option_value_name")) or None,
                option_model=_text(opt_el.find("option_model")) or None,
                option_image_url=_text(opt_el.find("option_image_url")) or None,
                stock_qty=opt_stock_qty,
                stock_qty_opencart=_int(opt_el.find("stock_qty_opencart")),
                date_expired=opt_date_expired,
                stock_status_id=_int(opt_el.find("stock_status_id")),
                stock_status=_text(opt_el.find("stock_status")) or None,
                sales_qty_1d=_int(opt_el.find("sales_qty_1d")),
                sales_qty_7d=_int(opt_el.find("sales_qty_7d")),
                sales_qty_30d=_int(opt_el.find("sales_qty_30d")),
                sales_qty_60d=_int(opt_el.find("sales_qty_60d")),
                sales_qty_90d=_int(opt_el.find("sales_qty_90d")),
                sales_qty_180d=_int(opt_el.find("sales_qty_180d")),
                sales_qty_360d=_int(opt_el.find("sales_qty_360d")),
                sales_avg_per_day_1d=_float(opt_el.find("sales_avg_per_day_1d")),
                sales_avg_per_day_7d=_float(opt_el.find("sales_avg_per_day_7d")),
                sales_avg_per_day_30d=_float(opt_el.find("sales_avg_per_day_30d")),
                sales_avg_per_day_60d=_float(opt_el.find("sales_avg_per_day_60d")),
                sales_avg_per_day_90d=_float(opt_el.find("sales_avg_per_day_90d")),
                sales_avg_per_day_180d=_float(opt_el.find("sales_avg_per_day_180d")),
                sales_avg_per_day_360d=_float(opt_el.find("sales_avg_per_day_360d")),
                last_sale_date=_text(opt_el.find("last_sale_date")) or None,
                purchase_price=_purchase_price(opt_el.find("purchase_price")),
                selling_price_order=_float(opt_el.find("selling_price_order")),
                selling_price_order_avg_30d=_float(opt_el.find("selling_price_order_avg_30d")),
                selling_price_catalog=_float(opt_el.find("selling_price_catalog")),
                dilovod_id=_text(opt_el.find("dilovod_id")) or None,
                dilovod_model=_text(opt_el.find("dilovod_model")) or None,
            )
            session.add(option)
            options_count += 1
            pov_id = _int(opt_el.find("product_option_value_id"))
            await sync_lots_after_feed_import(
                session,
                cosmy_product_id=pid,
                product_option_value_id=pov_id,
                stock_qty=opt_stock_qty,
                feed_date_elem=opt_el.find("date_expired"),
            )
    return options_count


async def sync_cosmy_products() -> None:
    """
    Повна синхронізація продуктів Cosmy:
    - Завантажує XML-фід
    - Очищає product_options, products, product_categories
    - Записує всі категорії, товари та варіації.
    """
    logger.info("🛒 Початок синхронізації продуктів Cosmy...")

    # Гарантуємо що всі таблиці та міграції застосовані перед будь-яким DELETE
    await init_products_db()

    path = await stream_url_to_tempfile(FEED_URL, suffix=".xml", timeout_total=900.0)
    try:
        await _import_products_feed(path)
    finally:
        path.unlink(missing_ok=True)


async def _import_products_feed(path) -> None:
    """iterparse: у пам'яті один товар, коміт пачками, порожні вузли з дерева прибираємо."""
    async with AsyncProductsSessionLocal() as session:
        logger.info("🧹 Очищення старих даних продуктів...")
        await session.execute(delete(ProcurementAlert))
        await session.execute(delete(ProductOption))
        await session.execute(delete(Product))
        await session.execute(delete(ProductCategory))
        await session.execute(delete(ProductBrand))
        await session.commit()

        cat_ids: dict[int, int] = {}
        brand_ids: dict[int, int] = {}
        products_count = 0
        options_count = 0
        pending = 0
        stack: list[ET.Element] = []

        def _drop(elem: ET.Element) -> None:
            elem.clear()
            if not stack:
                return
            parent = stack[-1]
            try:
                parent.remove(elem)
            except ValueError:
                pass

        try:
            for event, elem in ET.iterparse(path, events=("start", "end")):
                if event == "start":
                    stack.append(elem)
                    continue
                if stack:
                    stack.pop()
                tag = _local_tag(elem.tag)
                if tag == "category":
                    try:
                        ext_id = int(elem.attrib.get("id"))
                    except (TypeError, ValueError):
                        _drop(elem)
                        continue
                    try:
                        parent_ext_id = int(elem.attrib.get("parent_id")) if elem.attrib.get("parent_id") else None
                    except (TypeError, ValueError):
                        parent_ext_id = None
                    category = ProductCategory(
                        external_id=ext_id,
                        parent_external_id=parent_ext_id,
                        name=_text(elem.find("name")) or _text(elem.find("name_xml")) or f"Категорія {ext_id}",
                        name_xml=_text(elem.find("name_xml")) or None,
                        url=_text(elem.find("url")) or None,
                        products_count=_int(elem.find("products_count")),
                    )
                    session.add(category)
                    await session.flush()
                    cat_ids[ext_id] = category.id
                    session.expunge(category)
                elif tag == "brand":
                    try:
                        ext_id = int(elem.attrib.get("id"))
                    except (TypeError, ValueError):
                        _drop(elem)
                        continue
                    brand = ProductBrand(
                        external_id=ext_id,
                        name=_text(elem.find("name")) or f"Бренд {ext_id}",
                        url=_text(elem.find("url")) or None,
                        products_count=_int(elem.find("products_count")),
                    )
                    session.add(brand)
                    await session.flush()
                    brand_ids[ext_id] = brand.id
                    session.expunge(brand)
                elif tag == "product":
                    added = await _save_product_element(session, elem, cat_ids, brand_ids)
                    if added is not None:
                        options_count += added
                        products_count += 1
                        pending += 1
                        if pending >= _PRODUCT_COMMIT_EVERY:
                            await session.commit()
                            session.expunge_all()
                            pending = 0
                _drop(elem)
        except ET.ParseError as exc:
            logger.error("❌ Помилка парсингу XML фіда продуктів: %s", exc)
            raise

        await session.commit()
        logger.info(
            "✅ Синхронізація продуктів завершена: %d категорій, %d брендів, %d товарів, %d варіацій",
            len(cat_ids),
            len(brand_ids),
            products_count,
            options_count,
        )



async def sync_and_recalculate() -> None:
    """
    Повний цикл оновлення даних продуктів:
    1. Синхронізація XML-фіда
    2. Синхронізація CRM-замовлень (для продажів лише успішних)
    3. Перерахунок sales_qty з CRM (успішні замовлення)
    4. Щоденний зріз продажів
    5. Розрахунок аналітики (всі товари та варіації)
    6. ABC-аналіз
    """
    from bot.services.clients_sync import sync_clients_from_feed
    from bot.services.procurement_crm_sales import apply_success_crm_sales_to_products
    from bot.services.sales_history import collect_daily_sales_snapshot
    from bot.services.analytics import analytics_service
    from bot.services.abc_analysis import abc_analysis_service

    logger.info("🛒 [1/6] Синхронізація продуктів із фіда...")
    await sync_cosmy_products()
    logger.info("✅ [1/6] Продукти синхронізовано")

    logger.info("👥 [2/6] Синхронізація CRM-замовлень...")
    try:
        stats = await sync_clients_from_feed()
        logger.info(
            "✅ [2/6] CRM: клієнтів %s, замовлень %s",
            stats.get("customers"),
            stats.get("orders"),
        )
    except Exception as e:
        logger.warning("⚠️ [2/6] CRM-синхронізація не вдалась, використовуємо наявні дані: %s", e)

    logger.info("📦 [3/6] Продажі для закупівель — лише успішні замовлення CRM...")
    crm_stats = await apply_success_crm_sales_to_products()
    logger.info(
        "✅ [3/6] CRM-продажі: %s product_id, %s товарів",
        crm_stats.get("crm_product_ids"),
        crm_stats.get("products_updated"),
    )

    logger.info("📊 [4/6] Збір щоденного зрізу продажів...")
    await collect_daily_sales_snapshot()
    logger.info("✅ [4/6] Зріз зібрано")

    logger.info("🔍 [5/6] Розрахунок аналітики...")
    await analytics_service.calculate_all_analytics()
    logger.info("✅ [5/6] Аналітику розраховано")

    logger.info("🎯 [6/6] ABC-аналіз...")
    await abc_analysis_service.calculate_abc_analysis(days=30)
    logger.info("✅ [6/6] ABC-аналіз виконано")

