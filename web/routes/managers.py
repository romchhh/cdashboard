"""Керування користувачами веб-панелі та CRM-профілі менеджерів."""

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import desc, false, func, or_, select

from bot.database.clients_engine import AsyncSessionLocalClients
from bot.database.clients_models import Client, ClientManagerNote, ClientOrder, EmailCampaign
from bot.database.engine import AsyncSessionLocal
from bot.database.models import WebUser
from config import CLIENTS_EXPORT_EXCLUDED_CUSTOMER_IDS
from web.auth import ROLE_ADMIN, ROLE_MANAGER, require_admin
from web.security import verify_csrf_dependency
from web.client_crm import (
    CRM_STATUS_IN_PROGRESS,
    CRM_STATUS_NEUTRAL,
    INTERACTION_OPTIONS,
    INTERACTION_OPTIONS_AUTO,
    INTERACTION_OPTIONS_MANUAL,
    deserialize_interaction_keys,
    interaction_labels_for_keys,
    processing_status_label,
)
from web.deps import form_checkbox_on, templates
from web.manager_crm_stats import (
    PERIOD_CHOICES as CRM_PERIOD_CHOICES,
    author_matches_aliases,
    closure_result_bars_from_counts,
    load_assigned_clients_history,
    load_assigned_portfolio_stats,
    load_manager_action_stats,
    load_manager_activity_log,
    load_team_manager_stats,
    manager_author_aliases,
    parse_period_key,
    period_cutoff,
    period_label,
    sql_match_author_string,
)
from web.passwords import bcrypt_hash

router = APIRouter(tags=["managers"])


def _web_user_feed_identity_strings(manager: WebUser) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in ((manager.username or "").strip(), (manager.display_name or "").strip()):
        if not raw:
            continue
        key = raw.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(raw)
    return out


def _sql_match_feed_identity_string(column, aliases: list[str]):
    if not aliases:
        return false()
    norm = func.lower(func.trim(func.coalesce(column, "")))
    parts = [norm == a.casefold() for a in aliases]
    return or_(*parts) if len(parts) > 1 else parts[0]


def _clients_hidden_clause():
    if not CLIENTS_EXPORT_EXCLUDED_CUSTOMER_IDS:
        return None
    return Client.customer_id.notin_(CLIENTS_EXPORT_EXCLUDED_CUSTOMER_IDS)


@router.get("/managers", response_class=HTMLResponse)
async def managers_page(
    request: Request,
    user: str = Depends(require_admin),
    period: str = Query("30d"),
):
    period_key = parse_period_key(period)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(WebUser).order_by(WebUser.role.desc(), WebUser.username)
        )
        users = list(result.scalars().all())

    hidden_clause = _clients_hidden_clause()
    async with AsyncSessionLocalClients() as csession:
        team_stats = await load_team_manager_stats(
            csession, users, hidden_clause=hidden_clause, period_key=period_key
        )

    team_totals = {
        "assigned": sum(r["portfolio"]["assigned_total"] for r in team_stats),
        "in_progress": sum(
            r["portfolio"]["status_counts"].get(CRM_STATUS_IN_PROGRESS, 0) for r in team_stats
        ),
        "actions": sum(r["actions"]["total_actions"] for r in team_stats),
        "closures": sum(r["actions"]["closures"] for r in team_stats),
    }

    return templates.TemplateResponse(
        "managers.html",
        {
            "request": request,
            "users": users,
            "team_stats": team_stats,
            "team_totals": team_totals,
            "period_key": period_key,
            "period_choices": CRM_PERIOD_CHOICES,
            "period_label": period_label(period_key),
        },
    )


@router.get("/managers/add", response_class=HTMLResponse)
async def managers_add_page(request: Request, user: str = Depends(require_admin)):
    return templates.TemplateResponse(
        "manager_edit.html",
        {"request": request, "manager": None, "error": request.query_params.get("error")},
    )


