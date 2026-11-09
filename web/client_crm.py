"""
CRM-поля клієнта (менеджерські): статус опрацювання, канали взаємодії, історія.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import Request

    from bot.database.clients_models import Client, ClientCrmHistory


def apply_client_last_touch(client: "Client", request: "Request") -> None:
    """Оновити системне поле «останній менеджер» після дії в CRM / коментарі."""
    from mono.api import now_kyiv
    from web.auth import get_operator_display

    client.crm_last_touched_by = get_operator_display(request)
    client.crm_last_touched_at = now_kyiv()


# Статус «чи забрав останнє замовлення»
CRM_LAST_ORDER_UNKNOWN = "unknown"
CRM_LAST_ORDER_PICKED_UP = "picked_up"
CRM_LAST_ORDER_NOT_PICKED_UP = "not_picked_up"

CRM_LAST_ORDER_CHOICES: tuple[tuple[str, str], ...] = (
    (CRM_LAST_ORDER_UNKNOWN, "Невідомо"),
    (CRM_LAST_ORDER_PICKED_UP, "Забрав"),
    (CRM_LAST_ORDER_NOT_PICKED_UP, "Не забрав"),
)

# Статуси ручного опрацювання ліда
CRM_STATUS_NEUTRAL = "neutral"
CRM_STATUS_IN_PROGRESS = "in_progress"
CRM_STATUS_COMMUNICATED = "communicated"
CRM_STATUS_CLOSED = "closed"

CRM_PROCESSING_STATUS_CHOICES: tuple[tuple[str, str], ...] = (
    (CRM_STATUS_NEUTRAL, "Нейтральний"),
    (CRM_STATUS_IN_PROGRESS, "В роботі"),
    (CRM_STATUS_COMMUNICATED, "Прокомуніковано"),
    (CRM_STATUS_CLOSED, "Закрито"),
)

CRM_PROCESSING_ACTIVE_STATUSES: tuple[tuple[str, str], ...] = (
    (CRM_STATUS_IN_PROGRESS, "В роботі"),
    (CRM_STATUS_COMMUNICATED, "Прокомуніковано"),
    (CRM_STATUS_CLOSED, "Закрито"),
)

CRM_PROCESSING_STATUS_ALLOWED = frozenset(k for k, _ in CRM_PROCESSING_STATUS_CHOICES)

# Результати взаємодії при закритті
CRM_CLOSURE_ORDER_PLACED = "order_placed"
CRM_CLOSURE_DEFERRED = "deferred_demand"
CRM_CLOSURE_DECLINED = "declined"
CRM_CLOSURE_NO_RESPONSE = "no_response"
CRM_CLOSURE_NOT_FOUND = "not_found"

CRM_CLOSURE_RESULT_CHOICES: tuple[tuple[str, str], ...] = (
    (CRM_CLOSURE_ORDER_PLACED, "Замовлення оформлено"),
    (CRM_CLOSURE_DEFERRED, "Відкладений попит"),
    (CRM_CLOSURE_DECLINED, "Відмовився від покупки"),
    (CRM_CLOSURE_NO_RESPONSE, "Не відповів"),
    (CRM_CLOSURE_NOT_FOUND, "Не знайдено"),
)

CRM_CLOSURE_RESULTS_ALLOWED = frozenset(k for k, _ in CRM_CLOSURE_RESULT_CHOICES)

# Прямий контакт менеджера з клієнтом
INTERACTION_OPTIONS_DIRECT: tuple[tuple[str, str], ...] = (
    ("direct_contact", "Прямий контакт менеджера (дзвінок / чат)"),
    ("instagram_dm", "Прямий контакт Instagram"),
    ("followup_order", "Follow-up після замовлення (нагадування / допродаж)"),
)

# Масові канали (розсилки, боти, реклама)
INTERACTION_OPTIONS_MASS: tuple[tuple[str, str], ...] = (
    ("viber_broadcast", "Viber-розсилка"),
    ("chatbot", "Чат-бот (Telegram)"),
    ("targeted_meta", "Таргетована реклама (Meta / Instagram / Facebook)"),
    ("email_broadcast", "Email-розсилка"),
)

# Зворотна сумісність у статистиці менеджера
INTERACTION_OPTIONS_AUTO: tuple[tuple[str, str], ...] = INTERACTION_OPTIONS_MASS
INTERACTION_OPTIONS_MANUAL: tuple[tuple[str, str], ...] = INTERACTION_OPTIONS_DIRECT

INTERACTION_OPTIONS: tuple[tuple[str, str], ...] = INTERACTION_OPTIONS_DIRECT + INTERACTION_OPTIONS_MASS

INTERACTION_KEYS_ALLOWED = frozenset(k for k, _ in INTERACTION_OPTIONS)

INTERACTION_LABEL_SHORT: dict[str, str] = {
    "direct_contact": "Дзвінок / чат",
    "instagram_dm": "Instagram",
    "followup_order": "Follow-up",
    "viber_broadcast": "Viber",
    "chatbot": "Telegram-бот",
    "targeted_meta": "Meta / Facebook",
    "email_broadcast": "Email",
}


def interaction_short_label(key: str, full_label: str) -> str:
    return INTERACTION_LABEL_SHORT.get(str(key or "").strip(), full_label)


CRM_HISTORY_CLOSED = "closed"
CRM_HISTORY_STATUS = "status_change"
CRM_HISTORY_CHANNELS = "channel_set"
CRM_HISTORY_ASSIGNMENT = "assignment"


def normalize_processing_status(raw: str | None) -> str:
    v = (raw or "").strip().lower()
    if v in CRM_PROCESSING_STATUS_ALLOWED:
        return v
    return CRM_STATUS_NEUTRAL


def normalize_closure_result(raw: str | None) -> str | None:
    v = (raw or "").strip().lower()
    if v in CRM_CLOSURE_RESULTS_ALLOWED:
        return v
    return None


def processing_status_label(status: str | None) -> str:
    key = normalize_processing_status(status)
    return dict(CRM_PROCESSING_STATUS_CHOICES).get(key, key)


def closure_result_label(result: str | None) -> str:
    if not result:
        return "—"
    return dict(CRM_CLOSURE_RESULT_CHOICES).get(result, result)


def closure_result_tone(result: str | None) -> str:
    """Колірний тон для UI: success / warn / danger / muted."""
    key = (result or "").strip().lower()
    if key == CRM_CLOSURE_ORDER_PLACED:
        return "success"
    if key == CRM_CLOSURE_DEFERRED:
        return "warn"
    if key in (CRM_CLOSURE_DECLINED, CRM_CLOSURE_NO_RESPONSE, CRM_CLOSURE_NOT_FOUND):
        return "danger"
    return "muted"


def crm_history_event_label(entry: Any) -> str:
    """Короткий підпис для рядка історії в таблиці клієнтів."""
    et = (getattr(entry, "event_type", None) or "").strip()
    if et == CRM_HISTORY_CLOSED:
        return closure_result_label(getattr(entry, "closure_result", None))
    if et == CRM_HISTORY_STATUS:
        return processing_status_label(getattr(entry, "processing_status", None))
    if et == CRM_HISTORY_CHANNELS:
        return "Канали оновлено"
    if et == CRM_HISTORY_ASSIGNMENT:
        who = (getattr(entry, "assigned_username", None) or "").strip()
        return f"Відп.: {who}" if who else "Знято відповідального"
    return et or "подія"


def format_crm_history_items(entries: list[Any], *, limit: int = 4) -> list[dict[str, str]]:
    """Список {label, date, title, tone} для UI; новіші першими."""
    out: list[dict[str, str]] = []
    for entry in entries[:limit]:
        created = getattr(entry, "created_at", None)
        date_s = created.strftime("%d.%m.%Y") if created else "—"
        label = crm_history_event_label(entry)
        author = (getattr(entry, "author_display", None) or "").strip()
        tip_parts = [label, date_s]
        if author:
            tip_parts.append(author)
        et = (getattr(entry, "event_type", None) or "").strip()
        tone = (
            closure_result_tone(getattr(entry, "closure_result", None))
            if et == CRM_HISTORY_CLOSED
            else "muted"
        )
        out.append(
            {
                "label": label,
                "date": date_s,
                "title": " · ".join(tip_parts),
                "tone": tone,
            }
        )
    return out


def interaction_keys_from_raw_list(raw: list[Any]) -> list[str]:
    out: list[str] = []
    for x in raw or []:
        s = str(x).strip()
        if s in INTERACTION_KEYS_ALLOWED and s not in out:
            out.append(s)
    return out


def serialize_interaction_keys(keys: list[str]) -> str | None:
    cleaned = interaction_keys_from_raw_list(keys)
    if not cleaned:
        return None
    return json.dumps(cleaned, ensure_ascii=False)


def deserialize_interaction_keys(raw: str | None) -> list[str]:
    if not raw or not str(raw).strip():
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if isinstance(data, list):
        return interaction_keys_from_raw_list(data)
    return []


def interaction_labels_for_keys(keys: list[str]) -> list[str]:
    label_by = dict(INTERACTION_OPTIONS)
    return [label_by[k] for k in keys if k in label_by]


def _client_snapshot(client: "Client") -> dict[str, Any]:
    return {
        "processing_status": normalize_processing_status(getattr(client, "crm_processing_status", None)),
        "interaction_keys": deserialize_interaction_keys(getattr(client, "crm_interaction_keys", None)),
        "assigned_username": (getattr(client, "crm_assigned_username", None) or "").strip() or None,
    }


def reset_client_work_fields(client: "Client") -> None:
    """Повернути робочі поля до початкового стану після закриття ліда."""
    client.crm_processing_status = CRM_STATUS_NEUTRAL
    client.crm_interaction_keys = None


def record_crm_history(
    session,
    *,
    client: "Client",
    event_type: str,
    author_display: str,
    processing_status: str | None = None,
    closure_result: str | None = None,
    interaction_keys: list[str] | None = None,
    assigned_username: str | None = None,
    note: str | None = None,
) -> "ClientCrmHistory":
    from mono.api import now_kyiv
    from bot.database.clients_models import ClientCrmHistory

    snap = _client_snapshot(client)
    entry = ClientCrmHistory(
        client_customer_id=client.customer_id,
        event_type=event_type,
        processing_status=processing_status or snap["processing_status"],
        closure_result=closure_result,
        interaction_keys=serialize_interaction_keys(interaction_keys if interaction_keys is not None else snap["interaction_keys"]),
        assigned_username=assigned_username if assigned_username is not None else snap["assigned_username"],
        author_display=author_display,
        note=(note or "").strip() or None,
        created_at=now_kyiv(),
    )
    session.add(entry)
    return entry


def apply_client_interaction_keys(client: "Client", keys: list[str]) -> None:
    client.crm_interaction_keys = serialize_interaction_keys(keys)


def apply_processing_status_change(
    session,
    *,
    client: "Client",
    new_status: str,
    author_display: str,
    closure_result: str | None = None,
) -> str | None:
    """
    Змінити статус опрацювання. При «Закрито» — зберегти історію та скинути робочі поля.
    Повертає код помилки або None при успіху.
    """
    status = normalize_processing_status(new_status)
    if status == CRM_STATUS_NEUTRAL:
        client.crm_processing_status = CRM_STATUS_NEUTRAL
        record_crm_history(
            session,
            client=client,
            event_type=CRM_HISTORY_STATUS,
            author_display=author_display,
            processing_status=CRM_STATUS_NEUTRAL,
        )
        return None

    if status == CRM_STATUS_CLOSED:
        result = normalize_closure_result(closure_result)
        if not result:
            return "closure_required"
        record_crm_history(
            session,
            client=client,
            event_type=CRM_HISTORY_CLOSED,
            author_display=author_display,
            processing_status=CRM_STATUS_CLOSED,
            closure_result=result,
        )
        reset_client_work_fields(client)
        return None

    client.crm_processing_status = status
    record_crm_history(
        session,
        client=client,
        event_type=CRM_HISTORY_STATUS,
        author_display=author_display,
        processing_status=status,
    )
    return None
