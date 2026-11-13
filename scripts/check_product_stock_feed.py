#!/usr/bin/env python3
"""
Порівняння залишку товару: XML-фід Cosmy vs products.db.

На сервері (з кореня репозиторію, з активним venv):

  python scripts/check_product_stock_feed.py --query "Needly A to Cream"
  python scripts/check_product_stock_feed.py --product-id 12345
  python scripts/check_product_stock_feed.py --query "Needly" --feed /tmp/feed.xml

pytest (офлайн):

  python -m pytest tests/test_feed_stock.py -q
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from bot.database.products_engine import AsyncProductsSessionLocal, init_products_db  # noqa: E402
from bot.database.products_models import Product, ProductOption  # noqa: E402
from bot.services.feed_stock import (  # noqa: E402
    FEED_URL,
    FeedProductStock,
    find_feed_products_by_query,
    iter_feed_products_stock,
)
from bot.utils.feed_download import stream_url_to_tempfile  # noqa: E402


def _print_feed(item: FeedProductStock) -> None:
    print(f"--- Фід product_id={item.product_id} ---")
    print(f"  name: {item.name}")
    print(f"  brand: {item.brand_name or '—'}")
    print(f"  model: {item.model or '—'}")
    print(f"  has_options: {item.has_options}")
    print(f"  product stock_qty: {item.product_stock_qty}")
    print(f"  effective (UI-логіка): {item.feed_effective_stock}")
    for i, opt in enumerate(item.options, 1):
        print(
            f"  option[{i}] pov={opt.product_option_value_id} "
            f"{opt.option_value_name or ''} stock_qty={opt.stock_qty}"
        )


async def _load_db(product_id: int) -> tuple[Product | None, list[ProductOption]]:
    await init_products_db()
    async with AsyncProductsSessionLocal() as session:
        res = await session.execute(select(Product).where(Product.product_id == product_id))
        product = res.scalar_one_or_none()
        if not product:
            return None, []
        opts = (
            await session.execute(
                select(ProductOption).where(ProductOption.product_id == product.id)
            )
        ).scalars().all()
        return product, list(opts)


def _print_db(product: Product, options: list[ProductOption]) -> None:
    print(f"--- БД (products.db) product_id={product.product_id} ---")
    print(f"  name: {product.name}")
    print(f"  has_options: {product.has_options}")
    print(f"  product.stock_qty: {product.stock_qty}")
    opts_sum = sum(int(o.stock_qty or 0) for o in options)
    print(f"  сума option.stock_qty: {opts_sum}")
    for i, opt in enumerate(options, 1):
        print(
            f"  option[{i}] pov={opt.product_option_value_id} "
            f"{opt.option_value_name or ''} stock_qty={opt.stock_qty}"
        )


async def _check_one(item: FeedProductStock) -> int:
    _print_feed(item)
    product, options = await _load_db(item.product_id)
    if not product:
        print("  ❌ Товару немає в БД — запустіть синхронізацію товарів (/sync).")
        return 2
    _print_db(product, options)
    db_opts_sum = sum(int(o.stock_qty or 0) for o in options)
    feed_eff = item.feed_effective_stock
    db_eff = db_opts_sum if item.options or product.has_options else int(product.stock_qty or 0)
    hint = item.describe_mismatch_hint(product.stock_qty, db_opts_sum)
    ok = feed_eff == db_eff
    if ok:
        print(f"  ✅ Збіг effective stock: {feed_eff}")
        if hint:
            print(f"  ℹ️  {hint}")
        return 0
    print(f"  ❌ Розбіжність: фід effective={feed_eff}, БД effective={db_eff}")
    if hint:
        print(f"  ℹ️  {hint}")
    return 1


async def _run(args: argparse.Namespace) -> int:
    path = args.feed
    if not path:
        print(f"Завантаження фіду {FEED_URL} …")
        path = await stream_url_to_tempfile(FEED_URL, suffix=".xml", timeout_total=900.0)
    else:
        path = str(Path(path).expanduser().resolve())

    hits: list[FeedProductStock] = []
    if args.product_id is not None:
        for item in iter_feed_products_stock(path):
            if item.product_id == args.product_id:
                hits.append(item)
                break
    elif args.query:
        hits = find_feed_products_by_query(path, args.query)
    else:
        print("Вкажіть --query або --product-id", file=sys.stderr)
        return 2

    if not hits:
        print("У фіді нічого не знайдено за критерієм пошуку.")
        return 2

    worst = 0
    for item in hits:
        worst = max(worst, await _check_one(item))
        print()
    return worst


def main() -> None:
    parser = argparse.ArgumentParser(description="Порівняння stock_qty фід vs products.db")
    parser.add_argument("--query", "-q", help="Підрядок у назві/моделі/бренді")
    parser.add_argument("--product-id", "-p", type=int, help="Cosmy product_id")
    parser.add_argument("--feed", help="Локальний XML (без завантаження)")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
