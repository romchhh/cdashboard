"""Фільтр ФОП: активні записи з БД, окремий чекбокс на кожне джерело (N / M)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, or_, select

from bot.database.models import FOP, Transaction, TransactionSource

_SOURCE_MAP = {
    "novapay": TransactionSource.NOVAPAY,
    "monobank": TransactionSource.MONOBANK,
}
_SOURCE_ORDER = {"novapay": 0, "monobank": 1}
_TOKEN_TO_SOURCE = {"n": "novapay", "m": "monobank"}
_SOURCE_TO_TOKEN = {"novapay": "n", "monobank": "m"}


def fop_surname(name: str | None) -> str:
    """Прізвище / перше слово назви ФОП для групування однієї людини."""
    return ((name or "").strip().split() or ["—"])[0]


def parse_fop_filter_selection(raw: list[str] | None) -> tuple[bool, list[str]]:
    """Повертає (include_cash, інші значення фільтра)."""
    items = [str(x).strip() for x in (raw or []) if str(x).strip()]
    include_cash = "cash" in items
    values = [x for x in items if x != "cash"]
    return include_cash, values


def expand_fop_filter_values(values: list[str]) -> list[str]:
    """Розгортає групові значення g:fop:src|... у окремі scope-токени."""
    expanded: list[str] = []
    for value in values:
        if value.startswith("g:"):
            expanded.extend(part for part in value[2:].split("|") if part)
        else:
            expanded.append(value)
    return expanded


def encode_fop_filter_token(value: str) -> str:
    """Короткий токен для Telegram callback_data (legacy id / id:source)."""
    if value == "cash":
        return "cash"
    if value.startswith("g:"):
        return value
    if ":" in value:
        fid_part, src_key = value.split(":", 1)
        letter = _SOURCE_TO_TOKEN.get(src_key.strip().lower())
        if fid_part.isdigit() and letter:
            return f"{fid_part}{letter}"
    return value


def decode_fop_filter_token(token: str) -> str:
    """Зворотне перетворення токена з callback_data у значення фільтра."""
    if token == "cash" or token.startswith("g:"):
        return token
    if token.isdigit():
        return token
    if len(token) >= 2 and token[:-1].isdigit():
        src = _TOKEN_TO_SOURCE.get(token[-1].lower())
        if src:
            return f"{token[:-1]}:{src}"
    return token


def transaction_fop_scope_condition(raw_fop_ids: list[str] | None):
    """
    SQLAlchemy-умова для Transaction за значеннями чекбоксів:
    cash | g:... | {fop_id} | {fop_id}:novapay | {fop_id}:monobank
    """
    include_cash, values = parse_fop_filter_selection(raw_fop_ids)
    if not include_cash and not values:
        return None

    clauses = []
    for value in expand_fop_filter_values(values):
        if ":" in value:
            fid_part, src_key = value.split(":", 1)
            if not fid_part.isdigit():
                continue
            src = _SOURCE_MAP.get(src_key.strip().lower())
            if not src:
                continue
            clauses.append(
                and_(Transaction.fop_id == int(fid_part), Transaction.source == src)
            )
        elif value.isdigit():
            clauses.append(Transaction.fop_id == int(value))

    if include_cash:
        clauses.append(Transaction.source == TransactionSource.CASH)

    if not clauses:
        return None
    return or_(*clauses) if len(clauses) > 1 else clauses[0]


async def get_fop_sources(
    session,
    fop_ids: list[int],
    *,
    env_only: bool = False,
) -> dict[int, list[str]]:
    """Джерела ФОП з полів БД; опційно доповнюємо з транзакцій."""
    if not fop_ids:
        return {}
    from bot.services.fop_config import sources_from_fop_row

    rows = (
        await session.execute(select(FOP).where(FOP.id.in_(fop_ids)))
    ).scalars().all()
    out: dict[int, list[str]] = {int(fid): [] for fid in fop_ids}
    for fop in rows:
        fid = int(fop.id)
        for src in sources_from_fop_row(fop):
            bucket = out.setdefault(fid, [])
            if src not in bucket:
                bucket.append(src)

    if env_only:
        for fid, srcs in out.items():
            out[fid] = sorted(srcs, key=lambda s: _SOURCE_ORDER.get(s, 99))
        return out

    nova_result = await session.execute(
        select(Transaction.fop_id)
        .where(
            Transaction.fop_id.in_(fop_ids),
            Transaction.source == TransactionSource.NOVAPAY,
        )
        .distinct()
    )
    for (fop_id,) in nova_result.all():
        if fop_id is None:
            continue
        bucket = out.setdefault(int(fop_id), [])
        if "novapay" not in bucket:
            bucket.append("novapay")

    mono_result = await session.execute(
        select(Transaction.fop_id)
        .where(
            Transaction.fop_id.in_(fop_ids),
            Transaction.source == TransactionSource.MONOBANK,
        )
        .distinct()
    )
    for (fop_id,) in mono_result.all():
        if fop_id is None:
            continue
        bucket = out.setdefault(int(fop_id), [])
        if "monobank" not in bucket:
            bucket.append("monobank")

    for fid, srcs in out.items():
        out[fid] = sorted(srcs, key=lambda s: _SOURCE_ORDER.get(s, 99))
    return out


async def load_fop_filter_options(session) -> list[dict[str, Any]]:
    """ФОПи для фільтра: активні + архівні з транзакціями (окремий чекбокс N/M)."""
    from bot.keyboards.inline import get_fops_for_filters

    fops = await get_fops_for_filters(session)
    archived_ids = {int(f.id) for f in fops if not f.is_active}
    fop_sources = await get_fop_sources(session, [f.id for f in fops], env_only=False)
    return build_fop_filter_options(fops, fop_sources, archived_ids=archived_ids)


async def resolve_fop_filter_for_totals(
    session,
    raw_fop_ids: list[str] | None,
    *,
    include_cash: bool = False,
) -> list[str] | None:
    """
    Нормалізує фільтр для підсумків доходів/витрат.
    Порожній список або «усі активні ФОПи» → None (усі транзакції, включно з архівом).
    """
    if not raw_fop_ids:
        return None

    values = reports_fop_filter_values(raw_fop_ids, include_cash)
    if not values:
        return None

    _, fop_values = parse_fop_filter_selection(values)
    if not fop_values:
        return values

    from bot.keyboards.inline import get_fop_list

    active_fops = await get_fop_list(session)
    if not active_fops:
        return values

    active_sources = await get_fop_sources(session, [f.id for f in active_fops], env_only=False)
    active_option_values = {o["value"] for o in build_fop_filter_options(active_fops, active_sources)}
    selected = set(fop_values)

    if active_option_values and selected >= active_option_values:
        return None

    return values


def fop_display_name(name: str | None) -> str:
    return (name or "").strip() or "—"


def fop_filter_option_label(opt: dict[str, Any]) -> str:
    """Підпис пункту фільтра з бейджами N/M."""
    label = str(opt.get("label") or "—")
    sources = opt.get("sources") or []
    if not sources and opt.get("source"):
        sources = [opt["source"]]
    badges = []
    if "novapay" in sources:
        badges.append("N")
    if "monobank" in sources:
        badges.append("M")
    return f"{label} {' '.join(badges)}".strip() if badges else label


def report_fop_filter_label_from_options(
    fop_filter_options: list[dict[str, Any]] | None,
    selected_values: list[str] | None,
    include_cash: bool,
) -> str:
    if not selected_values:
        return "Усі ФОПи та готівка"
    by_value = {str(opt["value"]): opt for opt in (fop_filter_options or [])}
    parts: list[str] = []
    for value in selected_values:
        if value == "cash":
            parts.append("Готівка")
            continue
        opt = by_value.get(value)
        if opt:
            parts.append(fop_filter_option_label(opt))
        else:
            parts.append(value)
    if not parts:
        return "Лише готівка" if include_cash else "Обрані ФОПи"
    label = ", ".join(parts)
    if include_cash and "cash" not in selected_values:
        label += " + готівка"
    return label


def fop_labels_by_id(fops: list[FOP]) -> dict[int, str]:
    """Підпис для UI; при однакових назвах додаємо #id."""
    from collections import Counter

    names = [fop_display_name(f.name) for f in fops]
    counts = Counter(names)
    labels: dict[int, str] = {}
    for fop in fops:
        fid = int(fop.id)
        base = fop_display_name(fop.name)
        labels[fid] = f"{base} (#{fid})" if counts[base] > 1 else base
    return labels


