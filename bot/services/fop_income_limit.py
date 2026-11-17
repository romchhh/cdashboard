"""Річний ліміт надходжень ФОП + звірка Моно з БД."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import (
    Category,
    FOP,
    Transaction,
    TransactionSource,
    TransactionStatus,
    TransactionType,
)
from bot.services.fop_filter import fop_surname
from bot.services.period_range import calendar_year_to_date, mono_epoch_range
from bot.services.reports import CASH_MOVEMENT_CATEGORY_NAMES
from config import FOP_ANNUAL_INCOME_LIMIT_UAH, INACTIVE_FOP_NAMES, ROOT_DIR
from mono.api import format_amount, now_kyiv

logger = logging.getLogger(__name__)

RECONCILE_PATH = Path(ROOT_DIR) / "databases" / "fop_income_reconcile.json"
WARN_DELTA_UAH = 5.0
_YTD_CACHE: dict[str, Any] = {"at": 0.0, "rows": None}
_YTD_CACHE_TTL = 45.0


def load_reconcile_cache() -> dict[str, Any]:
    if not RECONCILE_PATH.is_file():
        return {}
    try:
        data = json.loads(RECONCILE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("fop reconcile cache read: %s", exc)
        return {}


def save_reconcile_cache(payload: dict[str, Any]) -> None:
    RECONCILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    RECONCILE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


async def load_ytd_income_by_fop(session: AsyncSession) -> list[dict[str, Any]]:
    """Надходження YTD з БД по прізвищу ФОП (Моно + NovaPay, без готівки/переміщень)."""
    now = time.monotonic()
    cached = _YTD_CACHE.get("rows")
    if cached is not None and (now - float(_YTD_CACHE.get("at") or 0)) < _YTD_CACHE_TTL:
        return [dict(row) for row in cached]
    date_from, date_to = calendar_year_to_date()
    skip_names = set(CASH_MOVEMENT_CATEGORY_NAMES) | {"Богдан"}
    rows = (
        await session.execute(
            select(Transaction, Category, FOP)
            .outerjoin(Category, Transaction.category_id == Category.id)
            .outerjoin(FOP, Transaction.fop_id == FOP.id)
            .where(
                and_(
                    Transaction.date >= date_from,
                    Transaction.date <= date_to,
                    Transaction.transaction_type == TransactionType.INCOME,
                    Transaction.status == TransactionStatus.CONFIRMED,
                    Transaction.source.in_((TransactionSource.MONOBANK, TransactionSource.NOVAPAY)),
                )
            )
        )
    ).all()

    grouped: dict[str, dict[str, Any]] = {}
    for tx, cat, fop in rows:
        if cat and cat.name in skip_names:
            continue
        if not fop or not fop.is_active or (fop.name or "") in INACTIVE_FOP_NAMES:
            continue
        surname = fop_surname(fop.name)
        bucket = grouped.setdefault(
            surname,
            {
                "name": surname,
                "year": date_from.year,
                "from_iso": date_from.strftime("%Y-%m-%d"),
                "to_iso": date_to.strftime("%Y-%m-%d"),
                "limit": float(FOP_ANNUAL_INCOME_LIMIT_UAH),
                "monobank": 0.0,
                "novapay": 0.0,
                "total": 0.0,
                "fop_names": set(),
            },
        )
        bucket["fop_names"].add(fop.name)
        amt = float(tx.amount or 0)
        if tx.source == TransactionSource.MONOBANK:
            bucket["monobank"] += amt
        elif tx.source == TransactionSource.NOVAPAY:
            bucket["novapay"] += amt
        bucket["total"] += amt

    out: list[dict[str, Any]] = []
    for item in grouped.values():
        total = round(float(item["total"]), 2)
        limit = float(item["limit"])
        remaining = round(limit - total, 2)
        item["fop_names"] = sorted(item["fop_names"])
        item["monobank"] = round(float(item["monobank"]), 2)
        item["novapay"] = round(float(item["novapay"]), 2)
        item["total"] = total
        item["remaining"] = remaining
        item["overlimit"] = round(max(0.0, -remaining), 2)
        item["over_limit"] = remaining < 0
        item["warn_near"] = remaining >= 0 and remaining <= limit * 0.05
        item["used_pct"] = round(100 * total / limit, 1) if limit else 0
        recon = (load_reconcile_cache().get("fops") or {}).get(item["name"]) or {}
        item["mono_live"] = recon.get("mono_live")
        item["mono_db"] = recon.get("mono_db")
        item["reconcile_at"] = recon.get("checked_at")
        item["reconcile_error"] = recon.get("error")
        item["mono_db_matched"] = recon.get("mono_db_matched")
        item["mono_db_orphan"] = recon.get("mono_db_orphan")
        item["mono_amount_drift"] = recon.get("mono_amount_drift")
        # mono_gap = незавантажені операції (кредити є в виписці, немає в БД), не «вся колонка Моно».
        if recon.get("mono_gap") is not None:
            item["mono_gap"] = recon.get("mono_gap")
        elif item["mono_live"] is not None:
            item["mono_gap"] = round(
                max(0.0, float(item["mono_live"]) - float(recon.get("mono_db_matched") or 0)),
                2,
            )
        else:
            item["mono_gap"] = None
        orphan = float(item.get("mono_db_orphan") or 0)
        drift = abs(float(item.get("mono_amount_drift") or 0))
        missing = float(item.get("mono_gap") or 0)
        item["sync_gap"] = (
            missing >= WARN_DELTA_UAH
            or orphan >= WARN_DELTA_UAH
            or drift >= WARN_DELTA_UAH
        )
        out.append(item)
    out.sort(key=lambda x: (-x["over_limit"], -x["used_pct"], x["name"]))
    _YTD_CACHE["rows"] = out
    _YTD_CACHE["at"] = time.monotonic()
    return out


def summarize_fop_limits(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Підсумки по активних ФОПах для рядка «залишок разом»."""
    total_remaining = round(sum(float(row.get("remaining") or 0) for row in rows), 2)
    return {
        "total_remaining": total_remaining,
        "total_overlimit": round(max(0.0, -total_remaining), 2),
    }


