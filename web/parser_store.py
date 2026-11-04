"""SQLite-сховище парсера: databases/parser.db.

Налаштування, вимкнені бренди, статуси, звіти, історія пошуку, черга і журнал
живуть у таблицях. JSON з databases/parser імпортується один раз і далі не пишеться.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "databases" / "parser.db"
JSON_DIR = ROOT / "databases" / "parser"

LIVE_SCOPE = "__live__"
LIVE_LOCAL_SCOPE = "__live_local__"
FULL_SCOPE = "full"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value_type TEXT NOT NULL,
    value_text TEXT
);
CREATE TABLE IF NOT EXISTS enabled_sites (
    position INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS disabled_brands (
    name TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS brand_status (
    brand TEXT PRIMARY KEY,
    state TEXT,
    last_parsed_at TEXT,
    total INTEGER,
    source TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS reports (
    scope TEXT PRIMARY KEY,
    brand TEXT,
    generated_at TEXT,
    started_at TEXT,
    total INTEGER,
    catalog_total INTEGER,
    partial INTEGER NOT NULL DEFAULT 0,
    saved_at REAL
);
CREATE TABLE IF NOT EXISTS report_sites (
    scope TEXT NOT NULL,
    position INTEGER NOT NULL,
    name TEXT NOT NULL,
    PRIMARY KEY (scope, position)
);
CREATE TABLE IF NOT EXISTS report_rows (
    scope TEXT NOT NULL,
    position INTEGER NOT NULL,
    row_id TEXT,
    brand TEXT,
    model TEXT,
    name TEXT,
    name_uk TEXT,
    query TEXT,
    our_price REAL,
    product_id INTEGER,
    option_value TEXT,
    image_url TEXT,
    product_url TEXT,
    ref_volume TEXT,
    found INTEGER,
    found_label TEXT,
    shops_with_brand INTEGER,
    market_min REAL,
    min_site TEXT,
    min_url TEXT,
    market_median REAL,
    market_avg REAL,
    market_max REAL,
    diff_min_uah REAL,
    diff_min_pct REAL,
    diff_median_pct REAL,
    rank INTEGER,
    rank_label TEXT,
    price_position TEXT,
    advice TEXT,
    partial INTEGER,
    error TEXT,
    PRIMARY KEY (scope, position)
);
CREATE TABLE IF NOT EXISTS report_shops (
    scope TEXT NOT NULL,
    row_position INTEGER NOT NULL,
    site TEXT NOT NULL,
    status TEXT,
    price REAL,
    title TEXT,
    url TEXT,
    volume TEXT,
    match_score REAL,
    in_stock INTEGER,
    note TEXT,
    diff_pct REAL,
    reason TEXT,
    PRIMARY KEY (scope, row_position, site)
);
CREATE TABLE IF NOT EXISTS report_shop_cards (
    scope TEXT NOT NULL,
    row_position INTEGER NOT NULL,
    site TEXT NOT NULL,
    kind TEXT NOT NULL,
    position INTEGER NOT NULL,
    title TEXT,
    url TEXT,
    price REAL,
    match_score REAL,
    volume TEXT,
    PRIMARY KEY (scope, row_position, site, kind, position)
);
CREATE TABLE IF NOT EXISTS search_history (
    id TEXT PRIMARY KEY,
    created_at TEXT,
    query TEXT,
    our_price REAL,
    found INTEGER,
    market_avg REAL,
    market_min REAL,
    market_max REAL,
    position_label TEXT,
    sort_order INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS search_hits (
    history_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    site TEXT,
    title TEXT,
    price REAL,
    url TEXT,
    image TEXT,
    volume TEXT,
    match_score REAL,
    PRIMARY KEY (history_id, position)
);
CREATE TABLE IF NOT EXISTS kv (
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    value_type TEXT NOT NULL,
    value_text TEXT,
    PRIMARY KEY (namespace, key)
);
CREATE TABLE IF NOT EXISTS search_rows (
    kind TEXT NOT NULL,
    position INTEGER NOT NULL,
    site TEXT,
    status TEXT,
    title TEXT,
    price REAL,
    url TEXT,
    image TEXT,
    volume TEXT,
    match_score REAL,
    reason TEXT,
    PRIMARY KEY (kind, position)
);
CREATE TABLE IF NOT EXISTS rotation_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    built_for TEXT,
    day INTEGER,
    current_brand TEXT,
    meta_json TEXT
);
CREATE TABLE IF NOT EXISTS rotation_items (
    bucket TEXT NOT NULL,
    position INTEGER NOT NULL,
    brand TEXT,
    error TEXT,
    PRIMARY KEY (bucket, position)
);
CREATE TABLE IF NOT EXISTS parse_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    level TEXT,
    kind TEXT,
    brand TEXT,
    site TEXT,
    message TEXT
);
CREATE TABLE IF NOT EXISTS catalog_products (
    position INTEGER PRIMARY KEY,
    row_id TEXT,
    product_id INTEGER,
    model TEXT,
    brand TEXT,
    name TEXT,
    name_uk TEXT,
    query TEXT,
    our_price REAL,
    option_value TEXT,
    image_url TEXT,
    product_url TEXT
);
"""