def build_fop_filter_options(
    fops: list[FOP],
    fop_sources: dict[int, list[str]],
    *,
    archived_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    """
    Окремий чекбокс на кожен запис ФОП і джерело (N / M).
    Два записи з одним прізвищем — різні пункти; один запис з N+M — теж два пункти.
    """
    archived_ids = archived_ids or set()
    options: list[dict[str, Any]] = []
    labels = fop_labels_by_id(fops)
    for fop in sorted(fops, key=lambda row: (row.name or "").casefold()):
        fid = int(fop.id)
        label = labels[fid]
        if fid in archived_ids:
            label = f"{label} (архів)"
        srcs = sorted(
            list(fop_sources.get(fid) or []),
            key=lambda s: _SOURCE_ORDER.get(s, 99),
        )
        if not srcs:
            options.append(
                {
                    "value": str(fid),
                    "label": label,
                    "source": None,
                    "sources": [],
                    "fop_id": fid,
                    "is_archived": fid in archived_ids,
                }
            )
            continue
        for src in srcs:
            options.append(
                {
                    "value": f"{fid}:{src}",
                    "label": label,
                    "source": src,
                    "sources": [src],
                    "fop_id": fid,
                    "is_archived": fid in archived_ids,
                }
            )
    return options


def reports_fop_filter_values(raw_fop_ids: list[str] | None, include_cash: bool) -> list[str] | None:
    values = [str(x).strip() for x in (raw_fop_ids or []) if str(x).strip()]
    if include_cash:
        values = ["cash"] + values
    return values if values else None


def fop_filter_values_to_legacy_ids(raw: list[str] | None) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    _, values = parse_fop_filter_selection(raw)
    for value in expand_fop_filter_values(values):
        if ":" in value:
            fid_part = value.split(":", 1)[0]
        else:
            fid_part = value
        if fid_part.isdigit():
            fid = int(fid_part)
            if fid not in seen:
                seen.add(fid)
                ids.append(fid)
    return ids