def _mono_credit_amount(tx: dict) -> float | None:
    raw = float(tx.get("amount") or 0)
    if raw <= 0:
        return None
    return round(abs(format_amount(raw)), 2)


def _tx_counts_mono_ytd(tx: Transaction | None, skip_ids: set[int]) -> bool:
    """Чи зараховуємо кредит у YTD надходжень ФОП (ті самі правила, що й у БД)."""
    if tx is None:
        return False
    if tx.status != TransactionStatus.CONFIRMED:
        return False
    if tx.category_id is not None and tx.category_id in skip_ids:
        return False
    if tx.transaction_type != TransactionType.INCOME:
        return False
    return True


def sum_mono_credits_aligned(
    transactions: list[dict],
    mono_by_ext: dict[str, Transaction],
    skip_ids: set[int],
) -> float:
    """
    Сума кредитів виписки з тими ж правилами, що й _db_mono_income.
    Готівкові перекази в виписці є, у БД для ліміту їх немає.
    """
    total = 0.0
    for tx in transactions:
        amt = _mono_credit_amount(tx)
        if amt is None:
            continue
        ext = f"mono_{tx.get('id')}"
        db_tx = mono_by_ext.get(ext)
        if not _tx_counts_mono_ytd(db_tx, skip_ids):
            continue
        total += amt
    return round(total, 2)