_INIT_LOCK = threading.Lock()
_READY_DB_PATHS: set[str] = set()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=120000")
    key = str(DB_PATH)
    if key not in _READY_DB_PATHS:
        with _INIT_LOCK:
            if key not in _READY_DB_PATHS:
                conn.executescript(_SCHEMA)
                _ensure_schema_extras(conn)
                _migrate_json_once(conn)
                _READY_DB_PATHS.add(key)
    return conn


def _ensure_schema_extras(conn: sqlite3.Connection) -> None:
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(rotation_state)")}
    if "meta_json" not in cols:
        conn.execute("ALTER TABLE rotation_state ADD COLUMN meta_json TEXT")
        conn.commit()


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Write-транзакція з retry при 'database is locked' (воркер + веб одночасно)."""
    conn: sqlite3.Connection | None = None
    last_lock: Exception | None = None
    for attempt in range(12):
        conn = connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            break
        except sqlite3.OperationalError as exc:
            try:
                conn.close()
            except Exception:
                pass
            conn = None
            if "locked" not in str(exc).lower():
                raise
            last_lock = exc
            time.sleep(min(0.05 * (2 ** attempt), 1.5))
    else:
        raise last_lock or sqlite3.OperationalError("database is locked")
    assert conn is not None
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def _meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else row["value"]


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _encode(value: Any) -> tuple[str, str | None]:
    if value is None:
        return "null", None
    if isinstance(value, bool):
        return "int", "1" if value else "0"
    if isinstance(value, int) and not isinstance(value, bool):
        return "int", str(value)
    if isinstance(value, float):
        return "float", repr(value)
    return "str", str(value)


def _decode(value_type: str, value_text: str | None) -> Any:
    if value_type == "null" or value_text is None:
        return None
    if value_type == "int":
        return int(value_text)
    if value_type == "float":
        return float(value_text)
    return value_text


def _f(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _i(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _id_out(value: str | None) -> Any:
    if value is None or value == "":
        return None
    if value.isdigit() or (value.startswith("-") and value[1:].isdigit()):
        try:
            return int(value)
        except ValueError:
            return value
    return value


def _bool_out(value: int | None) -> bool | None:
    if value is None:
        return None
    return bool(value)


# ---------------------------------------------------------------- settings


def read_settings() -> dict:
    """Скаляри, enabled_sites лише якщо список збережено явно, і disabled_brands."""
    conn = connect()
    try:
        out: dict[str, Any] = {}
        for row in conn.execute("SELECT key, value_type, value_text FROM settings"):
            out[row["key"]] = _decode(row["value_type"], row["value_text"])
        mode = _meta(conn, "sites_mode")
        if mode == "list":
            names = [
                r["name"]
                for r in conn.execute("SELECT name FROM enabled_sites ORDER BY position")
            ]
            out["enabled_sites"] = names
        brands = [r["name"] for r in conn.execute("SELECT name FROM disabled_brands ORDER BY name")]
        out["disabled_brands"] = brands
        return out
    finally:
        conn.close()


def write_settings(
    scalars: dict,
    enabled_sites: list[str] | None,
    *,
    update_sites: bool,
) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM settings")
        for key, value in scalars.items():
            if key in ("enabled_sites", "disabled_brands"):
                continue
            value_type, value_text = _encode(value)
            conn.execute(
                "INSERT INTO settings(key, value_type, value_text) VALUES(?, ?, ?)",
                (key, value_type, value_text),
            )
        if update_sites:
            conn.execute("DELETE FROM enabled_sites")
            for index, name in enumerate(enabled_sites or []):
                conn.execute(
                    "INSERT INTO enabled_sites(position, name) VALUES(?, ?)",
                    (index, name),
                )
            _set_meta(conn, "sites_mode", "list")


def read_disabled_brands() -> list[str]:
    conn = connect()
    try:
        return [r["name"] for r in conn.execute("SELECT name FROM disabled_brands ORDER BY name")]
    finally:
        conn.close()


def write_disabled_brands(names: list[str]) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM disabled_brands")
        conn.executemany(
            "INSERT INTO disabled_brands(name) VALUES(?)",
            [(name,) for name in names],
        )


def mutate_disabled_brands(mutator: Callable[[list[str]], list[str]]) -> list[str]:
    with transaction() as conn:
        names = [r["name"] for r in conn.execute("SELECT name FROM disabled_brands ORDER BY name")]
        updated = mutator(names)
        conn.execute("DELETE FROM disabled_brands")
        conn.executemany(
            "INSERT INTO disabled_brands(name) VALUES(?)",
            [(name,) for name in updated],
        )
        return updated


# ---------------------------------------------------------------- brand status


def _brand_row(row: sqlite3.Row) -> dict:
    return {
        "brand": row["brand"],
        "state": row["state"],
        "last_parsed_at": row["last_parsed_at"],
        "total": row["total"],
        "source": row["source"],
        "error": row["error"],
    }


def _load_brand_status(conn: sqlite3.Connection) -> dict[str, dict]:
    return {
        row["brand"]: _brand_row(row)
        for row in conn.execute("SELECT * FROM brand_status")
    }


def _replace_brand_status(conn: sqlite3.Connection, status: dict[str, dict]) -> None:
    conn.execute("DELETE FROM brand_status")
    conn.executemany(
        """
        INSERT INTO brand_status(brand, state, last_parsed_at, total, source, error)
        VALUES(?, ?, ?, ?, ?, ?)
        """,
        [
            (
                brand,
                (row or {}).get("state"),
                (row or {}).get("last_parsed_at"),
                _i((row or {}).get("total")),
                (row or {}).get("source"),
                (row or {}).get("error"),
            )
            for brand, row in status.items()
        ],
    )


def load_brand_status() -> dict[str, dict]:
    conn = connect()
    try:
        return _load_brand_status(conn)
    finally:
        conn.close()


def mutate_brand_status(mutator: Callable[[dict[str, dict]], None]) -> None:
    with transaction() as conn:
        status = _load_brand_status(conn)
        mutator(status)
        _replace_brand_status(conn, status)


# ---------------------------------------------------------------- reports


def _clear_scope(conn: sqlite3.Connection, scope: str) -> None:
    for table in ("report_shop_cards", "report_shops", "report_rows", "report_sites", "reports"):
        conn.execute(f"DELETE FROM {table} WHERE scope = ?", (scope,))


def save_report(scope: str, report: dict) -> None:
    rows = [r for r in (report.get("rows") or []) if isinstance(r, dict)]
    sites = [str(s) for s in (report.get("sites") or []) if str(s).strip()]
    with transaction() as conn:
        _clear_scope(conn, scope)
        conn.execute(
            """
            INSERT INTO reports(scope, brand, generated_at, started_at, total, catalog_total, partial, saved_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scope,
                report.get("brand"),
                report.get("generated_at"),
                report.get("started_at"),
                _i(report.get("total")),
                _i(report.get("catalog_total")),
                1 if report.get("partial") else 0,
                time.time(),
            ),
        )
        conn.executemany(
            "INSERT INTO report_sites(scope, position, name) VALUES(?, ?, ?)",
            [(scope, index, name) for index, name in enumerate(sites)],
        )
        row_params = []
        shop_params = []
        card_params = []
        for index, row in enumerate(rows):
            row_params.append(
                (
                    scope,
                    index,
                    None if row.get("id") is None else str(row.get("id")),
                    row.get("brand"),
                    row.get("model"),
                    row.get("name"),
                    row.get("name_uk"),
                    row.get("query"),
                    _f(row.get("our_price")),
                    _i(row.get("product_id")),
                    row.get("option_value"),
                    row.get("image_url"),
                    row.get("product_url"),
                    row.get("ref_volume"),
                    _i(row.get("found")),
                    row.get("found_label"),
                    _i(row.get("shops_with_brand")),
                    _f(row.get("market_min")),
                    row.get("min_site"),
                    row.get("min_url"),
                    _f(row.get("market_median")),
                    _f(row.get("market_avg")),
                    _f(row.get("market_max")),
                    _f(row.get("diff_min_uah")),
                    _f(row.get("diff_min_pct")),
                    _f(row.get("diff_median_pct")),
                    _i(row.get("rank")),
                    row.get("rank_label"),
                    row.get("position"),
                    row.get("advice"),
                    1 if row.get("partial") else 0 if "partial" in row else None,
                    row.get("error"),
                )
            )
            shops = row.get("shops") or {}
            if not isinstance(shops, dict):
                continue
            for site, info in shops.items():
                if not isinstance(info, dict):
                    continue
                in_stock = info.get("in_stock")
                shop_params.append(
                    (
                        scope,
                        index,
                        str(site),
                        info.get("status"),
                        _f(info.get("price")),
                        info.get("title"),
                        info.get("url"),
                        info.get("volume"),
                        _f(info.get("match")),
                        None if in_stock is None else (1 if in_stock else 0),
                        info.get("note"),
                        _f(info.get("diff_pct")),
                        info.get("reason"),
                    )
                )
                for kind in ("alternatives", "near"):
                    cards = info.get(kind) or []
                    if not isinstance(cards, list):
                        continue
                    for card_index, card in enumerate(cards):
                        if not isinstance(card, dict):
                            continue
                        card_params.append(
                            (
                                scope,
                                index,
                                str(site),
                                kind,
                                card_index,
                                card.get("title"),
                                card.get("url"),
                                _f(card.get("price")),
                                _f(card.get("match")),
                                card.get("volume"),
                            )
                        )
        conn.executemany(
            """
            INSERT INTO report_rows(
                scope, position, row_id, brand, model, name, name_uk, query, our_price,
                product_id, option_value, image_url, product_url, ref_volume, found, found_label,
                shops_with_brand, market_min, min_site, min_url, market_median, market_avg, market_max,
                diff_min_uah, diff_min_pct, diff_median_pct, rank, rank_label, price_position, advice,
                partial, error
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            row_params,
        )
        conn.executemany(
            """
            INSERT INTO report_shops(
                scope, row_position, site, status, price, title, url, volume, match_score,
                in_stock, note, diff_pct, reason
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            shop_params,
        )
        conn.executemany(
            """
            INSERT INTO report_shop_cards(
                scope, row_position, site, kind, position, title, url, price, match_score, volume
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            card_params,
        )


def _assemble_report(conn: sqlite3.Connection, scope: str) -> dict | None:
    header = conn.execute("SELECT * FROM reports WHERE scope = ?", (scope,)).fetchone()
    if header is None:
        return None
    sites = [
        r["name"]
        for r in conn.execute(
            "SELECT name FROM report_sites WHERE scope = ? ORDER BY position",
            (scope,),
        )
    ]
    rows_db = conn.execute(
        "SELECT * FROM report_rows WHERE scope = ? ORDER BY position",
        (scope,),
    ).fetchall()
    shops_db = conn.execute(
        "SELECT * FROM report_shops WHERE scope = ?",
        (scope,),
    ).fetchall()
    cards_db = conn.execute(
        "SELECT * FROM report_shop_cards WHERE scope = ? ORDER BY position",
        (scope,),
    ).fetchall()
    cards: dict[tuple, dict[str, list]] = {}
    for card in cards_db:
        bucket = cards.setdefault((card["row_position"], card["site"]), {})
        bucket.setdefault(card["kind"], []).append(
            {
                "title": card["title"],
                "url": card["url"],
                "price": card["price"],
                "match": card["match_score"],
                "volume": card["volume"],
            }
        )
    shops_by_row: dict[int, dict] = {}
    for shop in shops_db:
        info = {
            "status": shop["status"],
            "price": shop["price"],
            "title": shop["title"],
            "url": shop["url"],
            "volume": shop["volume"],
            "match": shop["match_score"],
            "in_stock": _bool_out(shop["in_stock"]),
            "note": shop["note"],
            "diff_pct": shop["diff_pct"],
            "reason": shop["reason"],
        }
        extra = cards.get((shop["row_position"], shop["site"])) or {}
        if extra.get("alternatives"):
            info["alternatives"] = extra["alternatives"]
        if extra.get("near"):
            info["near"] = extra["near"]
        shops_by_row.setdefault(shop["row_position"], {})[shop["site"]] = info
    rows = []
    for row in rows_db:
        item = {
            "id": _id_out(row["row_id"]),
            "brand": row["brand"],
            "model": row["model"],
            "name": row["name"],
            "name_uk": row["name_uk"],
            "query": row["query"],
            "our_price": row["our_price"],
            "product_id": row["product_id"],
            "option_value": row["option_value"],
            "image_url": row["image_url"],
            "product_url": row["product_url"],
            "ref_volume": row["ref_volume"],
            "found": row["found"],
            "found_label": row["found_label"],
            "shops_with_brand": row["shops_with_brand"],
            "market_min": row["market_min"],
            "min_site": row["min_site"],
            "min_url": row["min_url"],
            "market_median": row["market_median"],
            "market_avg": row["market_avg"],
            "market_max": row["market_max"],
            "diff_min_uah": row["diff_min_uah"],
            "diff_min_pct": row["diff_min_pct"],
            "diff_median_pct": row["diff_median_pct"],
            "rank": row["rank"],
            "rank_label": row["rank_label"],
            "position": row["price_position"],
            "advice": row["advice"],
            "error": row["error"],
            "shops": shops_by_row.get(row["position"]) or {},
        }
        if row["partial"] is not None:
            item["partial"] = bool(row["partial"])
        rows.append(item)
    return {
        "generated_at": header["generated_at"],
        "started_at": header["started_at"],
        "total": header["total"],
        "catalog_total": header["catalog_total"],
        "sites": sites,
        "rows": rows,
        "partial": bool(header["partial"]),
        "brand": header["brand"],
        "_saved_at": header["saved_at"] or 0,
    }


def load_report(scope: str) -> dict | None:
    conn = connect()
    try:
        return _assemble_report(conn, scope)
    finally:
        conn.close()


def report_saved_at(scope: str) -> float:
    conn = connect()
    try:
        row = conn.execute("SELECT saved_at FROM reports WHERE scope = ?", (scope,)).fetchone()
        return float(row["saved_at"] or 0) if row else 0.0
    finally:
        conn.close()


def load_stat_reports() -> list[dict]:
    """Збережені звіти крім живої таблиці прогону. Спочатку окремі бренди, потім повний."""
    conn = connect()
    try:
        scopes = [
            r["scope"]
            for r in conn.execute(
                "SELECT scope FROM reports WHERE scope != ? AND scope != ? ORDER BY CASE WHEN scope = ? THEN 1 ELSE 0 END, scope",
                (LIVE_SCOPE, LIVE_LOCAL_SCOPE, FULL_SCOPE),
            )
        ]
        return [report for scope in scopes if (report := _tag_scope(conn, scope))]
    finally:
        conn.close()


def _tag_scope(conn: sqlite3.Connection, scope: str) -> dict | None:
    report = _assemble_report(conn, scope)
    if report is None:
        return None
    report["_scope"] = scope
    return report


def live_scope_for(lane: str | None = None) -> str:
    return LIVE_LOCAL_SCOPE if (lane or "") == "local" else LIVE_SCOPE


def save_live_rows(rows: list[dict], lane: str | None = None) -> None:
    save_report(live_scope_for(lane), {"rows": rows, "partial": True, "generated_at": None})


def load_live_rows(lane: str | None = None) -> list[dict]:
    report = load_report(live_scope_for(lane))
    if not report:
        return []
    return list(report.get("rows") or [])


def clear_live_rows(lane: str | None = None) -> None:
    with transaction() as conn:
        _clear_scope(conn, live_scope_for(lane))


# ---------------------------------------------------------------- history


def _history_from_rows(conn: sqlite3.Connection) -> list[dict]:
    items = []
    headers = conn.execute("SELECT * FROM search_history ORDER BY sort_order").fetchall()
    for header in headers:
        hits = conn.execute(
            "SELECT * FROM search_hits WHERE history_id = ? ORDER BY position",
            (header["id"],),
        ).fetchall()
        items.append(
            {
                "id": header["id"],
                "created_at": header["created_at"],
                "query": header["query"],
                "our_price": header["our_price"],
                "found": header["found"],
                "market_avg": header["market_avg"],
                "market_min": header["market_min"],
                "market_max": header["market_max"],
                "position": header["position_label"],
                "results": [
                    {
                        "site": hit["site"],
                        "title": hit["title"],
                        "price": hit["price"],
                        "url": hit["url"],
                        "image": hit["image"],
                        "volume": hit["volume"],
                        "match": hit["match_score"],
                        "status": "ok",
                    }
                    for hit in hits
                ],
            }
        )
    return items


def load_history() -> list[dict]:
    conn = connect()
    try:
        return _history_from_rows(conn)
    finally:
        conn.close()


def _replace_history(conn: sqlite3.Connection, items: list[dict]) -> None:
    conn.execute("DELETE FROM search_hits")
    conn.execute("DELETE FROM search_history")
    for index, item in enumerate(items):
        history_id = str(item.get("id") or index)
        conn.execute(
            """
            INSERT INTO search_history(
                id, created_at, query, our_price, found, market_avg, market_min, market_max,
                position_label, sort_order
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                history_id,
                item.get("created_at"),
                item.get("query"),
                _f(item.get("our_price")),
                _i(item.get("found")),
                _f(item.get("market_avg")),
                _f(item.get("market_min")),
                _f(item.get("market_max")),
                item.get("position"),
                index,
            ),
        )
        hits = []
        for hit_index, hit in enumerate(item.get("results") or []):
            if not isinstance(hit, dict):
                continue
            hits.append(
                (
                    history_id,
                    hit_index,
                    hit.get("site"),
                    hit.get("title"),
                    _f(hit.get("price")),
                    hit.get("url"),
                    hit.get("image"),
                    hit.get("volume"),
                    _f(hit.get("match")),
                )
            )
        conn.executemany(
            """
            INSERT INTO search_hits(history_id, position, site, title, price, url, image, volume, match_score)
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            hits,
        )


def save_history(items: list[dict], *, limit: int = 40) -> None:
    with transaction() as conn:
        _replace_history(conn, list(items)[:limit])


def append_history(record: dict, *, limit: int = 40) -> list[dict]:
    with transaction() as conn:
        items = _history_from_rows(conn)
        items.insert(0, record)
        items = items[:limit]
        _replace_history(conn, items)
        return items


# ---------------------------------------------------------------- kv job / search


def _load_kv(conn: sqlite3.Connection, namespace: str) -> dict:
    return {
        row["key"]: _decode(row["value_type"], row["value_text"])
        for row in conn.execute("SELECT key, value_type, value_text FROM kv WHERE namespace = ?", (namespace,))
    }


def _replace_kv(conn: sqlite3.Connection, namespace: str, payload: dict) -> None:
    conn.execute("DELETE FROM kv WHERE namespace = ?", (namespace,))
    for key, value in payload.items():
        if isinstance(value, (dict, list)):
            continue
        value_type, value_text = _encode(value)
        conn.execute(
            "INSERT INTO kv(namespace, key, value_type, value_text) VALUES(?, ?, ?, ?)",
            (namespace, key, value_type, value_text),
        )


def _job_namespace(lane: str | None) -> str:
    return "job_local" if (lane or "") == "local" else "job"


def load_job(lane: str | None = None) -> dict | None:
    conn = connect()
    try:
        data = _load_kv(conn, _job_namespace(lane))
        if data:
            data.setdefault("lane", "local" if lane == "local" else "remote")
        return data or None
    finally:
        conn.close()


def load_all_jobs() -> list[dict]:
    jobs = []
    for lane in ("remote", "local"):
        job = load_job(lane)
        if job and job.get("state"):
            jobs.append(job)
    return jobs


def save_job(payload: dict, lane: str | None = None) -> None:
    lane = lane or payload.get("lane") or "remote"
    if lane not in ("remote", "local"):
        lane = "remote"
    body = dict(payload)
    body["lane"] = lane
    with transaction() as conn:
        _replace_kv(conn, _job_namespace(lane), body)


def load_search() -> dict:
    conn = connect()
    try:
        data = _load_kv(conn, "search") or {"state": "idle"}
        results = []
        misses = []
        for row in conn.execute("SELECT * FROM search_rows ORDER BY position"):
            item = {
                "site": row["site"],
                "status": row["status"],
                "title": row["title"],
                "price": row["price"],
                "url": row["url"],
                "image": row["image"],
                "volume": row["volume"],
                "match": row["match_score"],
                "reason": row["reason"],
            }
            if row["kind"] == "miss":
                misses.append(item)
            else:
                results.append(item)
        if results or misses or data.get("state") not in (None, "idle"):
            data["results"] = results
            data["misses"] = misses
        return data
    finally:
        conn.close()


def save_search(payload: dict) -> None:
    results = list(payload.get("results") or [])
    misses = list(payload.get("misses") or [])
    with transaction() as conn:
        _replace_kv(conn, "search", {k: v for k, v in payload.items() if k not in ("results", "misses", "history", "row")})
        conn.execute("DELETE FROM search_rows")
        rows = []
        for kind, items in (("result", results), ("miss", misses)):
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                rows.append(
                    (
                        kind,
                        index,
                        item.get("site"),
                        item.get("status"),
                        item.get("title"),
                        _f(item.get("price")),
                        item.get("url"),
                        item.get("image"),
                        item.get("volume"),
                        _f(item.get("match")),
                        item.get("reason"),
                    )
                )
        conn.executemany(
            """
            INSERT INTO search_rows(kind, position, site, status, title, price, url, image, volume, match_score, reason)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )


# ---------------------------------------------------------------- rotation


def _rotation_meta_loads(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_rotation() -> dict:
    conn = connect()
    try:
        header = conn.execute("SELECT * FROM rotation_state WHERE id = 1").fetchone()
        if header is None:
            return {}
        buckets: dict[str, list] = {"pending": [], "done": [], "skipped": []}
        for row in conn.execute("SELECT * FROM rotation_items ORDER BY position"):
            if row["bucket"] == "skipped":
                buckets["skipped"].append({"brand": row["brand"], "error": row["error"]})
            else:
                buckets.setdefault(row["bucket"], []).append(row["brand"])
        meta = {}
        try:
            meta = _rotation_meta_loads(header["meta_json"])
        except (KeyError, IndexError):
            meta = {}
        return {
            "built_for": header["built_for"],
            "day": header["day"],
            "current": header["current_brand"],
            "pending": buckets.get("pending") or [],
            "done": buckets.get("done") or [],
            "skipped": buckets.get("skipped") or [],
            "start_retries": dict(meta.get("start_retries") or {}),
            "run_retries": dict(meta.get("run_retries") or {}),
        }
    finally:
        conn.close()


def save_rotation(state: dict) -> None:
    meta = {
        "start_retries": state.get("start_retries") or {},
        "run_retries": state.get("run_retries") or {},
    }
    with transaction() as conn:
        _ensure_schema_extras(conn)
        conn.execute("DELETE FROM rotation_items")
        conn.execute("DELETE FROM rotation_state")
        conn.execute(
            "INSERT INTO rotation_state(id, built_for, day, current_brand, meta_json) VALUES(1, ?, ?, ?, ?)",
            (
                state.get("built_for"),
                _i(state.get("day")),
                state.get("current"),
                json.dumps(meta, ensure_ascii=False),
            ),
        )
        items = []
        for bucket in ("pending", "done"):
            for index, brand in enumerate(state.get(bucket) or []):
                items.append((bucket, index, brand, None))
        for index, item in enumerate(state.get("skipped") or []):
            if isinstance(item, dict):
                items.append(("skipped", index, item.get("brand"), item.get("error")))
            else:
                items.append(("skipped", index, str(item), None))
        conn.executemany(
            "INSERT INTO rotation_items(bucket, position, brand, error) VALUES(?, ?, ?, ?)",
            items,
        )


# ---------------------------------------------------------------- parse log


def append_parse_event(*, ts: str, level: str, kind: str, brand: str | None, site: str | None, message: str) -> None:
    with transaction() as conn:
        conn.execute(
            "INSERT INTO parse_events(ts, level, kind, brand, site, message) VALUES(?, ?, ?, ?, ?, ?)",
            (ts, level, kind, brand, site, message),
        )
        extra = conn.execute("SELECT COUNT(*) AS n FROM parse_events").fetchone()["n"] - 800
        if extra > 0:
            conn.execute(
                "DELETE FROM parse_events WHERE id IN (SELECT id FROM parse_events ORDER BY id LIMIT ?)",
                (extra,),
            )


def load_parse_events(limit: int = 250) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT ts, level, kind, brand, site, message FROM parse_events ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "ts": row["ts"],
                "level": row["level"],
                "kind": row["kind"],
                "brand": row["brand"],
                "site": row["site"],
                "message": row["message"],
            }
            for row in rows
        ]
    finally:
        conn.close()


