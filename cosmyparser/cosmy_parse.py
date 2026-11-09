#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Парсер цін конкурентів за назвою товару.

Шукає товар на сайтах конкурентів, витягує ціну, фото, обʼєм і варіанти,
і порівнює з нашою ціною.

Запуск CLI:
    python cosmy_parse.py

Веб-інтерфейс:
    python app.py
"""

from __future__ import annotations

import json
import re
import sys
import threading
import time
import difflib
import unicodedata
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "uk-UA,uk;q=0.9,ru;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

TIMEOUT = 10
MAX_CANDIDATES = 10
# Скільки карток з лістингу оцінюємо матчингом (решту відсікаємо префетч-скором).
LISTING_MATCH_POOL = 40
MIN_PRICE = 10

VOLUME_RE = re.compile(
    r"(?<![a-zа-яіїєґё0-9])(\d+(?:[.,]\d+)?)\s*-?\s*(мл|ml|мг|mg|г|g|кг|kg|шт|pcs)\b",
    flags=re.IGNORECASE,
)
PERCENT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
SPF_RE = re.compile(r"\bspf\s*[:\-]?\s*(\d+)\b", flags=re.IGNORECASE)
PRICE_IN_TEXT_RE = re.compile(
    r"(\d[\d\s]{0,8}(?:[.,]\d{1,2})?)\s*(?:грн|₴|UAH)",
    flags=re.IGNORECASE,
)
LOGO_RE = re.compile(r"logo|favicon|sprite|placeholder|og-logo", re.IGNORECASE)
LATIN_NAME_RE = re.compile(
    r"[A-Za-z][A-Za-z0-9&+]{1,}(?:[ \-]+[A-Za-z0-9&+]{1,}(?:[.,]\d+)?%?)+"
)
PACKAGING_RE = re.compile(
    r"(refill|рефіл|рефил|змінн\w*|сменн\w*|normal|стандарт|звичайн\w*|туба|банка|коробк\w*)",
    flags=re.IGNORECASE,
)

UNIT_MAP = {
    "мл": "ml",
    "ml": "ml",
    "мг": "mg",
    "mg": "mg",
    "г": "g",
    "g": "g",
    "кг": "kg",
    "kg": "kg",
    "шт": "pcs",
    "pcs": "pcs",
}
UNIT_LABEL = {
    "ml": "мл",
    "mg": "мг",
    "g": "г",
    "kg": "кг",
    "pcs": "шт",
}

KIND_TOKENS = {
    "cream": ("cream", "creme", "крем", "крему", "крема"),
    "gel": ("gel", "гель", "гелю"),
    "serum": ("serum", "сироват", "сыворот"),
    "cleanser": ("cleansing", "cleanser", "очищаюч", "вмиван", "умыван"),
    "mask": ("mask", "маска", "masque", "маску", "treatment"),
    "oil": ("oil", "олія", "масло"),
    "toner": ("toner", "tonic", "тонік", "тоник"),
    "peel": ("peel", "пілінг", "пилинг"),
    "shampoo": ("shampoo", "шампунь"),
    "conditioner": ("conditioner", "кондиціонер", "кондиционер", "бальзам"),
    "mousse": ("mousse", "мус", "мусс"),
    "lotion": ("lotion", "лосьйон", "лосьон"),
    "spray": ("spray", "спрей"),
    "mist": ("mist", "міст"),
    "fluid": ("fluid", "флюїд", "флюид"),
    "milk": ("milk", "молочко"),
}
KIND_CONFLICTS = (
    ({"cream"}, {"cleanser", "gel", "peel", "mousse", "shampoo"}),
    ({"cleanser", "gel"}, {"cream", "serum"}),
    ({"serum"}, {"cleanser", "mask", "cream", "gel", "fluid"}),
    ({"cream"}, {"serum"}),
    ({"fluid"}, {"serum"}),
    ({"milk"}, {"cream", "gel", "serum"}),
    ({"mask"}, {"cream", "serum", "cleanser", "shampoo", "fluid", "gel"}),
    ({"shampoo"}, {"conditioner", "mask", "spray", "cream"}),
    ({"conditioner"}, {"shampoo"}),
    ({"mousse"}, {"cream", "gel"}),
    ({"spray", "mist"}, {"shampoo", "cream"}),
    ({"toner"}, {"cream", "serum", "shampoo"}),
    ({"cream", "gel", "serum", "lotion"}, {"oil"}),
    ({"oil"}, {"cream", "gel", "serum", "mask", "lotion"}),
)
BRAND_ALIASES = {
    "sesderma": ["sesderma", "ses derma", "сесдерма"],
    "cuskin": ["cuskin", "cu skin", "cu-skin"],
    "xiaomoxuan": ["xiaomoxuan", "xiao mo xuan"],
    "sensilis": ["sensilis"],
    "braderm": ["braderm"],
    "vivant": ["vivant"],
    "sultane": ["sultane", "la sultane"],
    "dsd": ["dsd", "divination"],
    "dermaseries": ["derma series", "dermaseries"],
    "academie": [
        "academie", "académie", "академі", "академи", "академія", "академия",
        "academie scientifique", "academie scientifique de beaute",
        "académie scientifique", "asb",
    ],
    "erayba": ["erayba", "ерайба"],
    "medik8": ["medik8", "medik 8", "медік8", "медик8"],
    "moroccanoil": ["moroccanoil", "moroccan oil", "марокканойл"],
    "babor": ["babor", "бабор"],
    "acnemy": ["acnemy", "акнемі", "акнеми"],
    "christina": ["christina", "кристіна", "кристина", "христина"],
    "gigi": ["gigi", "gigi cosmetic", "джіджі", "джиджи"],
    "holyland": ["holy land", "holyland", "холі ленд", "холиленд"],
}
# Якщо в назві конкурента є ознака з групи, а в запиті немає жодного слова з цієї ж групи —
# це інший товар лінійки (крем для очей замість крему для обличчя, пробник, набір тощо).
MODIFIER_GROUPS = {
    "eye": ("eye", "eyes", "очей", "глаз", "повік"),
    "sun": ("spf", "sun", "sunscreen", "сонцезахис", "солнцезащит"),
    "body": ("body", "тіла", "тела", "тіло", "тело"),
    "hand": ("hand", "hands", "рук"),
    "lip": ("lip", "lips", "губ"),
    "hair": ("hair", "scalp", "волос", "шкіри голови", "кожи головы", "seskavel"),
    "sample": ("sample", "mini", "travel", "пробник", "міні", "мини", "tester", "тестер"),
    "set": ("set", "pack", "kit", "набір", "набор", "komplekt"),
    "bb": ("bb",),
    "cc": ("cc",),
    "patch": ("patch", "patches", "патч"),
    "cushion": ("cushion", "кушон"),
    "men": ("men", "man", "чоловіч", "мужск", "for men"),
    "night": ("night", "нічн", "ночн"),
    "shower": ("shower", "душ"),
    "shampoo": ("shampoo", "шампун"),
    "ampoule": ("ampoule", "ampoules", "ampule", "ампул"),
    "spray": ("spray", "mist", "спрей"),
    "tint": ("tinted", "color", "colour", "тонуюч", "тональн", "black", "brown", "purple", "violet", "фіолет", "blond", "блонд", "туш"),
    "neck": ("neck", "шиї", "шеи", "декольте"),
    "supplement": ("supplement", "drinkable", "capsules", "добавка", "питна", "капсул"),
}
MODIFIER_TOKENS = {token for group in MODIFIER_GROUPS.values() for token in group if " " not in token}
MODIFIER_PHRASES = tuple(token for group in MODIFIER_GROUPS.values() for token in group if " " in token)
# Слова, які магазини додають до бренду й не несуть сенсу для ідентифікації товару.
BRAND_NOISE = {
    "laboratories", "laboratorios", "laboratoire", "laboratoires", "labs",
    "cosmetics", "cosmetic", "professional", "original", "new", "official",
    "ml", "мл", "gr", "pcs", "шт", "uk", "ua",
}
# Описові слова: трохи допомагають, але самі по собі не ідентифікують товар.
DESCRIPTOR_TOKENS = {
    "face", "facial", "skin", "moisturizing", "moisturising", "moisturizer", "moisturiser",
    "hydrating", "cream", "creme", "gel", "serum", "lotion", "fluid", "emulsion", "foam",
    "anti", "aging", "ageing", "antiaging", "antiageing", "care", "daily", "intensive",
    "treatment", "solution", "liposomal", "liposome", "liposomic", "cleanser", "cleansing",
    "formula", "concentrate", "spray", "mist", "milk", "oil", "mask", "toner",
    "tonic", "balm", "stick", "drops", "ampoules", "ampoule", "contour",
    "repair", "protective", "protection", "sensitive", "all", "types",
    "body", "scalp", "hair", "day", "effect",
    "lash", "lashes", "eyebrow", "eyebrows", "brow", "growth", "booster",
}
# Прикметники, які магазин вставляє в ту саму назву («Ceramic», «Rejuvenating», «Antiwrinkle»).
# Короткі слова посередині (rose, purple) лишаються ознакою іншої позиції лінійки.
MARKETING_INSERTS = {
    "ceramic", "rejuvenating", "regenerative", "antiwrinkle", "antiwrinkles",
    "depigmenting", "depigmentation",
}
# Слова форми/призначення, які не роблять товар іншим, навіть якщо їх немає в запиті.
GENERIC_FORM_TOKENS = {
    "face", "facial", "skin", "cream", "creme", "gel", "serum", "lotion", "fluid", "emulsion",
    "foam", "care", "daily", "treatment", "solution", "formula", "all", "types", "body", "day",
    "effect", "anti", "aging", "ageing", "and", "for", "with", "system",
    "new", "sesderma", "laboratories", "tube", "bottle", "scalp", "hair", "eye", "eyes",
    "contour", "neck", "hand", "hands", "lip", "lips",
}
# Українські/російські основи в назві картки, які підтверджують англійські слова запиту.
# Використовуються лише для покриття запиту, а не як «зайві» слова назви.
CYRILLIC_HINTS = {
    "обличч": "face", "лиц": "face", "тіла": "body", "тела": "body", "тіло": "body",
    "волос": "hair", "шкіри голови": "scalp", "кожи головы": "scalp", "очей": "eye",
    "глаз": "eye", "рук": "hand", "губ": "lip", "крем": "cream", "гель": "gel",
    "сироват": "serum", "сыворот": "serum", "лосьйон": "lotion", "лосьон": "lotion",
    "флюїд": "fluid", "флюид": "fluid", "пінк": "foam", "пенк": "foam", "маск": "mask",
    "шампун": "shampoo", "тонік": "toner", "тоник": "toner", "олі": "oil", "масл": "oil",
    "молочк": "milk", "бальзам": "balm", "ампул": "ampoules", "спрей": "spray",
    "зволож": "moisturizing", "увлажн": "moisturizing", "антивік": "antiaging",
    "омолод": "antiaging", "інтенсив": "intensive", "интенсив": "intensive",
    "ліпосом": "liposomal", "липосом": "liposomal", "очищ": "cleanser", "вмиван": "cleanser",
    "умыван": "cleanser", "денн": "day", "дневн": "day", "нічн": "night", "ночн": "night",
    "набір": "set", "набор": "set", "миттєв": "flash", "мгновен": "flash",
    "контур": "contour", "захис": "protective",
}
# Слова лінійки в одному бренді: якщо є в запиті, інша в назві — інший SKU (не штраф за «зайві» SPF/опис).
LINE_DISCRIMINATORS: dict[str, tuple[str, ...]] = {
    "essential": ("nutritive", "healing", "intoning", "restoring", "toner"),
    "nutritive": ("essential", "healing", "intoning"),
    "healing": ("essential", "nutritive", "hydration"),
    "hydration": ("nutritive", "healing", "intoning"),
    "charging": ("regenetic", "regenerating", "deto2x", "detox", "deto"),
    "regenetic": ("charging", "deto2x", "detox"),
    "deto2x": ("charging", "regenetic", "hydra3"),
    "c-tetra": ("total", "moisture", "advanced"),
    "radiance": ("moisturizing", "total", "eye"),
}
# Групи, відсутність яких у картці означає інший товар (запит «Eye», а в картці — ні).
REQUIRED_MODIFIER_GROUPS = {
    "eye", "sun", "bb", "cc", "set", "men", "night", "tint", "lip", "hand",
    "patch", "cushion", "shampoo", "shower", "ampoule", "neck", "hair", "supplement",
}
MATCH_STOPWORDS = {
    "and", "for", "with", "the", "of", "de", "la", "le", "en", "et", "a", "in", "to",
    "by", "plus", "x", "ml", "mg", "g",
}
PACKAGING_STOPWORDS = {
    "refill", "рефіл", "рефил", "normal", "стандарт", "звичайна", "звичайний",
    "змінний", "змінна", "сменный", "упаковка", "package", "туба", "банка",
}
QUERY_STOPWORDS = {
    "new", "original", "professional", "for", "the", "with", "and", "plus",
}
GENERIC_BRANDS = {
    "daily", "total", "the", "new", "super", "cream", "facial", "moisture",
    "complex", "advanced", "professional", "skin", "care",
}

EXTERNAL_HOSTS = (
    "instagram.com",
    "facebook.com",
    "t.me",
    "youtube.com",
    "tiktok.com",
    "twitter.com",
)


@dataclass
class SiteConfig:
    name: str
    base_url: str
    search_url_patterns: list = field(default_factory=list)
    result_link_selector: str = "a[href*='/product']"
    result_href_contains: str = ""
    min_similarity: float = 0.58
    kind: str = "html"  # html | woocommerce | live_search | cards | brand_catalog | jsonld | microdata
    api_url: str = ""
    # Сторінки бренду: {slug} і {page}; перша сторінка може мати окремий шаблон.
    brand_first_url: str = ""
    brand_page_url: str = ""
    max_pages: int = 20
    # Розбір карток у списку (brand_catalog / cards)
    card_selector: str = ""
    card_title: str = ""
    card_title_attr: str = ""
    card_price: str = ""
    card_price_attr: str = ""
    card_volume_attr: str = ""
    card_link: str = "a[href]"
    card_image: str = "img"
    # Брати ціну й назву з картки, не відкриваючи сторінку товару.
    listing_only: bool = False
    # JoomShopping (Estetshop): GET ?search= не дає видачі — лише POST /search/result.
    search_post_url: str = ""
    search_post_form: dict = field(default_factory=dict)


SITES = [
    SiteConfig(
        name="Lullaby",
        base_url="https://lullaby.ua",
        search_url_patterns=[
            "https://lullaby.ua/search?text={q}",
            "https://lullaby.ua/search?q={q}",
        ],
        result_link_selector="a[href*='/products/']",
        result_href_contains="/products/",
    ),
    SiteConfig(
        name="Fabo",
        base_url="https://fabo.ua",
        search_url_patterns=["https://fabo.ua/search/?q={q}"],
        result_link_selector="a[href*='/product/']",
        result_href_contains="/product/",
    ),
    SiteConfig(
        name="AS Laboratory",
        base_url="https://aslaboratory.com.ua",
        search_url_patterns=[
            "https://aslaboratory.com.ua/index.php?route=product/search&search={q}",
            "https://aslaboratory.com.ua/search/?search={q}",
            "https://aslaboratory.com.ua/ua/index.php?route=product/search&search={q}",
        ],
        result_link_selector=".product-thumb .caption a, .product-layout .caption a",
    ),
    SiteConfig(
        name="Krasota.shop",
        base_url="https://krasota.shop",
        kind="cards",
        listing_only=True,
        search_url_patterns=[
            "https://krasota.shop/ua/search/?search={q}",
            "https://krasota.shop/search/?search={q}",
        ],
        # На пошуку h1.product-title = «Пошук - …» — НЕ PDP. Картки: li.catalog-product-item.
        card_selector="li.catalog-product-item, .catalog-product-item",
        card_title=".catalog-product-item__name a, .name a",
        card_price=".catalog-product-item__price, .price",
        card_link=".catalog-product-item__name a, .name a, a.catalog-product-item__thumb",
        card_image="img.catalog-product-item__img, img",
    ),
    SiteConfig(
        name="ByBeauty",
        base_url="https://bybeauty.com.ua",
        search_url_patterns=[
            "https://bybeauty.com.ua/index.php?route=product/search&filter_name={q}",
            "https://bybeauty.com.ua/ua/index.php?route=product/search&filter_name={q}",
        ],
        result_link_selector=".name a, .tb_listing .name a",
        result_href_contains="/product/",
    ),
    SiteConfig(
        name="All Face",
        base_url="https://all-face.com.ua",
        kind="woocommerce",
        api_url="https://all-face.com.ua/wp-json/wc/store/v1/products?search={q}&per_page=10",
        search_url_patterns=["https://all-face.com.ua/?s={q}&post_type=product"],
        result_link_selector="li.product a.woocommerce-LoopProduct-link, a.woocommerce-LoopProduct-link",
        result_href_contains="/product/",
    ),
    SiteConfig(
        name="Top Beauty",
        base_url="https://topbeauty.ua",
        search_url_patterns=[
            "https://topbeauty.ua/index.php?route=product/search&search={q}",
        ],
        result_link_selector=".product-thumb .caption a, .name a, .product-layout .caption a",
    ),
    SiteConfig(
        name="Pleka",
        base_url="https://pleka.ua",
        kind="cards",
        listing_only=True,
        search_url_patterns=["https://pleka.ua/search/?search={q}"],
        card_selector="div.product-item, .product-item",
        card_title="a.product-item__title",
        card_price=".product-price__new",
        card_link="a.product-item__title",
        card_image="img",
    ),
    SiteConfig(
        name="Profistyle",
        base_url="https://profistyle.in.ua",
        kind="live_search",
        api_url="https://profistyle.in.ua/index.php?route=extension/module/live_search&filter_name={q}",
        search_url_patterns=[
            "https://profistyle.in.ua/index.php?route=product/search&search={q}",
        ],
        result_link_selector="a.products-item__title, .products-item__title a, .products-item__title",
    ),
    SiteConfig(
        name="Divine Skin",
        base_url="https://divineskin.com.ua",
        search_url_patterns=[
            "https://divineskin.com.ua/ua-search/?search={q}",
            "https://divineskin.com.ua/index.php?route=product/search&search={q}",
        ],
        result_link_selector=".product-layout .caption a, .product-layout a",
        result_href_contains=".html",
    ),
    SiteConfig(
        name="MELANI",
        base_url="https://melani.ua",
        search_url_patterns=[
            "https://melani.ua/search/?search={q}",
            "https://melani.ua/index.php?route=product/search&search={q}",
        ],
        result_link_selector=".name a, .product-thumb .caption a",
    ),
    SiteConfig(
        name="Beautycom",
        base_url="https://beautycom.com.ua",
        kind="live_search",
        api_url="https://beautycom.com.ua/index.php?route=extension/module/live_search&filter_name={q}",
        search_url_patterns=[
            "https://beautycom.com.ua/ua/search/?search={q}",
            "https://beautycom.com.ua/index.php?route=product/search&search={q}",
        ],
        result_link_selector=".product-thumb .caption a, .product-layout .caption a",
    ),
    SiteConfig(
        name="Makeup",
        base_url="https://makeup.com.ua",
        kind="cards",
        listing_only=True,
        search_url_patterns=["https://makeup.com.ua/ua/search/?q={q}"],
        # CSS-modules додають суфікс shop_*: беремо title + EN subtitle (бренд+SKU).
        card_selector="[data-testid='product-tile'], .ProductCard__card",
        card_title=".ProductCard__title, .ProductCard__subTitle, [class*='ProductCard__title'], [class*='ProductCard__subTitle']",
        card_price=".Price__priceCurrent, [class*='Price__priceCurrent']",
        card_link="a.ProductCard__link, a[href*='/product/']",
        card_image="img[src]",
    ),
    SiteConfig(
        name="Dermal Cosmetics",
        base_url="https://dermal-cosmetics.com.ua",
        kind="brand_catalog",
        listing_only=True,
        brand_first_url="https://dermal-cosmetics.com.ua/product-brand/{slug}/",
        brand_page_url="https://dermal-cosmetics.com.ua/product-brand/{slug}/page/{page}/",
        search_url_patterns=[
            "https://dermal-cosmetics.com.ua/?s={q}&post_type=product",
            "https://dermal-cosmetics.com.ua/shop/?s={q}&post_type=product",
        ],
        card_selector="div.product-card",
        card_title=".title",
        card_price=".price-wrapper",
        card_link="a[href*='/product/']",
        card_image="img",
        max_pages=12,
    ),
    SiteConfig(
        name="Academie UA",
        base_url="https://academie.com.ua",
        kind="cards",
        listing_only=True,
        search_url_patterns=[
            "https://academie.com.ua/ua/search/?search={q}",
        ],
        # Картка = li.product-list-info (не ul.product: у WebAsyst він «протікає» на сусідів).
        card_selector="li.product-list-info",
        card_title=".lux-podzagolovok a, .product-list-title a",
        card_price="span.price",
        card_link=".product-list-title a, .lux-podzagolovok a",
        card_image="img",
    ),
    SiteConfig(
        name="DeadSea",
        base_url="https://deadsea.com.ua",
        kind="cards",
        listing_only=True,
        search_url_patterns=[
            "https://deadsea.com.ua/index.php?route=product/search&search={q}",
        ],
        card_selector="div.product-layout",
        card_title=".rm-module-title a",
        card_price=".rm-module-price",
        card_link=".rm-module-title a",
        card_image="img",
    ),
    SiteConfig(
        name="Estetshop",
        base_url="https://estetshop.com.ua",
        kind="cards",
        listing_only=True,
        search_url_patterns=[],
        search_post_url="https://estetshop.com.ua/search/result",
        search_post_form={
            "setsearchdata": "1",
            "search_type": "all",
            "search": "{q}",
        },
        card_selector="div.product.uk-card",
        card_title="a.uk-card-body",
        card_price=".uk-text-primary",
        card_link="a.uk-card-body",
        card_image="img",
    ),
    SiteConfig(
        name="Kstore",
        base_url="https://kstore.com.ua",
        kind="brand_catalog",
        listing_only=True,
        brand_first_url="https://kstore.com.ua/shop/brendi/{slug}/",
        brand_page_url="https://kstore.com.ua/shop/brendi/{slug}/page-{page}/",
        card_selector="div.product-block",
        card_title=".product-name",
        card_price="price[data-price]",
        card_price_attr="data-price",
        card_volume_attr="data-unit",
        card_link=".product-name a",
        card_image="img[data-src*='roduct'], img[src*='roduct']",
    ),
    SiteConfig(
        name="Beautis",
        base_url="https://beautis.com.ua",
        kind="brand_catalog",
        listing_only=True,
        brand_first_url="https://beautis.com.ua/ua/brand/{slug}/",
        brand_page_url="https://beautis.com.ua/ua/brand/{slug}/?page={page}",
        card_selector="div.product-slider-item",
        card_title=".brand-name, .eng-title",
        card_title_attr="title",
        card_price=".new-price",
        card_link="a[href]",
        card_image="img[src]",
    ),
    SiteConfig(
        name="Kosmetolog",
        base_url="https://kosmetolog.ua",
        kind="brand_catalog",
        listing_only=True,
        brand_first_url="https://kosmetolog.ua/ua/{slug}/",
        brand_page_url="https://kosmetolog.ua/ua/{slug}/filter/page={page}/",
        search_url_patterns=["https://kosmetolog.ua/ua/kataloh/search/?q={q}"],
        card_selector="div.catalogCard",
        card_title=".catalogCard-title",
        card_price=".catalogCard-price",
        card_link=".catalogCard-title a",
        card_image="img.catalogCard-img",
    ),
    SiteConfig(
        name="Dr.Beauty",
        base_url="https://drbeauty.com.ua",
        kind="cards",
        listing_only=True,
        search_url_patterns=[
            "https://drbeauty.com.ua/ua/index.php?route=product/search&search={q}",
        ],
        card_selector="div.product-layout",
        card_title=".caption h4 a, .caption a",
        card_price=".price",
        card_link=".caption a",
        card_image="img",
    ),
    SiteConfig(
        name="Laserhouse",
        base_url="https://laserhousecosmetics.com.ua",
        kind="brand_catalog",
        listing_only=True,
        brand_first_url="https://laserhousecosmetics.com.ua/ua/{slug}/",
        brand_page_url="https://laserhousecosmetics.com.ua/ua/{slug}/filter/page={page}/",
        card_selector="div.catalogCard",
        card_title=".catalogCard-title",
        card_price=".catalogCard-price",
        card_link=".catalogCard-title a",
        card_image="img.catalogCard-img",
        max_pages=8,
    ),
    SiteConfig(
        name="Skinkits",
        base_url="https://skinkits.com.ua",
        kind="cards",
        listing_only=True,
        search_url_patterns=[
            "https://skinkits.com.ua/search?type=product&q={q}",
            "https://skinkits.com.ua/search?q={q}",
        ],
        card_selector="div.product-card",
        card_title="a.product-card__title",
        card_price=".price",
        card_link="a.product-card__title",
        card_image="img",
    ),
    SiteConfig(
        name="Ptashuni",
        base_url="https://ptashuni.com",
        kind="cards",
        listing_only=True,
        min_similarity=0.62,
        search_url_patterns=[
            "https://ptashuni.com/search?type=product&q={q}",
        ],
        card_selector="li.grid__item",
        card_title="h3.card__heading",
        card_price=".price-item--sale, .price-item--regular",
        card_link="h3.card__heading a, a[href*='/products/']",
        card_image="img",
    ),
    SiteConfig(
        name="Skin Health",
        base_url="https://skin-health.com.ua",
        kind="cards",
        listing_only=True,
        search_url_patterns=["https://skin-health.com.ua/index.php?route=product/search&search={q}"],
        card_selector="div.product-thumb",
        # h4 часто без бренду; перший абзац опису містить «Академі» / Academie.
        card_title=".caption h4 a, .caption > p:not(.price)",
        card_price=".price",
        card_link=".caption h4 a",
        card_image="img",
    ),
    SiteConfig(
        name="Notino",
        base_url="https://www.notino.ua",
        kind="jsonld",
        search_url_patterns=["https://www.notino.ua/search.asp?exps={q}"],
        min_similarity=0.6,
    ),
    # --- нові конкуренти (2026-11) ---
    SiteConfig(
        name="Femi",
        base_url="https://femi.in.ua",
        kind="live_search",
        api_url="https://femi.in.ua/index.php?route=extension/module/live_search&filter_name={q}",
        search_url_patterns=["https://femi.in.ua/search/?search={q}"],
        result_link_selector=".product-thumb .caption a, .product-layout .caption a, .name a",
    ),
    SiteConfig(
        name="Top Beauty Shop",
        base_url="https://top-beauty-shop.com.ua",
        kind="cards",
        listing_only=True,
        # MODX mSearch2: параметр query на /poshuk-na-sajti/ (не OC ?search=).
        search_url_patterns=[
            "https://top-beauty-shop.com.ua/poshuk-na-sajti/?query={q}",
        ],
        card_selector="div.product-item",
        card_title=".product-item__title, .product-item__name a",
        # select_one бере перший матч у DOM: не ставити батьківський .product-item__price
        # (там ще «0 ₴» з modification-old-price).
        card_price=".product-item__price-item.modification-price, .product-item__price > .modification-price",
        card_link=".product-item__title a, a.product-item__title, a[href*='/produkti/']",
        card_image="img",
    ),
    SiteConfig(
        name="The Beauty Room",
        base_url="https://thebeautyroom.store",
        kind="cards",
        listing_only=True,
        search_url_patterns=[
            "https://thebeautyroom.store/ua/index.php?route=product/search&search={q}",
            "https://thebeautyroom.store/ua/?s={q}&post_type=product",
        ],
        card_selector="div.product-layout, div.product-thumb",
        card_title=".caption a, h4 a",
        card_price=".price",
        card_link=".caption a, h4 a",
        card_image="img",
    ),
    SiteConfig(
        name="Beauty Systema",
        base_url="https://www.beauty-systema.com",
        kind="woocommerce",
        api_url="https://www.beauty-systema.com/wp-json/wc/store/v1/products?search={q}&per_page=10",
        search_url_patterns=["https://www.beauty-systema.com/?s={q}&post_type=product"],
        result_link_selector="li.product a.woocommerce-LoopProduct-link, a.woocommerce-LoopProduct-link",
        result_href_contains="/product/",
    ),
    SiteConfig(
        name="Cosmodrugs",
        base_url="https://cosmodrugs.com.ua",
        kind="cards",
        listing_only=True,
        search_url_patterns=["https://cosmodrugs.com.ua/?s={q}&post_type=product"],
        card_selector="div.product_preview.fn_product, div.fn_product",
        card_title=".product_preview__name a, .product_preview__name",
        card_price=".fn_price, .price",
        card_link="a[href*='/products/']",
        card_image="img",
    ),
    SiteConfig(
        name="Cosmari",
        base_url="https://cosmari.com.ua",
        kind="cards",
        listing_only=True,
        search_url_patterns=[
            "https://cosmari.com.ua/index.php?route=product/search&search={q}",
            "https://cosmari.com.ua/ua/index.php?route=product/search&search={q}",
        ],
        card_selector="div.catalogue__products-list-item, li.catalogue__products-list-item",
        card_title=".products-list__name, .catalogue__product-name, .products-list__caption a",
        card_price=".catalogue__price, .products-list__price",
        card_link="a.products-list__link, .products-list__name a, a[href]",
        card_image="img",
    ),
    SiteConfig(
        name="EOS",
        base_url="https://eos.kiev.ua",
        kind="cards",
        listing_only=True,
        search_url_patterns=[
            "https://eos.kiev.ua/ua/search/?query={q}",
            "https://eos.kiev.ua/ua/search/?search={q}",
        ],
        card_selector="div.products__item",
        card_title=".products__item-name a, .products__item-info a",
        card_price=".products__item-price, .price",
        card_link=".products__item-name a, a.products__sgallery-link",
        card_image="img",
    ),
    SiteConfig(
        name="Clarity UA",
        base_url="https://clarity-ua.com",
        kind="cards",
        listing_only=True,
        search_url_patterns=[
            "https://clarity-ua.com/index.php?route=product/search&search={q}",
            "https://clarity-ua.com/ua/index.php?route=product/search&search={q}",
        ],
        card_selector="div.product-layout",
        card_title=".ds-module-title, a[title]",
        card_title_attr="title",
        card_price=".ds-module-price, .price",
        card_link="a[href*='cosmetics'], a[href]:not([href*='javascript'])",
        card_image="img",
    ),
    SiteConfig(
        name="Zvizda",
        base_url="https://zvizda.com.ua",
        kind="cards",
        listing_only=True,
        search_url_patterns=["https://zvizda.com.ua/?s={q}&post_type=product"],
        card_selector="div.product_item",
        card_title=".product_item-title, .us-module-title a",
        card_price=".us-module-price, .price",
        card_link="a[href*='/product/']",
        card_image="img",
    ),
    SiteConfig(
        name="Dazzle",
        base_url="https://dazzle.com.ua",
        kind="cards",
        listing_only=True,
        search_url_patterns=[
            "https://dazzle.com.ua/catalogsearch/result/?q={q}",
            "https://dazzle.com.ua/index.php?route=product/search&search={q}",
        ],
        card_selector="li.product-item, div.product-item",
        card_title=".product-item-link, .product-item-name a, .name a",
        card_price=".price, .price-wrapper",
        card_link="a.product-item-link, a.product-item-photo, .name a",
        card_image="img",
    ),
    SiteConfig(
        name="Moonali",
        base_url="https://moonali.com.ua",
        kind="cards",
        listing_only=True,
        search_url_patterns=[
            "https://moonali.com.ua/index.php?route=product/search&search={q}",
            "https://moonali.com.ua/search/?search={q}",
        ],
        card_selector="article.item-v1",
        card_title=".item-brand, .item-name, .item-name-orig",
        card_price="p.item-price, .item-price",
        card_link="a.item-v1-link",
        card_image="img.item-photo-img, img",
    ),
    SiteConfig(
        name="Bonvivant",
        base_url="https://bonvivant.ua",
        kind="live_search",
        api_url="https://search.edgelab.com.ua/v1/bonvivant/search?q={q}",
        search_url_patterns=["https://bonvivant.ua/products/search?search={q}"],
        result_link_selector="a[href*='/ua-']",
    ),
    SiteConfig(
        name="IsNuYou",
        base_url="https://isnuyou.com.ua",
        kind="cards",
        listing_only=True,
        search_url_patterns=[
            "https://isnuyou.com.ua/shop/?search={q}",
        ],
        card_selector="div.product-block",
        card_title=".product-name",
        card_price=".product-price-regular, .product-prices",
        card_link="a[href*='/shop/']",
        card_image="img",
    ),
    SiteConfig(
        name="Paradiz",
        base_url="https://paradiz.com.ua",
        kind="cards",
        listing_only=True,
        search_url_patterns=[
            "https://paradiz.com.ua/ua/search/?search={q}",
            "https://paradiz.com.ua/ua/index.php?route=product/search&search={q}",
        ],
        card_selector="div.product-layout, div.product-thumb",
        card_title=".caption a, h4 a",
        card_price=".price",
        card_link=".caption a, h4 a",
        card_image="img",
    ),
    SiteConfig(
        name="FLTR",
        base_url="https://fltr.shop",
        kind="cards",
        listing_only=True,
        search_url_patterns=[
            "https://fltr.shop/index.php?route=product/search&search={q}",
            "https://fltr.shop/?s={q}&post_type=product",
        ],
        card_selector="div.product-layout, .ds-module-item",
        card_title=".ds-module-title, a[title]",
        card_title_attr="title",
        card_price=".ds-module-price, .price",
        card_link="a[href]:not([href*='javascript'])",
        card_image="img",
    ),
    SiteConfig(
        name="Angel Beauty",
        base_url="https://angel-beauty.com.ua",
        kind="cards",
        listing_only=True,
        search_url_patterns=[
            "https://angel-beauty.com.ua/ua/catalogsearch/result/?q={q}",
            "https://angel-beauty.com.ua/catalogsearch/result/?q={q}",
        ],
        card_selector="li.product-item",
        card_title=".product-item-link, .product-item-name a, .name a",
        card_price=".price, .price-wrapper",
        card_link="a.product-item-link, a.product-item-photo",
        card_image="img",
    ),
    SiteConfig(
        name="Skini",
        base_url="https://skini.com.ua",
        kind="cards",
        listing_only=True,
        search_url_patterns=[
            "https://skini.com.ua/ru/search/?search={q}",
            "https://skini.com.ua/ua/search/?search={q}",
            "https://skini.com.ua/ru/index.php?route=product/search&search={q}",
        ],
        card_selector="div.product-thumb, div.product-layout",
        card_title=".caption a, h4 a",
        card_price=".price",
        card_link=".caption a, h4 a",
        card_image="img",
    ),
    SiteConfig(
        name="MiSo Shop",
        base_url="https://misoshop.com.ua",
        kind="cards",
        listing_only=True,
        search_url_patterns=["https://misoshop.com.ua/?q={q}"],
        card_selector="div.product-item-component",
        card_title="a.name, .name",
        card_price=".price, .price-wraper .price",
        card_link="a.name, a.img",
        card_image="img",
    ),
    SiteConfig(
        name="Japan Shampoo",
        base_url="https://japan-shampoo.com.ua",
        kind="cards",
        listing_only=True,
        search_url_patterns=["https://japan-shampoo.com.ua/products/search?search={q}"],
        card_selector="div.product-item",
        card_title=".title a, .title",
        card_price=".price, .product-price",
        card_link=".title a",
        card_image="img",
    ),
    SiteConfig(
        name="Krem UA",
        base_url="https://krem-ua.com",
        kind="cards",
        listing_only=True,
        search_url_patterns=[
            "https://krem-ua.com/?route=product/search&search={q}",
            "https://krem-ua.com/index.php?route=product/search&search={q}",
        ],
        card_selector="div.product",
        card_title="img[alt], a[title], a",
        card_price=".price, .p_price",
        card_link="a.p_img_href, a[href*='/p-']",
        card_image="img",
    ),
    SiteConfig(
        name="Paradise Fruit",
        base_url="https://paradisefruit.com.ua",
        kind="cards",
        listing_only=True,
        search_url_patterns=[
            "https://paradisefruit.com.ua/uk/search?text={q}",
            "https://paradisefruit.com.ua/uk/search?keys={q}",
        ],
        card_selector="div.product",
        card_title="a.product-link-wrapper, a[href*='/product/']",
        card_price=".price, .product-price, .field--price",
        card_link="a[href*='/product/']",
        card_image="img",
    ),
    SiteConfig(
        name="Profcare",
        base_url="https://profcare.com.ua",
        kind="cards",
        listing_only=True,
        search_url_patterns=[
            "https://profcare.com.ua/index.php?route=product/search&search={q}",
            "https://profcare.com.ua/search/?search={q}",
        ],
        card_selector="div.product-layout, div.product-thumb",
        card_title=".caption h4 a, h4 a",
        card_price=".price",
        card_link=".caption h4 a, h4 a",
        card_image="img",
    ),
    SiteConfig(
        name="Daviani",
        base_url="https://daviani.com.ua",
        kind="cards",
        listing_only=True,
        search_url_patterns=[
            "https://daviani.com.ua/index.php?route=product/search&search={q}",
            "https://daviani.com.ua/?s={q}&post_type=product",
        ],
        card_selector="div.product-item",
        card_title=".product-item__brand, .product-item__brand_name, img[alt]",
        card_price=".product-item__price, .price",
        card_link="a[href]",
        card_image="img",
    ),
    SiteConfig(
        name="Jouvence",
        base_url="https://jouvence.com.ua",
        kind="cards",
        listing_only=True,
        search_url_patterns=[
            "https://jouvence.com.ua/shop/search/?query={q}",
            "https://jouvence.com.ua/shop/search/?search={q}",
        ],
        card_selector="li.product-card-li",
        card_title=".product-title a, .product-card-title a, [itemprop='name']",
        card_price=".price, [itemprop='price']",
        card_link=".product-title a, a[href*='/shop/product/']",
        card_image="img",
    ),
    SiteConfig(
        name="Kapitanova",
        base_url="https://kapitanova.com.ua",
        kind="live_search",
        api_url="https://kapitanova.com.ua/api/search?q={q}",
        search_url_patterns=["https://kapitanova.com.ua/search?q={q}"],
        result_link_selector="a[href]",
    ),
    SiteConfig(
        name="Cosmohit",
        base_url="https://cosmohit.ua",
        search_url_patterns=["https://cosmohit.ua/?q={q}"],
        result_link_selector='a[href*="/catalogue/product/"]',
        result_href_contains="/catalogue/product/",
    ),
    SiteConfig(
        name="Amoris",
        base_url="https://amoris.com.ua",
        kind="cards",
        listing_only=True,
        search_url_patterns=[
            "https://amoris.com.ua/index.php?route=product/search&search={q}",
            "https://amoris.com.ua/search/?search={q}",
        ],
        card_selector="div.product-thumb.product-wrapper, div.product-wrapper",
        card_title=".name a, .caption h4 a, h4 a",
        card_price=".price",
        card_link=".name a, .caption h4 a",
        card_image="img",
    ),
    SiteConfig(
        name="Luxmarafet",
        base_url="https://luxmarafet.com",
        kind="cards",
        listing_only=True,
        search_url_patterns=[
            "https://luxmarafet.com/ua/searchml/?query={q}",
            "https://luxmarafet.com/ua/search/?search={q}",
        ],
        card_selector="li.lux-list-prod, .lux-list-prod",
        card_title="a[title], .lux-podzagolovok a, a",
        card_title_attr="title",
        card_price=".lux-div-price, .price, span.price",
        card_link="a[href*='/ua/']",
        card_image="img",
    ),
    # SPA / захист: додаємо з типовими URL — увімкнути після перевірки на VPS
    SiteConfig(
        name="Korea Beauty Store",
        base_url="https://koreabeautystore.com.ua",
        kind="cards",
        listing_only=True,
        search_url_patterns=["https://koreabeautystore.com.ua/katalog/search/?q={q}"],
        card_selector="div.catalogCard",
        card_title=".catalogCard-title",
        card_price=".catalogCard-price",
        card_link=".catalogCard-title a",
        card_image="img.catalogCard-img, img",
    ),
    SiteConfig(
        name="R1 Beauty",
        base_url="https://r1beauty.com.ua",
        kind="cards",
        listing_only=True,
        search_url_patterns=["https://r1beauty.com.ua/katalog/search/?q={q}"],
        card_selector="div.catalogCard",
        card_title=".catalogCard-title",
        card_price=".catalogCard-price",
        card_link=".catalogCard-title a",
        card_image="img.catalogCard-img, img",
    ),
    SiteConfig(
        name="Hitomi",
        base_url="https://hitomi.com.ua",
        kind="cards",
        listing_only=True,
        search_url_patterns=[
            "https://hitomi.com.ua/search/?search={q}",
            "https://hitomi.com.ua/search?q={q}",
            "https://hitomi.com.ua/?s={q}&post_type=product",
        ],
        card_selector="div.product-item, div.product-card, .product-thumb, li.product",
        card_title=".name a, .title a, h3 a, a",
        card_price=".price",
        card_link="a[href]",
        card_image="img",
    ),
    SiteConfig(
        name="Profline",
        base_url="https://profline.com.ua",
        kind="cards",
        listing_only=True,
        search_url_patterns=["https://profline.com.ua/katalog/search/?q={q}"],
        card_selector="div.catalogCard",
        card_title=".catalogCard-title",
        card_price=".catalogCard-price",
        card_link=".catalogCard-title a",
        card_image="img.catalogCard-img, img",
    ),
    SiteConfig(
        name="Oh My Beauty",
        base_url="https://ohmybeauty.com.ua",
        kind="cards",
        listing_only=True,
        search_url_patterns=["https://ohmybeauty.com.ua/kataloh/search/?q={q}"],
        card_selector="div.catalogCard",
        card_title=".catalogCard-title",
        card_price=".catalogCard-price",
        card_link=".catalogCard-title a",
        card_image="img.catalogCard-img, img",
    ),
    SiteConfig(
        name="VBeauty",
        base_url="https://vbeauty.com.ua",
        kind="cards",
        listing_only=True,
        search_url_patterns=["https://vbeauty.com.ua/katalog/search/?q={q}"],
        card_selector="div.catalogCard",
        card_title=".catalogCard-title",
        card_price=".catalogCard-price",
        card_link=".catalogCard-title a",
        card_image="img.catalogCard-img, img",
    ),
    SiteConfig(
        name="Bye Shop",
        base_url="https://bye-shop.com",
        kind="cards",
        listing_only=True,
        search_url_patterns=["https://bye-shop.com/katalog/search/?q={q}"],
        card_selector="div.catalogCard",
        card_title=".catalogCard-title",
        card_price=".catalogCard-price",
        card_link=".catalogCard-title a",
        card_image="img.catalogCard-img, img",
    ),
    SiteConfig(
        name="Okrylena",
        base_url="https://okrylena.ua",
        kind="cards",
        listing_only=True,
        search_url_patterns=[
            "https://okrylena.ua/search?q={q}",
            "https://okrylena.ua/ua/search/?search={q}",
            "https://okrylena.ua/?s={q}&post_type=product",
        ],
        card_selector="div.product-item, div.product-card, .product-thumb, li.product",
        card_title=".name a, .title a, h3 a, a",
        card_price=".price",
        card_link="a[href]",
        card_image="img",
    ),
    SiteConfig(
        name="Makoko",
        base_url="https://makoko.shop",
        kind="cards",
        listing_only=True,
        search_url_patterns=[
            "https://makoko.shop/search?type=product&q={q}",
            "https://makoko.shop/search?q={q}",
        ],
        card_selector="div.product-card, li.grid__item, div.product-item",
        card_title="a.product-card__title, h3 a, .name a, a",
        card_price=".price, .price-item",
        card_link="a[href*='/products/'], a[href]",
        card_image="img",
    ),
]

OUT_OF_STOCK_MARKERS = (
    "немає в наявності", "нет в наличии", "закінчився", "закончился",
    "очікується", "ожидается", "під замовлення", "под заказ", "out of stock",
    "повідомити про наявність", "сообщить о наличии",
)


def fetch(url: str, debug: bool = False, extra_headers: Optional[dict] = None) -> Optional[BeautifulSoup]:
    html = fetch_text(url, debug=debug, extra_headers=extra_headers)
    if html is None:
        return None
    return BeautifulSoup(html, "lxml")


_HTTP_CACHE: dict = {}
# Скільки живе кеш відповідей: достатньо для одного прогону, але повторний запуск бере свіжі ціни.
HTTP_CACHE_TTL = 900.0
EMPTY_SEARCH_404_HOSTS = {"lullaby.ua"}
_HOST_COOKIES: dict = {}
_HTTP_LOCK = threading.Lock()
# Заглушка «зачекай і перезавантаж»: скрипт кладе фіксований хеш у cookie.
JS_COOKIE_CHALLENGE_RE = re.compile(
    r'defaultHash\s*=\s*"(?P<value>[0-9a-f]{16,})".*?document\.cookie\s*=\s*"(?P<name>\w+)="',
    re.S,
)


def clear_http_cache() -> None:
    with _HTTP_LOCK:
        _HTTP_CACHE.clear()


# Скільки одночасних запитів до одного хоста. Без обмеження 8 SKU × N магазинів × кандидати
# перевантажують дрібні сайти → таймаути → хибне «магазин не відповів».
HOST_CONCURRENCY = 3
# Підряд невдалих запитів (таймаут/мережа), після яких хост відкладається на HOST_COOLDOWN секунд.
HOST_FAIL_LIMIT = 6
HOST_COOLDOWN = 40.0
# Хости, які без cookie з головної часто віддають 403 / порожній пошук.
COOKIE_WARMUP_ORIGINS = {
    "www.notino.ua": "https://www.notino.ua/",
    "notino.ua": "https://www.notino.ua/",
    "ptashuni.com": "https://ptashuni.com/",
    "skin-health.com.ua": "https://skin-health.com.ua/",
    "skinkits.com.ua": "https://skinkits.com.ua/",
    "academie.com.ua": "https://academie.com.ua/",
}
_HOST_SEMS: dict = {}
_HOST_FAILS: dict = {}
_HOST_DOWN_UNTIL: dict = {}
_HOST_WARMED: set = set()
CARD_OOS_CLASS_RE = re.compile(
    r"outofstock|out.?of.?stock|cardisoutofstock|isoutofstock|sold.?out|outofstockfooter",
    re.I,
)


def _host_sem(host: str) -> threading.BoundedSemaphore:
    with _HTTP_LOCK:
        sem = _HOST_SEMS.get(host)
        if sem is None:
            sem = _HOST_SEMS[host] = threading.BoundedSemaphore(HOST_CONCURRENCY)
        return sem


def _host_is_down(host: str) -> bool:
    with _HTTP_LOCK:
        until = _HOST_DOWN_UNTIL.get(host, 0.0)
        if until and time.time() < until:
            return True
        if until:  # cooldown минув — «напіввідкритий» стан: дозволяємо пробні запити
            _HOST_DOWN_UNTIL.pop(host, None)
            _HOST_FAILS[host] = HOST_FAIL_LIMIT - 2
        return False


def _host_result(host: str, ok: bool) -> None:
    with _HTTP_LOCK:
        if ok:
            _HOST_FAILS[host] = 0
            return
        _HOST_FAILS[host] = _HOST_FAILS.get(host, 0) + 1
        if _HOST_FAILS[host] >= HOST_FAIL_LIMIT:
            _HOST_DOWN_UNTIL[host] = time.time() + HOST_COOLDOWN


def reset_host_state() -> None:
    with _HTTP_LOCK:
        _HOST_FAILS.clear()
        _HOST_DOWN_UNTIL.clear()
        _HOST_WARMED.clear()
        _HOST_COOKIES.clear()


def _merge_host_cookies(host: str, response) -> None:
    jar = getattr(response, "cookies", None)
    if not jar:
        return
    fresh = [f"{cookie.name}={cookie.value}" for cookie in jar]
    if not fresh:
        return
    with _HTTP_LOCK:
        existing = _HOST_COOKIES.get(host, "")
        merged: dict[str, str] = {}
        for part in (existing.split(";") if existing else []):
            part = part.strip()
            if "=" in part:
                name, value = part.split("=", 1)
                merged[name.strip()] = value.strip()
        for part in fresh:
            name, value = part.split("=", 1)
            merged[name] = value
        _HOST_COOKIES[host] = "; ".join(f"{k}={v}" for k, v in merged.items())


def _warmup_host(host: str, headers: dict, debug: bool = False) -> None:
    origin = COOKIE_WARMUP_ORIGINS.get(host)
    if not origin:
        return
    with _HTTP_LOCK:
        if host in _HOST_WARMED:
            return
        _HOST_WARMED.add(host)
    try:
        with _host_sem(host):
            response = requests.get(origin, headers=headers, timeout=TIMEOUT)
        _merge_host_cookies(host, response)
        if debug:
            print(f"    [debug] warmup {origin} → {response.status_code}")
    except requests.RequestException as exc:
        if debug:
            print(f"    [debug] warmup {origin} → {exc}")


def fetch_text(url: str, debug: bool = False, extra_headers: Optional[dict] = None, as_json: bool = False):
    """GET з кешем у межах процесу, лімітом паралельності на хост і повтором при збоях мережі/5xx/429.

    Кешуються лише успішні відповіді (200 / спец-404); таймаути, 403 і 5xx не кешуються,
    щоб тимчасовий збій не став «постійним» на весь прогін.
    """
    headers = dict(HEADERS)
    if extra_headers:
        headers.update(extra_headers)
    key = (url, as_json, tuple(sorted((extra_headers or {}).items())))
    with _HTTP_LOCK:
        hit = _HTTP_CACHE.get(key)
        if hit is not None and time.time() - hit[0] < HTTP_CACHE_TTL:
            return hit[1]

    host = urlparse(url).netloc
    if _host_is_down(host):
        if debug:
            print(f"    [debug] {host} тимчасово відкладено після серії збоїв")
        return None
    if host in COOKIE_WARMUP_ORIGINS:
        _warmup_host(host, headers, debug=debug)
    result = None
    cacheable = False
    sem = _host_sem(host)
    for attempt in range(3):
        with _HTTP_LOCK:
            cookie = _HOST_COOKIES.get(host)
        req_headers = dict(headers)
        if cookie:
            req_headers["Cookie"] = cookie
        try:
            with sem:
                r = requests.get(url, headers=req_headers, timeout=TIMEOUT)
            _merge_host_cookies(host, r)
            challenge = JS_COOKIE_CHALLENGE_RE.search(r.text[:3000]) if len(r.content) < 4000 else None
            if challenge and attempt < 2:
                name, value = challenge.group("name"), challenge.group("value")
                with _HTTP_LOCK:
                    existing = _HOST_COOKIES.get(host, "")
                    piece = f"{name}={value}"
                    _HOST_COOKIES[host] = f"{existing}; {piece}" if existing else piece
                continue
            if debug:
                print(f"    [debug] GET {url} → статус {r.status_code}, {len(r.content)} байт")
            if r.status_code == 403 and attempt < 2:
                _warmup_host(host, headers, debug=debug)
                time.sleep(0.6 * (attempt + 1))
                continue
            if (r.status_code >= 500 or r.status_code == 429) and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code >= 500 or r.status_code == 429 or r.status_code == 403:
                _host_result(host, False)
                break
            _host_result(host, True)
            if r.status_code == 404 and host in EMPTY_SEARCH_404_HOSTS and len(r.content) > 20000:
                # цей магазин віддає 404 + повну сторінку на порожній пошук
                result = r.json() if as_json else r.text
                cacheable = True
                break
            if r.status_code != 200:
                break
            if "charset" not in (r.headers.get("content-type") or "").lower():
                r.encoding = "utf-8"
            result = r.json() if as_json else r.text
            cacheable = True
            break
        except (json.JSONDecodeError, ValueError) as e:
            if debug:
                print(f"    [debug] GET {url} → не JSON: {e}")
            break
        except requests.RequestException as e:
            if debug:
                print(f"    [debug] GET {url} → помилка: {e}")
            _host_result(host, False)
            if attempt == 0 and not _host_is_down(host):
                time.sleep(1.0)
                continue
            break
    if cacheable:
        with _HTTP_LOCK:
            _HTTP_CACHE[key] = (time.time(), result)
    return result


def fetch_post_text(
    url: str,
    data: dict,
    debug: bool = False,
    extra_headers: Optional[dict] = None,
) -> Optional[str]:
    """POST форми пошуку (JoomShopping / Estetshop) з тим самим кешем і лімітом на хост."""
    headers = dict(HEADERS)
    headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    if extra_headers:
        headers.update(extra_headers)
    key = ("POST", url, tuple(sorted(data.items())), tuple(sorted((extra_headers or {}).items())))
    with _HTTP_LOCK:
        hit = _HTTP_CACHE.get(key)
        if hit is not None and time.time() - hit[0] < HTTP_CACHE_TTL:
            return hit[1]

    host = urlparse(url).netloc
    if _host_is_down(host):
        if debug:
            print(f"    [debug] {host} тимчасово відкладено після серії збоїв")
        return None
    if host in COOKIE_WARMUP_ORIGINS:
        _warmup_host(host, headers, debug=debug)

    result = None
    cacheable = False
    sem = _host_sem(host)
    for attempt in range(3):
        with _HTTP_LOCK:
            cookie = _HOST_COOKIES.get(host)
        req_headers = dict(headers)
        if cookie:
            req_headers["Cookie"] = cookie
        try:
            with sem:
                r = requests.post(url, data=data, headers=req_headers, timeout=TIMEOUT)
            _merge_host_cookies(host, r)
            if debug:
                print(f"    [debug] POST {url} → статус {r.status_code}, {len(r.content)} байт")
            if r.status_code == 403 and attempt < 2:
                _warmup_host(host, headers, debug=debug)
                time.sleep(0.6 * (attempt + 1))
                continue
            if (r.status_code >= 500 or r.status_code == 429) and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code >= 500 or r.status_code == 429 or r.status_code == 403:
                _host_result(host, False)
                break
            _host_result(host, True)
            if r.status_code != 200:
                break
            if "charset" not in (r.headers.get("content-type") or "").lower():
                r.encoding = "utf-8"
            result = r.text
            cacheable = True
            break
        except requests.RequestException as e:
            if debug:
                print(f"    [debug] POST {url} → помилка: {e}")
            _host_result(host, False)
            if attempt == 0 and not _host_is_down(host):
                time.sleep(1.0)
                continue
            break
    if cacheable:
        with _HTTP_LOCK:
            _HTTP_CACHE[key] = (time.time(), result)
    return result


def _search_post_form(site: SiteConfig, query: str) -> dict:
    form = {}
    for key, value in (site.search_post_form or {}).items():
        if isinstance(value, str):
            form[key] = value.replace("{q}", query)
        else:
            form[key] = value
    return form


def _iter_search_pages(site: SiteConfig, query: str, debug: bool = False):
    """Усі сторінки пошуку для одного варіанта запиту (GET patterns + опційний POST)."""
    if site.search_post_url:
        html = fetch_post_text(site.search_post_url, _search_post_form(site, query), debug=debug)
        if html:
            yield site.search_post_url, BeautifulSoup(html, "lxml")
    for pattern in site.search_url_patterns:
        url = pattern.format(q=quote(query))
        soup = fetch(url, debug=debug)
        if soup is not None:
            yield url, soup


def fold_diacritics(text: str) -> str:
    """Académie / crème → Academie / creme — для URL-пошуку й порівняння назв."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def latin_product_name(query: str) -> str:
    # Спочатку знімаємо діакритики, інакше «Académie …» дає хибний матч з «mie …».
    folded = fold_diacritics(query or "")
    matches = LATIN_NAME_RE.findall(folded)
    if matches:
        return max(matches, key=len).strip(" -–—")
    singles = re.findall(r"\b[A-Za-z][A-Za-z0-9&+]{2,}\b", folded)
    if len(singles) >= 2:
        return " ".join(singles[:8])
    return singles[0] if singles else ""


