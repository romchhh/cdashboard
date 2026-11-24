"""Хост після серії збоїв не можна вимикати до кінця бренду.

Кожен наступний товар має отримати справжній запит. Інакше хвіст каталогу
пишеться як «сайти не відповіли», хоча магазин відповідає за секунду.
"""

from __future__ import annotations

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cosmyparser"))

import cosmy_parse as cp


PRODUCTS = (
    "American Crew Daily Conditioner 250 ml",
    "American Crew Classic Forming Cream 50 g",
    "American Crew Deep Moisturizing Shampoo 450 ml",
    "CeraVe Hydrating Cleanser 236 ml",
    "CeraVe Foaming Cleanser 236 ml",
    "CeraVe Moisturising Cream 50 ml",
    "Olaplex No 3 Hair Perfector 100 ml",
    "Olaplex No 4 Bond Maintenance Shampoo 250 ml",
    "Olaplex No 5 Conditioner 250 ml",
    "Sesderma C-Vit Liposomal Serum 30 ml",
    "Sesderma Azelac Ru Gel Cream 50 ml",
    "Elemis Pro-Collagen Marine Cream 50 ml",
)


class _Response:
    def __init__(self, status: int, body):
        self.status_code = status
        self.cookies = []
        self.encoding = "utf-8"
        if isinstance(body, (dict, list)):
            self._json = body
            raw = json.dumps(body)
            self.headers = {"content-type": "application/json"}
        else:
            self._json = None
            raw = body
            self.headers = {"content-type": "text/html; charset=utf-8"}
        self.text = raw
        self.content = raw.encode()

    def json(self):
        if self._json is None:
            raise ValueError("not json")
        return self._json


def _card(title: str, number: int) -> str:
    return (
        "<html><body>"
        f'<div class="card"><a href="/p/{number}">{title}</a>'
        '<span class="price">410 грн</span></div>'
        "</body></html>"
    )


def _woo(title: str, number: int) -> list:
    return [{
        "name": title,
        "permalink": f"https://maska.example/product/{number}",
        "prices": {"price": "41000", "currency_minor_unit": 2},
    }]


def _query_from_url(url: str) -> str:
    qs = parse_qs(urlparse(url).query)
    for key in ("q", "search"):
        if qs.get(key):
            return unquote(qs[key][0])
    return "item"


def _install_network(monkeypatch, *, fail_budget: dict | None = None):
    """GET/POST без мережі. fail_budget — скільки перших запитів хоста впадуть таймаутом."""
    monkeypatch.setattr(cp.time, "sleep", lambda *_args, **_kwargs: None)
    calls = []
    lock = threading.Lock()
    budget = dict(fail_budget or {})
    seq = {"n": 0}

    def _fail_or_title(host: str, title: str):
        with lock:
            left = budget.get(host, 0)
            if left > 0:
                budget[host] = left - 1
                raise cp.requests.Timeout("slow")
            seq["n"] += 1
            number = seq["n"]
        return title, number

    def get(url, headers=None, timeout=None):
        host = urlparse(url).netloc
        with lock:
            calls.append(("GET", host, url, None))
        if host == "makoko.example":
            return _Response(429, "Перевірка безпеки")
        title, number = _fail_or_title(host, _query_from_url(url))
        if host == "maska.example":
            return _Response(200, _woo(title, number))
        return _Response(200, _card(title, number))

    def post(url, data=None, headers=None, timeout=None):
        host = urlparse(url).netloc
        with lock:
            calls.append(("POST", host, url, dict(data or {})))
        if host == "makoko.example":
            return _Response(429, "Перевірка безпеки")
        title = (data or {}).get("q") or "item"
        title, number = _fail_or_title(host, title)
        return _Response(200, _card(title, number))

    monkeypatch.setattr(cp.requests, "get", get)
    monkeypatch.setattr(cp.requests, "post", post)
    return calls


def _reset():
    cp.clear_brand_presence_cache()
    cp.reset_host_state()
    cp.clear_http_cache()


def _sites():
    return [
        cp.SiteConfig(
            name="Makeup",
            base_url="https://makeup.example",
            kind="cards",
            listing_only=True,
            search_url_patterns=["https://makeup.example/search?q={q}"],
            card_selector="div.card",
            card_link="a",
            card_price=".price",
        ),
        cp.SiteConfig(
            name="Krasota.shop",
            base_url="https://krasota.example",
            kind="cards",
            listing_only=True,
            search_url_patterns=["https://krasota.example/search?q={q}"],
            card_selector="div.card",
            card_link="a",
            card_price=".price",
        ),
        cp.SiteConfig(
            name="Maska",
            base_url="https://maska.example",
            kind="woocommerce",
            listing_only=True,
            api_url="https://maska.example/wp-json/wc/store/v1/products?search={q}&per_page=10",
        ),
        cp.SiteConfig(
            name="Estet",
            base_url="https://estet.example",
            kind="cards",
            listing_only=True,
            search_post_url="https://estet.example/search",
            search_post_form={"q": "{q}"},
            card_selector="div.card",
            card_link="a",
            card_price=".price",
        ),
        cp.SiteConfig(
            name="Makoko",
            base_url="https://makoko.example",
            kind="cards",
            listing_only=True,
            search_url_patterns=["https://makoko.example/search?q={q}"],
            card_selector="div.card",
            card_link="a",
            card_price=".price",
        ),
    ]