@router.post("/managers/add")
async def managers_add_post(
    request: Request,
    user: str = Depends(require_admin),
    _csrf: None = Depends(verify_csrf_dependency),
    username: str = Form(...),
    password: str = Form(...),
    display_name: str = Form(""),
    role: str = Form("manager"),
    access_finance: str | None = Form(None),
    access_marketing: str | None = Form(None),
    access_warehouse: str | None = Form(None),
    access_clients_export: str | None = Form(None),
    access_advertising: str | None = Form(None),
):
    username = username.strip()
    if not username:
        return RedirectResponse(url="/managers/add?error=empty_login", status_code=302)
    if len(password) < 4:
        return RedirectResponse(url="/managers/add?error=short_password", status_code=302)
    role = ROLE_MANAGER if role != ROLE_ADMIN else ROLE_ADMIN
    af = form_checkbox_on(access_finance)
    am = form_checkbox_on(access_marketing)
    aw = form_checkbox_on(access_warehouse)
    ax = form_checkbox_on(access_clients_export)
    aa = form_checkbox_on(access_advertising)
    if role == ROLE_MANAGER and not (af or am or aw or aa):
        return RedirectResponse(url="/managers/add?error=no_scopes", status_code=302)
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(WebUser).where(WebUser.username == username))
        if res.scalar_one_or_none():
            return RedirectResponse(url="/managers/add?error=exists", status_code=302)
        if role == ROLE_ADMIN:
            af = am = aw = ax = aa = True
        web_user = WebUser(
            username=username,
            password_hash=bcrypt_hash(password),
            display_name=(display_name or "").strip() or None,
            role=role,
            access_finance=af,
            access_marketing=am,
            access_warehouse=aw,
            access_clients_export=ax,
            access_advertising=aa,
        )
        session.add(web_user)
        await session.commit()
    return RedirectResponse(url="/managers", status_code=302)


@router.get("/managers/{user_id:int}/edit", response_class=HTMLResponse)
async def managers_edit_page(
    request: Request,
    user_id: int,
    user: str = Depends(require_admin),
):
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(WebUser).where(WebUser.id == user_id))
        manager = res.scalar_one_or_none()
    if not manager:
        raise HTTPException(status_code=404, detail="Користувача не знайдено")
    return templates.TemplateResponse(
        "manager_edit.html",
        {"request": request, "manager": manager, "error": request.query_params.get("error")},
    )


@router.post("/managers/{user_id:int}/edit")
async def managers_edit_post(
    request: Request,
    user_id: int,
    user: str = Depends(require_admin),
    _csrf: None = Depends(verify_csrf_dependency),
    username: str = Form(...),
    password: str = Form(""),
    display_name: str = Form(""),
    role: str = Form("manager"),
    access_finance: str | None = Form(None),
    access_marketing: str | None = Form(None),
    access_warehouse: str | None = Form(None),
    access_clients_export: str | None = Form(None),
    access_advertising: str | None = Form(None),
):
    username = username.strip()
    if not username:
        return RedirectResponse(url=f"/managers/{user_id}/edit?error=empty_login", status_code=302)
    role = ROLE_MANAGER if role != ROLE_ADMIN else ROLE_ADMIN
    af = form_checkbox_on(access_finance)
    am = form_checkbox_on(access_marketing)
    aw = form_checkbox_on(access_warehouse)
    ax = form_checkbox_on(access_clients_export)
    aa = form_checkbox_on(access_advertising)
    if role == ROLE_MANAGER and not (af or am or aw or aa):
        return RedirectResponse(url=f"/managers/{user_id}/edit?error=no_scopes", status_code=302)
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(WebUser).where(WebUser.id == user_id))
        manager = res.scalar_one_or_none()
        if not manager:
            raise HTTPException(status_code=404, detail="Користувача не знайдено")
        other = await session.execute(
            select(WebUser).where(WebUser.username == username, WebUser.id != user_id)
        )
        if other.scalar_one_or_none():
            return RedirectResponse(url=f"/managers/{user_id}/edit?error=exists", status_code=302)
        manager.username = username
        manager.display_name = (display_name or "").strip() or None
        manager.role = role
        if role == ROLE_ADMIN:
            manager.access_finance = True
            manager.access_marketing = True
            manager.access_warehouse = True
            manager.access_clients_export = True
            manager.access_advertising = True
        else:
            manager.access_finance = af
            manager.access_marketing = am
            manager.access_warehouse = aw
            manager.access_clients_export = ax
            manager.access_advertising = aa
        if password:
            manager.password_hash = bcrypt_hash(password)
        await session.commit()
    return RedirectResponse(url="/managers", status_code=302)


