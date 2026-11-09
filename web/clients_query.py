"""Фільтри, сортування та допоміжні запити для розділу клієнтів."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlencode

from fastapi import HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import nullslast, or_, select
from sqlalchemy.orm import selectinload

from bot.database.clients_models import Client, ClientOrder
from bot.database.engine import AsyncSessionLocal
from bot.database.models import WebUser
from config import CLIENTS_EXPORT_EXCLUDED_CUSTOMER_IDS
from mono.api import now_kyiv
from web.client_marketing import (
    build_client_where_clauses,
    canonical_geo_city_filter_label,
    parse_client_filter_dates,
    reference_now,
)
from web.constants import CLIENTS_BULK_ASSIGN_MAX, CLIENTS_PER_PAGE

__all__ = [
    "CLIENTS_BULK_ASSIGN_MAX",
    "CLIENTS_PER_PAGE",
    "_download_response",
    "_clients_apply_sort_order",
    "_clients_apply_visibility_filters",
    "_clients_assigned_filter_for_clauses",
    "_clients_build_sort_urls",
    "_clients_chip_query_strings",
    "_clients_coerce_sort_params",
    "_clients_default_sort_dir",
    "_clients_export_filename",
    "_clients_filter_clauses_from_multidict",
    "_clients_filter_clauses_from_request",
    "_clients_get_by_id_or_404",
    "_clients_hidden_where",
    "_clients_last_order_manager_filter_for_clauses",
    "_clients_marketing_managers_list",
    "_clients_parse_bad_clients_filter",
    "_clients_parse_email_mode",
    "_clients_parse_gender_filter",
    "_clients_parse_optional_float",
    "_clients_parse_optional_int",
    "_clients_parse_registered_filter",
    "_clients_parse_sort_params",
    "_clients_qs_with_patches",
    "_clients_safe_return_query",
    "_clients_urlencode_params",
]


async def _clients_marketing_managers_list() -> list[WebUser]:
    """Користувачі веб-панелі з доступом до маркетингу (для призначення клієнтів)."""
    from web.auth import ROLE_ADMIN

    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(WebUser)
            .where(
                WebUser.is_active.is_(True),
                or_(WebUser.role == ROLE_ADMIN, WebUser.access_marketing.is_(True)),
            )
            .order_by(WebUser.display_name.nulls_last(), WebUser.username)
        )
        return list(res.scalars().all())


_CLIENTS_SEGMENT_VALUES = frozenset({"new", "repeat", "loyal", "vip"})
_CLIENTS_ACTIVITY_CANONICAL = {
    "r1_14": "r1_14",
    "a1_14": "r1_14",
    "r1_20": "r1_14",
    "a1_20": "r1_14",
    "active": "r1_14",
    "r14_20": "r14_20",
    "a14_20": "r14_20",
    "r20_45": "r20_45",
    "a20_45": "r20_45",
    "r45_90": "r45_90",
    "a45_90": "r45_90",
    "sleeping": "r45_90",
    "r90_plus": "r90_plus",
    "a90_plus": "r90_plus",
    "lost": "r90_plus",
}
_CLIENTS_ACTIVITY_VALUES = frozenset(_CLIENTS_ACTIVITY_CANONICAL.values())


def _clients_parse_segment_list(qp) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in qp.getlist("segment"):
        val = str(raw or "").strip().lower()
        if val in _CLIENTS_SEGMENT_VALUES and val not in seen:
            seen.add(val)
            out.append(val)
    return out


def _clients_parse_activity_list(qp) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in qp.getlist("activity"):
        val = _CLIENTS_ACTIVITY_CANONICAL.get(str(raw or "").strip().lower())
        if val and val not in seen:
            seen.add(val)
            out.append(val)
    return out


def _clients_parse_feed_tag_list(qp) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in qp.getlist("feed_tag"):
        val = str(raw or "").strip().lower()
        if not val or len(val) > 64 or val in seen:
            continue
        seen.add(val)
        out.append(val)
    return out


def clients_filter_list(filter_form: dict, key: str) -> list[str]:
    """Значення мультивибірного фільтра (segment, activity, …) як список."""
    raw = filter_form.get(key)
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    s = str(raw).strip()
    return [s] if s else []


def clients_filter_has(filter_form: dict, key: str, value: str) -> bool:
    return value in clients_filter_list(filter_form, key)


_CLIENTS_FILTER_KEYS = (
    "q",
    "registered",
    "email_mode",
    "bad_clients",
    "orders_min",
    "orders_max",
    "ltv_min",
    "ltv_max",
    "order_total_min",
    "order_total_max",
    "first_from",
    "first_to",
    "last_from",
    "last_to",
    "segment",
    "activity",
    "feed_tag",
    "assigned",
    "last_order_manager",
    "interaction_exclude",
    "brand_contains",
    "order_status",
    "product_contains",
    "product_id",
    "geo_city",
    "geo_oblast",
    "gender",
    # Історія закриття ліда (окремі ключі, щоб не колізити з CRM name=closure_result)
    "history_result",
    "history_from",
    "history_to",
)


def _clients_hidden_where():
    if not CLIENTS_EXPORT_EXCLUDED_CUSTOMER_IDS:
        return None
    return Client.customer_id.notin_(CLIENTS_EXPORT_EXCLUDED_CUSTOMER_IDS)


def _clients_parse_optional_int(val: str | None) -> int | None:
    if val is None or str(val).strip() == "":
        return None
    try:
        return int(str(val).strip())
    except ValueError:
        return None


def _clients_parse_registered_filter(val: str | None) -> bool | None:
    v = (val or "").strip()
    if v == "1":
        return True
    if v == "0":
        return False
    return None


def _clients_parse_gender_filter(val: str | None) -> str | None:
    v = (val or "").strip().lower()
    if v in ("male", "m", "ч", "чол", "чоловік", "чоловіки", "man", "men"):
        return "male"
    if v in ("female", "f", "ж", "жін", "жінка", "жінки", "woman", "women"):
        return "female"
    if v in (
        "unknown",
        "unk",
        "u",
        "?",
        "невизначено",
        "невизначена",
        "невідомо",
        "не визначено",
        "без статі",
    ):
        return "unknown"
    return None


def _clients_parse_email_mode(val: str | None) -> str | None:
    v = (val or "").strip().lower()
    if v in ("with", "without"):
        return v
    return None


def _clients_parse_bad_clients_filter(val: str | None) -> str | None:
    v = (val or "").strip().lower()
    if v in ("1", "true", "yes", "on", "with"):
        return "with"
    if v in ("0", "false", "no", "off", "without"):
        return "without"
    return None


def _clients_parse_optional_float(val: str | None) -> float | None:
    if val is None or str(val).strip() == "":
        return None
    s = str(val).strip().lower()
    for token in ("грн.", "грн", "uah", "₴", "грн "):
        s = s.replace(token, "")
    s = s.replace("\u00a0", " ").replace(" ", "")
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _clients_assigned_filter_for_clauses(val: str | None) -> str | None:
    if val is None:
        return None
    v = str(val).strip()
    if not v:
        return None
    low = v.lower()
    if low in ("all", "усі", "всі"):
        return None
    if low in ("none", "__none__", "unassigned", "0", "без", "немає"):
        return "__none__"
    return v


def _clients_last_order_manager_filter_for_clauses(val: str | None) -> str | None:
    if val is None:
        return None
    v = str(val).strip()
    if not v:
        return None
    low = v.lower()
    if low in ("all", "усі", "всі"):
        return None
    if low in ("none", "__none__", "unassigned", "0", "без", "немає"):
        return "__none__"
    return v


def _clients_filter_clauses_from_multidict(qp, now: datetime) -> tuple[list, dict]:
    q_clean = (qp.get("q") or "").strip()
    first_from, first_to, last_from, last_to = parse_client_filter_dates(
        qp.get("first_from"),
        qp.get("first_to"),
        qp.get("last_from"),
        qp.get("last_to"),
    )
    from web.client_marketing import _parse_date_param
    from web.client_crm import CRM_CLOSURE_RESULTS_ALLOWED

    def _history_result_values() -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        # Нові ключі + старі closure_* (зворотна сумісність зі старими посиланнями)
        for raw in list(qp.getlist("history_result")) + list(qp.getlist("closure_result")):
            val = str(raw or "").strip().lower()
            if val in CRM_CLOSURE_RESULTS_ALLOWED and val not in seen:
                seen.add(val)
                out.append(val)
        return out

    closure_results = _history_result_values()
    closure_from = _parse_date_param(qp.get("history_from") or qp.get("closure_from"))
    closure_to = _parse_date_param(qp.get("history_to") or qp.get("closure_to"))
    segment = _clients_parse_segment_list(qp) or None
    activity = _clients_parse_activity_list(qp) or None
    feed_tag = _clients_parse_feed_tag_list(qp) or None
    brand_contains_list = [str(x).strip() for x in qp.getlist("brand_contains") if str(x).strip()]
    order_statuses = [str(x).strip() for x in qp.getlist("order_status") if str(x).strip()]

    geo_cities_seen: set[str] = set()
    geo_cities: list[str] = []
    for x in qp.getlist("geo_city"):
        label = canonical_geo_city_filter_label(str(x))
        if label and label not in geo_cities_seen:
            geo_cities_seen.add(label)
            geo_cities.append(label)
    geo_oblasts = [str(x).strip() for x in qp.getlist("geo_oblast") if str(x).strip()]
    product_contains = (qp.get("product_contains") or "").strip()
    clauses = build_client_where_clauses(
        q_clean=q_clean,
        orders_min=_clients_parse_optional_int(qp.get("orders_min")),
        orders_max=_clients_parse_optional_int(qp.get("orders_max")),
        ltv_min=_clients_parse_optional_float(qp.get("ltv_min")),
        ltv_max=_clients_parse_optional_float(qp.get("ltv_max")),
        order_total_min=_clients_parse_optional_float(qp.get("order_total_min")),
        order_total_max=_clients_parse_optional_float(qp.get("order_total_max")),
        first_from=first_from,
        first_to=first_to,
        last_from=last_from,
        last_to=last_to,
        segment=segment,
        activity=activity,
        feed_tag=feed_tag,
        brand_contains_list=brand_contains_list,
        product_contains=product_contains,
        product_id=_clients_parse_optional_int(qp.get("product_id")),
        order_statuses=order_statuses,
        interaction_exclude_keys=[str(x).strip() for x in qp.getlist("interaction_exclude") if str(x).strip()],
        registered=_clients_parse_registered_filter(qp.get("registered")),
        email_mode=_clients_parse_email_mode(qp.get("email_mode")),
        characteristics_mode=_clients_parse_bad_clients_filter(qp.get("bad_clients") or qp.get("characteristics_mode")),
        now=now,
        assigned_username_filter=_clients_assigned_filter_for_clauses(qp.get("assigned")),
        last_order_manager_filter=_clients_last_order_manager_filter_for_clauses(qp.get("last_order_manager")),
        geo_cities=geo_cities or None,
        geo_oblasts=geo_oblasts or None,
        gender=_clients_parse_gender_filter(qp.get("gender")),
        closure_results=closure_results or None,
        closure_from=closure_from,
        closure_to=closure_to,
    )
    filter_form: dict = {}
    for key in _CLIENTS_FILTER_KEYS:
        if key in (
            "brand_contains",
            "order_status",
            "interaction_exclude",
            "geo_city",
            "geo_oblast",
            "segment",
            "activity",
            "feed_tag",
            "history_result",
        ):
            if key == "segment":
                vals = _clients_parse_segment_list(qp)
            elif key == "activity":
                vals = _clients_parse_activity_list(qp)
            elif key == "feed_tag":
                vals = _clients_parse_feed_tag_list(qp)
            elif key == "history_result":
                vals = closure_results
            else:
                vals = [str(x).strip() for x in qp.getlist(key) if str(x).strip()]
            if vals:
                filter_form[key] = vals
            continue
        if key == "history_from":
            raw = (qp.get("history_from") or qp.get("closure_from") or "").strip()
            if raw:
                filter_form[key] = raw
            continue
        if key == "history_to":
            raw = (qp.get("history_to") or qp.get("closure_to") or "").strip()
            if raw:
                filter_form[key] = raw
            continue
        val = qp.get(key)
        if val is not None and str(val).strip() != "":
            filter_form[key] = str(val).strip()
    return clauses, filter_form


def _clients_filter_clauses_from_request(request: Request) -> tuple[list, dict]:
    return _clients_filter_clauses_from_multidict(request.query_params, reference_now())


def _clients_default_sort_dir(sort_by: str) -> str:
    if sort_by in ("name", "city", "phone", "channel", "status", "id", "assigned"):
        return "asc"
    return "desc"


def _clients_coerce_sort_params(sort_by_raw: str | None, sort_dir_raw: str | None) -> tuple[str, str]:
    raw = (sort_by_raw or "last_order").strip()
    if raw == "ltv_desc":
        return "ltv", "desc"
    if raw == "ltv_asc":
        return "ltv", "asc"
    allowed = frozenset(
        {
            "last_order",
            "ltv",
            "orders",
            "name",
            "city",
            "phone",
            "channel",
            "activity",
            "rfm",
            "id",
            "status",
            "assigned",
        }
    )
    if raw not in allowed:
        raw = "last_order"
    sd = (sort_dir_raw or "").strip().lower()
    if sd not in ("asc", "desc"):
        sd = _clients_default_sort_dir(raw)
    return raw, sd


def _clients_parse_sort_params(request: Request) -> tuple[str, str]:
    qp = request.query_params
    return _clients_coerce_sort_params(qp.get("sort_by"), qp.get("sort_dir"))


def _clients_apply_sort_order(stmt, sort_by: str, sort_dir: str):
    desc = sort_dir == "desc"
    id_o = Client.id.desc() if desc else Client.id.asc()
    status_name_expr = (
        select(ClientOrder.order_status)
        .where(ClientOrder.client_customer_id == Client.customer_id)
        .order_by(nullslast(ClientOrder.order_date.desc()), ClientOrder.order_id.desc())
        .limit(1)
        .scalar_subquery()
    )

    if sort_by == "id":
        return stmt.order_by(Client.id.desc() if desc else Client.id.asc())
    if sort_by == "ltv":
        return stmt.order_by(nullslast(Client.ltv.desc() if desc else Client.ltv.asc()), id_o)
    if sort_by == "orders":
        return stmt.order_by(nullslast(Client.orders_count.desc() if desc else Client.orders_count.asc()), id_o)
    if sort_by == "name":
        return stmt.order_by(
            nullslast(Client.lastname.desc() if desc else Client.lastname.asc()),
            nullslast(Client.firstname.desc() if desc else Client.firstname.asc()),
            Client.id,
        )
    if sort_by == "city":
        return stmt.order_by(nullslast(Client.geo_city.desc() if desc else Client.geo_city.asc()), id_o)
    if sort_by == "phone":
        return stmt.order_by(nullslast(Client.telephone.desc() if desc else Client.telephone.asc()), id_o)
    if sort_by == "assigned":
        return stmt.order_by(
            nullslast(Client.crm_assigned_username.desc() if desc else Client.crm_assigned_username.asc()),
            id_o,
        )
    if sort_by == "channel":
        return stmt.order_by(nullslast(Client.source_raw.desc() if desc else Client.source_raw.asc()), id_o)
    if sort_by == "status":
        return stmt.order_by(nullslast(status_name_expr.desc() if desc else status_name_expr.asc()), id_o)
    if sort_by in ("last_order", "activity", "rfm"):
        return stmt.order_by(
            nullslast(Client.last_order_date.desc() if desc else Client.last_order_date.asc()),
            id_o,
        )
    return stmt.order_by(nullslast(Client.last_order_date.desc()), Client.id.desc())


def _clients_build_sort_urls(filter_form: dict, sort_by: str, sort_dir: str) -> dict[str, str]:
    cols = (
        "id",
        "name",
        "phone",
        "city",
        "activity",
        "rfm",
        "channel",
        "status",
        "orders",
        "ltv",
        "last_order",
        "assigned",
    )
    out: dict[str, str] = {}
    for col in cols:
        new_dir = (
            ("asc" if sort_dir == "desc" else "desc")
            if col == sort_by
            else _clients_default_sort_dir(col)
        )
        d = dict(filter_form)
        d["sort_by"] = col
        d["sort_dir"] = new_dir
        d["page"] = "1"
        out[col] = "/clients?" + urlencode(d)
    return out


def _clients_urlencode_params(
    filter_form: dict,
    sort_by: str,
    sort_dir: str,
    page: int | None = None,
) -> str:
    d = dict(filter_form)
    d["sort_by"] = sort_by
    d["sort_dir"] = sort_dir
    if page is not None:
        d["page"] = str(page)
    return urlencode(d, doseq=True)


def _clients_qs_with_patches(
    filter_form: dict,
    sort_by: str,
    sort_dir: str,
    *,
    set_fields: dict | None = None,
    remove_fields: frozenset | None = None,
) -> str:
    d = dict(filter_form)
    for k in remove_fields or frozenset():
        d.pop(k, None)
    if set_fields:
        for k, v in set_fields.items():
            if isinstance(v, (list, tuple)):
                vv = [str(x).strip() for x in v if str(x).strip()]
                if vv:
                    d[k] = vv
                else:
                    d.pop(k, None)
            elif v is None or str(v).strip() == "":
                d.pop(k, None)
            else:
                d[k] = str(v).strip()
    return _clients_urlencode_params(d, sort_by, sort_dir)


def _clients_qs_with_toggle(
    filter_form: dict,
    sort_by: str,
    sort_dir: str,
    field: str,
    value: str,
) -> str:
    """Query string з перемиканням одного значення мультивибірного фільтра."""
    current = clients_filter_list(filter_form, field)
    if value in current:
        new_vals = [v for v in current if v != value]
    else:
        new_vals = [*current, value]
    d = dict(filter_form)
    if new_vals:
        d[field] = new_vals
    else:
        d.pop(field, None)
    return _clients_urlencode_params(d, sort_by, sort_dir)


def _clients_chip_query_strings(
    filter_form: dict,
    sort_by: str,
    sort_dir: str,
    feed_tag_codes: list[str] | None = None,
) -> dict:
    ff, sb, sd = filter_form, sort_by, sort_dir
    feed_tag_chips: dict[str, str] = {
        "all": _clients_qs_with_patches(ff, sb, sd, remove_fields=frozenset({"feed_tag"})),
    }
    for code in feed_tag_codes or []:
        c = str(code or "").strip().lower()
        if c:
            feed_tag_chips[c] = _clients_qs_with_toggle(ff, sb, sd, "feed_tag", c)
    return {
        "segment": {
            "all": _clients_qs_with_patches(ff, sb, sd, remove_fields=frozenset({"segment"})),
            "new": _clients_qs_with_toggle(ff, sb, sd, "segment", "new"),
            "repeat": _clients_qs_with_toggle(ff, sb, sd, "segment", "repeat"),
            "loyal": _clients_qs_with_toggle(ff, sb, sd, "segment", "loyal"),
            "vip": _clients_qs_with_toggle(ff, sb, sd, "segment", "vip"),
        },
        "activity": {
            "all": _clients_qs_with_patches(ff, sb, sd, remove_fields=frozenset({"activity"})),
            "r1_14": _clients_qs_with_toggle(ff, sb, sd, "activity", "r1_14"),
            "r14_20": _clients_qs_with_toggle(ff, sb, sd, "activity", "r14_20"),
            "r20_45": _clients_qs_with_toggle(ff, sb, sd, "activity", "r20_45"),
            "r45_90": _clients_qs_with_toggle(ff, sb, sd, "activity", "r45_90"),
            "r90_plus": _clients_qs_with_toggle(ff, sb, sd, "activity", "r90_plus"),
        },
        "feed_tag": feed_tag_chips,
    }


def _clients_export_filename(prefix: str, ext: str) -> str:
    return f"{prefix}_{now_kyiv().strftime('%Y%m%d_%H%M')}.{ext}"


def _clients_safe_return_query(raw: str | None) -> str:
    if not raw:
        return ""
    s = str(raw).strip()
    if len(s) > 4096 or "\n" in s or "\r" in s or "\t" in s:
        return ""
    if s.startswith(("http://", "https://", "//")):
        return ""
    return s


def _download_response(content: bytes, media_type: str, filename: str) -> Response:
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _clients_apply_visibility_filters(stmt, clauses: list):
    hidden_clause = _clients_hidden_where()
    if hidden_clause is not None:
        stmt = stmt.where(hidden_clause)
    if clauses:
        stmt = stmt.where(*clauses)
    return stmt


async def _clients_get_by_id_or_404(
    session,
    client_id: int,
    *,
    with_orders: bool = False,
) -> Client:
    stmt = select(Client).where(Client.id == client_id)
    if with_orders:
        stmt = stmt.options(selectinload(Client.shop_orders).selectinload(ClientOrder.lines))
    res = await session.execute(stmt)
    client = res.scalar_one_or_none()
    if not client or client.customer_id in CLIENTS_EXPORT_EXCLUDED_CUSTOMER_IDS:
        raise HTTPException(status_code=404, detail="Клієнта не знайдено")
    return client