def mono_sync_missing_and_drift(
    transactions: list[dict],
    mono_by_ext: dict[str, Transaction],
    skip_ids: set[int],
) -> tuple[float, float]:
    """
    Розрив синку: лише кредити виписки без рядка в БД (missing).
    drift — різниця суми виписки й БД по вже збережених id (виправляється повторним синхом).
    """
    missing = 0.0
    stmt_matched = 0.0
    db_matched = 0.0
    for tx in transactions:
        amt = _mono_credit_amount(tx)
        if amt is None:
            continue
        ext = f"mono_{tx.get('id')}"
        db_tx = mono_by_ext.get(ext)
        if db_tx is None:
            missing += amt
            continue
        if not _tx_counts_mono_ytd(db_tx, skip_ids):
            continue
        stmt_matched += amt
        db_matched += float(db_tx.amount or 0)
    drift = round(stmt_matched - db_matched, 2)
    return round(missing, 2), drift


async def _excluded_category_ids(session: AsyncSession) -> set[int]:
    rows = await session.execute(
        select(Category.id).where(Category.name.in_([*CASH_MOVEMENT_CATEGORY_NAMES, "Богдан"]))
    )
    return {int(row[0]) for row in rows.all() if row[0] is not None}


async def _mono_transactions_map(
    session: AsyncSession,
    fop_id: int,
    date_from: datetime,
    date_to: datetime,
) -> dict[str, Transaction]:
    rows = await session.execute(
        select(Transaction).where(
            and_(
                Transaction.fop_id == fop_id,
                Transaction.source == TransactionSource.MONOBANK,
                Transaction.date >= date_from,
                Transaction.date <= date_to,
                Transaction.external_id.isnot(None),
            )
        )
    )
    out: dict[str, Transaction] = {}
    for tx in rows.scalars().all():
        if tx.external_id:
            out[str(tx.external_id)] = tx
    return out


async def _mono_external_id_map(session: AsyncSession, fop_id: int) -> dict[str, Transaction]:
    """Усі Mono-рядки ФОП по external_id (без фільтра дати — для звірки id)."""
    rows = await session.execute(
        select(Transaction).where(
            and_(
                Transaction.fop_id == fop_id,
                Transaction.source == TransactionSource.MONOBANK,
                Transaction.external_id.isnot(None),
            )
        )
    )
    out: dict[str, Transaction] = {}
    for tx in rows.scalars().all():
        if tx.external_id:
            out[str(tx.external_id)] = tx
    return out


async def _db_mono_income(
    session: AsyncSession,
    fop_id: int,
    date_from: datetime,
    date_to: datetime,
    *,
    skip_ids: set[int] | None = None,
    external_ids: set[str] | None = None,
) -> float:
    if skip_ids is None:
        skip_ids = await _excluded_category_ids(session)
    conditions = [
        Transaction.fop_id == fop_id,
        Transaction.source == TransactionSource.MONOBANK,
        Transaction.transaction_type == TransactionType.INCOME,
        Transaction.status == TransactionStatus.CONFIRMED,
        Transaction.date >= date_from,
        Transaction.date <= date_to,
    ]
    if external_ids is not None:
        if not external_ids:
            return 0.0
        conditions.append(Transaction.external_id.in_(external_ids))
    if skip_ids:
        conditions.append(
            (Transaction.category_id.is_(None)) | (Transaction.category_id.notin_(skip_ids))
        )
    value = (
        await session.execute(select(func.coalesce(func.sum(Transaction.amount), 0)).where(and_(*conditions)))
    ).scalar()
    return round(float(value or 0), 2)


