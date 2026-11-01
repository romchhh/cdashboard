#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Конвертує експорт Finadmin (tz.txt) у каталог databases/parser.db."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from web.parser_store import save_catalog_products  # noqa: E402

TZ_FILE = ROOT / "tz.txt"


def money_ua(value: str):
    text = (value or "").replace("\xa0", " ")
    text = re.sub(r"(грн|₴)", "", text)
    text = text.replace(" ", "").replace(",", ".")
    if not text or text in ("—", "-", "–"):
        return None
    try:
        number = float(text.strip())
        return number if number > 0 else None
    except ValueError:
        return None


def pct(value: str):
    text = (value or "").strip().replace("%", "").replace(",", ".")
    if not text or text in ("—", "-", "–"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def int_num(value: str) -> int:
    match = re.search(r"(\d+)", value or "")
    return int(match.group(1)) if match else 0


def parse_tz(path: Path = TZ_FILE) -> list:
    lines = path.read_text(encoding="utf-8").splitlines()
    products = []
    i = 0
    while i < len(lines):
        raw = lines[i].strip()
        match = re.match(r"^(\d+)\s*$", raw) or re.match(r"^(\d+)\t\s*$", raw)
        if not match:
            i += 1
            continue

        idx = int(match.group(1))
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        name_line = lines[j].strip() if j < len(lines) else ""
        # інколи в експорті немає пробілу: «маска- SesDerma …»
        name_split = re.split(r"\s+-\s+|-(?=\s*[A-Za-z])", name_line, maxsplit=1)
        if j >= len(lines) or len(name_split) < 2:
            i += 1
            continue

        uk, latin = name_split[0].strip(), name_split[1].strip()
        j += 1
        while j < len(lines) and not lines[j].strip():
            j += 1

        brand, model, margin = "SesDerma", f"ses-{idx:03d}", None
        if j < len(lines) and "\t" in lines[j]:
            parts = lines[j].split("\t")
            brand = (parts[0] or "").strip() or brand
            if len(parts) > 1 and parts[1].strip():
                model = parts[1].strip()
            if len(parts) > 2:
                margin = pct(parts[2])
            j += 1

        summary = None
        while j < len(lines):
            line = lines[j].strip()
            if re.match(r"^\d+\s*$", line) or re.match(r"^\d+\t\s*$", line):
                break
            if "₴" in line and ("грн" in line or line.count("\t") >= 4):
                summary = line
                j += 1
                break
            j += 1

        our_price = None
        if summary:
            cols = summary.split("\t")
            while len(cols) < 3:
                cols.append("")
            # колонки: продажі | виручка | ціна на сайті | ...
            our_price = money_ua(cols[2])
        products.append(
            {
                "id": idx,
                "model": model,
                "brand": brand,
                "name": latin,
                "name_uk": uk,
                "query": latin,
                "our_price": our_price,
            }
        )
        i = j

    products.sort(key=lambda item: item["id"])
    return products


def main():
    products = parse_tz()
    save_catalog_products(products)
    priced = sum(1 for item in products if item.get("our_price"))
    print(f"Збережено {len(products)} товарів (з ціною: {priced})")
    print("  → databases/parser.db")
    print("Далі:  .venv/bin/python catalog_report.py")


if __name__ == "__main__":
    main()