def identity_query(query: str) -> str:
    latin = latin_product_name(query)
    if latin and len(latin.split()) >= 2:
        return latin
    return query


_BRAND_WEAK_WORDS = GENERIC_BRANDS | {
    "beauty", "dr", "doctor", "natural", "organic", "organics", "laboratories", "laboratory",
    "derma", "dermo", "cosmetics", "korean", "japan", "pro", "bio", "eco", "color", "colour",
    "hair", "body", "skin", "face", "line", "lab", "labs", "la", "le", "les", "the", "of", "by",
}


def register_brand(name: str) -> str:
    """Додає бренд з фіда до таблиці аліасів: повна назва, склеєна, через дефіс і (для довгих назв) перше слово.

    Повертає канонічний ключ. Без цього рушій знав лише ~10 брендів і для решти брав перше слово.
    """
    raw = (name or "").strip()
    norm = normalize_text(raw)
    if not norm or not re.search(r"[a-z]", norm):
        return ""
    # бренд, який уже відомий за аліасом, — не дублюємо
    for canon, aliases in BRAND_ALIASES.items():
        if norm == canon or norm in {normalize_text(a) for a in aliases}:
            return canon
    words = norm.split()
    aliases = [norm]
    if len(words) > 1:
        aliases.append("".join(words))
        first = words[0]
        if len(first) >= 5 and first not in _BRAND_WEAK_WORDS and first.isascii():
            aliases.append(first)
    BRAND_ALIASES[norm] = list(dict.fromkeys(aliases))
    return norm


