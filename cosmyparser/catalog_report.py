#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Пакетне порівняння каталогу з конкурентами і Excel-звіт по цінах.

Запуск:
    .venv/bin/python catalog_report.py              # увесь каталог з databases/parser.db
    .venv/bin/python catalog_report.py 1,2,3        # лише вибрані id
    .venv/bin/python catalog_report.py --rebuild    # перебудувати Excel зі збереженого звіту
"""

from __future__ import annotations

import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from cosmy_parse import (
    SITES,
    clear_brand_presence_cache,
    ensure_brand_in_query,
    extract_brand,
    extract_volumes,
    format_volume,
    market_summary,
    search_all,
    set_search_feed_model,
    site_carries_brand,
    valid_price,
    volumes_equal,
)

SITE_NAMES = [site.name for site in SITES]
SITE_KINDS = {
    "html": "пошук на сайті",
    "woocommerce": "API пошуку",
    "live_search": "живий пошук",
    "cards": "картки пошуку",
    "brand_catalog": "увесь каталог бренду",
    "jsonld": "структуровані дані (JSON-LD)",
    "microdata": "мікророзмітка товару",
}

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT.parent / "databases" / "parser"
CATALOG_FILE = DATA_DIR / "catalog.json"
REPORT_XLSX = DATA_DIR / "price_report.xlsx"
REPORT_JSON = DATA_DIR / "price_report.json"


def _parser_store():
    import sys

    root = str(ROOT.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    from web import parser_store

    return parser_store


def _scope_for_output(path: Path) -> str:
    if path.name == "price_report.json":
        return "full"
    if path.name == "report.json" and path.parent.parent.name == "brands":
        return path.parent.name
    return "extra:" + path.stem


def _save_report(scope: str, report: dict) -> None:
    _parser_store().save_report(scope, report)


def _load_saved_report(scope: str) -> dict | None:
    return _parser_store().load_report(scope)

SKU_WORKERS = 8
# Ціна конкурента поза цим діапазоном відносно нашої — майже завжди інший товар/фасовка.
# 2× (типовий промах Krasota/набори) має бути odd_price, не «збіг».
# Реальний розкид роздріб/салон; ×2+ майже завжди інша фасовка або битий збіг (Krasota «Пошук»).
PRICE_LOW_RATIO = 0.50
PRICE_HIGH_RATIO = 1.85
# Нижче цього score не потрапляє в ринкову статистику (лише «≈ збіг?»).
MIN_MATCH_FOR_STATS = 0.64
AT_MARKET_PCT = 3.0
SUSPICIOUS_GAP_PCT = 30.0
# Менше стількох надійних магазинів — позиція «умовна», не «дешевше за всіх».
MIN_SHOPS_FOR_STRONG_POS = 2
RETRY_SHOP_STATUSES = frozenset({"unavailable", "error"})
# Жорстко вимкнено: друге коло по «н/д» магазинах роздувало прогін на тисячі позицій.
ENABLE_SHOP_RETRY = False
# Магазин, який мовчить на більшій частині бренду, не перепитуємо по кожному товару.
_WIDESPREAD_FAIL_RATIO = 0.45
_WIDESPREAD_PROBE_LIMIT = 2

STATUS_LABELS = {
    "match": "збіг",
    "other_volume": "інший обʼєм",
    "out_of_stock": "немає в наявності",
    "odd_price": "підозріла ціна",
    "low_match": "схожий, але не той",
    "not_found": "не знайдено",
    "unavailable": "сайт не відповів",
    "error": "помилка",
    "no_brand": "бренду немає",
}


def load_catalog() -> list:
    return _parser_store().load_catalog_products()


# ---------------------------------------------------------------- аналітика

def _volume_raw(item: dict):
    vols = extract_volumes(item.get("volume") or "") or extract_volumes(item.get("title") or "")
    return vols[0] if vols else None


def reference_volume(product: dict, results: list):
    """Обʼєм, з яким порівнюємо: опція з фіда → назва/query → консенсус збігів."""
    from_option = extract_volumes(product.get("option_value") or "")
    if from_option:
        return from_option[0]
    own = extract_volumes(product.get("query") or "") or extract_volumes(product.get("name") or "")
    if own:
        return own[0]
    our = product.get("our_price")
    counts = Counter()
    closeness = {}
    for item in results:
        vol = _volume_raw(item)
        price = valid_price(item.get("price"))
        if vol is None:
            continue
        key = format_volume(vol)
        counts[key] += 1
        if our and price:
            closeness[key] = min(closeness.get(key, 9e9), abs(price - our) / our)
    if not counts:
        return None
    best = sorted(counts, key=lambda k: (-counts[k], closeness.get(k, 9e9)))[0]
    return extract_volumes(best)[0]


def classify(product: dict, item: dict, ref_volume) -> str:
    price = valid_price(item.get("price"))
    our = product.get("our_price")
    vol = _volume_raw(item)
    if ref_volume is not None and vol is not None and not volumes_equal(vol, ref_volume):
        return "other_volume"
    if item.get("in_stock") is False:
        return "out_of_stock"
    score = item.get("match")
    try:
        if score is not None and float(score) < MIN_MATCH_FOR_STATS:
            return "low_match"
    except (TypeError, ValueError):
        pass
    if our and price and not (our * PRICE_LOW_RATIO <= price <= our * PRICE_HIGH_RATIO):
        return "odd_price"
    return "match"


def position_and_advice(our: Optional[float], stats: dict) -> tuple:
    position, advice = _position_and_advice(our, stats)
    low = stats.get("min")
    count = int(stats.get("count") or 0)
    if our and low and abs(our - low) / low * 100 > SUSPICIOUS_GAP_PCT:
        advice = (advice + " · " if advice else "") + "великий розрив — перевірте обʼєм/фасовку"
        if position == "дешевше за всіх":
            position = "перевірте збіг"
    if count < MIN_SHOPS_FOR_STRONG_POS and position == "дешевше за всіх":
        position = "умовно дешевше"
        advice = (advice + " · " if advice else "") + f"лише {count} магазин — слабке порівняння"
    return position, advice


def _position_and_advice(our: Optional[float], stats: dict) -> tuple:
    low, med = stats.get("min"), stats.get("median")
    if not our:
        return "немає нашої ціни", ""
    if low is None:
        return "немає конкурентів", "порівняти ні з ким — ціну тримаємо"
    diff_min = (our - low) / low * 100
    if our < low * 0.97:
        advice = "найдешевші на ринку"
        if our < low * 0.9:
            advice += f"; є запас до {low:.0f} ₴ (+{low - our:.0f} ₴)"
        return "дешевше за всіх", advice
    if diff_min <= AT_MARKET_PCT:
        return "на рівні мінімуму", "ціна конкурентна — тримати"
    if med is not None and our <= med * (1 + AT_MARKET_PCT / 100):
        return "в ринку", f"мінімум у {stats['min_site']} нижче на {our - low:.0f} ₴ ({diff_min:.1f}%)"
    return (
        "дорожче за ринок",
        f"дорожче мінімуму на {our - low:.0f} ₴ ({diff_min:.1f}%); "
        f"медіана {med:.0f} ₴ — розглянути зниження",
    )


def _shop_needs_retry(info: dict) -> bool:
    reason = str(info.get("reason") or "")
    # Уже свідомо пропущено в цьому прогоні — повтор лише подовжує той самий пропуск.
    if "занадто повільно" in reason:
        return False
    st = info.get("status") or ""
    if st in RETRY_SHOP_STATUSES:
        return True
    return "не відповів" in reason


def trim_retry_queue(
    queue: list[tuple[int, dict, str]],
    catalog_size: int,
) -> list[tuple[int, dict, str]]:
    """Залишити точкові таймаути. Магазин, що впав на більшості товарів, — лише кілька проб."""
    if catalog_size <= 0 or not queue:
        return list(queue)
    counts = Counter(site for _, _, site in queue)
    cutoff = max(4, int(catalog_size * _WIDESPREAD_FAIL_RATIO))
    probes: Counter = Counter()
    trimmed: list[tuple[int, dict, str]] = []
    for item in queue:
        site = item[2]
        if counts[site] < cutoff:
            trimmed.append(item)
            continue
        if probes[site] < _WIDESPREAD_PROBE_LIMIT:
            probes[site] += 1
            trimmed.append(item)
    return trimmed


def merge_search_payload(base: dict, patch: dict) -> dict:
    """Оновити results/misses для окремих магазинів (retry pass)."""
    by_site: dict[str, tuple[str, dict]] = {}
    for item in base.get("results") or []:
        by_site[item["site"]] = ("ok", item)
    for item in base.get("misses") or []:
        by_site[item["site"]] = ("miss", item)
    for item in patch.get("results") or []:
        by_site[item["site"]] = ("ok", item)
    for item in patch.get("misses") or []:
        by_site[item["site"]] = ("miss", item)
    results = [pair[1] for pair in by_site.values() if pair[0] == "ok"]
    misses = [pair[1] for pair in by_site.values() if pair[0] == "miss"]
    our = base.get("our_price")
    merged = {
        **base,
        "query": base.get("query") or patch.get("query"),
        "our_price": our,
        "results": results,
        "misses": misses,
    }
    merged.update(market_summary(results, our))
    return merged


def build_row(product: dict, payload: dict) -> dict:
    our = product.get("our_price")
    results = payload.get("results") or []
    ref_vol = reference_volume(product, results)

    shops = {}
    for item in results:
        status = classify(product, item, ref_vol)
        price = valid_price(item.get("price"))
        shops[item["site"]] = {
            "status": status,
            "price": price,
            "title": item.get("title"),
            "url": item.get("url"),
            "volume": item.get("volume"),
            "match": round(item.get("match") or 0, 2),
            "in_stock": item.get("in_stock", True),
            "note": item.get("price_note"),
            "diff_pct": round((price - our) / our * 100, 1) if our and price else None,
            "alternatives": item.get("alternatives") or [],
        }
    for miss in payload.get("misses") or []:
        shops[miss["site"]] = {
            "status": miss.get("status") or "not_found",
            "reason": miss.get("reason"),
            "title": miss.get("best_title"),
            "url": miss.get("best_url"),
            "price": miss.get("best_price"),
            "match": round(miss.get("match") or 0, 2) if miss.get("match") else None,
            "near": miss.get("near") or [],
        }

    matched = {site: info for site, info in shops.items() if info["status"] == "match"}
    prices = sorted(info["price"] for info in matched.values())
    stats = {"count": len(prices), "min": None, "min_site": None, "median": None, "avg": None, "max": None}
    if prices:
        min_site = min(matched, key=lambda s: matched[s]["price"])
        stats.update(
            {
                "min": prices[0],
                "min_site": min_site,
                "min_url": matched[min_site].get("url"),
                "median": round(statistics.median(prices), 2),
                "avg": round(sum(prices) / len(prices), 2),
                "max": prices[-1],
            }
        )
    position, advice = position_and_advice(our, stats)
    rank = None
    if our and prices:
        rank = 1 + sum(1 for p in prices if p < our - 0.5)

    carrying = [s for s, info in shops.items() if info["status"] != "no_brand"]
    return {
        **product,
        "ref_volume": format_volume(ref_vol) if ref_vol else None,
        "found": stats["count"],
        "found_label": f"{stats['count']} з {len(carrying) or len(SITE_NAMES)}",
        "shops_with_brand": len(carrying),
        "market_min": stats["min"],
        "min_site": stats["min_site"],
        "min_url": stats.get("min_url"),
        "market_median": stats["median"],
        "market_avg": stats["avg"],
        "market_max": stats["max"],
        "diff_min_uah": round(our - stats["min"], 2) if our and stats["min"] else None,
        "diff_min_pct": round((our - stats["min"]) / stats["min"] * 100, 1) if our and stats["min"] else None,
        "diff_median_pct": round((our - stats["median"]) / stats["median"] * 100, 1)
        if our and stats["median"] else None,
        "rank": rank,
        "rank_label": f"{rank} з {len(prices) + 1}" if rank else None,
        "position": position,
        "advice": advice,
        "shops": shops,
        "raw": payload,
    }


# ---------------------------------------------------------------- запуск

def run_catalog(
    progress: Optional[Callable[[dict], None]] = None,
    ids: Optional[set] = None,
    workers: int = SKU_WORKERS,
    output_json: Optional[Path] = None,
    output_xlsx: Optional[Path] = None,
    write_excel_file: bool = True,
    catalog: Optional[list] = None,
    report_scope: Optional[str] = None,
) -> dict:
    catalog = list(catalog) if catalog is not None else load_catalog()
    if ids:
        catalog = [p for p in catalog if p.get("id") in ids]
    total = len(catalog)
    started = datetime.now().isoformat(timespec="seconds")
    started_epoch = time.time()
    out_xlsx = Path(output_xlsx) if output_xlsx else REPORT_XLSX
    scope = report_scope or _scope_for_output(Path(output_json) if output_json else REPORT_JSON)

    # бренд з фіда -> аліаси рушія; бренд завжди присутній у запиті
    catalog = [
        {**p, "query": ensure_brand_in_query(p["query"], p.get("brand") or "")} for p in catalog
    ]
    samples_by_brand = {}
    for product in catalog:
        samples_by_brand.setdefault(extract_brand(product["query"]), []).append(product["query"])
    samples_by_brand = {b: tuple(q[:3]) for b, q in samples_by_brand.items()}
    done = 0

    def _beat(phase: str, product: str, **extra) -> None:
        if not progress:
            return
        progress({
            "state": "running",
            "phase": phase,
            "current": done,
            "total": total,
            "product": product,
            "started_at": started,
            "started_epoch": started_epoch,
            **extra,
        })

    # Наявність бренду з’ясовується всередині пошуку і кешується.
    # Окремий прохід по всіх магазинах перед товарами лише відкладав перші ціни в таблиці.
    clear_brand_presence_cache()
    _beat("products", "починаю товари…")

    rows = [None] * total

    def _write_partial(*, final: bool = False) -> dict:
        ready = [r for r in rows if r]
        stored = ready if final else [{k: v for k, v in r.items() if k != "raw"} for r in ready]
        report = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "started_at": started,
            "total": total if final else len(ready),
            "catalog_total": total,
            "sites": SITE_NAMES,
            "rows": stored,
            "partial": not final,
        }
        try:
            _save_report(scope, report)
        except OSError:
            pass
        report["rows"] = ready
        return report

    def _live_row(product: dict, payload: dict, *, partial: bool) -> dict:
        row = build_row(product, payload)
        row["partial"] = partial
        return row

    def work(idx: int, product: dict):
        brand = extract_brand(product["query"])
        bucket = {"results": [], "misses": []}

        def publish(partial: bool, payload: dict) -> None:
            _beat(
                "products",
                product.get("name") or product.get("query") or "",
                row=_live_row(product, payload, partial=partial),
            )

        def on_site(item: dict) -> None:
            if item.get("status") == "ok":
                bucket["results"].append(item)
            else:
                bucket["misses"].append(item)
            publish(True, {
                "query": product["query"],
                "our_price": product.get("our_price"),
                "results": bucket["results"],
                "misses": bucket["misses"],
            })

        publish(True, {
            "query": product["query"],
            "our_price": product.get("our_price"),
            "results": [],
            "misses": [],
        })
        try:
            set_search_feed_model(product.get("model"))
            payload = search_all(
                product["query"],
                our_price=product.get("our_price"),
                samples=samples_by_brand.get(brand, ()),
                on_site=on_site,
            )
            return idx, _live_row(product, payload, partial=False)
        except Exception as exc:
            return idx, {
                **product,
                "partial": False,
                "found": 0,
                "found_label": "помилка",
                "position": "немає конкурентів",
                "advice": "",
                "shops": {},
                "error": str(exc),
            }
        finally:
            set_search_feed_model(None)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(work, idx, product) for idx, product in enumerate(catalog)]
        for fut in as_completed(futs):
            try:
                idx, row = fut.result()
            except Exception:
                continue
            rows[idx] = row
            done += 1
            # частковий JSON рідше і без сирих карток — жива таблиця йде через live.json
            if done == 1 or done % 10 == 0 or done == total:
                _write_partial(final=False)
            if progress:
                progress({
                    "state": "running",
                    "phase": "products",
                    "current": done,
                    "total": total,
                    "product": row.get("name"),
                    "started_at": started,
                    "started_epoch": started_epoch,
                    "row": row,
                })

    # ENABLE_SHOP_RETRY=False: магазин «н/д» лишається як є, без другого кола.
    report = _write_partial(final=True)
    report["total"] = total
    report["partial"] = False
    if write_excel_file:
        try:
            write_excel(report, out_xlsx)
        except Exception as exc:
            report["excel_error"] = str(exc)
    if progress:
        progress({
            "state": "done",
            "phase": "done",
            "current": total,
            "total": total,
            "file": str(out_xlsx if write_excel_file else scope),
            "generated_at": report["generated_at"],
            "started_epoch": started_epoch,
        })
    return report


def rebuild_excel_from_saved() -> dict:
    report = _load_saved_report("full")
    if not report or not report.get("rows"):
        raise FileNotFoundError("У базі парсера немає повного звіту")
    write_excel(report, REPORT_XLSX)
    return report


# ---------------------------------------------------------------- Excel

BRAND_COLOR = "9E3F45"
FILL = {
    "дешевше за всіх": PatternFill("solid", fgColor="D9EFDF"),
    "на рівні мінімуму": PatternFill("solid", fgColor="EAF5EC"),
    "в ринку": PatternFill("solid", fgColor="FFF4D6"),
    "дорожче за ринок": PatternFill("solid", fgColor="F8DAD7"),
    "немає конкурентів": PatternFill("solid", fgColor="EEEEEE"),
}
CHEAPER_FILL = PatternFill("solid", fgColor="F8DAD7")   # конкурент дешевший за нас
PRICIER_FILL = PatternFill("solid", fgColor="D9EFDF")   # конкурент дорожчий
MIN_FONT = Font(bold=True, name="Calibri")
GREY_FONT = Font(italic=True, color="8A8A8A", name="Calibri")
LINK_FONT = Font(color="0563C1", underline="single", name="Calibri")
THIN = Border(*(Side(style="thin", color="E4D8CC"),) * 4)
WRAP = Alignment(wrap_text=True, vertical="center")
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
MONEY = '#,##0 "₴"'
PCT = '+0.0"%";-0.0"%";0"%"'


def _header_row(ws, headers: list, row: int = 1, height: int = 34):
    for col, title in enumerate(headers, start=1):
        cell = ws.cell(row, col, title)
        cell.font = Font(bold=True, color="FFFFFF", name="Calibri")
        cell.fill = PatternFill("solid", fgColor=BRAND_COLOR)
        cell.alignment = CENTER
        cell.border = THIN
    ws.row_dimensions[row].height = height


def _widths(ws, widths: list):
    for idx, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width


def _link(cell, url, label=None):
    if not url:
        return
    cell.value = label if label is not None else url
    cell.hyperlink = url
    cell.font = LINK_FONT


def _style_row(ws, row: int, ncols: int, fill=None):
    for col in range(1, ncols + 1):
        cell = ws.cell(row, col)
        cell.border = THIN
        if cell.alignment is None or not cell.alignment.wrap_text:
            cell.alignment = WRAP
        if fill is not None:
            cell.fill = fill


def _product_label(row: dict) -> str:
    return row.get("name_uk") or row.get("name") or row.get("query") or ""


def _active_sites(rows: list) -> list:
    """Магазини, у яких бренд знайшовся хоча б для одного SKU — лише вони йдуть у колонки."""
    active = []
    for site in SITE_NAMES:
        if any((r.get("shops") or {}).get(site, {}).get("status") not in (None, "no_brand") for r in rows):
            active.append(site)
    return active


def write_excel(report: dict, path: Path) -> Path:
    rows = [r for r in report["rows"] if r]
    sites = _active_sites(rows)
    wb = Workbook()
    _sheet_summary(wb.active, report, rows, sites)
    _sheet_compare(wb.create_sheet("Порівняння"), rows, sites)
    _sheet_details(wb.create_sheet("Деталі збігів"), rows)
    _sheet_missing(wb.create_sheet("Не знайдено"), rows)
    _sheet_shops(wb.create_sheet("Магазини"), rows)
    _sheet_help(wb.create_sheet("Як читати"), report, sites)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def _sheet_summary(ws, report: dict, rows: list, sites: list):
    ws.title = "Підсумок"
    ws["A1"] = "Порівняння цін Cosmy з конкурентами"
    ws["A1"].font = Font(bold=True, size=16, color=BRAND_COLOR)
    ws["A2"] = f"Згенеровано {report.get('generated_at', '')[:16].replace('T', ' ')} · " \
               f"товарів: {len(rows)} · магазинів перевірено: {len(SITE_NAMES)}, з брендом: {len(sites)}"
    ws["A2"].font = GREY_FONT

    with_comp = [r for r in rows if r.get("found")]
    positions = Counter(r.get("position") for r in rows)
    diffs = [r["diff_median_pct"] for r in with_comp if r.get("diff_median_pct") is not None]
    avg_all = round(sum(r["found"] for r in rows) / max(len(rows), 1), 1)
    avg_if_any = (
        round(sum(r["found"] for r in with_comp) / len(with_comp), 1) if with_comp else 0
    )
    kpis = [
        ("Товарів у каталозі", len(rows)),
        ("Знайдено хоча б в одного конкурента", f"{len(with_comp)} ({len(with_comp) / max(len(rows), 1):.0%})"),
        (
            "Середня кількість магазинів із точним збігом на товар",
            f"{avg_all} (по всьому каталогу; якщо лише зі знайденими — {avg_if_any})",
        ),
        ("Дешевше за всіх", positions.get("дешевше за всіх", 0)),
        ("На рівні мінімуму (±3%)", positions.get("на рівні мінімуму", 0)),
        ("В ринку (між мінімумом і медіаною)", positions.get("в ринку", 0)),
        ("Дорожче за ринок", positions.get("дорожче за ринок", 0)),
        ("Без конкурентів", positions.get("немає конкурентів", 0)),
        ("Наша ціна vs медіана ринку (медіана відхилень)",
         f"{statistics.median(diffs):+.1f}%" if diffs else "—"),
    ]
    ws["A4"] = "Ключові показники"
    ws["A4"].font = Font(bold=True, size=12, color=BRAND_COLOR)
    for idx, (label, value) in enumerate(kpis, start=5):
        ws.cell(idx, 1, label).border = THIN
        cell = ws.cell(idx, 2, value)
        cell.border = THIN
        cell.font = Font(bold=True, name="Calibri")
        cell.alignment = Alignment(horizontal="right")
        fill = FILL.get({8: "дешевше за всіх", 9: "на рівні мінімуму", 10: "в ринку",
                         11: "дорожче за ринок", 12: "немає конкурентів"}.get(idx, ""))
        if fill:
            ws.cell(idx, 1).fill = fill
            cell.fill = fill

    def top_table(start: int, title: str, items: list, headers: list, values):
        ws.cell(start, 1, title).font = Font(bold=True, size=12, color=BRAND_COLOR)
        _header_row(ws, headers, row=start + 1, height=30)
        for offset, item in enumerate(items, start=start + 2):
            for col, (value, fmt) in enumerate(values(item), start=1):
                cell = ws.cell(offset, col, value)
                if fmt:
                    cell.number_format = fmt
            _link(ws.cell(offset, 6), item.get("min_url"), item.get("min_site"))
            _style_row(ws, offset, len(headers))
        return start + 3 + max(len(items), 1)

    pricier = sorted(
        [r for r in rows if r.get("position") == "дорожче за ринок"],
        key=lambda r: -(r.get("diff_min_uah") or 0),
    )[:20]
    headers = ["Товар", "Модель", "Наша ціна", "Мін. ринку", "Різниця, ₴", "Де мінімум", "Різниця, %", "Конкурентів"]
    row_values = lambda r: [
        (_product_label(r), None), (r.get("model"), None), (r.get("our_price"), MONEY),
        (r.get("market_min"), MONEY), (r.get("diff_min_uah"), MONEY), (None, None),
        (r.get("diff_min_pct"), PCT), (r.get("found_label"), None),
    ]
    nxt = top_table(15, "Найбільше переплачуємо (топ-20 за різницею з мінімумом)", pricier, headers, row_values)

    room = sorted(
        [r for r in rows if r.get("position") == "дешевше за всіх"],
        key=lambda r: r.get("diff_min_pct") or 0,
    )[:15]
    headers_room = ["Товар", "Модель", "Наша ціна", "Мін. ринку", "Запас, ₴", "Де мінімум", "Дешевше на, %", "Конкурентів"]
    room_values = lambda r: [
        (_product_label(r), None), (r.get("model"), None), (r.get("our_price"), MONEY),
        (r.get("market_min"), MONEY), (-(r.get("diff_min_uah") or 0), MONEY), (None, None),
        (r.get("diff_min_pct"), PCT), (r.get("found_label"), None),
    ]
    top_table(nxt, "Де ми найдешевші — можна підняти ціну (топ-15)", room, headers_room, room_values)
    _widths(ws, [58, 14, 13, 13, 13, 16, 13, 13])


def _sheet_compare(ws, rows: list, sites: list):
    base = [
        "ID", "Модель", "Товар", "Назва (EN)", "Обʼєм", "Наша ціна", "Знайдено",
        "Мін. ринку", "Де мінімум", "Медіана", "Макс.", "Vs мін., ₴", "Vs мін., %",
        "Vs медіана, %", "Місце (1 = найдешевший)", "Позиція", "Рекомендація",
    ]
    headers = base + sites
    _header_row(ws, headers, height=42)
    ws.freeze_panes = ws.cell(2, 4)
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}1"
    first_site_col = len(base) + 1

    for row in rows:
        values = [
            row.get("id"), row.get("model"), _product_label(row), row.get("name"),
            row.get("ref_volume"), row.get("our_price"), row.get("found_label"),
            row.get("market_min"), None, row.get("market_median"), row.get("market_max"),
            row.get("diff_min_uah"), row.get("diff_min_pct"), row.get("diff_median_pct"),
            row.get("rank_label"), row.get("position"), row.get("advice"),
        ]
        ws.append(values)
        r = ws.max_row
        _style_row(ws, r, len(headers))
        pos_fill = FILL.get(row.get("position"))
        for col in (16, 17):
            if pos_fill:
                ws.cell(r, col).fill = pos_fill
        for col in (6, 8, 10, 11, 12):
            ws.cell(r, col).number_format = MONEY
        for col in (13, 14):
            ws.cell(r, col).number_format = PCT
        ws.cell(r, 6).font = MIN_FONT
        _link(ws.cell(r, 9), row.get("min_url"), row.get("min_site"))

        shops = row.get("shops") or {}
        for offset, site in enumerate(sites):
            info = shops.get(site) or {}
            cell = ws.cell(r, first_site_col + offset)
            cell.border = THIN
            cell.alignment = CENTER
            status = info.get("status")
            price = info.get("price")
            if status in ("match", "other_volume", "out_of_stock", "odd_price") and price:
                _link(cell, info.get("url"), price)
                cell.number_format = MONEY
                if status == "match":
                    our = row.get("our_price")
                    if our:
                        cell.fill = CHEAPER_FILL if price < our - 0.5 else PRICIER_FILL if price > our + 0.5 else PatternFill()
                    if price == row.get("market_min"):
                        cell.font = Font(bold=True, color="0563C1", underline="single", name="Calibri")
                else:
                    cell.font = Font(italic=True, color="8A8A8A", underline="single", name="Calibri")
                text = f"{info.get('title') or ''}\nОбʼєм: {info.get('volume') or '—'} · схожість {int((info.get('match') or 0) * 100)}%"
                if status != "match":
                    text = f"[{STATUS_LABELS[status]} — не в статистиці]\n" + text
                cell.comment = Comment(text[:500], "Cosmy parser", width=320, height=110)
            elif status == "low_match":
                cell.value = "≈"
                cell.font = GREY_FONT
                cell.comment = Comment(
                    f"Схожий, але не той товар:\n{info.get('title') or ''}\n{info.get('price') or ''} ₴ · "
                    f"схожість {int((info.get('match') or 0) * 100)}%",
                    "Cosmy parser", width=320, height=110,
                )
            elif status in ("not_found",):
                cell.value = "—"
                cell.font = GREY_FONT
            elif status == "no_brand":
                cell.value = "нема бренду"
                cell.font = GREY_FONT
            elif status:
                cell.value = "?"
                cell.font = GREY_FONT
                cell.comment = Comment(info.get("reason") or STATUS_LABELS.get(status, status), "Cosmy parser")

    _widths(ws, [6, 11, 46, 38, 9, 11, 10, 11, 14, 11, 11, 11, 10, 11, 12, 18, 44] + [12] * len(sites))


def _sheet_details(ws, rows: list):
    headers = [
        "ID", "Модель", "Товар", "Наша ціна", "Магазин", "Статус", "Назва в магазині",
        "Ціна", "Обʼєм", "Схожість, %", "Vs наша, %", "Посилання", "Примітка",
    ]
    _header_row(ws, headers)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}1"
    order = {k: i for i, k in enumerate(STATUS_LABELS)}
    status_fill = {
        "match": PatternFill("solid", fgColor="EAF5EC"),
        "other_volume": PatternFill("solid", fgColor="FFF4D6"),
        "out_of_stock": PatternFill("solid", fgColor="F2F2F2"),
        "odd_price": PatternFill("solid", fgColor="FFF4D6"),
        "low_match": PatternFill("solid", fgColor="F2F2F2"),
    }
    for row in rows:
        shops = row.get("shops") or {}
        for site in sorted(shops, key=lambda s: (order.get(shops[s]["status"], 99), s)):
            info = shops[site]
            if info["status"] == "no_brand":
                continue
            note_bits = []
            if info.get("reason"):
                note_bits.append(info["reason"])
            if info.get("note"):
                note_bits.append(info["note"])
            for alt in (info.get("alternatives") or info.get("near") or [])[:2]:
                note_bits.append(f"ще: {alt.get('title')} — {alt.get('price') or '?'} ₴ ({int((alt.get('match') or 0) * 100)}%)")
            ws.append([
                row.get("id"), row.get("model"), _product_label(row), row.get("our_price"), site,
                STATUS_LABELS.get(info["status"], info["status"]), info.get("title"), info.get("price"),
                info.get("volume"), int((info.get("match") or 0) * 100) if info.get("match") else None,
                info.get("diff_pct"), None, "\n".join(note_bits),
            ])
            r = ws.max_row
            _style_row(ws, r, len(headers), status_fill.get(info["status"]))
            ws.cell(r, 4).number_format = MONEY
            ws.cell(r, 8).number_format = MONEY
            ws.cell(r, 11).number_format = PCT
            _link(ws.cell(r, 12), info.get("url"), "відкрити" if info.get("url") else None)
    _widths(ws, [6, 11, 42, 11, 13, 17, 60, 11, 9, 10, 10, 11, 60])


def _sheet_missing(ws, rows: list):
    headers = ["ID", "Модель", "Товар", "Назва (EN)", "Наша ціна", "Магазинів з брендом", "Найближчі кандидати"]
    _header_row(ws, headers)
    ws.freeze_panes = "A2"
    for row in rows:
        if row.get("found"):
            continue
        near = []
        for site, info in (row.get("shops") or {}).items():
            if info["status"] in ("low_match", "other_volume", "out_of_stock", "odd_price") and info.get("title"):
                near.append(f"{site}: {info['title']} — {info.get('price') or '?'} ₴ "
                            f"[{STATUS_LABELS[info['status']]}]")
        ws.append([
            row.get("id"), row.get("model"), _product_label(row), row.get("name"), row.get("our_price"),
            row.get("shops_with_brand"), "\n".join(near) or "у жодному магазині не знайдено схожого",
        ])
        r = ws.max_row
        _style_row(ws, r, len(headers))
        ws.cell(r, 5).number_format = MONEY
    _widths(ws, [6, 11, 44, 44, 11, 12, 90])


def _sheet_shops(ws, rows: list):
    headers = [
        "Магазин", "Джерело даних", "Бренд в асортименті", "Знайдено товарів", "Покриття каталогу",
        "Разів найдешевший", "Ціна vs наша (медіана)", "Дешевші за нас", "Дорожчі за нас",
        "Інший обʼєм / нема в наявності", "Сайт",
    ]
    _header_row(ws, headers)
    total = len(rows) or 1
    for site in SITES:
        stats = Counter()
        diffs = []
        for row in rows:
            info = (row.get("shops") or {}).get(site.name) or {}
            status = info.get("status")
            stats[status] += 1
            if status == "match":
                if info.get("diff_pct") is not None:
                    diffs.append(info["diff_pct"])
                if row.get("min_site") == site.name:
                    stats["cheapest"] += 1
        found = stats["match"]
        presence = site_carries_brand(site, rows[0]["query"] if rows else "")
        if presence is True or found:
            carries = "так"
        elif presence is False:
            carries = "ні"
        else:
            carries = "невідомо"
        ws.append([
            site.name, SITE_KINDS.get(site.kind, site.kind), carries, found, found / total,
            stats["cheapest"], statistics.median(diffs) if diffs else None,
            sum(1 for d in diffs if d < -0.5), sum(1 for d in diffs if d > 0.5),
            stats["other_volume"] + stats["out_of_stock"] + stats["odd_price"], None,
        ])
        r = ws.max_row
        _style_row(ws, r, len(headers), None if found else PatternFill("solid", fgColor="F2F2F2"))
        ws.cell(r, 5).number_format = "0%"
        ws.cell(r, 7).number_format = PCT
        _link(ws.cell(r, 11), site.base_url)
    _widths(ws, [16, 22, 12, 11, 11, 12, 14, 11, 11, 16, 32])


def _sheet_help(ws, report: dict, sites: list):
    ws["A1"] = "Як читати звіт"
    ws["A1"].font = Font(bold=True, size=14, color=BRAND_COLOR)
    notes = [
        "",
        "ПОРІВНЯННЯ — один рядок на товар.",
        "• «Знайдено N з M»: у скількох магазинах знайшли саме цей товар, з тих M, де бренд взагалі продається.",
        "• «Мін. ринку / Медіана / Макс.» рахуються лише з точних збігів у наявності того ж обʼєму.",
        "• «Vs мін.» — наскільки наша ціна вища (+) або нижча (−) за найдешевшого конкурента.",
        "• «Місце»: 1 — ми найдешевші; 3 з 5 — двоє конкурентів дешевші за нас.",
        "• Колонки магазинів: ціна клікабельна (веде на товар), наведіть курсор — побачите назву й обʼєм у магазині.",
        "   червоне тло — конкурент дешевший за нас, зелене — дорожчий, жирним — мінімум ринку;",
        "   сірий курсив — знайдено, але не в статистиці (інший обʼєм / немає в наявності / підозріла ціна);",
        "   «≈» — у магазині є схожий товар лінійки, але не той (наведіть курсор); «—» — бренд є, товару немає.",
        "",
        "ПОЗИЦІЯ",
        "• дешевше за всіх — наша ціна нижча за мінімум ринку більш ніж на 3%;",
        "• на рівні мінімуму — у межах +3% від найдешевшого;",
        "• «В ринку» — наша ціна вища за найдешевшого конкурента більш ніж на 3%, але не вища за медіану ринку (ти типово в «середній» частині цін).",
        "• дорожче за ринок — вище медіани конкурентів.",
        "",
        "ІНШІ АРКУШІ",
        "• Підсумок — ключові цифри, топ товарів, де переплачуємо, і де є запас підняти ціну.",
        "• Деталі збігів — кожна пара товар × магазин зі статусом, назвою в магазині, схожістю та альтернативами.",
        "• Не знайдено — товари без жодного точного збігу з найближчими кандидатами для ручної перевірки.",
        "• Магазини — покриття, скільки разів магазин найдешевший і його типовий рівень цін відносно нас.",
        "",
        "ЯК ШУКАЄМО",
        "• Для Kstore, Beautis, Kosmetolog забираємо весь каталог бренду з усіма сторінками і зіставляємо локально — пропусків через пошук сайту немає.",
        "• Для інших магазинів — кілька варіантів запиту (повна назва, без обʼєму, «бренд + лінійка»), доки не знайдеться точний збіг.",
        "• Магазини, де бренду немає зовсім, пропускаються й позначені на аркуші «Магазини».",
        "• Збіг перевіряється за брендом, ключовими словами назви, цифрами (C-Vit 5 ≠ C-Vit), типом засобу (крем для очей ≠ крем для обличчя), SPF і обʼємом.",
        f"• Магазинів перевірено: {len(SITE_NAMES)} ({', '.join(SITE_NAMES)}). З брендом: {', '.join(sites) or '—'}.",
        f"Згенеровано: {report.get('generated_at')}",
    ]
    for idx, line in enumerate(notes, start=2):
        ws[f"A{idx}"] = line
        ws[f"A{idx}"].alignment = Alignment(wrap_text=True)
        if line and line.isupper():
            ws[f"A{idx}"].font = Font(bold=True, color=BRAND_COLOR)
    ws.column_dimensions["A"].width = 130


if __name__ == "__main__":
    if "--rebuild" in sys.argv:
        rebuild_excel_from_saved()
        print("Excel перебудовано:", REPORT_XLSX)
        sys.exit(0)

    selected = None
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args:
        selected = {int(x) for x in args[0].split(",") if x.strip()}

    def _print(status):
        if status.get("state") == "running":
            print(f"[{status['current']}/{status['total']}] {status.get('product')}", flush=True)
        else:
            print("Готово:", status.get("file"))

    report = run_catalog(progress=_print, ids=selected)
    rows = [r for r in report["rows"] if r]
    found = sum(1 for r in rows if r.get("found"))
    print(f"Знайдено у конкурентів: {found} з {len(rows)} товарів")
    print(Counter(r.get("position") for r in rows).most_common())