@router.post("/managers/{user_id:int}/delete")
async def managers_delete_post(
    request: Request,
    user_id: int,
    user: str = Depends(require_admin),
    _csrf: None = Depends(verify_csrf_dependency),
):
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(WebUser).where(WebUser.id == user_id))
        manager = res.scalar_one_or_none()
        if not manager:
            raise HTTPException(status_code=404, detail="Користувача не знайдено")
        await session.delete(manager)
        await session.commit()
    return RedirectResponse(url="/managers", status_code=302)


@router.get("/managers/{user_id:int}/crm", response_class=HTMLResponse)
async def managers_crm_profile_page(
    request: Request,
    user_id: int,
    user: str = Depends(require_admin),
    period: str = Query("30d"),
):
    period_key = parse_period_key(period)
    cutoff = period_cutoff(period_key)

    async with AsyncSessionLocal() as session:
        res = await session.execute(select(WebUser).where(WebUser.id == user_id))
        manager = res.scalar_one_or_none()
    if not manager:
        raise HTTPException(status_code=404, detail="Користувача не знайдено")
    uname = (manager.username or "").strip()
    display = ((manager.display_name or "").strip() or uname or "—").strip()
    feed_aliases = _web_user_feed_identity_strings(manager)
    author_aliases = manager_author_aliases(manager)
    hidden_clause = _clients_hidden_clause()

    interaction_counts: dict[str, int] = {k: 0 for k, _ in INTERACTION_OPTIONS}
    interaction_auto_counts: dict[str, int] = {k: 0 for k, _ in INTERACTION_OPTIONS_AUTO}
    interaction_manual_counts: dict[str, int] = {k: 0 for k, _ in INTERACTION_OPTIONS_MANUAL}
    assigned_clients: list[Client] = []
    orders_as_manager_total = 0
    clients_last_order_mgr = 0
    recent_order_rows: list[dict] = []
    campaigns: list[EmailCampaign] = []
    recent_notes: list[dict] = []
    recent_crm_history: list[dict] = []
    manager_activity_log: list[dict] = []
    feed_manager_samples: list[dict[str, object]] = []
    manager_action_stats: dict = {}
    manager_action_stats_assigned: dict = {}

    async with AsyncSessionLocalClients() as csession:
        portfolio = await load_assigned_portfolio_stats(
            csession, username=uname, hidden_clause=hidden_clause
        )
        assigned_total = portfolio["assigned_total"]
        clients_with_any_interaction = portfolio["clients_with_any_interaction"]
        status_counts = portfolio["status_counts"]

        manager_action_stats = await load_manager_action_stats(
            csession, aliases=author_aliases, cutoff=cutoff, hidden_clause=hidden_clause
        )
        manager_action_stats_assigned = await load_manager_action_stats(
            csession,
            aliases=author_aliases,
            cutoff=cutoff,
            hidden_clause=hidden_clause,
            only_assigned_username=uname,
        )
        manager_activity_log = await load_manager_activity_log(
            csession, aliases=author_aliases, cutoff=cutoff, hidden_clause=hidden_clause, limit=100
        )
        closure_result_bars = closure_result_bars_from_counts(manager_action_stats["closure_results"])

        q_keys = select(Client.crm_interaction_keys).where(Client.crm_assigned_username == uname)
        if hidden_clause is not None:
            q_keys = q_keys.where(hidden_clause)
        for raw in (await csession.execute(q_keys)).scalars().all():
            keys = deserialize_interaction_keys(raw)
            for k in keys:
                if k in interaction_counts:
                    interaction_counts[k] += 1
                if k in interaction_auto_counts:
                    interaction_auto_counts[k] += 1
                if k in interaction_manual_counts:
                    interaction_manual_counts[k] += 1

        q_clients = (
            select(Client)
            .where(Client.crm_assigned_username == uname)
            .order_by(desc(func.coalesce(Client.last_order_date, Client.updated_at)))
            .limit(80)
        )
        if hidden_clause is not None:
            q_clients = q_clients.where(hidden_clause)
        assigned_clients = list((await csession.execute(q_clients)).scalars().all())

        ord_match = _sql_match_feed_identity_string(ClientOrder.manager_username, feed_aliases)
        q_ord_count = (
            select(func.count())
            .select_from(ClientOrder)
            .join(Client, Client.customer_id == ClientOrder.client_customer_id)
            .where(ord_match)
        )
        if hidden_clause is not None:
            q_ord_count = q_ord_count.where(hidden_clause)
        orders_as_manager_total = int((await csession.execute(q_ord_count)).scalar() or 0)

        lom_match = _sql_match_feed_identity_string(Client.last_order_manager_username, feed_aliases)
        q_lom = select(func.count()).select_from(Client).where(lom_match)
        if hidden_clause is not None:
            q_lom = q_lom.where(hidden_clause)
        clients_last_order_mgr = int((await csession.execute(q_lom)).scalar() or 0)

        q_ord_recent = (
            select(ClientOrder, Client.id.label("crm_web_client_id"))
            .join(Client, Client.customer_id == ClientOrder.client_customer_id)
            .where(ord_match)
            .order_by(desc(ClientOrder.order_date))
            .limit(25)
        )
        if hidden_clause is not None:
            q_ord_recent = q_ord_recent.where(hidden_clause)
        for row in (await csession.execute(q_ord_recent)).all():
            recent_order_rows.append({"order": row[0], "client_id": row[1]})

        created_match = _sql_match_feed_identity_string(EmailCampaign.created_by, feed_aliases)
        q_em = (
            select(EmailCampaign)
            .where(created_match)
            .order_by(desc(func.coalesce(EmailCampaign.last_sent_at, EmailCampaign.created_at)))
            .limit(40)
        )
        campaigns = list((await csession.execute(q_em)).scalars().all())

        cnt_lbl = func.count(ClientOrder.id).label("ord_cnt")
        q_samples = (
            select(ClientOrder.manager_username, cnt_lbl)
            .where(
                ClientOrder.manager_username.isnot(None),
                func.trim(func.coalesce(ClientOrder.manager_username, "")) != "",
            )
            .group_by(ClientOrder.manager_username)
            .order_by(desc(cnt_lbl))
            .limit(18)
        )
        for name, cnt in (await csession.execute(q_samples)).all():
            feed_manager_samples.append({"name": str(name).strip(), "count": int(cnt or 0)})

        q_notes = (
            select(ClientManagerNote, Client.id.label("client_web_id"))
            .join(Client, Client.customer_id == ClientManagerNote.client_customer_id)
            .where(sql_match_author_string(ClientManagerNote.author_display, author_aliases))
            .order_by(desc(ClientManagerNote.created_at))
            .limit(50)
        )
        recent_notes = [
            {"note": row[0], "client_id": row[1]}
            for row in (await csession.execute(q_notes)).all()
        ]

        recent_crm_history = await load_assigned_clients_history(
            csession, username=uname, hidden_clause=hidden_clause, limit=60
        )
        for row in recent_crm_history:
            row["by_manager"] = author_matches_aliases(row["entry"].author_display, author_aliases)

    max_inter = max((interaction_counts[k] for k, _ in INTERACTION_OPTIONS), default=0) or 1
    interaction_bars = []
    for k, label in INTERACTION_OPTIONS:
        c = int(interaction_counts.get(k, 0) or 0)
        interaction_bars.append(
            {
                "key": k,
                "label": label,
                "count": c,
                "pct_bar": round(100.0 * c / max_inter, 1) if max_inter else 0.0,
                "pct_of_clients": round(100.0 * c / assigned_total, 1) if assigned_total else 0.0,
            }
        )

    def _build_interaction_bars(options: tuple[tuple[str, str], ...], counts: dict[str, int]) -> list[dict]:
        mx = max((counts.get(k, 0) for k, _ in options), default=0) or 1
        bars = []
        for k, label in options:
            c = int(counts.get(k, 0) or 0)
            bars.append(
                {
                    "key": k,
                    "label": label,
                    "count": c,
                    "pct_bar": round(100.0 * c / mx, 1) if mx else 0.0,
                    "pct_of_clients": round(100.0 * c / assigned_total, 1) if assigned_total else 0.0,
                }
            )
        return bars

    interaction_bars_auto = _build_interaction_bars(INTERACTION_OPTIONS_AUTO, interaction_auto_counts)
    interaction_bars_manual = _build_interaction_bars(INTERACTION_OPTIONS_MANUAL, interaction_manual_counts)

    assigned_client_rows = []
    for c in assigned_clients:
        keys = deserialize_interaction_keys(getattr(c, "crm_interaction_keys", None))
        lbl = ", ".join(interaction_labels_for_keys(keys))
        st = (getattr(c, "crm_processing_status", None) or CRM_STATUS_NEUTRAL).strip().lower()
        assigned_client_rows.append(
            {
                "client": c,
                "interactions": lbl or "—",
                "processing_status": st,
                "processing_status_label": processing_status_label(st),
            }
        )

    campaigns_sent_sum = sum(int(x.sent_total or 0) for x in campaigns)
    campaigns_runs_sum = sum(int(x.run_count or 0) for x in campaigns)

    feed_alias_rows = [{"label": "Логін веб-панелі", "value": uname}]
    if (manager.display_name or "").strip() and (
        manager.display_name or ""
    ).strip().casefold() != uname.casefold():
        feed_alias_rows.append(
            {"label": "Ім'я в профілі (display_name)", "value": (manager.display_name or "").strip()}
        )

    return templates.TemplateResponse(
        "finance/manager_crm.html",
        {
            "request": request,
            "manager": manager,
            "manager_display": display,
            "feed_aliases": feed_aliases,
            "feed_alias_rows": feed_alias_rows,
            "feed_manager_samples": feed_manager_samples,
            "assigned_total": assigned_total,
            "assigned_client_rows": assigned_client_rows,
            "interaction_bars": interaction_bars,
            "interaction_bars_auto": interaction_bars_auto,
            "interaction_bars_manual": interaction_bars_manual,
            "status_counts": status_counts,
            "manager_action_stats": manager_action_stats,
            "manager_action_stats_assigned": manager_action_stats_assigned,
            "manager_activity_log": manager_activity_log,
            "closure_result_bars": closure_result_bars,
            "recent_crm_history": recent_crm_history,
            "period_key": period_key,
            "period_choices": CRM_PERIOD_CHOICES,
            "period_label": period_label(period_key),
            "clients_with_any_interaction": clients_with_any_interaction,
            "orders_as_manager_total": orders_as_manager_total,
            "clients_last_order_mgr": clients_last_order_mgr,
            "recent_order_rows": recent_order_rows,
            "campaigns": campaigns,
            "campaigns_sent_sum": campaigns_sent_sum,
            "campaigns_runs_sum": campaigns_runs_sum,
            "recent_notes": recent_notes,
        },
    )