def brand_slugs(brand: str) -> list:
    """Варіанти slug для сторінки бренду в магазині: abril-et-nature, abriletnature, abril."""
    out = []
    for alias in BRAND_ALIASES.get(brand, [brand]):
        for candidate in (brand_slug(normalize_text(alias)), brand_slug(alias).replace("-", "")):
            if candidate and candidate not in out:
                out.append(candidate)
    return out


def ensure_brand_in_query(query: str, brand: str) -> str:
    """Додає бренд на початок запиту, якщо його там немає (щоб рушій впізнав бренд з фіда)."""
    canon = register_brand(brand)
    if not canon:
        return query
    # у фіді трапляється «SesDermaREPASKIN …» — склеєне з назвою бренд розділяємо пробілом
    glued = re.match(rf"^({re.escape(brand.strip())})(?=[A-Za-z0-9])", query, flags=re.IGNORECASE)
    if glued:
        query = f"{glued.group(1)} {query[glued.end():]}"
    if extract_brand(query) == canon:
        return query
    return f"{brand} {query}".strip()


def extract_brand(query: str) -> str:
    haystack = normalize_text(query)
    compact = haystack.replace(" ", "")
    best = ""
    best_alias = ""
    for canon, aliases in BRAND_ALIASES.items():
        for alias in aliases:
            alias_n = normalize_text(alias)
            if not alias_n:
                continue
            if alias_n in haystack or alias_n.replace(" ", "") in compact:
                if len(alias_n) > len(best_alias):
                    best = canon
                    best_alias = alias_n
    if best:
        return best
    latin = latin_product_name(query)
    tokens = [
        token.lower() for token in latin.split()
        if token.lower() not in GENERIC_BRANDS and len(token) >= 3
    ]
    return tokens[0] if tokens else ""


def _group_hits(blob: str, tokens: set) -> set:
    hits = set()
    # «SPF50» і «SPF 50» — одна ознака сонцезахисту.
    expanded = set(tokens)
    for token in tokens:
        glued = re.match(r"spf(\d{1,3})", token)
        if glued:
            expanded.add("spf")
            expanded.add(glued.group(1))
    tokens = expanded
    for group, members in MODIFIER_GROUPS.items():
        for member in members:
            member_n = normalize_text(member)
            if not member_n:
                continue
            if " " in member_n or not member_n.isascii():
                # кирилиця — за початком слова (очей, тіла, сонцезахисний…)
                if f" {member_n}" in blob:
                    hits.add(group)
                    break
            elif member_n in tokens:
                hits.add(group)
                break
    return hits


def _line_token_conflict(query: str, title: str, url: str = "") -> bool:
    """Чи назва конкурента належить іншій лінійці того ж бренду (Essential vs Nutritive)."""
    drop = _brand_tokens(query)
    q_tokens = set(_latin_tokens(identity_query(query) or query, drop))
    t_text = f"{title} {slug_text(url)}" if url and not re.search(r"[A-Za-z]{4,}", title or "") else title
    t_tokens = set(_latin_tokens(t_text, drop))
    for tok in q_tokens:
        if tok not in LINE_DISCRIMINATORS:
            continue
        for other in LINE_DISCRIMINATORS[tok]:
            if other in t_tokens:
                return True
    return False


