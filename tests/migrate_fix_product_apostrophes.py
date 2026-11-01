"""
Міграція даних: виправляє HTML-ентіті в текстових полях продуктної БД.

Зокрема, перетворює '&apos;' / '&amp;apos;' та зламані '&аро;' у нормальний апостроф.
"""

import asyncio
import logging

from bot.database.products_engine import AsyncProductsSessionLocal, init_products_db
from bot.database.products_models import Product, ProductBrand, ProductCategory, ProductOption
from bot.utils.text_normalize import normalize_product_text


logger = logging.getLogger(__name__)


def _maybe_fix(obj, field: str) -> bool:
    old = getattr(obj, field, None)
    if old is None:
        return False
    new = normalize_product_text(old)
    if new != old:
        setattr(obj, field, new)
        return True
    return False


async def run() -> None:
    await init_products_db()

    fixed = {
        "product_categories": 0,
        "product_brands": 0,
        "products": 0,
        "product_options": 0,
        "fields": 0,
    }

    # Use ORM selects
    from sqlalchemy import select

    async with AsyncProductsSessionLocal() as session:
        for cat in (await session.execute(select(ProductCategory))).scalars().all():
            fields_changed = 0
            fields_changed += int(_maybe_fix(cat, "name"))
            fields_changed += int(_maybe_fix(cat, "name_xml"))
            if fields_changed:
                fixed["product_categories"] += 1
                fixed["fields"] += fields_changed

        for brand in (await session.execute(select(ProductBrand))).scalars().all():
            if _maybe_fix(brand, "name"):
                fixed["product_brands"] += 1
                fixed["fields"] += 1

        for product in (await session.execute(select(Product))).scalars().all():
            touched_any = False
            for field in (
                "name",
                "sku",
                "model",
                "model_internal",
                "brand_name",
                "category_name",
                "subcategory_name",
                "leaf_category_name",
                "category_path",
            ):
                if _maybe_fix(product, field):
                    touched_any = True
                    fixed["fields"] += 1
            if touched_any:
                fixed["products"] += 1

        for opt in (await session.execute(select(ProductOption))).scalars().all():
            touched_any = False
            for field in (
                "option_name",
                "option_value_name",
                "option_model",
                "dilovod_model",
            ):
                if _maybe_fix(opt, field):
                    touched_any = True
                    fixed["fields"] += 1
            if touched_any:
                fixed["product_options"] += 1

        await session.commit()

    logger.info(
        "✅ Fix apostrophes done. Rows changed: categories=%d, brands=%d, products=%d, options=%d; fields changed=%d",
        fixed["product_categories"],
        fixed["product_brands"],
        fixed["products"],
        fixed["product_options"],
        fixed["fields"],
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())

