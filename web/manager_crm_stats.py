"""
CRM-статистика менеджерів: дії з клієнтами, журнал, зведення по команді.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import desc, func, or_, select

from mono.api import now_kyiv
from web.auth import ROLE_ADMIN
from web.client_crm import (
    CRM_CLOSURE_RESULT_CHOICES,
    CRM_HISTORY_ASSIGNMENT,
    CRM_HISTORY_CHANNELS,
    CRM_HISTORY_CLOSED,
    CRM_HISTORY_STATUS,
    CRM_STATUS_COMMUNICATED,
    CRM_STATUS_IN_PROGRESS,
    CRM_STATUS_NEUTRAL,
    deserialize_interaction_keys,
    interaction_labels_for_keys,
    processing_status_label,
)

if TYPE_CHECKING:
    from bot.database.clients_models import Client, ClientCrmHistory, ClientManagerNote


PERIOD_CHOICES: tuple[tuple[str, str], ...] = (
    ("7d", "7 днів"),
    ("30d", "30 днів"),
    ("all", "Увесь час"),
)

CRM_EVENT_TYPE_LABELS: dict[str, str] = {
    CRM_HISTORY_CLOSED: "Закриття ліда",
    CRM_HISTORY_STATUS: "Зміна статусу",
    CRM_HISTORY_CHANNELS: "Канали взаємодії",
    CRM_HISTORY_ASSIGNMENT: "Призначення відповідального",
}


def crm_event_type_label(event_type: str | None) -> str:
    key = (event_type or "").strip()
    return CRM_EVENT_TYPE_LABELS.get(key, key or "—")


def parse_period_key(raw: str | None) -> str:
    key = (raw or "30d").strip().lower()
    if key in {k for k, _ in PERIOD_CHOICES}:
        return key
    return "30d"


def period_days_for_key(key: str) -> int | None:
    if key == "7d":
        return 7
    if key == "30d":
        return 30
    return None


def period_cutoff(key: str):
    days = period_days_for_key(key)
    if days is None:
        return None
    return now_kyiv() - timedelta(days=days)


def period_label(key: str) -> str:
    return dict(PERIOD_CHOICES).get(key, key)


def manager_author_aliases(user) -> list[str]:
    """Усі можливі значення author_display для веб-користувача."""
    uname = (getattr(user, "username", None) or "").strip()
    display = (getattr(user, "display_name", None) or "").strip()
    role = getattr(user, "role", None)
    out: list[str] = []
    seen: set[str] = set()

    def add(val: str) -> None:
        v = (val or "").strip()
        if not v:
            return
        k = v.casefold()
        if k in seen:
            return
        seen.add(k)
        out.append(v)

    add(uname)
    add(display)
    if uname:
        add(f"Менеджер ({uname})")
        if role == ROLE_ADMIN:
            add(f"Адмін ({uname})")
    return out


def build_author_resolver(users: list) -> dict[str, int]:
    """author_display (casefold) → web user id."""
    resolver: dict[str, int] = {}
    for u in users:
        uid = int(getattr(u, "id", 0) or 0)
        if not uid:
            continue
        for alias in manager_author_aliases(u):
            resolver[alias.casefold()] = uid
    return resolver


def author_matches_aliases(author_display: str | None, aliases: list[str]) -> bool:
    raw = (author_display or "").strip()
    if not raw or not aliases:
        return False
    key = raw.casefold()
    return any(key == a.casefold() for a in aliases)


def empty_action_stats() -> dict[str, Any]:
    return {
        "status_changes": 0,
        "status_in_progress": 0,
        "status_communicated": 0,
        "status_to_neutral": 0,
        "channel_updates": 0,
        "closures": 0,
        "assignments": 0,
        "comments": 0,
        "total_actions": 0,
        "last_action_at": None,
        "closure_results": {k: 0 for k, _ in CRM_CLOSURE_RESULT_CHOICES},
    }


def _accumulate_history_event(stats: dict[str, Any], entry: "ClientCrmHistory") -> None:
    et = (entry.event_type or "").strip()
    stats["total_actions"] += 1
    created = getattr(entry, "created_at", None)
    if created and (stats["last_action_at"] is None or created > stats["last_action_at"]):
        stats["last_action_at"] = created

    if et == CRM_HISTORY_CLOSED:
        stats["closures"] += 1
        cr = (entry.closure_result or "").strip()
        if cr in stats["closure_results"]:
            stats["closure_results"][cr] += 1
    elif et == CRM_HISTORY_STATUS:
        stats["status_changes"] += 1
        st = (entry.processing_status or "").strip().lower()
        if st == CRM_STATUS_IN_PROGRESS:
            stats["status_in_progress"] += 1
        elif st == CRM_STATUS_COMMUNICATED:
            stats["status_communicated"] += 1
        elif st == CRM_STATUS_NEUTRAL:
            stats["status_to_neutral"] += 1
    elif et == CRM_HISTORY_CHANNELS:
        stats["channel_updates"] += 1
    elif et == CRM_HISTORY_ASSIGNMENT:
        stats["assignments"] += 1


def sql_match_author_string(column, aliases: list[str]):
    if not aliases:
        from sqlalchemy import false

        return false()
    norm = func.lower(func.trim(func.coalesce(column, "")))
    parts = [norm == a.casefold() for a in aliases]
    return or_(*parts) if len(parts) > 1 else parts[0]


def closure_result_bars_from_counts(counts: dict[str, int]) -> list[dict[str, Any]]:
    max_closure = max(counts.values(), default=0) or 1
    bars = []
    for k, label in CRM_CLOSURE_RESULT_CHOICES:
        c = int(counts.get(k, 0) or 0)
        bars.append(
            {
                "key": k,
                "label": label,
                "count": c,
                "pct_bar": round(100.0 * c / max_closure, 1) if max_closure else 0.0,
            }
        )
    return bars


async def load_assigned_portfolio_stats(
    csession,
    *,
    username: str,
    hidden_clause,
) -> dict[str, Any]:
    """Поточний портфель призначених клієнтів (без прив'язки до періоду)."""
    from bot.database.clients_models import Client

    assigned_total = 0
    clients_with_any_interaction = 0
    status_counts = {
        CRM_STATUS_NEUTRAL: 0,
        CRM_STATUS_IN_PROGRESS: 0,
        CRM_STATUS_COMMUNICATED: 0,
    }

    q_count = select(func.count()).select_from(Client).where(Client.crm_assigned_username == username)
    if hidden_clause is not None:
        q_count = q_count.where(hidden_clause)
    assigned_total = int((await csession.execute(q_count)).scalar() or 0)

    q_keys = select(Client.crm_interaction_keys).where(Client.crm_assigned_username == username)
    if hidden_clause is not None:
        q_keys = q_keys.where(hidden_clause)
    for raw in (await csession.execute(q_keys)).scalars().all():
        if deserialize_interaction_keys(raw):
            clients_with_any_interaction += 1

    q_status = select(Client.crm_processing_status).where(Client.crm_assigned_username == username)
    if hidden_clause is not None:
        q_status = q_status.where(hidden_clause)
    for st in (await csession.execute(q_status)).scalars().all():
        key = (st or CRM_STATUS_NEUTRAL).strip().lower() or CRM_STATUS_NEUTRAL
        if key in status_counts:
            status_counts[key] += 1
        else:
            status_counts[CRM_STATUS_NEUTRAL] += 1

    return {
        "assigned_total": assigned_total,
        "clients_with_any_interaction": clients_with_any_interaction,
        "status_counts": status_counts,
    }


async def load_manager_action_stats(
    csession,
    *,
    aliases: list[str],
    cutoff,
    hidden_clause=None,
    only_assigned_username: str | None = None,
) -> dict[str, Any]:
    """Підрахунок CRM-подій за автором (повна вибірка за період, не обмежена 60 записами)."""
    from bot.database.clients_models import Client, ClientCrmHistory, ClientManagerNote

    stats = empty_action_stats()
    author_match = sql_match_author_string(ClientCrmHistory.author_display, aliases)

    q_hist = (
        select(ClientCrmHistory)
        .join(Client, Client.customer_id == ClientCrmHistory.client_customer_id)
        .where(author_match)
    )
    if hidden_clause is not None:
        q_hist = q_hist.where(hidden_clause)
    if cutoff is not None:
        q_hist = q_hist.where(ClientCrmHistory.created_at >= cutoff)
    if only_assigned_username:
        q_hist = q_hist.where(Client.crm_assigned_username == only_assigned_username)

    for entry in (await csession.execute(q_hist)).scalars().all():
        _accumulate_history_event(stats, entry)

    q_notes = (
        select(func.count())
        .select_from(ClientManagerNote)
        .where(sql_match_author_string(ClientManagerNote.author_display, aliases))
    )
    if cutoff is not None:
        q_notes = q_notes.where(ClientManagerNote.created_at >= cutoff)
    if only_assigned_username:
        cust_sub = select(Client.customer_id).where(Client.crm_assigned_username == only_assigned_username)
        if hidden_clause is not None:
            cust_sub = cust_sub.where(hidden_clause)
        q_notes = q_notes.where(ClientManagerNote.client_customer_id.in_(cust_sub))

    stats["comments"] = int((await csession.execute(q_notes)).scalar() or 0)
    stats["total_actions"] += stats["comments"]

    q_last_note = (
        select(func.max(ClientManagerNote.created_at))
        .select_from(ClientManagerNote)
        .where(sql_match_author_string(ClientManagerNote.author_display, aliases))
    )
    if cutoff is not None:
        q_last_note = q_last_note.where(ClientManagerNote.created_at >= cutoff)
    if only_assigned_username:
        cust_sub = select(Client.customer_id).where(Client.crm_assigned_username == only_assigned_username)
        if hidden_clause is not None:
            cust_sub = cust_sub.where(hidden_clause)
        q_last_note = q_last_note.where(ClientManagerNote.client_customer_id.in_(cust_sub))
    last_note_at = (await csession.execute(q_last_note)).scalar()
    if last_note_at and (stats["last_action_at"] is None or last_note_at > stats["last_action_at"]):
        stats["last_action_at"] = last_note_at

    return stats


async def load_manager_activity_log(
    csession,
    *,
    aliases: list[str],
    cutoff,
    hidden_clause=None,
    limit: int = 80,
) -> list[dict[str, Any]]:
    """Журнал дій менеджера по всіх клієнтах."""
    from bot.database.clients_models import Client, ClientCrmHistory

    author_match = sql_match_author_string(ClientCrmHistory.author_display, aliases)
    q = (
        select(ClientCrmHistory, Client)
        .join(Client, Client.customer_id == ClientCrmHistory.client_customer_id)
        .where(author_match)
        .order_by(desc(ClientCrmHistory.created_at))
        .limit(limit)
    )
    if hidden_clause is not None:
        q = q.where(hidden_clause)
    if cutoff is not None:
        q = q.where(ClientCrmHistory.created_at >= cutoff)

    rows = []
    for entry, client in (await csession.execute(q)).all():
        keys = deserialize_interaction_keys(entry.interaction_keys)
        name = f"{(client.firstname or '').strip()} {(client.lastname or '').strip()}".strip() or "—"
        rows.append(
            {
                "entry": entry,
                "client": client,
                "client_id": client.id,
                "client_name": name,
                "client_phone": (client.telephone or "").strip() or "—",
                "event_label": crm_event_type_label(entry.event_type),
                "interaction_labels": interaction_labels_for_keys(keys),
            }
        )
    return rows


async def load_team_manager_stats(
    csession,
    users: list,
    *,
    hidden_clause,
    period_key: str,
) -> list[dict[str, Any]]:
    """Зведена статистика по кожному менеджеру для списку /managers."""
    from bot.database.clients_models import Client, ClientCrmHistory, ClientManagerNote

    cutoff = period_cutoff(period_key)
    resolver = build_author_resolver(users)
    per_user: dict[int, dict[str, Any]] = {}

    for u in users:
        uid = int(u.id)
        display = ((u.display_name or "").strip() or (u.username or "").strip() or "—")
        portfolio = await load_assigned_portfolio_stats(csession, username=(u.username or "").strip(), hidden_clause=hidden_clause)
        per_user[uid] = {
            "user": u,
            "user_id": uid,
            "display": display,
            "username": (u.username or "").strip(),
            "role": getattr(u, "role", None),
            "portfolio": portfolio,
            "actions": empty_action_stats(),
        }

    q_hist = select(ClientCrmHistory).join(
        Client, Client.customer_id == ClientCrmHistory.client_customer_id
    )
    if hidden_clause is not None:
        q_hist = q_hist.where(hidden_clause)
    if cutoff is not None:
        q_hist = q_hist.where(ClientCrmHistory.created_at >= cutoff)

    for entry in (await csession.execute(q_hist)).scalars().all():
        author_key = (entry.author_display or "").strip().casefold()
        uid = resolver.get(author_key)
        if not uid or uid not in per_user:
            continue
        _accumulate_history_event(per_user[uid]["actions"], entry)

    q_notes = (
        select(ClientManagerNote.author_display, func.count(), func.max(ClientManagerNote.created_at))
        .group_by(ClientManagerNote.author_display)
    )
    if cutoff is not None:
        q_notes = q_notes.where(ClientManagerNote.created_at >= cutoff)
    for author, cnt, last_note_at in (await csession.execute(q_notes)).all():
        uid = resolver.get((author or "").strip().casefold())
        if not uid or uid not in per_user:
            continue
        per_user[uid]["actions"]["comments"] += int(cnt or 0)
        per_user[uid]["actions"]["total_actions"] += int(cnt or 0)
        if last_note_at:
            cur = per_user[uid]["actions"]["last_action_at"]
            if cur is None or last_note_at > cur:
                per_user[uid]["actions"]["last_action_at"] = last_note_at

    team = list(per_user.values())
    team.sort(
        key=lambda r: (
            -(r["actions"]["total_actions"] or 0),
            -(r["portfolio"]["assigned_total"] or 0),
            r["display"].casefold(),
        )
    )
    return team


async def load_assigned_clients_history(
    csession,
    *,
    username: str,
    hidden_clause,
    limit: int = 60,
) -> list[dict[str, Any]]:
    """Історія CRM по призначених клієнтах (усі автори)."""
    from bot.database.clients_models import Client, ClientCrmHistory

    q_hist = (
        select(ClientCrmHistory, Client.id.label("crm_web_client_id"))
        .join(Client, Client.customer_id == ClientCrmHistory.client_customer_id)
        .where(Client.crm_assigned_username == username)
        .order_by(desc(ClientCrmHistory.created_at))
        .limit(limit)
    )
    if hidden_clause is not None:
        q_hist = q_hist.where(hidden_clause)
    rows = []
    for entry, cid in (await csession.execute(q_hist)).all():
        rows.append({"entry": entry, "client_id": cid, "by_manager": False})
    return rows