def clear_parse_events() -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM parse_events")


# ---------------------------------------------------------------- catalog fallback


def load_catalog_products() -> list[dict]:
    conn = connect()
    try:
        return [
            {
                "id": _id_out(row["row_id"]),
                "product_id": row["product_id"],
                "model": row["model"],
                "brand": row["brand"],
                "name": row["name"],
                "name_uk": row["name_uk"],
                "query": row["query"],
                "our_price": row["our_price"],
                "option_value": row["option_value"],
                "image_url": row["image_url"],
                "product_url": row["product_url"],
            }
            for row in conn.execute("SELECT * FROM catalog_products ORDER BY position")
        ]
    finally:
        conn.close()


def save_catalog_products(products: list[dict]) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM catalog_products")
        conn.executemany(
            """
            INSERT INTO catalog_products(
                position, row_id, product_id, model, brand, name, name_uk, query, our_price,
                option_value, image_url, product_url
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    index,
                    None if item.get("id") is None else str(item.get("id")),
                    _i(item.get("product_id")),
                    item.get("model"),
                    item.get("brand"),
                    item.get("name"),
                    item.get("name_uk"),
                    item.get("query"),
                    _f(item.get("our_price")),
                    item.get("option_value"),
                    item.get("image_url"),
                    item.get("product_url"),
                )
                for index, item in enumerate(products)
                if isinstance(item, dict)
            ],
        )


# ---------------------------------------------------------------- one-time JSON import


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.exception("Не вдалося прочитати %s", path)
        return default


def _migrate_json_once(conn: sqlite3.Connection) -> None:
    if _meta(conn, "imported_json") == "1":
        _catch_up_disabled_brands(conn)
        return
    if not JSON_DIR.is_dir():
        _set_meta(conn, "imported_json", "1")
        conn.commit()
        return
    try:
        conn.execute("BEGIN IMMEDIATE")
        if _meta(conn, "imported_json") == "1":
            conn.commit()
            return
        _import_json(conn, JSON_DIR)
        _set_meta(conn, "imported_json", "1")
        _remember_disabled_mtime(conn)
        conn.commit()
        logger.info("Імпортовано JSON парсера з %s у %s", JSON_DIR, DB_PATH)
    except Exception:
        conn.rollback()
        logger.exception("Імпорт JSON парсера не вдався")


def _remember_disabled_mtime(conn: sqlite3.Connection) -> None:
    path = JSON_DIR / "disabled_brands.json"
    if path.is_file():
        _set_meta(conn, "disabled_json_mtime", str(path.stat().st_mtime))


def _catch_up_disabled_brands(conn: sqlite3.Connection) -> None:
    """Поки старий процес ще пише JSON, підхопити вимкнені бренди, якщо файл новіший."""
    path = JSON_DIR / "disabled_brands.json"
    if not path.is_file():
        return
    mtime = str(path.stat().st_mtime)
    if _meta(conn, "disabled_json_mtime") == mtime:
        return
    data = _read_json(path, None)
    if not isinstance(data, list):
        return
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM disabled_brands")
        conn.executemany(
            "INSERT INTO disabled_brands(name) VALUES(?)",
            [(str(name).strip(),) for name in data if str(name).strip()],
        )
        _set_meta(conn, "disabled_json_mtime", mtime)
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Не вдалося підхопити disabled_brands.json")


def _import_json(conn: sqlite3.Connection, folder: Path) -> None:
    settings = _read_json(folder / "parser_settings.json", {})
    if isinstance(settings, dict):
        for key, value in settings.items():
            if key in ("enabled_sites", "disabled_brands") or isinstance(value, (dict, list)):
                continue
            value_type, value_text = _encode(value)
            conn.execute(
                "INSERT INTO settings(key, value_type, value_text) VALUES(?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value_type = excluded.value_type, value_text = excluded.value_text",
                (key, value_type, value_text),
            )
        if "enabled_sites" in settings and isinstance(settings.get("enabled_sites"), list):
            conn.execute("DELETE FROM enabled_sites")
            for index, name in enumerate(settings["enabled_sites"]):
                conn.execute("INSERT INTO enabled_sites(position, name) VALUES(?, ?)", (index, str(name)))
            _set_meta(conn, "sites_mode", "list")

    disabled = _read_json(folder / "disabled_brands.json", None)
    if not isinstance(disabled, list) and isinstance(settings, dict):
        disabled = settings.get("disabled_brands")
    if isinstance(disabled, list) and disabled:
        conn.execute("DELETE FROM disabled_brands")
        conn.executemany(
            "INSERT OR IGNORE INTO disabled_brands(name) VALUES(?)",
            [(str(name).strip(),) for name in disabled if str(name).strip()],
        )

    status = _read_json(folder / "brand_status.json", {})
    if isinstance(status, dict) and status:
        _replace_brand_status(conn, {str(k): v if isinstance(v, dict) else {} for k, v in status.items()})

    history = _read_json(folder / "history.json", [])
    if isinstance(history, list) and history:
        _replace_history(conn, [item for item in history if isinstance(item, dict)][:40])

    catalog = _read_json(folder / "catalog.json", [])
    if isinstance(catalog, list) and catalog and not conn.execute("SELECT 1 FROM catalog_products LIMIT 1").fetchone():
        conn.executemany(
            """
            INSERT INTO catalog_products(
                position, row_id, product_id, model, brand, name, name_uk, query, our_price,
                option_value, image_url, product_url
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    index,
                    None if item.get("id") is None else str(item.get("id")),
                    _i(item.get("product_id")),
                    item.get("model"),
                    item.get("brand"),
                    item.get("name"),
                    item.get("name_uk"),
                    item.get("query"),
                    _f(item.get("our_price")),
                    item.get("option_value"),
                    item.get("image_url"),
                    item.get("product_url"),
                )
                for index, item in enumerate(catalog)
                if isinstance(item, dict)
            ],
        )

    price = _read_json(folder / "price_report.json", None)
    if isinstance(price, dict) and price.get("rows"):
        _import_report(conn, FULL_SCOPE, price)

    brands_dir = folder / "brands"
    if brands_dir.is_dir():
        for child in sorted(brands_dir.iterdir()):
            report_path = child / "report.json"
            if not report_path.is_file():
                continue
            report = _read_json(report_path, None)
            if isinstance(report, dict) and report.get("rows"):
                _import_report(conn, child.name, report)

    job = _read_json(folder / "jobs" / "active.json", None)
    if isinstance(job, dict) and job.get("state"):
        _replace_kv(conn, "job", job)
    live = _read_json(folder / "jobs" / "live.json", None)
    if isinstance(live, dict) and isinstance(live.get("rows"), list) and live["rows"]:
        _import_report(conn, LIVE_SCOPE, {"rows": live["rows"], "partial": True})

    rotation = _read_json(folder / "jobs" / "rotation.json", None)
    if isinstance(rotation, dict) and rotation:
        conn.execute("DELETE FROM rotation_items")
        conn.execute("DELETE FROM rotation_state")
        conn.execute(
            "INSERT INTO rotation_state(id, built_for, day, current_brand) VALUES(1, ?, ?, ?)",
            (rotation.get("built_for"), _i(rotation.get("day")), rotation.get("current")),
        )
        items = []
        for bucket in ("pending", "done"):
            for index, brand in enumerate(rotation.get(bucket) or []):
                items.append((bucket, index, brand, None))
        for index, item in enumerate(rotation.get("skipped") or []):
            if isinstance(item, dict):
                items.append(("skipped", index, item.get("brand"), item.get("error")))
        if items:
            conn.executemany(
                "INSERT INTO rotation_items(bucket, position, brand, error) VALUES(?, ?, ?, ?)",
                items,
            )

    log_path = folder / "parse_log.jsonl"
    if log_path.is_file() and not conn.execute("SELECT 1 FROM parse_events LIMIT 1").fetchone():
        events = []
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-800:]:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                events.append(
                    (
                        item.get("ts"),
                        item.get("level"),
                        item.get("kind"),
                        item.get("brand"),
                        item.get("site"),
                        item.get("message"),
                    )
                )
        conn.executemany(
            "INSERT INTO parse_events(ts, level, kind, brand, site, message) VALUES(?, ?, ?, ?, ?, ?)",
            events,
        )


