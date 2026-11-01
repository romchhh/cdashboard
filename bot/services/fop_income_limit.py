"""Річний ліміт надходжень ФОП + звірка Моно з БД."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import asyncio

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
from bot.services.period_range import calendar_year_to_date, day_start
from bot.services.reports import CASH_MOVEMENT_CATEGORY_NAMES
from config import FOP_ANNUAL_INCOME_LIMIT_UAH, INACTIVE_FOP_NAMES, ROOT_DIR
from mono.api import KYIV_TZ, format_amount, now_kyiv

logger = logging.getLogger(__name__)

RECONCILE_PATH = Path(ROOT_DIR) / "databases" / "fop_income_reconcile.json"
WARN_DELTA_UAH = 1.0


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
        item["mono_gap"] = recon.get("mono_gap")
        item["reconcile_at"] = recon.get("checked_at")
        item["reconcile_error"] = recon.get("error")
        item["sync_gap"] = abs(float(item["mono_gap"] or 0)) >= WARN_DELTA_UAH
        out.append(item)
    out.sort(key=lambda x: (-x["over_limit"], -x["used_pct"], x["name"]))
    return out


def summarize_fop_limits(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Підсумки по активних ФОПах для рядка «залишок разом»."""
    total_remaining = round(sum(float(row.get("remaining") or 0) for row in rows), 2)
    return {
        "total_remaining": total_remaining,
        "total_overlimit": round(max(0.0, -total_remaining), 2),
    }


def _sum_mono_credits(transactions: list[dict]) -> float:
    total = 0.0
    for tx in transactions:
        raw = float(tx.get("amount") or 0)
        if raw > 0:
            total += abs(format_amount(raw))
    return round(total, 2)


async def _db_mono_income(
    session: AsyncSession,
    fop_id: int,
    date_from: datetime,
    date_to: datetime,
) -> float:
    skip_ids = [
        row[0]
        for row in (
            await session.execute(
                select(Category.id).where(Category.name.in_([*CASH_MOVEMENT_CATEGORY_NAMES, "Богдан"]))
            )
        ).all()
        if row[0] is not None
    ]
    conditions = [
        Transaction.fop_id == fop_id,
        Transaction.source == TransactionSource.MONOBANK,
        Transaction.transaction_type == TransactionType.INCOME,
        Transaction.status == TransactionStatus.CONFIRMED,
        Transaction.date >= date_from,
        Transaction.date <= date_to,
    ]
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
    from_dt = day_start(date_from).replace(tzinfo=KYIV_TZ)
    to_dt = date_to.replace(tzinfo=KYIV_TZ)
    from_time = int(from_dt.timestamp())
    to_time = int(to_dt.timestamp())

    fops_out: dict[str, Any] = {}
    for fop_name, token in MONO_TOKENS.items():
        if fop_name in INACTIVE_FOP_NAMES or not token:
            continue
        surname = fop_surname(fop_name)
        row = (await session.execute(select(FOP).where(FOP.name == fop_name))).scalar_one_or_none()
        db_sum = await _db_mono_income(session, row.id, date_from, date_to) if row else 0.0
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
            live_sum = _sum_mono_credits(live_txs)
            entry["mono_live"] = live_sum
            entry["mono_gap"] = round(live_sum - db_sum, 2)
        except Exception as exc:
            logger.warning("Mono YTD reconcile %s: %s", fop_name, exc)
            entry["error"] = str(exc)

        prev = fops_out.get(surname) or {
            "mono_db": 0.0,
            "mono_live": 0.0,
            "mono_gap": 0.0,
            "checked_at": entry["checked_at"],
            "error": None,
            "accounts": [],
        }
        prev["mono_db"] = round(float(prev["mono_db"] or 0) + db_sum, 2)
        if entry["mono_live"] is not None:
            prev["mono_live"] = round(float(prev.get("mono_live") or 0) + float(entry["mono_live"]), 2)
            prev["mono_gap"] = round(float(prev["mono_live"]) - float(prev["mono_db"]), 2)
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
