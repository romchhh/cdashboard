#!/usr/bin/env python3
"""
Залишок у фіді Cosmy — БЕЗ sqlalchemy / venv (лише стандартна бібліотека).

На VPS:

  python3 scripts/check_product_stock_feed_lite.py --query "Needly A to Cream"
  python3 scripts/check_product_stock_feed_lite.py --feed /tmp/analytics_bot.xml -q "Needly"

Повне порівняння з БД — myenv + scripts/check_product_stock_feed.py
"""

from __future__ import annotations

import argparse
import ssl
import sys
import tempfile
import urllib.request
from dataclasses import dataclass, field
from typing import Iterator
from xml.etree import ElementTree as ET

FEED_URL = "https://cosmy.com.ua/price/analytics_bot.xml"


def _local_tag(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _text(elem: ET.Element | None) -> str:
    if elem is None or not elem.text:
        return ""
    return (elem.text or "").strip()


def _int(elem: ET.Element | None) -> int | None:
    s = _text(elem)
    if not s:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def iter_feed_products(path: str) -> Iterator[ET.Element]:
    stack: list[str] = []
    for event, elem in ET.iterparse(path, events=("start", "end")):
        tag = _local_tag(elem.tag)
        if event == "start":
            stack.append(tag)
            continue
        if stack:
            stack.pop()
        parent = stack[-1] if stack else ""
        if tag in ("category", "brand") and parent == "product":
            continue
        if tag != "product":
            continue
        yield elem
        elem.clear()


@dataclass
class StockSnap:
    product_id: int
    name: str
    brand: str | None
    has_options: bool
    product_stock: int | None
    options: list[tuple[int | None, str | None, int | None]] = field(default_factory=list)

    @property
    def options_sum(self) -> int:
        return sum(int(x[2] or 0) for x in self.options)

    @property
    def effective(self) -> int:
        if self.has_options or self.options:
            return self.options_sum if self.options else int(self.product_stock or 0)
        return int(self.product_stock or 0)


def parse_product(elem: ET.Element) -> StockSnap | None:
    pid = _int(elem.find("product_id"))
    if pid is None:
        return None
    has_opt = bool(_int(elem.find("has_options")) or 0)
    opts: list[tuple[int | None, str | None, int | None]] = []
    options_el = elem.find("options")
    if options_el is not None:
        for o in options_el.findall("option"):
            opts.append(
                (
                    _int(o.find("product_option_value_id")),
                    _text(o.find("option_value_name")) or None,
                    _int(o.find("stock_qty")),
                )
            )
    return StockSnap(
        product_id=pid,
        name=_text(elem.find("name")) or f"id={pid}",
        brand=_text(elem.find("brand")) or None,
        has_options=has_opt,
        product_stock=_int(elem.find("stock_qty")),
        options=opts,
    )


def download_feed(url: str, dest: str) -> None:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": "CosmyFinancesBot/stock-check"})
    with urllib.request.urlopen(req, context=ctx, timeout=600) as resp:
        with open(dest, "wb") as out:
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                out.write(chunk)


def print_snap(s: StockSnap) -> None:
    print(f"product_id={s.product_id}")
    print(f"  name: {s.name}")
    print(f"  brand: {s.brand or '—'}")
    print(f"  has_options: {s.has_options}")
    print(f"  product <stock_qty>: {s.product_stock}")
    print(f"  сума option stock_qty: {s.options_sum}")
    print(f"  effective (для порівняння з UI): {s.effective}")
    for i, (pov, name, qty) in enumerate(s.options, 1):
        print(f"  option[{i}] pov={pov} {name or ''} stock_qty={qty}")
    if s.effective > 0 and (s.product_stock or 0) == 0 and s.options_sum > 0:
        print(
            "  ℹ️  Залишок на опції, product.stock_qty=0 — у кабінеті «0» на рівні товару нормально,"
        )
        print("     якщо екран не показує варіацію; дивіться stock опції або effective вище.")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("-q", "--query", required=True, help="Підрядок у назві")
    p.add_argument("--product-id", type=int)
    p.add_argument("--feed", help="Локальний XML")
    args = p.parse_args()
    q = args.query.casefold()

    path = args.feed
    tmp: str | None = None
    if not path:
        fd, tmp = tempfile.mkstemp(suffix=".xml")
        import os

        os.close(fd)
        print(f"Завантаження {FEED_URL} …", file=sys.stderr)
        download_feed(FEED_URL, tmp)
        path = tmp

    hits: list[StockSnap] = []
    try:
        for elem in iter_feed_products(path):
            snap = parse_product(elem)
            if not snap:
                continue
            if args.product_id is not None and snap.product_id != args.product_id:
                continue
            hay = f"{snap.name} {snap.brand or ''} {snap.product_id}".casefold()
            if args.product_id is not None or q in hay:
                hits.append(snap)
                if args.product_id is not None:
                    break
    finally:
        if tmp:
            import os

            try:
                os.unlink(tmp)
            except OSError:
                pass

    if not hits:
        print("Не знайдено у фіді.")
        return 2
    for s in hits:
        print("---")
        print_snap(s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
