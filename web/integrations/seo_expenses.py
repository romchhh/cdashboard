"""Витрати на SEO — ручний облік (дата, сума, опис) у SQLite (cosmy_clients.db)."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import unquote

from config import CLIENTS_DATABASE_URL

_MAX_DESCRIPTION_LEN = 500
_LEGACY_JSON_PATH = Path(__file__).resolve().parents[2] / "databases" / "seo_expenses.json"


class SeoExpenseError(ValueError):
    """Помилка валідації або відсутній запис."""


def _sqlite_path_from_url(url: str) -> Path:
    prefix = "sqlite+aiosqlite:///"
    if not url.startswith(prefix):
        raise ValueError(f"Очікується SQLite URL клієнтської БД, отримано: {url[:48]}…")
    if url.startswith(prefix + "//"):
        inner = url[len(prefix + "//") :]
        if inner.startswith("/"):
            return Path(unquote(inner))
    rest = unquote(url[len(prefix) :].lstrip("/"))
    root = Path(__file__).resolve().parents[2]
    return (root / rest).resolve()


def clients_db_path() -> Path:
    return _sqlite_path_from_url(CLIENTS_DATABASE_URL)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _parse_date(value: str | date | datetime | None) -> date:
    if value is None:
        raise SeoExpenseError("Дату витрати потрібно вказати")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise SeoExpenseError("Невірний формат дати (очікується YYYY-MM-DD)") from exc


def _parse_amount(value: Any) -> float:
    try:
        amount = float(str(value).replace(",", ".").strip())
    except (TypeError, ValueError) as exc:
        raise SeoExpenseError("Сума має бути числом") from exc
    if amount <= 0:
        raise SeoExpenseError("Сума має бути більше 0")
    return round(amount, 2)


def _parse_description(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise SeoExpenseError("Вкажіть, за що була витрата")
    if len(text) > _MAX_DESCRIPTION_LEN:
        raise SeoExpenseError(f"Опис занадто довгий (макс. {_MAX_DESCRIPTION_LEN} символів)")
    return text


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    exp_date = _parse_date(str(row["expense_date"])[:10])
    return {
        "id": str(row["id"]),
        "expense_date": exp_date.isoformat(),
        "amount_uah": round(float(row["amount_uah"]), 2),
        "description": str(row["description"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seo_expenses (
            id TEXT PRIMARY KEY,
            expense_date TEXT NOT NULL,
            amount_uah REAL NOT NULL,
            description TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_seo_expenses_expense_date ON seo_expenses (expense_date)"
    )
    conn.commit()
    _migrate_legacy_json(conn)


def _migrate_legacy_json(conn: sqlite3.Connection) -> None:
    """Одноразовий імпорт з databases/seo_expenses.json, якщо таблиця порожня."""
    count = conn.execute("SELECT COUNT(*) FROM seo_expenses").fetchone()[0]
    if count or not _LEGACY_JSON_PATH.is_file():
        return
    try:
        raw = json.loads(_LEGACY_JSON_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if not isinstance(raw, list) or not raw:
        return
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            exp_date = _parse_date(item.get("expense_date")).isoformat()
            amount = _parse_amount(item.get("amount_uah"))
            desc = _parse_description(item.get("description"))
        except SeoExpenseError:
            continue
        row_id = str(item.get("id") or uuid.uuid4())
        created = str(item.get("created_at") or _now_iso())
        updated = str(item.get("updated_at") or created)
        conn.execute(
            """
            INSERT OR IGNORE INTO seo_expenses
            (id, expense_date, amount_uah, description, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (row_id, exp_date, amount, desc, created, updated),
        )
    conn.commit()


@contextmanager
def _db_conn() -> Iterator[sqlite3.Connection]:
    path = clients_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=60.0)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_schema(conn)
        yield conn
    finally:
        conn.close()


def list_seo_expenses(
    *,
    date_from: date | datetime | str | None = None,
    date_to: date | datetime | str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[str] = []
    if date_from is not None:
        clauses.append("expense_date >= ?")
        params.append(_parse_date(date_from).isoformat())
    if date_to is not None:
        clauses.append("expense_date <= ?")
        params.append(_parse_date(date_to).isoformat())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT * FROM seo_expenses {where} ORDER BY expense_date DESC, created_at DESC"
    with _db_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def sum_seo_expenses_uah(
    start: date | datetime,
    end: date | datetime,
) -> float:
    """Сума витрат SEO за період (включно з межами дат)."""
    start_d = _parse_date(start)
    end_d = _parse_date(end)
    if end_d < start_d:
        return 0.0
    with _db_conn() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(amount_uah), 0)
            FROM seo_expenses
            WHERE expense_date >= ? AND expense_date <= ?
            """,
            (start_d.isoformat(), end_d.isoformat()),
        ).fetchone()
    return round(float(row[0] if row else 0), 2)


def get_seo_expense(expense_id: str) -> dict[str, Any] | None:
    eid = (expense_id or "").strip()
    if not eid:
        return None
    with _db_conn() as conn:
        row = conn.execute("SELECT * FROM seo_expenses WHERE id = ?", (eid,)).fetchone()
    return _row_to_dict(row) if row else None


def create_seo_expense(
    *,
    expense_date: str | date | datetime,
    amount_uah: Any,
    description: str,
) -> dict[str, Any]:
    row = {
        "id": str(uuid.uuid4()),
        "expense_date": _parse_date(expense_date).isoformat(),
        "amount_uah": _parse_amount(amount_uah),
        "description": _parse_description(description),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    with _db_conn() as conn:
        conn.execute(
            """
            INSERT INTO seo_expenses (id, expense_date, amount_uah, description, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                row["expense_date"],
                row["amount_uah"],
                row["description"],
                row["created_at"],
                row["updated_at"],
            ),
        )
        conn.commit()
    return row


def update_seo_expense(
    expense_id: str,
    *,
    expense_date: str | date | datetime,
    amount_uah: Any,
    description: str,
) -> dict[str, Any]:
    eid = (expense_id or "").strip()
    if not eid:
        raise SeoExpenseError("Не вказано ID витрати")
    updated = {
        "expense_date": _parse_date(expense_date).isoformat(),
        "amount_uah": _parse_amount(amount_uah),
        "description": _parse_description(description),
        "updated_at": _now_iso(),
    }
    with _db_conn() as conn:
        cur = conn.execute(
            """
            UPDATE seo_expenses
            SET expense_date = ?, amount_uah = ?, description = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                updated["expense_date"],
                updated["amount_uah"],
                updated["description"],
                updated["updated_at"],
                eid,
            ),
        )
        if cur.rowcount == 0:
            raise SeoExpenseError("Витрату не знайдено")
        row = conn.execute("SELECT * FROM seo_expenses WHERE id = ?", (eid,)).fetchone()
        conn.commit()
    if not row:
        raise SeoExpenseError("Витрату не знайдено")
    return _row_to_dict(row)


def delete_seo_expense(expense_id: str) -> bool:
    eid = (expense_id or "").strip()
    if not eid:
        raise SeoExpenseError("Не вказано ID витрати")
    with _db_conn() as conn:
        cur = conn.execute("DELETE FROM seo_expenses WHERE id = ?", (eid,))
        if cur.rowcount == 0:
            raise SeoExpenseError("Витрату не знайдено")
        conn.commit()
    return True
