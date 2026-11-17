"""Сайт, який мовчить, не можна записувати як «товар не знайдено»."""

from __future__ import annotations

import sys
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cosmyparser"))

import cosmy_parse as cp


def _cards_site() -> cp.SiteConfig:
    return cp.SiteConfig(
        name="Probe",
        base_url="https://probe.example",
        kind="cards",
        listing_only=True,
        search_url_patterns=["https://probe.example/search?q={q}"],
        card_selector="div.card",
        card_link="a",
    )


def _catalog_site() -> cp.SiteConfig:
    return cp.SiteConfig(
        name="Catalog",
        base_url="https://catalog.example",
        kind="brand_catalog",
        listing_only=True,
        brand_page_url="https://catalog.example/brand/{slug}?page={page}",
        max_pages=2,
        card_selector="div.card",
        card_link="a",
    )


def test_silent_shop_is_unavailable_even_if_brand_is_known(monkeypatch):
    cp.clear_brand_presence_cache()
    monkeypatch.setattr(cp, "site_carries_brand", lambda *args, **kwargs: True)
    monkeypatch.setattr(cp, "fetch", lambda *args, **kwargs: None)
    item = cp.search_one_site(_cards_site(), "Sesderma C-Vit serum 30 ml")
    assert item["status"] == "unavailable"


def test_confirmed_empty_search_stays_not_found(monkeypatch):
    cp.clear_brand_presence_cache()
    monkeypatch.setattr(cp, "site_carries_brand", lambda *args, **kwargs: True)
    monkeypatch.setattr(cp, "fetch", lambda *args, **kwargs: BeautifulSoup("<html></html>", "lxml"))
    monkeypatch.setattr(cp, "parse_cards", lambda *args, **kwargs: [])
    item = cp.search_one_site(_cards_site(), "Sesderma C-Vit serum 30 ml")
    assert item["status"] == "not_found"


def test_broken_brand_catalog_is_not_a_miss(monkeypatch):
    cp.clear_brand_presence_cache()

    def fetch(url, debug=False, extra_headers=None):
        if "page=2" in url:
            return None
        return BeautifulSoup("<html></html>", "lxml")

    monkeypatch.setattr(cp, "fetch", fetch)
    monkeypatch.setattr(
        cp,
        "parse_cards",
        lambda *args, **kwargs: [
            {
                "title": "Zzzqqq unrelated",
                "url": "https://catalog.example/p/1",
                "price": 10,
                "preloaded": True,
                "in_stock": True,
            }
        ],
    )
    item = cp.search_one_site(_catalog_site(), "Sesderma C-Vit serum 30 ml")
    assert item["status"] == "unavailable"
    assert cp.listing_is_partial("Catalog", "sesderma")


def test_failed_brand_page_is_not_cached_as_empty(monkeypatch):
    cp.clear_listing_cache()
    calls = {"n": 0}

    def fetch(url, debug=False, extra_headers=None):
        calls["n"] += 1
        return None

    monkeypatch.setattr(cp, "fetch", fetch)
    site = _catalog_site()
    assert cp.brand_listing(site, "sesderma") is None
    assert cp.brand_listing(site, "sesderma") is None
    assert calls["n"] == 2
