import asyncio
from sqlalchemy import select, or_

from bot.database.products_engine import AsyncProductsSessionLocal
from bot.database.products_models import Product, ProductOption


async def main():
    async with AsyncProductsSessionLocal() as session:
        # ТОВАРИ
        q_products = select(Product).where(
            or_(
                (Product.sales_qty_1d.is_not(None)) & (Product.sales_qty_1d != 0),
                (Product.sales_qty_7d.is_not(None)) & (Product.sales_qty_7d != 0),
                (Product.sales_qty_30d.is_not(None)) & (Product.sales_qty_30d != 0),
            )
        )
        res_products = await session.execute(q_products)
        products = res_products.scalars().all()

        print(f"=== PRODUCTS з ненульовими sales_qty_* ===")
        print(f"Кількість: {len(products)}\n")
        for p in products:
            print(
                f"[product_id={p.product_id}] {p.name} | "
                f"1d={p.sales_qty_1d} | "
                f"7d={p.sales_qty_7d} | "
                f"30d={p.sales_qty_30d}"
            )

        # ОПЦІЇ ТОВАРІВ
        q_options = select(ProductOption).where(
            or_(
                (ProductOption.sales_qty_1d.is_not(None)) & (ProductOption.sales_qty_1d != 0),
                (ProductOption.sales_qty_7d.is_not(None)) & (ProductOption.sales_qty_7d != 0),
                (ProductOption.sales_qty_30d.is_not(None)) & (ProductOption.sales_qty_30d != 0),
            )
        )
        res_options = await session.execute(q_options)
        options = res_options.scalars().all()

        print(f"\n=== OPTIONS з ненульовими sales_qty_* ===")
        print(f"Кількість: {len(options)}\n")
        for o in options:
            print(
                f"[product_id={o.product_id} option_id={o.id}] {o.option_name or ''} {o.option_value_name or ''} | "
                f"1d={o.sales_qty_1d} | "
                f"7d={o.sales_qty_7d} | "
                f"30d={o.sales_qty_30d}"
            )


if __name__ == "__main__":
    asyncio.run(main())