def _import_report(conn: sqlite3.Connection, scope: str, report: dict) -> None:
    # Викликається всередині транзакції міграції. save_report відкриває свою, тому пишемо тут напряму.
    rows = [r for r in (report.get("rows") or []) if isinstance(r, dict)]
    _clear_scope(conn, scope)
    conn.execute(
        """
        INSERT INTO reports(scope, brand, generated_at, started_at, total, catalog_total, partial, saved_at)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            scope,
            report.get("brand"),
            report.get("generated_at"),
            report.get("started_at"),
            _i(report.get("total")) if report.get("total") is not None else len(rows),
            _i(report.get("catalog_total")),
            1 if report.get("partial") else 0,
            time.time(),
        ),
    )
    sites = [str(s) for s in (report.get("sites") or []) if str(s).strip()]
    conn.executemany(
        "INSERT INTO report_sites(scope, position, name) VALUES(?, ?, ?)",
        [(scope, index, name) for index, name in enumerate(sites)],
    )
    # Повторно використовуємо публічний розбір через тимчасовий виклик не можна (інша транзакція).
    # Тут лише рядки і магазини — той самий набір колонок, що й save_report.
    for index, row in enumerate(rows):
        conn.execute(
            """
            INSERT INTO report_rows(
                scope, position, row_id, brand, model, name, name_uk, query, our_price,
                product_id, option_value, image_url, product_url, ref_volume, found, found_label,
                shops_with_brand, market_min, min_site, min_url, market_median, market_avg, market_max,
                diff_min_uah, diff_min_pct, diff_median_pct, rank, rank_label, price_position, advice,
                partial, error
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                scope,
                index,
                None if row.get("id") is None else str(row.get("id")),
                row.get("brand"),
                row.get("model"),
                row.get("name"),
                row.get("name_uk"),
                row.get("query"),
                _f(row.get("our_price")),
                _i(row.get("product_id")),
                row.get("option_value"),
                row.get("image_url"),
                row.get("product_url"),
                row.get("ref_volume"),
                _i(row.get("found")),
                row.get("found_label"),
                _i(row.get("shops_with_brand")),
                _f(row.get("market_min")),
                row.get("min_site"),
                row.get("min_url"),
                _f(row.get("market_median")),
                _f(row.get("market_avg")),
                _f(row.get("market_max")),
                _f(row.get("diff_min_uah")),
                _f(row.get("diff_min_pct")),
                _f(row.get("diff_median_pct")),
                _i(row.get("rank")),
                row.get("rank_label"),
                row.get("position"),
                row.get("advice"),
                1 if row.get("partial") else 0 if "partial" in row else None,
                row.get("error"),
            ),
        )
        shops = row.get("shops") or {}
        if not isinstance(shops, dict):
            continue
        for site, info in shops.items():
            if not isinstance(info, dict):
                continue
            in_stock = info.get("in_stock")
            conn.execute(
                """
                INSERT INTO report_shops(
                    scope, row_position, site, status, price, title, url, volume, match_score,
                    in_stock, note, diff_pct, reason
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    scope,
                    index,
                    str(site),
                    info.get("status"),
                    _f(info.get("price")),
                    info.get("title"),
                    info.get("url"),
                    info.get("volume"),
                    _f(info.get("match")),
                    None if in_stock is None else (1 if in_stock else 0),
                    info.get("note"),
                    _f(info.get("diff_pct")),
                    info.get("reason"),
                ),
            )
            for kind in ("alternatives", "near"):
                for card_index, card in enumerate(info.get(kind) or []):
                    if not isinstance(card, dict):
                        continue
                    conn.execute(
                        """
                        INSERT INTO report_shop_cards(
                            scope, row_position, site, kind, position, title, url, price, match_score, volume
                        ) VALUES(?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            scope,
                            index,
                            str(site),
                            kind,
                            card_index,
                            card.get("title"),
                            card.get("url"),
                            _f(card.get("price")),
                            _f(card.get("match")),
                            card.get("volume"),
                        ),
                    )