def extra_modifiers(query: str, *texts: str) -> set:
    """Групи ознак, які є в назві конкурента, але відсутні в запиті."""
    q_blob = f" {normalize_text(query)} "
    t_blob = f" {normalize_text(' '.join(texts))} "
    q_groups = _group_hits(q_blob, set(q_blob.split()))
    t_groups = _group_hits(t_blob, set(t_blob.split()))
    extras = t_groups - q_groups
    # «для обличчя і тіла» — універсальний засіб, а не окремий товар для тіла.
    if "body" in extras and re.search(r"\b(face|facial)\b| облич| лиц", t_blob):
        extras.discard("body")
    # «Solar Dew» у запиті — лінійка SPF; укр. «сонцезахисн» у картці не інший SKU.
    if "sun" in extras:
        q_blob = normalize_text(query)
        if "spf" in q_blob or "сонц" in q_blob or re.search(r"\b(sun|solar|repaskin)\b", q_blob):
            extras.discard("sun")
    return extras


def missing_modifiers(query: str, *texts: str) -> set:
    """Групи ознак із запиту, яких немає в назві конкурента (запит «Eye», а в картці — ні)."""
    q_blob = f" {normalize_text(query)} "
    t_blob = f" {normalize_text(' '.join(texts))} "
    q_groups = _group_hits(q_blob, set(q_blob.split()))
    t_groups = _group_hits(t_blob, set(t_blob.split()))
    return (q_groups - t_groups) & REQUIRED_MODIFIER_GROUPS


def product_kinds(text: str) -> set:
    blob = f" {normalize_text(text)} "
    found = set()
    for kind, tokens in KIND_TOKENS.items():
        if any(token in blob for token in tokens):
            found.add(kind)
    return found


def kinds_compatible(query: str, title: str) -> bool:
    q_kinds, t_kinds = product_kinds(query), product_kinds(title)
    if not q_kinds or not t_kinds:
        return True
    for left, right in KIND_CONFLICTS:
        if q_kinds & left and t_kinds & right and not (t_kinds & left):
            return False
    return True


def brand_matches(query: str, title: str, url: str = "") -> bool:
    brand = extract_brand(query)
    if not brand:
        return True
    haystack = f"{normalize_text(title)} {normalize_text(slug_text(url))}"
    compact = haystack.replace(" ", "")
    for alias in BRAND_ALIASES.get(brand, [brand]):
        alias_n = normalize_text(alias)
        if alias_n and (alias_n in haystack or alias_n.replace(" ", "") in compact):
            return True
    return brand in haystack or brand.replace(" ", "") in compact


def parse_packaging(text: str) -> Optional[str]:
    match = PACKAGING_RE.search(text or "")
    if not match:
        return None
    token = match.group(1).lower()
    if token.startswith(("refill", "рефіл", "рефил", "змін", "смен")):
        return "Refill"
    if token.startswith(("normal", "стандарт", "звичай")):
        return "стандарт"
    return match.group(1)


def core_title_key(title: str) -> str:
    text = VOLUME_RE.sub(" ", title or "")
    tokens = [
        token for token in normalize_text(text).split()
        if token not in PACKAGING_STOPWORDS and len(token) >= 2
    ]
    return " ".join(tokens)


def valid_price(value) -> Optional[float]:
    price = parse_price(value) if not isinstance(value, (int, float)) else float(value)
    if price is None or price < MIN_PRICE:
        return None
    return price


def option_label(option: dict) -> str:
    parts = [option.get("volume"), option.get("packaging"), option.get("label")]
    seen, clean = set(), []
    for part in parts:
        text = re.sub(r"\s+", " ", str(part or "")).strip(" ·")
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        clean.append(text)
    return " · ".join(clean[:3]) or "варіант"


ARTICLE_CODE_RE = re.compile(r"\b\d{4,7}(?:-\d{2,6})?\b")


def article_codes(query: str) -> list[str]:
    """Артикули з фіда / назви (02261, 03047-1444) — сильний ключ для пошуку в UA-магазинах."""
    return list(dict.fromkeys(ARTICLE_CODE_RE.findall(query or "")))


def cyrillic_search_phrase(query: str, *, max_words: int = 5) -> str:
    """Значуща кирилична частина запиту (укр. назва з фіда) без обʼєму."""
    text = VOLUME_RE.sub(" ", query or "")
    words = [
        w for w in normalize_text(text).split()
        if not w.isascii() and len(w) >= 4 and w not in QUERY_STOPWORDS
    ]
    return " ".join(words[:max_words]).strip()


_SEARCH_MODEL = threading.local()


def set_search_feed_model(model: Optional[str]) -> None:
    """Артикул з фіда (product.model) для query_variants у межах поточного потоку."""
    _SEARCH_MODEL.value = (str(model).strip() if model else "") or None


def _feed_model_codes(query: str) -> list[str]:
    model = getattr(_SEARCH_MODEL, "value", None)
    if not model:
        return []
    codes = [model] if model not in article_codes(query) else []
    return codes


def all_article_codes(query: str) -> list[str]:
    """Артикули з назви + product.model з фіда."""
    return list(dict.fromkeys([*article_codes(query), *_feed_model_codes(query)]))


def query_variants(query: str) -> list:
    """Латинська назва, укр. назва, артикул, варіанти без дефісів / обʼєму."""
    variants = []
    latin = latin_product_name(query)
    stripped = " ".join(
        token for token in (latin or query).split()
        if token.lower() not in QUERY_STOPWORDS
    )
    stripped_no_vol = VOLUME_RE.sub("", stripped)
    brand_only = ""
    brand = extract_brand(query)
    alias = ""
    if brand:
        brand_only = BRAND_ALIASES.get(brand, [brand])[0]
        alias = brand_only
    cyr = cyrillic_search_phrase(query)
    cyr_brand = f"{alias} {cyr}".strip() if alias and cyr else ""
    article_vars = []
    feed_codes = _feed_model_codes(query)
    for code in list(dict.fromkeys([*article_codes(query), *feed_codes])):
        if alias:
            article_vars.append(f"{alias} {code}")
        article_vars.append(code)
    # Спочатку точніші запити — менше зайвих HTTP-раундів.
    seeds = (
        query,
        latin,
        stripped,
        stripped_no_vol,
        VOLUME_RE.sub("", query),
        brand_line_query(query),
        *article_vars,
        cyr_brand,
        cyr,
        brand_only,
    )
    for raw in seeds:
        cleaned = re.sub(r"\s+", " ", raw or "").strip(" -–—")
        if cleaned and cleaned not in variants:
            variants.append(cleaned)
        folded = fold_diacritics(cleaned)
        if folded and folded != cleaned and folded not in variants:
            variants.append(folded)
        # Shopify / OpenCart часто індексують без дефісів: PHYTO-GOMMAGE → PHYTO GOMMAGE / PHYTOGOMMAGE
        if cleaned and "-" in cleaned:
            spaced = cleaned.replace("-", " ")
            glued = re.sub(r"(?<=\w)-(?=\w)", "", cleaned)
            for alt in (spaced, glued):
                alt = re.sub(r"\s+", " ", alt).strip()
                if alt and alt not in variants:
                    variants.append(alt)
        # Деякі магазини нормалізують пробіли/дефіси в один токен: Sesderma C Vit → sesdermacvit.
        composite = re.sub(r"[\s_-]+", "", cleaned)
        if composite and composite not in variants:
            variants.append(composite)
        if len(cleaned.split()) > 1:
            words = [w for w in re.split(r"[\s_-]+", cleaned) if w]
            if len(words) > 1:
                compact_tail = "".join(words[1:])
                merged = f"{words[0]}{compact_tail}"
                if merged and merged not in variants:
                    variants.append(merged)
                if len(words) > 2:
                    variants.append(f"{words[0]}{words[-1]}")
    # lowercase першим: Notino рідше віддає 403 на lower; інші магазини case-insensitive.
    ordered: list[str] = []
    for item in variants:
        low = item.lower()
        if low not in ordered:
            ordered.append(low)
        if item not in ordered:
            ordered.append(item)
    # Занадто довгий список гальмує прогін — залишаємо найкорисніші.
    cap = 12 if article_vars else 16
    return ordered[:cap]


def brand_line_query(query: str) -> str:
    """Бренд + назва лінійки («Sesderma C-Vit») — найширший запит, коли точний нічого не дав."""
    brand = extract_brand(query)
    drop = _brand_tokens(query)
    words = [
        w for w in (latin_product_name(query) or query).split()
        if normalize_text(w) and not set(normalize_text(w).split()) <= drop
        and normalize_text(w) not in QUERY_STOPWORDS
    ]
    if not brand or not words:
        return ""
    alias = BRAND_ALIASES.get(brand, [brand])[0]
    return f"{alias} {words[0]}"


def _strong_listing_hit(query: str, item: dict, site: SiteConfig) -> bool:
    if not isinstance(item, dict):
        return False
    title, url = item.get("title") or "", item.get("url") or ""
    score = candidate_prefetch_score(query, item)
    hay = f"{title} {url}".lower()
    if any(code.lower() in hay for code in all_article_codes(query)) and score >= 0.72:
        return True
    if score >= 0.78 and brand_matches(query, title, url):
        return True
    if score >= 0.86 and brand_matches(query, title, url):
        return True
    return score >= site.min_similarity and brand_matches(query, title, url)


def results_good_enough(site: SiteConfig, query: str, items: list) -> bool:
    for item in items:
        if _strong_listing_hit(query, item, site):
            return True
    return False


def normalize_product_url(url: str) -> str:
    url = url.split("#")[0].strip()
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=False)
    kept = {}
    if "route" in qs or "product_id" in qs:
        return url
    if "variant" in qs:
        raw = qs["variant"][0]
        digits = re.match(r"(\d+)", raw or "")
        if digits:
            kept["variant"] = digits.group(1)
    query = urlencode(kept) if kept else ""
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", query, ""))


def collect_result_links(site: SiteConfig, soup) -> list:
    items = []
    by_url = {}
    skip_paths = ("/delivery", "/about", "/contact", "/blog", "/cart", "/account", "/login")
    for a in soup.select(site.result_link_selector):
        node = a if a.name == "a" else a.find("a") or a.find_parent("a")
        if node is None:
            continue
        href = (node.get("href") or "").strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:")):
            continue
        full = normalize_product_url(urljoin(site.base_url, href))
        host = urlparse(full).netloc
        if any(skip in host for skip in EXTERNAL_HOSTS):
            continue
        shop_host = urlparse(site.base_url).netloc
        if host and host != shop_host and not host.endswith("." + shop_host):
            continue
        if site.result_href_contains and site.result_href_contains not in full:
            continue
        path = urlparse(full).path.lower()
        if any(skip in path for skip in skip_paths):
            continue
        title = (node.get_text(" ", strip=True) or a.get_text(" ", strip=True) or "").strip()
        parent = node.find_parent(["article", "li", "div"])
        if not title:
            scope = parent or node.parent
            if scope is not None:
                for img in scope.select("img[alt]"):
                    alt = (img.get("alt") or "").strip()
                    if alt and len(alt) > 3 and not LOGO_RE.search(alt):
                        title = re.sub(r"\s*\(Фото-?\d+\)\s*", " ", alt, flags=re.I).strip()
                        break
        if parent and len((parent.get_text(" ", strip=True) or "")) < 280:
            parent_title = parent.get_text(" ", strip=True)
            if len(parent_title) > len(title) + 8:
                title = parent_title
        existing = by_url.get(full)
        if existing:
            if len(title) > len(existing.get("title") or ""):
                existing["title"] = title
            continue
        item = {"url": full, "title": title}
        by_url[full] = item
        items.append(item)
    # Посилання з назвою корисніші за порожні img-лінкі на той самий товар.
    items.sort(key=lambda row: (0 if (row.get("title") or "").strip() else 1, row.get("url") or ""))
    return items


def _brand_filtered_results(query: str, items: list) -> list:
    """Якщо серед видачі є картки з брендом — лишаємо лише їх (Shopify часто підсовує чуже)."""
    branded = [
        item for item in items
        if isinstance(item, dict)
        and brand_matches(query, item.get("title") or "", item.get("url") or "")
    ]
    if extract_brand(query):
        return branded
    return branded or items


def _article_filtered_results(query: str, items: list) -> list:
    """Якщо в запиті є артикул — лишаємо картки, де він є в назві або URL."""
    codes = [c.lower() for c in all_article_codes(query)]
    if not codes or not items:
        return items
    matched = []
    for item in items:
        if not isinstance(item, dict):
            continue
        hay = f"{item.get('title') or ''} {item.get('url') or ''}".lower()
        if any(code in hay for code in codes):
            matched.append(item)
    return matched or items


def _search_results_for_query(query: str, items: list) -> list:
    items = _brand_filtered_results(query, items)
    return _article_filtered_results(query, items)


def find_html_search_results(site: SiteConfig, query: str, debug: bool = False) -> list:
    found = []
    seen = set()
    for q in query_variants(query):
        for pattern in site.search_url_patterns:
            url = pattern.format(q=quote(q))
            soup = fetch(url, debug=debug)
            if soup is None:
                continue
            items = collect_result_links(site, soup)
            if debug:
                print(
                    f"    [debug] {url} — {len(items)} посилань за селектором "
                    f"'{site.result_link_selector}'"
                )
            for item in items:
                if item["url"] not in seen:
                    seen.add(item["url"])
                    found.append(item)
            # Чужі картки не зупиняють пошук — пробуємо наступний варіант запиту.
            if results_good_enough(site, query, found):
                return _search_results_for_query(query, found)
        if results_good_enough(site, query, found):
            return _search_results_for_query(query, found)
    return _search_results_for_query(query, found)


def find_woocommerce_results(site: SiteConfig, query: str, debug: bool = False) -> list:
    products = []
    for q in query_variants(query):
        url = site.api_url.format(q=quote(q))
        data = fetch_text(
            url,
            debug=debug,
            extra_headers={"Accept": "application/json"},
            as_json=True,
        )
        if not isinstance(data, list) or not data:
            continue
        for item in data:
            name = (item.get("name") or "").strip()
            permalink = item.get("permalink") or ""
            if not permalink:
                continue
            prices = item.get("prices") or {}
            minor = int(prices.get("currency_minor_unit") or 2)
            raw_price = prices.get("sale_price") or prices.get("price")
            price = None
            try:
                if raw_price not in (None, ""):
                    price = float(str(raw_price)) / (10 ** minor)
            except ValueError:
                price = None
            images = item.get("images") or []
            image = None
            if images and isinstance(images[0], dict):
                image = images[0].get("src")
            elif images and isinstance(images[0], str):
                image = images[0]
            products.append(
                {
                    "title": clean_title(name),
                    "price": price,
                    "url": permalink,
                    "image": image,
                    "preloaded": True,
                }
            )
        if results_good_enough(site, query, products):
            return products
    return products or find_html_search_results(site, query, debug=debug)