def test_cooled_host_is_still_requested_and_success_clears_pause(monkeypatch):
    _reset()
    calls = _install_network(monkeypatch)
    host = "makeup.example"
    with cp._HTTP_LOCK:
        cp._HOST_DOWN_UNTIL[host] = cp.time.time() + 40
        cp._HOST_FAILS[host] = cp.HOST_FAIL_LIMIT

    body = cp.fetch_text("https://makeup.example/search?q=american-crew")
    assert body and "american-crew" in body
    assert [item for item in calls if item[1] == host]
    assert host not in cp._HOST_DOWN_UNTIL
    assert cp._HOST_FAILS.get(host, 0) == 0

    # Після успіху магазин знову має право на повтор, а не на одну спробу.
    raised = {"n": 0}
    real_get = cp.requests.get

    def flaky_get(url, headers=None, timeout=None):
        raised["n"] += 1
        if raised["n"] == 1:
            raise cp.requests.Timeout("once")
        return real_get(url, headers=headers, timeout=timeout)

    monkeypatch.setattr(cp.requests, "get", flaky_get)
    second = cp.fetch_text("https://makeup.example/search?q=cerave")
    assert second and "cerave" in second
    assert raised["n"] >= 2


def test_cooled_host_timeout_still_makes_one_request(monkeypatch):
    _reset()
    monkeypatch.setattr(cp.time, "sleep", lambda *_a, **_k: None)
    host = "krasota.example"
    with cp._HTTP_LOCK:
        cp._HOST_DOWN_UNTIL[host] = cp.time.time() + 40
        cp._HOST_FAILS[host] = 9
    calls = {"n": 0}

    def get(url, headers=None, timeout=None):
        calls["n"] += 1
        raise cp.requests.Timeout("down")

    monkeypatch.setattr(cp.requests, "get", get)
    assert cp.fetch_text("https://krasota.example/search?q=1") is None
    assert calls["n"] == 1


def test_security_check_is_not_retried_on_get_or_post(monkeypatch):
    _reset()
    monkeypatch.setattr(cp.time, "sleep", lambda *_a, **_k: None)
    calls = {"get": 0, "post": 0}

    def get(url, headers=None, timeout=None):
        calls["get"] += 1
        return _Response(429, "Перевірка безпеки")

    def post(url, data=None, headers=None, timeout=None):
        calls["post"] += 1
        return _Response(200, "please wait перевірка безпеки")

    monkeypatch.setattr(cp.requests, "get", get)
    monkeypatch.setattr(cp.requests, "post", post)

    urls = [f"https://hold.example/search?q={i}" for i in range(8)]
    assert all(cp.fetch_text(url) is None for url in urls)
    assert calls["get"] == 8
    assert cp._HOST_NOTE["hold.example"] == "магазин показав перевірку безпеки"

    assert cp.fetch_post_text("https://hold.example/search", {"q": "cream"}) is None
    assert calls["post"] == 1


def test_parallel_products_keep_asking_a_paused_host(monkeypatch):
    _reset()
    calls = _install_network(monkeypatch)
    host = "makeup.example"
    with cp._HTTP_LOCK:
        cp._HOST_DOWN_UNTIL[host] = cp.time.time() + 40
        cp._HOST_FAILS[host] = cp.HOST_FAIL_LIMIT

    urls = [
        f"https://makeup.example/search?q={name.replace(' ', '-')}-{idx}"
        for idx, name in enumerate(PRODUCTS)
    ]
    with ThreadPoolExecutor(max_workers=8) as pool:
        bodies = list(pool.map(cp.fetch_text, urls))

    assert all(bodies)
    asked = [url for method, site, url, _data in calls if site == host]
    assert len(asked) == len(PRODUCTS)
    assert host not in cp._HOST_DOWN_UNTIL


def test_catalog_tail_still_reaches_every_shop(monkeypatch):
    _reset()
    calls = _install_network(monkeypatch, fail_budget={
        "krasota.example": 6,
        "estet.example": 3,
    })
    monkeypatch.setattr(cp, "SITES", _sites())

    def one(query: str):
        return query, cp.search_all(query, our_price=400)

    with ThreadPoolExecutor(max_workers=8) as pool:
        reports = list(pool.map(one, PRODUCTS))

    by_site = {name: [] for name in ("Makeup", "Krasota.shop", "Maska", "Estet", "Makoko")}
    for query, payload in reports:
        assert payload["query"] == query
        seen = {}
        for row in payload["results"] + payload["misses"]:
            seen[row["site"]] = row
            reason = row.get("reason") or ""
            assert "не відповів за 85" not in reason
            assert "пропущено в цьому прогоні" not in reason
        assert set(seen) == set(by_site)
        for name, row in seen.items():
            by_site[name].append((query, row))

    def oks(name: str) -> int:
        return sum(1 for _query, row in by_site[name] if row.get("status") == "ok")

    assert oks("Makeup") == len(PRODUCTS)
    assert oks("Maska") == len(PRODUCTS)
    assert oks("Krasota.shop") >= 8
    assert oks("Estet") >= 8
    assert oks("Makoko") == 0
    for query, row in by_site["Makoko"]:
        assert row["status"] == "unavailable"
        assert "безпек" in (row.get("reason") or "")

    def asked(host: str, query: str) -> bool:
        needle = query.lower().replace(" ", "+")
        needle_pct = query.lower().replace(" ", "%20")
        for method, site, url, data in calls:
            if site != host:
                continue
            blob = f"{url} {data or ''}".lower()
            if needle in blob or needle_pct in blob or query.lower() in blob:
                return True
        return False

    for query, _row in by_site["Krasota.shop"]:
        assert asked("krasota.example", query), query
    for query, _row in by_site["Makoko"]:
        assert asked("makoko.example", query), query
    for query, _row in by_site["Estet"]:
        assert asked("estet.example", query), query