async def reconcile_mono_ytd(session: AsyncSession) -> dict[str, Any]:
    """
    Жива виписка Моно YTD vs сума в БД.
    Показує дірки синку, через які Фінадмін занижує надходження.
    """
    from config import MONO_TOKENS, mono_iban_for_fop
    from mono.api import MonoStatementError, get_all_statements, get_client_info_retried

    date_from, date_to = calendar_year_to_date()
    from_time, to_time = mono_epoch_range(ytd=True)
    skip_ids = await _excluded_category_ids(session)

    fops_out: dict[str, Any] = {}
    for fop_name, token in MONO_TOKENS.items():
        if fop_name in INACTIVE_FOP_NAMES or not token:
            continue
        surname = fop_surname(fop_name)
        row = (await session.execute(select(FOP).where(FOP.name == fop_name))).scalar_one_or_none()
        mono_map = await _mono_external_id_map(session, row.id) if row else {}
        db_sum = await _db_mono_income(session, row.id, date_from, date_to, skip_ids=skip_ids) if row else 0.0
        entry: dict[str, Any] = {
            "fop_name": fop_name,
            "checked_at": now_kyiv().isoformat(timespec="minutes"),
            "mono_db": db_sum,
            "mono_live": None,
            "mono_gap": None,
            "error": None,
        }
        try:
            client_info, error_reason, _status = get_client_info_retried(token)
            if not client_info:
                raise MonoStatementError(error_reason or "немає client-info")
            preferred_iban = mono_iban_for_fop(fop_name)
            account = None
            if preferred_iban:
                account = next(
                    (a for a in client_info.get("accounts") or [] if (a.get("iban") or "").strip() == preferred_iban),
                    None,
                )
            if not account:
                account = next((a for a in client_info.get("accounts") or [] if a.get("type") == "fop"), None)
            if not account:
                raise MonoStatementError("немає рахунку type=fop")
            live_txs = await asyncio.to_thread(
                get_all_statements, token, account["id"], from_time, to_time
            )
            statement_ext_ids = {
                f"mono_{tx.get('id')}"
                for tx in live_txs
                if _mono_credit_amount(tx) is not None and tx.get("id") is not None
            }
            db_matched = await _db_mono_income(
                session,
                row.id,
                date_from,
                date_to,
                skip_ids=skip_ids,
                external_ids=statement_ext_ids,
            )
            live_sum = sum_mono_credits_aligned(live_txs, mono_map, skip_ids)
            missing_uah, amount_drift = mono_sync_missing_and_drift(live_txs, mono_map, skip_ids)
            entry["mono_live"] = live_sum
            entry["mono_db_matched"] = db_matched
            entry["mono_db_orphan"] = round(max(0.0, db_sum - db_matched), 2)
            entry["mono_amount_drift"] = amount_drift
            entry["statement_ops"] = len(statement_ext_ids)
            entry["mono_gap"] = missing_uah
        except Exception as exc:
            logger.warning("Mono YTD reconcile %s: %s", fop_name, exc)
            entry["error"] = str(exc)

        prev = fops_out.get(surname) or {
            "mono_db": 0.0,
            "mono_db_matched": 0.0,
            "mono_db_orphan": 0.0,
            "mono_amount_drift": 0.0,
            "mono_live": 0.0,
            "mono_gap": 0.0,
            "checked_at": entry["checked_at"],
            "error": None,
            "accounts": [],
        }
        prev["mono_db"] = round(float(prev["mono_db"] or 0) + db_sum, 2)
        prev["mono_db_matched"] = round(
            float(prev.get("mono_db_matched") or 0) + float(entry.get("mono_db_matched") or 0),
            2,
        )
        prev["mono_db_orphan"] = round(
            float(prev.get("mono_db_orphan") or 0) + float(entry.get("mono_db_orphan") or 0),
            2,
        )
        prev["mono_amount_drift"] = round(
            float(prev.get("mono_amount_drift") or 0) + float(entry.get("mono_amount_drift") or 0),
            2,
        )
        if entry["mono_live"] is not None:
            prev["mono_live"] = round(float(prev.get("mono_live") or 0) + float(entry["mono_live"]), 2)
            prev["mono_gap"] = round(float(prev.get("mono_gap") or 0) + float(entry.get("mono_gap") or 0), 2)
        if entry["error"]:
            prev["error"] = entry["error"]
        prev["checked_at"] = entry["checked_at"]
        prev.setdefault("accounts", []).append(entry)
        fops_out[surname] = prev

    payload = {
        "checked_at": now_kyiv().isoformat(timespec="minutes"),
        "from_iso": date_from.strftime("%Y-%m-%d"),
        "to_iso": date_to.strftime("%Y-%m-%d"),
        "fops": fops_out,
    }
    save_reconcile_cache(payload)
    return payload