def find_live_search_results(site: SiteConfig, query: str, debug: bool = False) -> list:
    products = []
    for q in query_variants(query):
        url = site.api_url.format(q=quote(q))
        data = fetch_text(
            url,
            debug=debug,
            extra_headers={
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/javascript, */*; q=0.01",
            },
            as_json=True,
        )
        items = []
        if isinstance(data, dict):
            items = data.get("products") or data.get("items") or []
        elif isinstance(data, list):
            items = data
        for item in items:
            if not isinstance(item, dict):
                continue
            raw_name = item.get("name") or item.get("title") or item.get("product_name") or ""
            name = BeautifulSoup(str(raw_name), "lxml").get_text(" ", strip=True)
            href = item.get("url") or item.get("href") or item.get("link") or item.get("permalink") or ""
            if not href:
                continue
            products.append(
                {
                    "title": clean_title(name),
                    "price": parse_price(item.get("special") or item.get("price") or item.get("sale_price")),
                    "url": normalize_product_url(urljoin(site.base_url, href)),
                    "image": item.get("image") or item.get("img") or item.get("thumbnail"),
                    "preloaded": True,
                }
            )
        if results_good_enough(site, query, products):
            return products
    return products or find_html_search_results(site, query, debug=debug)


def _card_text(card, selectors: str) -> str:
    parts = []
    for el in card.select(selectors) if selectors else []:
        text = el.get_text(" ", strip=True)
        if not text:
            text = (
                (el.get("alt") or "").strip()
                or (el.get("title") or "").strip()
                or (el.get("aria-label") or "").strip()
            )
        if text and text not in parts:
            parts.append(text)
    return " ".join(parts)


def _card_image(site: SiteConfig, card) -> Optional[str]:
    for img in card.select(site.card_image or "img"):
        for attr in ("data-src", "data-original", "data-lazy", "src"):
            src = (img.get(attr) or "").strip()
            if src and not src.startswith("data:") and not src.endswith(".svg"):
                return urljoin(site.base_url, src)
    return None


def _soup_out_of_stock(soup) -> bool:
    """Сторінка товару позначена як відсутня в наявності."""
    if soup is None:
        return False
    body = soup.find("body")
    classes = " ".join((body.get("class") or []) if body else []).lower()
    if CARD_OOS_CLASS_RE.search(classes):
        return True
    text = soup.get_text(" ", strip=True).lower()
    return any(marker in text for marker in OUT_OF_STOCK_MARKERS)


def _card_out_of_stock(card) -> bool:
    card_text = card.get_text(" ", strip=True).lower()
    if any(marker in card_text for marker in OUT_OF_STOCK_MARKERS):
        return True
    for el in card.find_all(True):
        classes = " ".join(el.get("class") or [])
        compact = classes.replace("_", "").replace("-", "").replace(" ", "")
        if CARD_OOS_CLASS_RE.search(classes) or CARD_OOS_CLASS_RE.search(compact):
            return True
    return False


def parse_cards(site: SiteConfig, soup) -> list:
    """Картки товарів зі сторінки пошуку або каталогу бренду: назва, ціна, посилання, наявність."""
    items = []
    seen = set()
    for card in soup.select(site.card_selector):
        link = card.select_one(site.card_link) if site.card_link else None
        href = (link.get("href") if link else "") or ""
        if not href or href.startswith(("javascript:", "#")):
            # IsNuYou тощо: URL товару лише в data-product-url, а <a href="#">.
            href = (
                (card.get("data-product-url") or "")
                or (card.get("data-url") or "")
                or (card.get("data-href") or "")
            ).strip()
        if not href or href.startswith(("javascript:", "#")):
            continue
        # кошик / wishlist — не сторінка товару
        if any(x in href.lower() for x in ("/cart", "add-to-cart", "wishlist", "compare")):
            continue
        url = normalize_product_url(urljoin(site.base_url, href))
        if url in seen or _is_search_or_listing_url(url):
            continue
        parts = []
        if site.card_title_attr and link is not None:
            parts.append((link.get(site.card_title_attr) or "").strip())
        parts.append(_card_text(card, site.card_title))
        title = ""
        for part in parts:
            if part and normalize_text(part) not in normalize_text(title):
                title = f"{title} {part}".strip()
        if not title and link is not None:
            title = link.get_text(" ", strip=True)
        price_el = card.select_one(site.card_price) if site.card_price else None
        raw_price = None
        if price_el is not None:
            raw_price = price_el.get(site.card_price_attr) if site.card_price_attr else price_el.get_text(" ", strip=True)
        price = valid_price(raw_price)
        if site.card_volume_attr and price_el is not None:
            unit = (price_el.get(site.card_volume_attr) or "").strip()
            if unit and not extract_volumes(title):
                title = f"{title} {re.sub(r'(?<=[0-9])(?=[a-zA-Zа-яА-Я])', ' ', unit)}"
        title = clean_title(title)
        # Довгі описи з карток (Skin Health) лише для бренду — обрізаємо шум.
        if len(title) > 160:
            title = title[:160].rsplit(" ", 1)[0].strip()
        if not title or _is_junk_product_title(title):
            continue
        in_stock = not _card_out_of_stock(card)
        # Немає в наявності — лишаємо картку навіть без ціни в списку.
        if price is None and in_stock:
            continue
        seen.add(url)
        items.append(
            {
                "title": title,
                "price": price,
                "url": url,
                "image": _card_image(site, card),
                "in_stock": in_stock,
                "preloaded": True,
            }
        )
    return items


def _is_search_or_listing_url(url: str) -> bool:
    """Посилання на пошук/фільтр каталогу — не сторінка товару."""
    low = (url or "").lower()
    if not low:
        return True
    path = low.split("?", 1)[0]
    # Top Beauty (MODX): /poshuk-na-sajti/; інші UA/DE-шляхи пошуку.
    if re.search(r"/(poshuk|poisk|suche)(?:[-_/]|$)", path):
        return True
    if any(x in low for x in (
        "/search", "/search?", "search.asp", "route=product/search",
        "?search=", "&search=", "?s=", "&s=", "?q=", "&q=",
        "filter_name=", "post_type=product&s=",
    )):
        # PDP інколи містить ?search= у query — рідко; типовий шлях товару інший.
        if path.rstrip("/").endswith("/search") or "/search/" in path or path.endswith("search.asp"):
            return True
        if "route=product/search" in low or "/search?" in low:
            return True
        # krasota/opencart: https://host/ua/search/?search=...
        if re.search(r"/search/?$", path) or re.search(r"/search/?\?", low):
            return True
    return False


def _is_junk_product_title(title: str) -> bool:
    t = (title or "").strip().lower()
    if not t:
        return True
    return t.startswith((
        "пошук", "поиск", "search", "результати пошуку", "результаты поиска",
        "ничего не найдено", "нічого не знайдено",
    ))


def _looks_like_product_page(soup, page_url: str = "") -> bool:
    """WooCommerce / OpenCart часто редіректять точний пошук на картку товару."""
    if page_url and _is_search_or_listing_url(page_url):
        return False
    body = soup.find("body")
    classes = " ".join((body.get("class") or []) if body else []).lower()
    # Krasota.shop: body.product-search + h1.product-title «Пошук - …» — це НЕ PDP.
    if "product-search" in classes or "search-results" in classes or "page-search" in classes:
        return False
    h1 = soup.select_one("h1")
    if h1 is not None and _is_junk_product_title(h1.get_text(" ", strip=True)):
        return False
    if any(token in classes for token in ("single-product", "product-template", "product-detail")):
        return True
    # Не використовувати голий h1.product-title — на Krasota так називається заголовок пошуку.
    if soup.select_one("h1.product_title, meta[property='og:type'][content='product']"):
        return True
    if soup.select_one("h1.product-title") and "product-search" not in classes:
        # Лише якщо це схоже на картку (ціна + gallery), не пошук.
        if soup.select_one(".woocommerce-product-gallery, .product-images, .product-info, #product"):
            return True
    return False


def parse_single_product_page(site: SiteConfig, soup, fallback_url: str = "") -> Optional[dict]:
    """Один товар зі сторінки PDP (коли пошук зробив редірект замість видачі списку)."""
    if not _looks_like_product_page(soup, page_url=fallback_url):
        return None
    title_el = soup.select_one(
        "h1.product_title, h1[itemprop='name'], "
        ".product_title, .product-info h1, .product-essential h1"
    )
    # h1.product-title лише якщо вже підтвердили PDP (не пошук).
    if title_el is None and _looks_like_product_page(soup, page_url=fallback_url):
        title_el = soup.select_one("h1.product-title")
    title = ""
    if title_el is not None:
        title = title_el.get_text(" ", strip=True)
    if not title:
        og = soup.select_one("meta[property='og:title']")
        title = (og.get("content") or "").strip() if og else ""
    title = clean_title(title)
    if not title or _is_junk_product_title(title):
        return None
    if fallback_url and _is_search_or_listing_url(fallback_url):
        return None
    price = None
    for sel in (
        "p.price ins .woocommerce-Price-amount",
        "p.price .woocommerce-Price-amount",
        ".summary p.price",
        "p.price",
        ".product-price",
        ".rm-module-price",
        "span.price",
        "[itemprop='price']",
    ):
        el = soup.select_one(sel)
        if el is None:
            continue
        raw = el.get("content") or el.get_text(" ", strip=True)
        price = valid_price(raw)
        if price is not None:
            break
    if price is None:
        return None
    href = ""
    canonical = soup.select_one("link[rel='canonical']")
    if canonical is not None:
        href = (canonical.get("href") or "").strip()
    if not href:
        og_url = soup.select_one("meta[property='og:url']")
        href = (og_url.get("content") or "").strip() if og_url else ""
    if not href:
        href = fallback_url
    if not href:
        return None
    url = normalize_product_url(urljoin(site.base_url, href))
    image = None
    for sel in (
        "meta[property='og:image']",
        ".woocommerce-product-gallery img",
        ".product-images img",
        "img.wp-post-image",
    ):
        el = soup.select_one(sel)
        if el is None:
            continue
        src = (el.get("content") or el.get("data-src") or el.get("src") or "").strip()
        if src and not src.startswith("data:"):
            image = urljoin(site.base_url, src)
            break
    return {
        "title": title,
        "price": price,
        "url": url,
        "image": image,
        "in_stock": not any(m in soup.get_text(" ", strip=True).lower() for m in OUT_OF_STOCK_MARKERS),
        "preloaded": True,
    }


def find_card_results(site: SiteConfig, query: str, debug: bool = False) -> list:
    products, seen = [], set()
    for q in query_variants(query):
        for url, soup in _iter_search_pages(site, q, debug=debug):
            # Редірект на PDP: related-картки зі сторінки товару гірші за сам товар.
            # Ніколи не плутати сторінку пошуку (Krasota h1.product-title «Пошук») з товаром.
            cards = []
            if not _is_search_or_listing_url(url):
                single = parse_single_product_page(site, soup, fallback_url=url)
                if single is not None:
                    cards = [single]
            if not cards:
                cards = parse_cards(site, soup)
            if debug:
                branded_n = sum(
                    1 for card in cards
                    if brand_matches(query, card.get("title") or "", card.get("url") or "")
                )
                print(f"    [debug] {url} — {len(cards)} карток (бренд {branded_n})")
            for card in cards:
                if card["url"] not in seen:
                    seen.add(card["url"])
                    products.append(card)
            if results_good_enough(site, query, products):
                return _search_results_for_query(query, products)
        if results_good_enough(site, query, products):
            return _search_results_for_query(query, products)
    return _search_results_for_query(query, products)


def parse_jsonld_list(site: SiteConfig, soup) -> list:
    """Картки з JSON-LD (CollectionPage → mainEntity[Product]) — так віддає список Notino."""
    items, seen = [], set()

    def add(node):
        if not isinstance(node, dict):
            return
        types = str(node.get("@type", ""))
        if "Product" not in types:
            return
        offers = node.get("offers")
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        offers = offers if isinstance(offers, dict) else {}
        href = offers.get("url") or node.get("url") or ""
        title = clean_title(str(node.get("name") or ""))
        if not href or not title:
            return
        url = normalize_product_url(urljoin(site.base_url, href))
        if url in seen:
            return
        seen.add(url)
        availability = str(offers.get("availability") or "")
        items.append({
            "title": title,
            "price": valid_price(offers.get("price") or offers.get("lowPrice")),
            "url": url,
            "image": pick_image_url(node.get("image")),
            "in_stock": "OutOfStock" not in availability,
            "preloaded": True,
        })

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        def walk(node):
            if isinstance(node, list):
                for sub in node:
                    walk(sub)
                return
            if not isinstance(node, dict):
                return
            add(node)
            for val in node.values():
                if isinstance(val, (dict, list)):
                    walk(val)

        for node in data if isinstance(data, list) else [data]:
            walk(node)
    return items


def find_jsonld_results(site: SiteConfig, query: str, debug: bool = False) -> list:
    products, seen = [], set()
    for q in query_variants(query):
        for pattern in site.search_url_patterns:
            soup = fetch(pattern.format(q=quote(q)), debug=debug)
            if soup is None:
                continue
            cards = parse_jsonld_list(site, soup)
            if not cards:
                cards = parse_microdata_products(site, soup)
            if debug:
                print(f"    [debug] {site.name} «{q}» — {len(cards)} карток JSON-LD")
            for card in cards:
                if card["url"] not in seen:
                    seen.add(card["url"])
                    products.append(card)
            if results_good_enough(site, query, products):
                return _search_results_for_query(query, products)
        if results_good_enough(site, query, products):
            return _search_results_for_query(query, products)
    return _search_results_for_query(query, products)


def extract_jsonld_options(soup, page_url: str) -> list:
    """Варіанти обʼєму з JSON-LD Product.offers[] (назва пропозиції містить обʼєм)."""
    options, seen = [], set()
    page_path = urlparse(page_url).path.rstrip("/")

    def walk(node):
        if isinstance(node, list):
            for sub in node:
                walk(sub)
            return
        if not isinstance(node, dict):
            return
        if node.get("@graph"):
            walk(node["@graph"])
        if "Product" not in str(node.get("@type", "")):
            return
        offers = node.get("offers")
        offers = offers.get("offers") if isinstance(offers, dict) and offers.get("offers") else offers
        if isinstance(offers, dict):
            offers = [offers]
        if not isinstance(offers, list) or len(offers) < 2:
            return
        for offer in offers:
            if not isinstance(offer, dict):
                continue
            label = str(offer.get("name") or "")
            volumes = extract_volumes(label)
            price = valid_price(offer.get("price"))
            if not volumes or price is None:
                continue
            href = urljoin(page_url, offer.get("url") or "")
            key = (volumes[0], price)
            if key in seen:
                continue
            seen.add(key)
            options.append({
                "volume": format_volume(volumes[0]),
                "volume_raw": volumes[0],
                "packaging": parse_packaging(label),
                "price": price,
                "active": bool(href) and urlparse(href).path.rstrip("/") == page_path,
                "label": label[:90],
                "url": normalize_product_url(href) if href else page_url,
                "in_stock": "OutOfStock" not in str(offer.get("availability") or ""),
            })

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            walk(json.loads(script.string or script.get_text() or ""))
        except (json.JSONDecodeError, TypeError):
            continue
    return options


def brand_slug(brand: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (brand or "").lower()).strip("-")


_LISTING_CACHE: dict = {}
_LISTING_LOCKS: dict = {}
_LISTING_GUARD = threading.Lock()


def brand_listing(site: SiteConfig, brand: str, debug: bool = False) -> list:
    """Увесь асортимент бренду в магазині (з пагінацією). Кешується на процес."""
    key = (site.name, brand)
    with _LISTING_GUARD:
        lock = _LISTING_LOCKS.setdefault(key, threading.Lock())
    with lock:
        if key in _LISTING_CACHE:
            return _LISTING_CACHE[key]
        items, seen = [], set()
        if site.brand_page_url:
            for slug in brand_slugs(brand):
                for page in range(1, site.max_pages + 1):
                    template = site.brand_first_url if page == 1 and site.brand_first_url else site.brand_page_url
                    url = template.format(slug=slug, page=page)
                    soup = fetch(url, debug=debug)
                    if soup is None:
                        break
                    cards = [card for card in parse_cards(site, soup) if card["url"] not in seen]
                    if debug:
                        print(f"    [debug] {site.name} бренд {brand} ({slug}) стор. {page}: {len(cards)} нових карток")
                    if not cards:
                        break
                    for card in cards:
                        seen.add(card["url"])
                        items.append(card)
                if items:
                    break
        _LISTING_CACHE[key] = items
        return items


_PRESENCE_CACHE: dict = {}
_PRESENCE_LOCKS: dict = {}


NO_RESULTS_RE = re.compile(
    r"не\s+знайдено|не\s+найдено|нічого\s+не\s+знайдено|"
    r"запитувана\s+сторінка\s+не\s+знайдена|nothing\s+found|no\s+products\s+found",
    re.IGNORECASE,
)


def clear_listing_cache() -> None:
    with _LISTING_GUARD:
        _LISTING_CACHE.clear()


def clear_presence_cache() -> None:
    with _LISTING_GUARD:
        _PRESENCE_CACHE.clear()


def clear_scrape_runtime_caches() -> None:
    """HTTP + стан хостів/магазинів; каталог бренду (_LISTING_CACHE) лишається."""
    clear_http_cache()
    try:
        reset_site_strikes()
        reset_host_state()
    except NameError:
        pass


def clear_brand_presence_cache() -> None:
    """Повне очищення на старт/фін catalog job."""
    clear_presence_cache()
    clear_listing_cache()
    clear_scrape_runtime_caches()


def site_carries_brand(site: SiteConfig, query: str, samples: tuple = (), debug: bool = False) -> Optional[bool]:
    """Чи продає магазин цей бренд.

    True — знайшли товари з брендом (пошук або каталог бренду).
    False — пошук за назвою бренду відповів, але Sesderma немає (порожньо / «не знайдено»).
    None — сайт не відповів або пошук ненадійний (не можна стверджувати «немає»).
    """
    del samples  # лише alias бренду; довгі назви SKU на зламаному пошуку давали хибне «ні»
    brand = extract_brand(query)
    if not brand:
        return None
    key = (site.name, brand)
    with _LISTING_GUARD:
        lock = _PRESENCE_LOCKS.setdefault(key, threading.Lock())
    with lock:
        if key in _PRESENCE_CACHE:
            return _PRESENCE_CACHE[key]
        result: Optional[bool] = None
        if site.kind == "brand_catalog" and brand_listing(site, brand, debug=debug):
            result = True
        else:
            aliases = BRAND_ALIASES.get(brand, [brand])
            probes = list(dict.fromkeys([brand, *aliases[:3]]))
            if brand == "sesderma":
                probes.append("сесдерма")
            got_response = False
            saw_confirmed_empty = False
            saw_unrelated_hits = False
            for probe in probes:
                items = raw_search(site, probe, debug=debug, exhaustive=False)
                if items is None:
                    continue
                got_response = True
                def _presence_hit(it) -> bool:
                    if isinstance(it, dict):
                        title = it.get("title") or ""
                        href = it.get("url") or ""
                    else:
                        title, href = "", it or ""
                    if _is_junk_product_title(title) or _is_search_or_listing_url(href):
                        return False
                    return brand_matches(query, title, href)

                if any(_presence_hit(it) for it in items):
                    result = True
                    break
                if not items:
                    saw_confirmed_empty = True
                else:
                    saw_unrelated_hits = True
                    probe_n = normalize_text(probe)
                    brand_probes = {normalize_text(brand), *[normalize_text(a) for a in aliases[:3]]}
                    if probe_n in brand_probes:
                        # «Acnemy» → 30 карток Academie: бренду в магазині немає.
                        result = False
                        break
            if result is None:
                if got_response and saw_confirmed_empty and not saw_unrelated_hits:
                    result = False
                elif got_response and site.kind in ("live_search", "woocommerce") and not saw_unrelated_hits:
                    # JSON-пошук: порожній список — реальна відсутність бренду.
                    result = False
                else:
                    # Чужі картки на коротке імʼя бренду не означають, що точного SKU немає:
                    # пошук «Abril» ловить APRILSKIN, а повна назва ще може знайтися.
                    result = None
        _PRESENCE_CACHE[key] = result
        return result


def parse_microdata_products(site: SiteConfig, soup) -> list:
    """Товари зі schema.org/Product у мікророзмітці (так віддає видачу Епіцентр)."""
    items, seen = [], set()
    for node in soup.select("[itemscope][itemtype]"):
        if not str(node.get("itemtype") or "").rstrip("/").endswith("Product"):
            continue
        title = ""
        for el in node.select("[itemprop=name]"):
            if el.find_parent(attrs={"itemprop": "potentialAction"}):
                continue
            text = el.get_text(" ", strip=True)
            if text and len(text) > 3:
                title = clean_title(text)
                break
        link = node.select_one("a[itemprop=url][href]") or node.select_one("[itemprop=url][href]")
        href = (link.get("href") if link else "") or ""
        if not href or not title:
            continue
        url = normalize_product_url(urljoin(site.base_url, href))
        if url in seen:
            continue
        prices = []
        for pr in node.select("[itemprop=price]"):
            spec = pr.find_parent(attrs={"itemprop": "priceSpecification"})
            if spec is not None:
                ptype = spec.select_one("[itemprop=priceType]")
                if ptype is not None and "Strikethrough" in (ptype.get("content") or ""):
                    continue
            price = valid_price(pr.get("content") or pr.get("value") or pr.get_text(" ", strip=True))
            if price is not None:
                prices.append(price)
        if not prices:
            continue
        avail = node.select_one("[itemprop=availability]")
        availability = ""
        if avail is not None:
            availability = avail.get("content") or avail.get("href") or ""
        img = node.select_one("img[itemprop=image], [itemprop=image]")
        image = None
        if img is not None:
            image = img.get("src") or img.get("content") or img.get("href")
            if image:
                image = urljoin(site.base_url, image)
        seen.add(url)
        items.append({
            "title": title,
            "price": min(prices),
            "url": url,
            "image": image,
            "in_stock": "OutOfStock" not in availability,
            "preloaded": True,
        })
    if items:
        return items
    # Епіцентр інколи віддає картки без itemscope: назва в [data-product-card-name], ціна поруч у тексті.
    for name_el in soup.select("[data-product-card-name]"):
        title = clean_title(name_el.get_text(" ", strip=True))
        node = name_el
        link = name_el.find("a", href=True)
        blob = ""
        for _ in range(5):
            node = node.parent
            if node is None:
                break
            if link is None:
                link = node.find("a", href=True)
            blob = node.get_text(" ", strip=True)
            if len(blob) > 420:
                break
        href = (link.get("href") if link else "") or ""
        if not title or "/shop/" not in href:
            continue
        url = normalize_product_url(urljoin(site.base_url, href))
        if url in seen:
            continue
        unit = re.search(r"(\d[\d\s]{0,8})\s*₴\s*/", blob)
        price = valid_price(unit.group(1)) if unit else None
        if price is None:
            nums = [valid_price(n) for n in re.findall(r"(\d[\d\s]{0,8})\s*₴", blob)]
            nums = [n for n in nums if n]
            price = min(nums) if nums else None
        if price is None:
            continue
        seen.add(url)
        items.append({
            "title": title,
            "price": price,
            "url": url,
            "image": None,
            "in_stock": True,
            "preloaded": True,
        })
    if not items:
        # Ціни інколи є лише в JSON-LD (Organization.owns → Product), без мікророзмітки карток.
        items = parse_jsonld_list(site, soup)
    return items


def find_microdata_results(site: SiteConfig, query: str, debug: bool = False) -> list:
    products, seen = [], set()
    for q in query_variants(query):
        for pattern in site.search_url_patterns:
            soup = fetch(pattern.format(q=quote(q)), debug=debug)
            if soup is None:
                continue
            cards = parse_microdata_products(site, soup)
            if debug:
                print(f"    [debug] {site.name} «{q}» — {len(cards)} карток мікророзмітки")
            for card in cards:
                if card["url"] not in seen:
                    seen.add(card["url"])
                    products.append(card)
            if cards:
                break
        if results_good_enough(site, query, products):
            return products
    return products


def raw_search(site: SiteConfig, query: str, debug: bool = False, exhaustive: bool = True):
    """Один запит до пошуку сайту без варіантів (для перевірки бренду). None — сайт не відповів."""
    if site.kind == "microdata":
        for pattern in site.search_url_patterns:
            soup = fetch(pattern.format(q=quote(query)), debug=debug)
            if soup is not None:
                return parse_microdata_products(site, soup)
        return None
    if site.kind == "jsonld":
        for pattern in site.search_url_patterns:
            soup = fetch(pattern.format(q=quote(query)), debug=debug)
            if soup is not None:
                cards = parse_jsonld_list(site, soup)
                if not cards:
                    cards = parse_microdata_products(site, soup)
                return cards
        return None
    if site.kind in ("cards", "brand_catalog") and (site.search_url_patterns or site.search_post_url):
        for _url, soup in _iter_search_pages(site, query, debug=debug):
            return parse_cards(site, soup)
        return None
    if site.kind == "brand_catalog":
        return []
    if site.kind == "woocommerce" and site.api_url:
        data = fetch_text(site.api_url.format(q=quote(query)), debug=debug,
                          extra_headers={"Accept": "application/json"}, as_json=True)
        if isinstance(data, list):
            return [{"title": it.get("name") or "", "url": it.get("permalink") or ""} for it in data]
    if site.kind == "live_search" and site.api_url:
        data = fetch_text(site.api_url.format(q=quote(query)), debug=debug, extra_headers={
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        }, as_json=True)
        if isinstance(data, (dict, list)):
            rows = (data.get("products") or data.get("items") or []) if isinstance(data, dict) else data
            out = []
            for it in rows:
                if not isinstance(it, dict):
                    continue
                raw_name = it.get("name") or it.get("title") or it.get("product_name") or ""
                title = BeautifulSoup(str(raw_name), "lxml").get_text(" ", strip=True)
                href = it.get("url") or it.get("href") or it.get("link") or it.get("permalink") or ""
                if not href:
                    continue
                out.append({"title": title, "url": href})
            return out
    responded = False
    for pattern in site.search_url_patterns:
        soup = fetch(pattern.format(q=quote(query)), debug=debug)
        if soup is None:
            continue
        responded = True
        items = collect_result_links(site, soup)
        if items:
            return items
    return [] if responded else None


def find_search_results(site: SiteConfig, query: str, debug: bool = False):
    if site.kind == "woocommerce" and site.api_url:
        return find_woocommerce_results(site, query, debug=debug)
    if site.kind == "live_search" and site.api_url:
        return find_live_search_results(site, query, debug=debug)
    if site.kind == "cards":
        return find_card_results(site, query, debug=debug)
    if site.kind == "jsonld":
        return find_jsonld_results(site, query, debug=debug)
    if site.kind == "microdata":
        return find_microdata_results(site, query, debug=debug)
    if site.kind == "brand_catalog":
        listing = brand_listing(site, extract_brand(query), debug=debug)
        if listing:
            return listing
        if site.search_url_patterns:
            return find_card_results(site, query, debug=debug)
        return []
    return find_html_search_results(site, query, debug=debug)


def dedupe_title_repetition(title: str) -> str:
    """Прибирає дубль фрази, коли назву зібрано з кількох селекторів."""
    text = re.sub(r"\s+", " ", (title or "").strip())
    if not text:
        return text
    words = text.split()
    n = len(words)
    for span in range(n // 2, 2, -1):
        if words[:span] == words[span : 2 * span]:
            return " ".join(words[:span])
    for span in range(min(14, n // 2), 3, -1):
        chunk = " ".join(words[:span])
        if len(chunk) < 12:
            continue
        first = text.find(chunk)
        second = text.find(chunk, first + len(chunk))
        if second > 0:
            merged = (text[:second] + text[second + len(chunk) :]).strip()
            merged = re.sub(r"\s+", " ", merged)
            if merged != text:
                return merged
    return text


def clean_title(title: str) -> str:
    """Прибирає типове маркетингове сміття з заголовків сторінок товару."""
    title = dedupe_title_repetition((title or "").replace("ᐉ", "").strip())
    title = re.sub(r"\s*\(\d{6,}\)\s*$", "", title)
    cut_markers = [
        " - купити", " – купити", " - Купити", " – Купити",
        " ➤ Купити", " ➤ Купить",
        " | Інтернет-магазин", " | Интернет-магазин",
        " | A’s Laboratory", " | A's Laboratory",
        " | Інтернет магазин",
        " купити в Києві", " купити за найкращою",
        " – купити за",
    ]
    lower = title.lower()
    cut_at = None
    for sep in cut_markers:
        idx = lower.find(sep.lower())
        if idx > 0 and (cut_at is None or idx < cut_at):
            cut_at = idx
    if cut_at is not None:
        title = title[:cut_at]
    return title.strip(" -–—|")


def parse_price(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[\xa0\u202f\u2009\u2007]|&nbsp;|&thinsp;", " ", text)
    # 1,053.00 або 12,500 — кома як роздільник тисяч (Shopify). Інакше кома лишається десятковою: 1053,50.
    thousands = re.search(r"(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?)", text)
    if thousands:
        try:
            return float(thousands.group(1).replace(",", ""))
        except ValueError:
            return None
    # Кілька чисел («0 ₴ 2 022 ₴» або «1450 грн 1700 грн») — перше валідне > 0
    # (акційна / поточна зазвичай йде першою; 0 з old-price пропускаємо).
    matches = re.findall(r"(\d[\d\s]{0,8}(?:[.,]\d{1,2})?)", text)
    parsed: list[float] = []
    for raw in matches:
        try:
            parsed.append(float(raw.replace(" ", "").replace(",", ".")))
        except ValueError:
            continue
    for price in parsed:
        if price > 0:
            return price
    return parsed[-1] if parsed else None


def extract_volumes(text: str) -> list:
    found = []
    seen = set()
    for amount, unit in VOLUME_RE.findall(text or ""):
        try:
            value = float(amount.replace(",", "."))
        except ValueError:
            continue
        norm = UNIT_MAP.get(unit.lower())
        if not norm:
            continue
        key = (value, norm)
        if key not in seen:
            seen.add(key)
            found.append(key)
    return found


def format_volume(volume) -> Optional[str]:
    if not volume:
        return None
    value, unit = volume
    if value.is_integer():
        amount = str(int(value))
    else:
        amount = str(value).replace(".", ",")
    return f"{amount} {UNIT_LABEL.get(unit, unit)}"


def volumes_equal(a, b) -> bool:
    if not a or not b:
        return False
    return abs(a[0] - b[0]) < 0.001 and a[1] == b[1]


def extract_specs(text: str) -> dict:
    percents = []
    for raw in PERCENT_RE.findall(text or ""):
        try:
            percents.append(float(raw.replace(",", ".")))
        except ValueError:
            continue
    spf = None
    spf_match = SPF_RE.search(text or "")
    if spf_match:
        spf = int(spf_match.group(1))
    return {
        "percents": percents,
        "spf": spf,
        "volumes": extract_volumes(text),
    }


def extract_jsonld_product(soup):
    """Повертає (name, price, image) з JSON-LD schema.org/Product."""
    name, price, image = None, None, None

    def walk(item):
        nonlocal name, price, image
        if not isinstance(item, dict):
            return
        if item.get("@graph"):
            for nested in item["@graph"]:
                walk(nested)
            return
        types = item.get("@type", "")
        if types != "Product" and "Product" not in str(types):
            return
        if item.get("name") and not name:
            name = item.get("name")
        offers = item.get("offers")
        offer_price = None
        if isinstance(offers, dict):
            offer_price = offers.get("price") or offers.get("lowPrice")
        elif isinstance(offers, list) and offers:
            offer_price = offers[0].get("price") or offers[0].get("lowPrice")
        parsed = parse_price(offer_price)
        if parsed is not None:
            price = parsed
        img = item.get("image")
        picked = pick_image_url(img)
        if picked:
            image = picked

    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text() or ""
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            walk(item)
            if name and price is not None and image:
                return name, price, image
    return name, price, image


def pick_image_url(value) -> Optional[str]:
    candidates = []
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, dict):
        candidates = [value.get("url") or value.get("src") or value.get("contentUrl") or ""]
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                candidates.append(item)
            elif isinstance(item, dict):
                candidates.append(item.get("url") or item.get("src") or item.get("contentUrl") or "")
    for url in candidates:
        url = (url or "").strip()
        if url and not LOGO_RE.search(url):
            return url
    return None


def collect_page_titles(soup) -> list:
    titles = []
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        titles.append(h1.get_text(" ", strip=True))
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        titles.append(og_title["content"])
    if soup.title:
        titles.append(soup.title.get_text(strip=True))
    cleaned, seen = [], set()
    for title in titles:
        title = clean_title(title)
        if title and title not in seen:
            seen.add(title)
            cleaned.append(title)
    return cleaned


def pick_best_title(titles: list, query: str, fallback: str) -> str:
    if not titles:
        return fallback
    if not query:
        return titles[0]
    best = titles[0]
    best_score = similarity(query, best)
    for title in titles[1:]:
        score = similarity(query, title)
        if score > best_score + 0.03:
            best, best_score = title, score
    return best


def extract_page_image(soup, page_url: str, jsonld_image: Optional[str] = None) -> Optional[str]:
    candidates = []
    if jsonld_image:
        candidates.append(jsonld_image)
    for prop in ("og:image", "og:image:secure_url", "twitter:image"):
        tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
        if tag and tag.get("content"):
            candidates.append(tag["content"])
    for sel in (
        ".product-image img",
        ".product-photo img",
        ".thumbnails a",
        "a.thumbnail img",
        "#content .image img",
        ".product-info img",
        "img.product-image",
    ):
        for node in soup.select(sel)[:4]:
            src = node.get("href") or node.get("src") or node.get("data-src") or node.get("content")
            if src:
                candidates.append(src)
    for url in candidates:
        full = urljoin(page_url, url.strip())
        if full.startswith("http") and not LOGO_RE.search(full):
            return full
    return None


def extract_visible_price(soup) -> Optional[float]:
    scopes = [
        soup.select_one(
            ".product-info, #product, .product-page, .tb_product, "
            ".product-details, .product-essential, .info-section"
        ),
        soup,
    ]
    selectors = (
        ".info-section__price",
        ".price-original",
        "span[itemprop='price']",
        "meta[itemprop='price']",
        ".price-new",
        ".autocalc-product-special",
        ".autocalc-product-price",
        ".product-price .price",
        ".product-item__price-new",
        "p.price",
    )
    seen = set()
    for scope in scopes:
        if scope is None or id(scope) in seen:
            continue
        seen.add(id(scope))
        for sel in selectors:
            node = scope.select_one(sel)
            if node is None:
                continue
            raw = node.get("content") or node.get_text(" ", strip=True)
            price = valid_price(raw)
            if price is not None:
                return price
    return None


def extract_options(soup) -> list:
    options = []
    seen = set()
    scope = soup.select_one(
        ".product-info, .product-variants, #product, .product-details, "
        ".product-page, .tb_product, .options"
    ) or soup
    nodes = scope.select(
        "select option, .options label, .product-options label, .radio label, "
        ".form-group option, .products-item__select option"
    )
    for node in nodes:
        text = re.sub(r"\s+", " ", node.get_text(" ", strip=True))
        if len(text) < 2 or len(text) > 120:
            continue
        volumes = extract_volumes(text)
        packaging = parse_packaging(text)
        if not volumes and not packaging:
            continue
        price = None
        price_match = PRICE_IN_TEXT_RE.search(text)
        if price_match:
            price = valid_price(price_match.group(1))
        active = "active" in (node.get("class") or []) or node.has_attr("selected")
        volume_raw = volumes[0] if volumes else None
        key = (volume_raw, packaging, price, text.lower())
        if key in seen:
            continue
        seen.add(key)
        options.append(
            {
                "volume": format_volume(volume_raw) if volume_raw else None,
                "volume_raw": volume_raw,
                "packaging": packaging,
                "price": price,
                "active": active,
                "label": text[:90],
            }
        )
    return options


def apply_matching_option(product: dict, query_volume) -> dict:
    if not query_volume:
        return product
    options = product.get("options") or []
    current = product.get("volume_raw")
    if current and volumes_equal(current, query_volume):
        product["volume_match"] = "exact"
        return product
    for option in options:
        if not volumes_equal(option.get("volume_raw"), query_volume):
            continue
        if option.get("price") is not None:
            product["volume"] = option["volume"]
            product["volume_raw"] = option["volume_raw"]
            product["volume_match"] = "exact"
            product["price"] = option["price"]
            product["price_note"] = f"ціна для {option['volume']}"
            if option.get("url"):
                product["url"] = option["url"]
            return product
        if option.get("active"):
            product["volume"] = option["volume"]
            product["volume_raw"] = option["volume_raw"]
            product["volume_match"] = "exact"
            return product
        product["volume_match"] = "option_no_price"
        product["price_note"] = (
            f"на сторінці є {option['volume']}, але показана ціна може бути за інший обʼєм"
        )
        return product
    if current or options:
        product["volume_match"] = "mismatch"
    return product


def parse_fabo_active_variant(html: str, page_url: str) -> Optional[dict]:
    soup = BeautifulSoup(html, "lxml")
    attrs = {}
    for item in soup.select(".product-variants .variant-item"):
        title = item.select_one(".variant-item__title")
        active = item.select_one(".ui-badge-item.active")
        if title is None or active is None:
            continue
        attrs[normalize_text(title.get_text())] = active.get_text(" ", strip=True)
    volume_text = None
    packaging = None
    for key, value in attrs.items():
        if "упаков" in key or "package" in key:
            packaging = parse_packaging(value) or value
        if "об" in key or "volume" in key or "об ем" in key:
            volume_text = value
    if not volume_text:
        for value in attrs.values():
            if extract_volumes(value):
                volume_text = value
                break
    price = extract_visible_price(soup)
    if price is None:
        price = valid_price(extract_jsonld_product(soup)[1])
    volume_raw = extract_volumes(volume_text or "")[:1]
    volume_raw = volume_raw[0] if volume_raw else None
    if price is None and not volume_raw and not packaging:
        return None
    label_bits = [format_volume(volume_raw) if volume_raw else None, packaging]
    return {
        "volume": format_volume(volume_raw) if volume_raw else None,
        "volume_raw": volume_raw,
        "packaging": packaging,
        "price": price,
        "active": True,
        "label": " · ".join(bit for bit in label_bits if bit),
        "url": page_url,
    }


def parse_fabo_variants(html: str, page_url: str) -> list:
    """Збирає всі комбінації обʼєму/упаковки fabo.ua зі сторінок варіантів."""
    options = []
    seen = set()

    def add(option: Optional[dict]):
        if not option:
            return
        key = (option.get("volume"), option.get("packaging"), option.get("price"), option.get("url"))
        if key in seen:
            return
        seen.add(key)
        options.append(option)

    add(parse_fabo_active_variant(html, page_url))
    base = page_url.split("?")[0]
    current = urlparse(page_url).query
    current_id = (parse_qs(current).get("variant") or [None])[0]
    ids = []
    for vid in re.findall(r"variant=(\d+)", html):
        if vid not in ids:
            ids.append(vid)
    for vid in ids[:6]:
        if vid == current_id:
            continue
        variant_url = normalize_product_url(f"{base}?variant={vid}")
        page_html = fetch_text(variant_url)
        if page_html:
            add(parse_fabo_active_variant(page_html, variant_url))
    return options


def slug_text(url: str) -> str:
    path = urlparse(url).path
    slug = path.rstrip("/").split("/")[-1]
    slug = re.sub(r"\.[a-z0-9]+$", "", slug, flags=re.I)
    slug = re.sub(r"\d{3,}", " ", slug)
    return re.sub(r"[-_]+", " ", slug)


def candidate_prefetch_score(query: str, item: dict) -> float:
    title = item.get("title") or ""
    url = item.get("url") or ""
    score = max(
        product_match_score(query, title, url),
        product_match_score(query, slug_text(url)),
    )
    extras = extra_modifiers(query, title, slug_text(url))
    if extras:
        # тип засобу («спрей», «шампунь») у кириличній частині назви — не інша лінійка
        extras = _cyrillic_only_type_extras(extras, title, _title_has_full_phrase(query, title, url))
    if extras:
        score = max(0.0, score - 0.45 * min(len(extras), 2))
    return score


def extract_product_from_page(url: str, query: str = "", preloaded: Optional[dict] = None):
    """Дістає назву, ціну, фото, обʼєм і варіанти зі сторінки товару."""
    html = fetch_text(url)
    if html is None:
        if preloaded and (
            valid_price(preloaded.get("price")) is not None or preloaded.get("in_stock") is False
        ):
            return _preloaded_product(preloaded, query)
        return None
    soup = BeautifulSoup(html, "lxml")
    in_stock = preloaded.get("in_stock", True) if preloaded else True
    if _soup_out_of_stock(soup):
        in_stock = False

    jsonld_name, jsonld_price, jsonld_image = extract_jsonld_product(soup)
    titles = collect_page_titles(soup)
    if jsonld_name:
        jsonld_name = clean_title(jsonld_name)
        if jsonld_name not in titles:
            titles.insert(1 if titles else 0, jsonld_name)
    if preloaded and preloaded.get("title") and preloaded["title"] not in titles:
        titles.append(preloaded["title"])
    title = pick_best_title(titles, identity_query(query), fallback=(preloaded or {}).get("title") or url)

    visible_price = extract_visible_price(soup)
    price = valid_price(jsonld_price) or visible_price
    if price is None:
        for prop in ["og:price:amount", "product:price:amount"]:
            tag = soup.find("meta", property=prop)
            if tag and tag.get("content"):
                price = valid_price(tag["content"])
                if price is not None:
                    break
    if price is None:
        text = soup.get_text(" ", strip=True)
        matches = re.findall(r"(\d[\d\s]{0,7}(?:[.,]\d{1,2})?)\s?(?:грн|₴|UAH)", text)
        for match in matches:
            price = valid_price(match)
            if price is not None:
                break
    if price is None and preloaded:
        price = valid_price(preloaded.get("price"))
    if price is None and not in_stock and title:
        product = {
            "title": title,
            "price": None,
            "url": url,
            "image": extract_page_image(soup, url, jsonld_image) or (preloaded or {}).get("image"),
            "volume": None,
            "volume_raw": None,
            "volume_match": "unknown",
            "packaging": parse_packaging(title) or parse_packaging(url),
            "options": [],
            "percents": [],
            "spf": None,
            "in_stock": False,
            "price_note": "немає в наявності",
        }
        return finalize_product(product, query)
    if price is None:
        return None

    image = extract_page_image(soup, url, jsonld_image)
    if not image and preloaded:
        image = preloaded.get("image")

    options = extract_options(soup)
    if not any(opt.get("price") is not None for opt in options):
        ld_options = extract_jsonld_options(soup, url)
        if ld_options:
            options = ld_options
            active = next((opt for opt in ld_options if opt.get("active")), None)
            if active and active.get("price") is not None:
                price = active["price"]
    if "fabo.ua" in url:
        fabo_options = parse_fabo_variants(html, url)
        if fabo_options:
            options = fabo_options
    packaging = parse_packaging(title) or parse_packaging(url)
    combined_text = " ".join([title, soup.get_text(" ", strip=True)[:2000]])
    specs = extract_specs(combined_text)
    # SPF і відсотки з тексту сторінки часто належать сусіднім товарам/банерам.
    title_specs = extract_specs(title)
    specs["spf"] = title_specs["spf"]
    specs["percents"] = title_specs["percents"] or specs["percents"]
    volume_raw = None
    title_volumes = extract_volumes(title)
    if title_volumes:
        volume_raw = title_volumes[0]
    else:
        slug_volumes = extract_volumes(slug_text(url))
        if slug_volumes:
            volume_raw = slug_volumes[0]
    if not volume_raw and options:
        active = next((opt for opt in options if opt.get("active")), None)
        if active:
            volume_raw = active.get("volume_raw")

    product = {
        "title": title,
        "price": price,
        "url": url,
        "image": image,
        "volume": format_volume(volume_raw),
        "volume_raw": volume_raw,
        "volume_match": "unknown",
        "packaging": packaging,
        "options": options,
        "percents": specs["percents"][:4],
        "spf": specs["spf"],
        "in_stock": in_stock,
    }
    return finalize_product(product, query)


def serialize_option(opt: dict) -> dict:
    label = opt.get("label") or option_label(opt)
    return {
        "volume": opt.get("volume"),
        "packaging": opt.get("packaging"),
        "price": opt.get("price"),
        "active": bool(opt.get("active")),
        "label": label,
        "url": opt.get("url"),
    }


def option_from_product(product: dict) -> dict:
    packaging = product.get("packaging") or parse_packaging(product.get("title") or "")
    volumes = extract_volumes(product.get("volume") or product.get("title") or "")
    volume_raw = volumes[0] if volumes else None
    return {
        "volume": product.get("volume") or format_volume(volume_raw),
        "volume_raw": volume_raw,
        "packaging": packaging,
        "price": product.get("price"),
        "active": True,
        "label": " · ".join(
            bit for bit in [
                product.get("volume"),
                packaging or ("стандарт" if not is_refill_product(product) else "Refill"),
            ] if bit
        ),
        "url": product.get("url"),
    }


def dedupe_options(options: list) -> list:
    unique, seen = [], set()
    for opt in options:
        if not opt:
            continue
        key = (
            opt.get("volume") or "",
            (opt.get("packaging") or "").lower(),
            opt.get("price"),
        )
        if key in seen:
            # Prefer the copy that has a product URL.
            if opt.get("url"):
                for idx, existing in enumerate(unique):
                    existing_key = (
                        existing.get("volume") or "",
                        (existing.get("packaging") or "").lower(),
                        existing.get("price"),
                    )
                    if existing_key == key and not existing.get("url"):
                        unique[idx] = opt
                        break
            continue
        if not opt.get("volume") and not opt.get("packaging") and opt.get("price") is None:
            continue
        seen.add(key)
        unique.append(opt)
    return unique


def prefer_option(query: str, options: list) -> Optional[dict]:
    if not options:
        return None
    query_volume = extract_specs(query)["volumes"]
    query_volume = query_volume[0] if query_volume else None
    want_refill = bool(parse_packaging(query) == "Refill")
    ranked = []
    for opt in options:
        score = 0
        vol = opt.get("volume_raw")
        if not vol:
            extracted = extract_volumes(" ".join(filter(None, [opt.get("volume"), opt.get("label")])))
            vol = extracted[0] if extracted else None
        if query_volume and volumes_equal(vol, query_volume):
            score += 5
        packaging = (opt.get("packaging") or "").lower()
        if want_refill and packaging == "refill":
            score += 4
        elif not want_refill and packaging in {"", "стандарт", "normal"}:
            score += 3
        if opt.get("price") is not None:
            score += 1
        ranked.append((score, opt))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1]


def same_product_line(left: dict, right: dict) -> bool:
    left_title = left.get("title") or ""
    right_title = right.get("title") or ""
    left_core = core_title_key(latin_product_name(left_title) or left_title)
    right_core = core_title_key(latin_product_name(right_title) or right_title)
    return similarity(left_core, right_core) >= 0.78


def is_refill_product(product: dict) -> bool:
    text = " ".join(
        filter(None, [product.get("title"), product.get("packaging"), product.get("url")])
    )
    return parse_packaging(text) == "Refill"


def merge_site_matches(query: str, matches: list) -> dict:
    want_refill = parse_packaging(query) == "Refill"
    primary = None
    for item in matches:
        refill = is_refill_product(item)
        if want_refill and refill:
            primary = item
            break
        if not want_refill and not refill:
            primary = item
            break
    best = dict(primary or matches[0])
    options = list(best.get("options") or [])
    options.append(option_from_product(best))
    for other in matches:
        if other is primary:
            continue
        if not same_product_line(best, other):
            continue
        options.extend(other.get("options") or [])
        options.append(option_from_product(other))
    best["options"] = dedupe_options(options)
    chosen = prefer_option(query, best["options"])
    if chosen and chosen.get("price") is not None:
        best["price"] = chosen["price"]
        best["volume"] = chosen.get("volume") or best.get("volume")
        best["packaging"] = chosen.get("packaging")
        if chosen.get("url"):
            best["url"] = chosen["url"]
            for item in matches:
                if item.get("url") == chosen.get("url") and item.get("title"):
                    best["title"] = item["title"]
                    break
        bits = [chosen.get("volume"), chosen.get("packaging") or ("стандарт" if not want_refill else None)]
        best["price_note"] = "ціна для " + " · ".join(bit for bit in bits if bit)
    return best


def finalize_product(product: dict, query: str) -> dict:
    query_specs = extract_specs(query)
    query_volume = query_specs["volumes"][0] if query_specs["volumes"] else None
    if not product.get("volume_raw"):
        title_volumes = extract_volumes(product.get("title") or "")
        if title_volumes:
            product["volume_raw"] = title_volumes[0]
            product["volume"] = format_volume(title_volumes[0])
    if not product.get("packaging"):
        product["packaging"] = parse_packaging(product.get("title") or "")
    product = apply_matching_option(product, query_volume)
    product["spec_notes"] = spec_notes(query_specs, product)
    product["options"] = [serialize_option(opt) for opt in dedupe_options(product.get("options") or [])]
    product.pop("volume_raw", None)
    return product


def spec_notes(query_specs: dict, product: dict) -> list:
    notes = []
    q_vol = query_specs["volumes"][0] if query_specs["volumes"] else None
    p_vol = extract_volumes(product.get("volume") or "")
    p_vol = p_vol[0] if p_vol else None
    if q_vol and p_vol:
        if volumes_equal(q_vol, p_vol):
            notes.append(f"обʼєм збігається: {format_volume(q_vol)}")
        else:
            notes.append(
                f"обʼєм інший: у запиті {format_volume(q_vol)}, у товарі {format_volume(p_vol)}"
            )
    elif q_vol and product.get("volume_match") == "unknown":
        notes.append(f"обʼєм {format_volume(q_vol)} у картці не знайдено")

    if query_specs["percents"]:
        q_set = {round(x, 3) for x in query_specs["percents"]}
        p_set = {round(x, 3) for x in (product.get("percents") or [])}
        if q_set & p_set:
            notes.append("концентрація % збігається")
        elif p_set:
            notes.append("концентрація % може відрізнятися")

    if query_specs["spf"] is not None:
        if product.get("spf") == query_specs["spf"]:
            notes.append(f"SPF {query_specs['spf']} збігається")
        elif product.get("spf"):
            notes.append(f"SPF інший: {product.get('spf')} замість {query_specs['spf']}")
    return notes


def normalize_text(text: str) -> str:
    text = fold_diacritics((text or "").lower().replace("ʼ", "'").replace("’", "'"))
    text = text.replace("мл", " ml ").replace("мг", " mg ")
    text = re.sub(r"[^a-z0-9а-яіїєґё]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def similarity(a: str, b: str) -> float:
    """Максимум із SequenceMatcher і покриття токенів запиту в назві товару."""
    a_n, b_n = normalize_text(a), normalize_text(b)
    if not a_n or not b_n:
        return 0.0
    seq = difflib.SequenceMatcher(None, a_n, b_n).ratio()
    query_tokens = {t for t in a_n.split() if len(t) >= 2}
    title_tokens = {t for t in b_n.split() if len(t) >= 2}
    coverage = (len(query_tokens & title_tokens) / len(query_tokens)) if query_tokens else 0.0
    contained = 1.0 if a_n in b_n else 0.0
    core = normalize_text(VOLUME_RE.sub("", a))
    if core and core in b_n:
        contained = max(contained, 0.95)
    score = max(seq, coverage, contained)
    extra = {
        t for t in title_tokens
        if t.isascii() and t.isalpha() and len(t) >= 4
        and t not in query_tokens
        and t not in PACKAGING_STOPWORDS
    }
    if extra:
        score = max(0.0, score - 0.16 * min(len(extra), 2))
    missing = {
        t for t in query_tokens
        if t.isascii() and t.isalpha() and len(t) >= 5
        and t not in title_tokens
        and t not in PACKAGING_STOPWORDS
        and t not in GENERIC_BRANDS
        and t not in QUERY_STOPWORDS
        and not any(
            alias in b_n
            for kind, aliases in KIND_TOKENS.items()
            if t in aliases
            for alias in aliases
        )
    }
    if missing:
        score = max(0.0, score - 0.18 * min(len(missing), 2))
    return score


def _brand_tokens(query: str) -> set:
    brand = extract_brand(query)
    tokens = set()
    for alias in BRAND_ALIASES.get(brand, [brand] if brand else []):
        alias_n = normalize_text(alias)
        tokens.update(alias_n.split())
        tokens.add(alias_n.replace(" ", ""))
    return tokens


def _scrub_match_text(text: str) -> str:
    text = re.sub(r"\b\d+\s*[xх×]\s*\d+(?:[.,]\d+)?", " ", text or "", flags=re.IGNORECASE)
    text = re.sub(r"\b\d+(?:[.,]\d+)?\s*(?:мл|ml)?\s*\+\s*\d+(?:[.,]\d+)?", " ", text, flags=re.IGNORECASE)
    text = VOLUME_RE.sub(" ", text)
    text = re.sub(r"\bspf\s*\d+\+?", " spf ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bph\s*\d+(?:[.,]\d+)?", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b\d\s*-?\s*retinol\s+system\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\d+(?:[.,]\d+)?\s*%", " ", text)
    return text


def _latin_tokens(text: str, drop: set) -> list:
    tokens = []
    for token in normalize_text(_scrub_match_text(text)).split():
        if not token.isascii():
            continue
        if token in drop or token in BRAND_NOISE or token in MATCH_STOPWORDS:
            continue
        tokens.append(token)
    return tokens


def _cyrillic_tokens(text: str, drop: set) -> list:
    """Кириличні токени для збігу укр. назви фіда з карткою магазину."""
    stop = {
        "для", "від", "або", "та", "із", "со", "на", "по", "при", "без", "про",
        "это", "или", "при", "как", "все", "усі", "ваш", "наш",
    }
    tokens = []
    for token in normalize_text(_scrub_match_text(text)).split():
        if token.isascii() or len(token) < 4:
            continue
        if token in drop or token in stop or token in MATCH_STOPWORDS:
            continue
        tokens.append(token)
    return tokens


def _fuzzy_twin(token: str, candidates: set) -> Optional[str]:
    """Інше написання того ж слова: Epithelializing/Epithelizing, Filler/Filling, Wate/Water."""
    if len(token) < 4 or token.isdigit():
        return None
    for cand in candidates:
        if len(cand) < 4 or cand.isdigit() or abs(len(cand) - len(token)) > 3:
            continue
        short, long_ = sorted((token, cand), key=len)
        if token[:4] == cand[:4]:
            if long_.startswith(short) and len(long_) - len(short) <= 1:
                return cand
            if _stem(token) == _stem(cand) and len(_stem(token)) >= 4:
                return cand
        ratio = difflib.SequenceMatcher(None, token, cand).ratio()
        if min(len(token), len(cand)) >= 5 and abs(len(token) - len(cand)) <= 3 and ratio >= 0.74:
            return cand
        if len(token) >= 6 and len(cand) >= 6 and ratio >= 0.78:
            return cand
        if ratio >= 0.84:
            return cand
    return None


def _stem(token: str) -> str:
    for suffix in ("izing", "ising", "ing", "ers", "er", "ed", "ive", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            return token[: -len(suffix)]
    return token


def _token_weight(token: str) -> float:
    if token.isdigit():
        return 1.3
    if token in DESCRIPTOR_TOKENS:
        return 0.35
    if len(token) == 1:
        return 0.5
    return 1.0


def _phrase_in_title(q_tokens: list, t_tokens: list) -> bool:
    """Усі «характерні» токени запиту йдуть у назві конкурента підряд (довга назва з хвостом не заважає)."""
    core = list(q_tokens)
    # Короткий хвіст на кшталт «c vit» збігається з усією лінійкою — підсилюємо лише
    # фразу, у якій є справжнє імʼя товару (liposomal, dinamic, hydration…).
    if len(core) < 2 or not any(len(t) >= 5 and t not in DESCRIPTOR_TOKENS for t in core):
        return False
    n = len(core)
    for i in range(len(t_tokens) - n + 1):
        if t_tokens[i:i + n] == core:
            return True
    return False


def _token_set_score(
    q_tokens: list,
    t_tokens: list,
    *,
    title: str = "",
    use_cyrillic_hints: bool = True,
) -> float:
    """F-β по токенах запиту/назви (латиниця або кирилиця)."""
    if not q_tokens or not t_tokens:
        return 0.0
    t_set = set(t_tokens)
    t_pairs = {t_tokens[i] + t_tokens[i + 1] for i in range(len(t_tokens) - 1)}
    q_pairs = {
        q_tokens[i] + q_tokens[i + 1]: (q_tokens[i], q_tokens[i + 1])
        for i in range(len(q_tokens) - 1)
    }
    t_blob = f" {normalize_text(title)} "
    hinted = set()
    if use_cyrillic_hints:
        hinted = {english for stem, english in CYRILLIC_HINTS.items() if f" {stem}" in t_blob}

    matched_q = set()
    matched_t = set()
    for token in q_tokens:
        if token in t_set:
            matched_q.add(token)
            matched_t.add(token)
        elif token in hinted:
            matched_q.add(token)
        elif token in t_pairs:
            matched_q.add(token)
            for i in range(len(t_tokens) - 1):
                if t_tokens[i] + t_tokens[i + 1] == token:
                    matched_t.update({t_tokens[i], t_tokens[i + 1]})
        else:
            twin = _fuzzy_twin(token, t_set - matched_t)
            if twin:
                matched_q.add(token)
                matched_t.add(twin)
            else:
                # Кирилиця: спільний префікс (≥5) — «пілінг»/«пілінгом», «водорост»/«водоростями»
                if not token.isascii() and len(token) >= 5:
                    for cand in t_set - matched_t:
                        if cand.isascii() or len(cand) < 5:
                            continue
                        if token[:5] == cand[:5] or (
                            len(token) >= 6 and len(cand) >= 6 and token[:6] == cand[:6]
                        ):
                            matched_q.add(token)
                            matched_t.add(cand)
                            break
    for joined, (left, right) in q_pairs.items():
        if joined in t_set:
            matched_q.update({left, right})
            matched_t.add(joined)

    positions = [i for i, t in enumerate(t_tokens) if t in matched_t]
    inner_extra = tail_extra = 0
    if positions:
        lo, hi = min(positions), max(positions)
        for i, t in enumerate(t_tokens):
            if t in matched_t or t in DESCRIPTOR_TOKENS or t in MARKETING_INSERTS or len(t) < 3:
                continue
            if lo < i < hi:
                inner_extra += 1
            else:
                tail_extra += 1

    q_weight = sum(_token_weight(t) for t in set(q_tokens))
    hit_weight = sum(_token_weight(t) for t in matched_q)
    recall = hit_weight / q_weight if q_weight else 0.0
    extra = [t for t in t_set - matched_t]
    extra_weight = sum(_token_weight(t) for t in extra)
    precision = hit_weight / (hit_weight + extra_weight) if hit_weight else 0.0
    if not recall or not precision:
        return 0.0
    beta2 = 4.0
    score = (1 + beta2) * precision * recall / (beta2 * precision + recall)
    score -= 0.4 * min(inner_extra, 2) + 0.04 * min(tail_extra, 3)

    def significant(tokens):
        return [t for t in tokens if not t.isdigit() and len(t) > 2 and t not in GENERIC_FORM_TOKENS]

    lost = significant(set(q_tokens) - matched_q)
    added = significant(extra)
    if lost and added:
        score -= 0.3 * min(len(lost), len(added), 2)
    if _phrase_in_title(q_tokens, t_tokens) and not lost:
        score = max(score, 0.82)

    q_numbers = {t for t in q_tokens if t.isdigit()}
    t_numbers = {t for t in t_tokens if t.isdigit()}
    if q_numbers and t_numbers and not (q_numbers & t_numbers):
        score -= 0.35
    elif q_numbers and not t_numbers:
        score -= 0.3
    elif t_numbers - q_numbers and not q_numbers:
        score -= 0.25
    return max(0.0, min(1.0, score))


def product_match_score(query: str, title: str, url: str = "") -> float:
    """
    Оцінка 0…1, що назва конкурента — той самий товар, що й у запиті.
    Латинські токени (PHYTO-GOMMAGE) + кириличні з укр. назви фіда
    («рослинний пілінг»), щоб ловити UA-картки без французької назви.
    """
    drop = _brand_tokens(query)
    identity = identity_query(query) or query
    q_latin = _latin_tokens(identity, drop)
    t_text = f"{title} {slug_text(url)}" if url and not re.search(r"[A-Za-z]{4,}", title or "") else title
    t_latin = _latin_tokens(t_text, drop)
    q_cyr = _cyrillic_tokens(query, drop)
    t_cyr = _cyrillic_tokens(title, drop)

    if not q_latin and not q_cyr:
        return similarity(query, title)

    latin_score = _token_set_score(q_latin, t_latin, title=title, use_cyrillic_hints=True)
    cyr_score = _token_set_score(q_cyr, t_cyr, title=title, use_cyrillic_hints=False)

    if latin_score and cyr_score:
        # Обидві мови згодні — підсилюємо; інакше беремо сильнішу сторону.
        score = max(latin_score, cyr_score, 0.55 * latin_score + 0.45 * cyr_score)
    elif latin_score:
        score = latin_score
    elif cyr_score:
        # Укр. назва в запиті збіглась із карткою (французької в магазині немає).
        score = max(cyr_score, 0.72 if cyr_score >= 0.55 else cyr_score)
    else:
        # Латинський запит, чисто кирилична картка без спільних слів — слабкий fallback.
        score = similarity(query, title) * 0.45

    # Артикул у назві / URL конкурента — майже точний збіг.
    q_codes = {c.lower() for c in all_article_codes(query)}
    hay = f"{title} {url}".lower()
    if q_codes and any(code in hay for code in q_codes):
        score = max(score, 0.88)

    id_core = normalize_text(_scrub_match_text(latin_product_name(identity) or identity))
    id_tokens = [t for t in id_core.split() if t not in drop and len(t) >= 3]
    if len(id_tokens) >= 2:
        phrase = " ".join(id_tokens)
        title_core = normalize_text(_scrub_match_text(title))
        if len(phrase) >= 8 and phrase in title_core:
            score = max(score, min(0.93, score + 0.14))

    return max(0.0, min(1.0, score))


# Форма й зона нанесення, які магазин дописує українською до унікальної назви
# («Zitback» → «спрей для тіла Zitback»). Очі, набір, відтінок, SPF лишаються окремим товаром.
_SOFT_FORM_EXTRAS = {"spray", "shampoo", "ampoule", "shower", "hair", "body"}


def _identity_tokens(query: str) -> list:
    """Слова, які відрізняють товар у лінійці: не бренд і не «крем/serum/treatment»."""
    drop = _brand_tokens(query)
    tokens = _latin_tokens(identity_query(query) or query, drop)
    unique = []
    seen = set()
    for token in tokens:
        if token in seen or token.isdigit() or len(token) < 5:
            continue
        if token in DESCRIPTOR_TOKENS or token in GENERIC_FORM_TOKENS or token in MATCH_STOPWORDS:
            continue
        seen.add(token)
        unique.append(token)
    return unique


def _identity_covered(query: str, title: str, url: str = "") -> bool:
    tokens = _identity_tokens(query)
    if not tokens:
        return False
    drop = _brand_tokens(query)
    t_text = f"{title} {slug_text(url)}" if url and not re.search(r"[A-Za-z]{4,}", title or "") else title
    t_set = set(_latin_tokens(t_text, drop))
    return all(token in t_set or _fuzzy_twin(token, t_set) for token in tokens)


def _strict_name_covered(query: str, title: str, url: str = "") -> bool:
    """Кожне змістовне слово запиту, включно з короткими (dry, 5, liposomal), є в назві.

    Зайві слова картки не заважають: «Zitback» покриває «Zitback Body Spray …».
    """
    drop = _brand_tokens(query)
    needed = []
    seen = set()
    for token in _latin_tokens(identity_query(query) or query, drop):
        if token in seen or token in MATCH_STOPWORDS or token in BRAND_NOISE:
            continue
        if len(token) < 3 and not token.isdigit():
            continue
        seen.add(token)
        needed.append(token)
    if not needed:
        return False
    t_text = f"{title} {slug_text(url)}" if url and not re.search(r"[A-Za-z]{4,}", title or "") else title
    t_set = set(_latin_tokens(t_text, drop))
    return all(token in t_set for token in needed)


def _cyrillic_only_type_extras(
    extras: set,
    title: str,
    phrase: bool = False,
    query: str = "",
) -> set:
    """Групи, що з'явились лише з кириличної частини назви («для волосся», «спрей»), — не інша лінійка.

    «hair» знімається завжди; тип засобу (спрей/шампунь/ампула/нічний) — лише коли назву запиту знайдено повністю.
    «Нічна сироватка» під повний ключ «Crystal Retinal 20» — той самий SKU, не інша лінійка.
    Якщо в запиті вже є day/денний — кириличне «нічн» лишаємо як конфлікт.
    """
    latin = set(_latin_tokens(title, set()))
    keep = set(extras)
    groups = ("hair", "spray", "shampoo", "ampoule", "shower", "body", "night") if phrase else ("hair",)
    qn = normalize_text(query)
    if any(marker in qn for marker in ("day", "денн", "morning", "ранков", "день")):
        groups = tuple(group for group in groups if group != "night")
    for group in groups:
        if group in keep and not (set(MODIFIER_GROUPS[group]) & latin):
            keep.discard(group)
    return keep


def _title_has_full_phrase(query: str, title: str, url: str = "") -> bool:
    drop = _brand_tokens(query)
    q_tokens = _latin_tokens(identity_query(query) or query, drop)
    t_text = f"{title} {slug_text(url)}" if url and not re.search(r"[A-Za-z]{4,}", title or "") else title
    return bool(q_tokens) and _phrase_in_title(q_tokens, _latin_tokens(t_text, drop))


def candidate_score(query: str, product: dict, *, trust_brand_context: bool = False) -> float:
    title = product.get("title") or ""
    url = product.get("url") or ""
    if not trust_brand_context and not brand_matches(query, title, url):
        return 0.0
    if not kinds_compatible(identity_query(query) + " " + query, title):
        return 0.0
    if _line_token_conflict(query, title, url):
        return 0.0
    score = product_match_score(query, title, url)
    missing_groups = missing_modifiers(query, title, slug_text(url))
    if missing_groups:
        score = max(0.0, score - 0.25 * min(len(missing_groups), 2))
    volume_match = product.get("volume_match")
    if volume_match == "exact":
        score = min(1.0, score + 0.18)
    elif volume_match == "option_no_price":
        score = min(1.0, score + 0.06)
    elif volume_match == "mismatch":
        score = max(0.0, score - 0.38)
    query_specs = extract_specs(query)
    q_spf, p_spf = query_specs["spf"], product.get("spf")
    if q_spf is not None:
        if p_spf == q_spf:
            score = min(1.0, score + 0.05)
        elif p_spf:
            score = max(0.0, score - 0.12)
    elif p_spf is not None:
        # У фіді SPF немає, а магазин дописав «SPF 30» — не відкидаємо правильну лінійку.
        base = product_match_score(query, title, url)
        if _identity_covered(query, title, url) or base >= 0.72:
            pass
        elif base >= 0.55:
            score = max(0.0, score - 0.05)
        else:
            score = max(0.0, score - 0.14)
    extras = extra_modifiers(query, title, slug_text(url))
    covered = _identity_covered(query, title, url)
    if extras:
        extras = _cyrillic_only_type_extras(
            extras,
            title,
            _title_has_full_phrase(query, title, url) or covered,
            query=query,
        )
    # Запит без форми («Acnemy Zitback»), а в картці «спрей для тіла» — той самий товар.
    # Якщо в запиті вже є крем/сироватка/маска, «для тіла» лишається іншою позицією.
    if covered and extras and not product_kinds(identity_query(query) or query):
        extras = {group for group in extras if group not in _SOFT_FORM_EXTRAS}
    if extras:
        score = max(0.0, score - 0.45 * min(len(extras), 2))
    q_pct = set(query_specs["percents"])
    t_pct = set(extract_specs(title)["percents"])
    if q_pct and t_pct and not (q_pct & t_pct):
        score = max(0.0, score - 0.4)
    elif q_pct and not t_pct:
        score = max(0.0, score - 0.15)
    elif t_pct and not q_pct:
        score = max(0.0, score - 0.1)
    # Унікальна назва повністю в картці, а магазин лише дописав опис
    # («Zitback» → «Zitback Body Spray For Blemish-Prone Skin»).
    spf_conflict = (
        query_specs["spf"] is not None
        and product.get("spf")
        and product.get("spf") != query_specs["spf"]
    )
    percent_conflict = bool(q_pct and t_pct and not (q_pct & t_pct))
    if (
        _strict_name_covered(query, title, url)
        and not extras
        and not missing_groups
        and not spf_conflict
        and not percent_conflict
        and volume_match != "mismatch"
    ):
        score = max(score, 0.84)
    return score


def rank_key(product: dict, our_price: Optional[float] = None):
    order = {"exact": 0, "option_no_price": 1, "unknown": 2, "mismatch": 3}
    closeness = 0.0
    price = valid_price(product.get("price"))
    if our_price and price:
        closeness = abs(price - our_price) / our_price
    return (
        order.get(product.get("volume_match"), 4),
        -round(product.get("match", 0) * 20) / 20,
        0 if product.get("in_stock", True) else 1,
        closeness,
    )


def _preloaded_product(item: dict, query: str) -> dict:
    title = item.get("title") or ""
    specs = extract_specs(title)
    product = finalize_product(
        {
            "title": title,
            "price": item.get("price"),
            "url": item["url"],
            "image": item.get("image"),
            "options": [],
            "percents": specs["percents"][:4],
            "spf": specs["spf"],
            "volume_match": "unknown",
        },
        query,
    )
    product["in_stock"] = item.get("in_stock", True)
    return product


def _match_threshold(site: SiteConfig, query: str, product: dict) -> float:
    """Поріг схожості для статусу ok (нижчий для каталогу бренду та артикулу)."""
    threshold = float(site.min_similarity)
    if site.kind == "brand_catalog":
        threshold = max(0.50, threshold - 0.06)
    hay = f"{product.get('title') or ''} {product.get('url') or ''}".lower()
    for code in all_article_codes(query):
        if code.lower() in hay:
            threshold = max(0.48, threshold - 0.10)
            break
    title = product.get("title") or ""
    url = product.get("url") or ""
    if _strict_name_covered(query, title, url):
        threshold = max(0.48, threshold - 0.06)
    match = float(product.get("match") or 0)
    if match >= 0.82:
        threshold = max(0.50, threshold - 0.04)
    elif match >= 0.74:
        threshold = max(0.52, threshold - 0.02)
    return threshold


def _near_misses(candidates: list, limit: int = 3) -> list:
    return [
        {
            "title": c.get("title"),
            "url": c.get("url"),
            "price": valid_price(c.get("price")),
            "match": round(c.get("match", 0), 2),
            "volume": c.get("volume"),
        }
        for c in candidates[:limit]
    ]


def search_one_site(
    site: SiteConfig,
    query: str,
    debug: bool = False,
    our_price: Optional[float] = None,
    samples: tuple = (),
) -> dict:
    carries = site_carries_brand(site, query, samples=samples, debug=debug)
    if carries is False:
        return {
            "site": site.name,
            "status": "no_brand",
            "reason": "бренду немає в асортименті магазину",
        }

    if site.kind == "brand_catalog":
        brand = extract_brand(query)
        listing = brand_listing(site, brand, debug=debug)
        if listing:
            raw_results = listing
        elif site.search_url_patterns:
            raw_results = find_card_results(site, query, debug=debug) or []
        else:
            raw_results = []
    else:
        raw_results = find_search_results(site, query, debug=debug)
    codes = all_article_codes(query)
    if codes and site.kind != "brand_catalog" and (site.search_url_patterns or site.search_post_url):
        need_article_pass = not raw_results
        if raw_results and extract_brand(query):
            need_article_pass = not any(
                brand_matches(query, it.get("title") or "", it.get("url") or "")
                for it in raw_results
                if isinstance(it, dict)
            )
        if need_article_pass or (codes and raw_results and len(raw_results) > 12):
            for code in codes[:3]:
                if site.kind == "cards":
                    extra = find_card_results(site, code, debug=debug)
                elif site.kind == "jsonld":
                    extra = find_jsonld_results(site, code, debug=debug)
                else:
                    extra = find_search_results(site, code, debug=debug)
                if extra:
                    raw_results = extra
                    break
    if raw_results is None or not raw_results:
        return {
            "site": site.name,
            "status": "not_found" if carries else "unavailable",
            "reason": "товар не знайдено в пошуку" if carries else "сайт не відповів або пошук порожній",
        }

    unique, seen = [], set()
    for item in raw_results:
        if isinstance(item, str):
            item = {"url": item}
        if not isinstance(item, dict) or not item.get("url") or item["url"] in seen:
            continue
        seen.add(item["url"])
        unique.append(item)
    branded = [
        item for item in unique
        if brand_matches(query, item.get("title") or "", item.get("url") or "")
    ]
    if branded:
        unique = branded
    elif extract_brand(query) and site.listing_only and site.kind != "brand_catalog":
        # Картки без бренду в назві (типовий шум Shopify) не ганяємо далі.
        # У brand_catalog сторінка вже відфільтрована по бренду — назва часто без «Acnemy».
        unique = []

    if codes:
        by_code = [
            item for item in unique
            if any(
                code.lower() in f"{item.get('title') or ''} {item.get('url') or ''}".lower()
                for code in codes
            )
        ]
        if by_code:
            unique = by_code

    trust_brand = site.kind == "brand_catalog"
    candidates = []
    if site.listing_only:
        pool = unique
        if site.kind == "brand_catalog" and len(pool) > 30:
            pool = sorted(pool, key=lambda it: -candidate_prefetch_score(query, it))[:140]
        elif len(pool) > LISTING_MATCH_POOL:
            pool = sorted(pool, key=lambda it: -candidate_prefetch_score(query, it))[:LISTING_MATCH_POOL]
        for item in pool:
            has_price = valid_price(item.get("price")) is not None
            out_of_stock = item.get("in_stock") is False
            if not has_price and not out_of_stock:
                continue
            if _is_search_or_listing_url(item.get("url") or "") or _is_junk_product_title(item.get("title") or ""):
                continue
            product = _preloaded_product(item, query)
            product["match"] = candidate_score(query, product, trust_brand_context=trust_brand)
            if product["match"] > 0:
                candidates.append(product)
    else:
        scored = sorted(unique, key=lambda item: -candidate_prefetch_score(query, item))
        picked, seen_pick = [], set()
        for item in unique[:4] + scored:
            if item["url"] in seen_pick:
                continue
            seen_pick.add(item["url"])
            picked.append(item)
            if len(picked) >= MAX_CANDIDATES:
                break

        def load_candidate(item: dict):
            preloaded = item if item.get("preloaded") else None
            detailed = extract_product_from_page(item["url"], query=query, preloaded=preloaded)
            if detailed is None and preloaded and (
                valid_price(preloaded.get("price")) is not None or preloaded.get("in_stock") is False
            ):
                return _preloaded_product(preloaded, query)
            return detailed

        with ThreadPoolExecutor(max_workers=4) as pool:
            for fut in [pool.submit(load_candidate, item) for item in picked]:
                try:
                    product = fut.result()
                except Exception as e:
                    if debug:
                        print(f"    [debug] {site.name} кандидат упав: {e}")
                    continue
                if not product:
                    continue
                product["match"] = candidate_score(query, product)
                if product["match"] <= 0:
                    continue
                if valid_price(product.get("price")) is None and not product.get("options"):
                    if product.get("in_stock") is False and (product.get("title") or "").strip():
                        candidates.append(product)
                    continue
                candidates.append(product)

    if debug:
        for product in sorted(candidates, key=lambda c: -c["match"])[:6]:
            print(
                f"    [debug] {site.name}: «{product['title']}» {product['match']:.0%} "
                f"обʼєм={product.get('volume')} {product.get('volume_match')} "
                f"{product.get('price')} ({product['url']})"
            )

    if not candidates:
        return {
            "site": site.name,
            "status": "not_found",
            "reason": "у магазині є бренд, але цього товару не знайдено"
            if carries else "товар не знайдено",
            "brand_items": len(branded),
        }

    candidates.sort(key=lambda c: rank_key(c, our_price))
    viable = [item for item in candidates if item["match"] >= _match_threshold(site, query, item)]
    by_match = sorted(candidates, key=lambda c: -c["match"])
    if not viable:
        best = by_match[0]
        return {
            "site": site.name,
            "status": "low_match",
            "reason": "схожий товар є, але не збігається точно",
            "best_title": best.get("title"),
            "best_url": best.get("url"),
            "best_price": valid_price(best.get("price")),
            "match": best.get("match"),
            "near": _near_misses(by_match),
        }
    in_stock_items = [item for item in viable if item.get("in_stock", True)]
    merged = merge_site_matches(query, in_stock_items or viable)
    merged["in_stock"] = bool(in_stock_items)
    merged["options"] = [serialize_option(opt) for opt in dedupe_options(merged.get("options") or [])]
    merged["alternatives"] = _near_misses([c for c in by_match if c.get("url") != merged.get("url")], 2)
    if valid_price(merged.get("price")) is None:
        if merged.get("in_stock") is False:
            merged.setdefault("price_note", "немає в наявності")
            return {"site": site.name, "status": "ok", **merged}
        return {
            "site": site.name,
            "status": "low_match",
            "reason": "знайдено товар, але без коректної ціни",
            "best_title": merged.get("title"),
            "best_url": merged.get("url"),
            "match": merged.get("match"),
        }
    return {"site": site.name, "status": "ok", **merged}


def market_summary(results: list, our_price: Optional[float] = None) -> dict:
    priced = []
    for row in results:
        if row.get("in_stock") is False:
            continue
        price = valid_price(row.get("price"))
        if price is None:
            for opt in row.get("options") or []:
                price = valid_price(opt.get("price"))
                if price is not None:
                    break
        if price is not None:
            priced.append({**row, "price": price})
    prices = [row["price"] for row in priced]
    summary = {
        "found": len(results),
        "sites_total": len(SITES),
        "market_avg": None,
        "market_min": None,
        "market_max": None,
        "cheapest_site": None,
        "our_price": our_price,
        "diff_pct": None,
        "position": None,
    }
    if not prices:
        return summary
    avg = sum(prices) / len(prices)
    cheapest = min(priced, key=lambda r: r["price"])
    summary.update(
        {
            "market_avg": round(avg, 2),
            "market_min": round(min(prices), 2),
            "market_max": round(max(prices), 2),
            "cheapest_site": cheapest.get("site"),
        }
    )
    if our_price is not None and avg:
        diff_pct = (our_price - avg) / avg * 100
        if diff_pct > 10:
            position = f"дорожче за ринок на {diff_pct:.1f}%"
        elif diff_pct < -10:
            position = f"дешевше за ринок на {abs(diff_pct):.1f}%"
        else:
            position = f"на рівні ринку ({diff_pct:+.1f}%)"
        summary["diff_pct"] = round(diff_pct, 1)
        summary["position"] = position
    return summary


# Ліміт часу на ОДИН товар (усі магазини паралельно). Хто не встиг — «не відповів» лише
# для цього товару. Наступний товар питає магазин знову: під навантаженням живий сайт
# часто не вкладається в 45 с, хоча сам відповідає за 4–12 с.
SEARCH_DEADLINE = 85.0
# Залишено для ручного відключення магазину, який стабільно падає з винятком.
SLOW_SITE_STRIKES = 6
SITE_COOLDOWN = 45.0

_SITE_STRIKES: dict[str, int] = {}
_SITE_TRIPPED_AT: dict[str, float] = {}
_STRIKES_LOCK = threading.Lock()


def reset_site_strikes() -> None:
    with _STRIKES_LOCK:
        _SITE_STRIKES.clear()
        _SITE_TRIPPED_AT.clear()


def _site_is_tripped(name: str) -> bool:
    """Магазин відкладено, але через SITE_COOLDOWN він отримує пробний запит («напіввідкритий» стан)."""
    with _STRIKES_LOCK:
        if _SITE_STRIKES.get(name, 0) < SLOW_SITE_STRIKES:
            return False
        since = _SITE_TRIPPED_AT.get(name, 0.0)
        if time.time() - since >= SITE_COOLDOWN:
            _SITE_TRIPPED_AT[name] = time.time()
            return False
        return True


def _site_strike(name: str, *, slow: bool) -> None:
    with _STRIKES_LOCK:
        if slow:
            _SITE_STRIKES[name] = _SITE_STRIKES.get(name, 0) + 1
            if _SITE_STRIKES[name] == SLOW_SITE_STRIKES:
                _SITE_TRIPPED_AT[name] = time.time()
        else:
            _SITE_STRIKES[name] = 0


# Глобальний ліміт паралельних магазинів у search_all (None = усі).
# Worker виставляє низьке значення, щоб catalog×sites не вбивав VPS OOM.
SITE_SEARCH_WORKERS: Optional[int] = None


RETRY_SEARCH_DEADLINE = 120.0


def search_all(
    query: str,
    our_price: Optional[float] = None,
    debug: bool = False,
    samples: tuple = (),
    deadline: Optional[float] = None,
    on_site: Optional[Callable] = None,
    max_workers: Optional[int] = None,
    site_names: Optional[tuple] = None,
) -> dict:
    reset_site_strikes()
    hits = []
    misses = []
    limit = SEARCH_DEADLINE if deadline is None else deadline

    def emit(item: dict) -> None:
        if item.get("status") == "ok":
            hits.append(item)
        else:
            misses.append(item)
        if on_site is None:
            return
        try:
            on_site(item)
        except Exception:
            if debug:
                print(f"    [debug] on_site упав для {item.get('site')}")

    only = frozenset(site_names) if site_names else None
    active = []
    for site in SITES:
        if only is not None and site.name not in only:
            continue
        if _site_is_tripped(site.name):
            emit({
                "site": site.name,
                "status": "unavailable",
                "reason": "магазин відповідає занадто повільно — пропущено в цьому прогоні",
            })
        else:
            active.append(site)

    cap = max_workers if max_workers is not None else SITE_SEARCH_WORKERS
    pool_size = max(1, len(active))
    if cap is not None:
        try:
            pool_size = max(1, min(pool_size, int(cap)))
        except (TypeError, ValueError):
            pool_size = max(1, len(active))
    # 63 магазини × низький pool — інакше дедлайн зрізає хвіст як «unavailable».
    waves = (len(active) + pool_size - 1) // pool_size if active else 1
    min_limit = waves * 14.0 + 20.0
    if limit <= 0 or limit < min_limit:
        limit = max(SEARCH_DEADLINE, min_limit)
    pool = ThreadPoolExecutor(max_workers=pool_size)
    futs = {
        pool.submit(search_one_site, site, query, debug, our_price, samples): site
        for site in active
    }
    pending = set(futs)
    end = time.monotonic() + limit if limit and limit > 0 else None
    try:
        while pending:
            timeout = None
            if end is not None:
                timeout = end - time.monotonic()
                if timeout <= 0:
                    break
            done, pending = wait(pending, timeout=timeout, return_when=FIRST_COMPLETED)
            if not done:
                break
            for fut in done:
                site = futs[fut]
                try:
                    item = fut.result()
                except Exception as e:
                    item = {"site": site.name, "status": "error", "reason": str(e)}
                    _site_strike(site.name, slow=False)
                else:
                    _site_strike(site.name, slow=False)
                emit(item)
    finally:
        for fut in list(pending):
            site = futs[fut]
            emit({
                "site": site.name,
                "status": "unavailable",
                "reason": f"не відповів за {limit:.0f} с",
            })
            fut.cancel()
        # не чекаємо «зависли» запити — вони доробляться у фоні й кешуються
        pool.shutdown(wait=False, cancel_futures=True)

    hits.sort(key=lambda r: r.get("price") or 0)
    misses.sort(key=lambda r: r.get("site") or "")
    summary = market_summary(hits, our_price)
    return {
        "query": query,
        "our_price": our_price,
        "results": hits,
        "misses": misses,
        **summary,
    }


def check_product(query: str, our_price: Optional[float] = None, debug: bool = True):
    print(f"\nШукаю: «{query}»\n" + "-" * 60)
    payload = search_all(query, our_price, debug=debug)
    for miss in payload["misses"]:
        extra = f" — {miss.get('best_title')}" if miss.get("best_title") else ""
        print(f"[{miss['site']}] {miss.get('reason')}{extra}")
    for row in payload["results"]:
        volume = f", {row['volume']}" if row.get("volume") else ""
        print(
            f"[{row['site']}] {row['title']} — {row['price']:.2f} грн "
            f"(схожість {row['match']:.0%}{volume}, {row.get('volume_match')})"
        )
        print(f"    {row['url']}")
        if row.get("image"):
            print(f"    фото: {row['image']}")

    if not payload["results"]:
        print(
            "\nЖодного результату не знайдено. Перевір назву товару або "
            "конфігурацію пошуку для сайтів у SITES."
        )
        return payload

    print("\n" + "=" * 60)
    print("Посилання на товари:")
    for row in payload["results"]:
        print(f"  {row['site']}: {row['url']}")
    print("-" * 60)
    print(f"Середня ціна по ринку: {payload['market_avg']:.2f} грн")
    print(f"Діапазон: {payload['market_min']:.2f} – {payload['market_max']:.2f} грн")
    if payload.get("position"):
        print(f"Наша ціна: {our_price:.2f} грн → {payload['position']}")
    print("=" * 60)
    return payload


def main():
    print(
        "Перевірка ціни товару відносно конкурентів "
        f"({', '.join(site.name.lower() for site in SITES)})"
    )
    query = input("Назва товару (бренд + назва, бажано з обʼємом): ").strip()
    if not query:
        print("Порожня назва — виходжу.")
        sys.exit(0)
    price_raw = input("Наша ціна (грн), Enter щоб пропустити: ").strip()
    our_price = None
    if price_raw:
        try:
            our_price = float(price_raw.replace(",", "."))
        except ValueError:
            print("Не розпізнав ціну, продовжую без неї.")
    debug = "--debug" in sys.argv
    check_product(query, our_price, debug=debug)


if __name__ == "__main__":
    main()
