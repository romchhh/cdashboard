"""Clients routes."""

from web.deps import *  # noqa: F403, F401
from web.clients_query import *  # noqa: F403, F401

import asyncio
from fastapi import APIRouter

from web.auth import require_admin
from web.client_export_audit import (
    EXPORT_KIND_LABELS,
    count_clients_for_export,
    fetch_client_export_logs,
    record_client_export_log,
    require_clients_export,
    summarize_export_query,
)
from web.security import _get_client_ip

router = APIRouter()


async def _log_and_download_export(
    request: Request,
    user: str,
    *,
    export_kind: str,
    file_format: str,
    media_type: str,
    filename_prefix: str,
    body: bytes,
    row_count: int | None = None,
    include_email: bool = False,
    filters_summary: str | None = None,
    client_id: int | None = None,
):
    filename = _clients_export_filename(filename_prefix, file_format)
    await record_client_export_log(
        username=user,
        display_name=getattr(request.state, "display_name", None),
        role=getattr(request.state, "role", "manager"),
        export_kind=export_kind,
        file_format=file_format,
        filename=filename,
        row_count=row_count,
        include_email=include_email,
        filters_summary=filters_summary,
        client_ip=_get_client_ip(request),
        client_id=client_id,
    )
    return _download_response(body, media_type, filename)


@router.get("/clients/export.csv")
async def clients_export_csv(
    request: Request,
    user: str = Depends(require_clients_export),
):
    clauses, _ = _clients_filter_clauses_from_request(request)
    include_email_col = _clients_parse_email_mode(request.query_params.get("email_mode")) == "with"
    async with AsyncSessionLocalClients() as session:
        row_count = await count_clients_for_export(session, clauses)
        body = await build_clients_csv_bytes_async(session, clauses, include_email=include_email_col)
    return await _log_and_download_export(
        request,
        user,
        export_kind="clients_list",
        file_format="csv",
        media_type="text/csv; charset=utf-8",
        filename_prefix="clients",
        body=body,
        row_count=row_count,
        include_email=include_email_col,
        filters_summary=summarize_export_query(str(request.query_params)),
    )



@router.get("/clients/export.xlsx")
async def clients_export_xlsx(
    request: Request,
    user: str = Depends(require_clients_export),
):
    clauses, _ = _clients_filter_clauses_from_request(request)
    include_email_col = _clients_parse_email_mode(request.query_params.get("email_mode")) == "with"
    async with AsyncSessionLocalClients() as session:
        row_count = await count_clients_for_export(session, clauses)
        body = await build_clients_xlsx_bytes(session, clauses, include_email=include_email_col)
    return await _log_and_download_export(
        request,
        user,
        export_kind="clients_list",
        file_format="xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename_prefix="clients",
        body=body,
        row_count=row_count,
        include_email=include_email_col,
        filters_summary=summarize_export_query(str(request.query_params)),
    )



@router.get("/clients/orders-export.csv")
async def clients_orders_export_csv(
    request: Request,
    user: str = Depends(require_clients_export),
):
    clauses, _ = _clients_filter_clauses_from_request(request)
    async with AsyncSessionLocalClients() as session:
        body = await build_all_orders_csv_bytes(session, clauses)
    return await _log_and_download_export(
        request,
        user,
        export_kind="clients_orders",
        file_format="csv",
        media_type="text/csv; charset=utf-8",
        filename_prefix="clients_orders",
        body=body,
        filters_summary=summarize_export_query(str(request.query_params)),
    )



@router.get("/clients/orders-export.xlsx")
async def clients_orders_export_xlsx(
    request: Request,
    user: str = Depends(require_clients_export),
):
    clauses, _ = _clients_filter_clauses_from_request(request)
    async with AsyncSessionLocalClients() as session:
        body = await build_all_orders_xlsx_bytes(session, clauses)
    return await _log_and_download_export(
        request,
        user,
        export_kind="clients_orders",
        file_format="xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename_prefix="clients_orders",
        body=body,
        filters_summary=summarize_export_query(str(request.query_params)),
    )



@router.get("/clients/{client_id}/export-orders.csv")
async def client_detail_orders_export_csv(
    request: Request,
    client_id: int,
    user: str = Depends(require_clients_export),
):
    async with AsyncSessionLocalClients() as session:
        client = await _clients_get_by_id_or_404(session, client_id)
        body = await build_client_orders_csv_bytes(session, client.customer_id)
    return await _log_and_download_export(
        request,
        user,
        export_kind="client_orders",
        file_format="csv",
        media_type="text/csv; charset=utf-8",
        filename_prefix=f"client_{client_id}_orders",
        body=body,
        filters_summary=f"client_id={client_id}",
        client_id=client_id,
    )



@router.get("/clients/{client_id}/export-orders.xlsx")
async def client_detail_orders_export_xlsx(
    request: Request,
    client_id: int,
    user: str = Depends(require_clients_export),
):
    async with AsyncSessionLocalClients() as session:
        client = await _clients_get_by_id_or_404(session, client_id)
        body = await build_client_orders_xlsx_bytes(session, client.customer_id)
    return await _log_and_download_export(
        request,
        user,
        export_kind="client_orders",
        file_format="xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename_prefix=f"client_{client_id}_orders",
        body=body,
        filters_summary=f"client_id={client_id}",
        client_id=client_id,
    )


@router.get("/clients/exports-log", response_class=HTMLResponse)
async def clients_exports_log_page(
    request: Request,
    user: str = Depends(require_admin),
):
    logs = await fetch_client_export_logs(limit=250)
    return templates.TemplateResponse(
        "clients/exports_log.html",
        {
            "request": request,
            "clients_nav": "exports_log",
            "export_logs": logs,
            "export_kind_labels": EXPORT_KIND_LABELS,
        },
    )


async def _client_geo_cities_ordered(session) -> list[str]:
    """Канонічні міста для фільтра (об'єднання Київ/Киев тощо — як на дашборді)."""
    from web.client_marketing import list_canonical_city_names_from_raw

    stmt = (
        select(Client.geo_city, func.count(Client.id))
        .group_by(Client.geo_city)
        .order_by(func.count(Client.id).desc())
    )
    hidden_clause = _clients_hidden_where()
    if hidden_clause is not None:
        stmt = stmt.where(hidden_clause)
    city_raw = [(row[0], int(row[1] or 0)) for row in (await session.execute(stmt)).all()]
    return list_canonical_city_names_from_raw(city_raw)


async def _client_geo_oblasts_ordered(session) -> list[str]:
    """Області з geo_city (лише ті, що явно присутні у даних)."""
    from web.client_marketing import list_geo_oblast_names_from_raw

    stmt = (
        select(Client.geo_city, func.count(Client.id))
        .group_by(Client.geo_city)
        .order_by(func.count(Client.id).desc())
    )
    hidden_clause = _clients_hidden_where()
    if hidden_clause is not None:
        stmt = stmt.where(hidden_clause)
    city_raw = [(row[0], int(row[1] or 0)) for row in (await session.execute(stmt)).all()]
    return list_geo_oblast_names_from_raw(city_raw)


async def _catalog_brand_names_ordered() -> list[str]:
    """Назви брендів з БД товарів (product_brands) для фільтра клієнтів."""
    async with AsyncProductsSessionLocal() as session:
        r = await session.execute(select(ProductBrand.name).order_by(ProductBrand.name))
        raw = [str(x).strip() for x in r.scalars().all() if x and str(x).strip()]
    seen: set[str] = set()
    out: list[str] = []
    for n in raw:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out



@router.get("/clients", response_class=HTMLResponse)
async def clients_list_page(
    request: Request,
    user: str = Depends(get_current_user),
    page: int = Query(1, ge=1),
):
    sort_by, sort_dir = _clients_parse_sort_params(request)
    clauses, filter_form = _clients_filter_clauses_from_request(request)

    async with AsyncSessionLocalClients() as session:
        client_totals = await load_clients_base_totals(session)
        feed_tag_options = await load_feed_tag_filter_options(session)
        count_stmt = _clients_apply_visibility_filters(select(func.count()).select_from(Client), clauses)
        total_r = await session.execute(count_stmt)
        total = int(total_r.scalar() or 0)

        total_pages = max(1, (total + CLIENTS_PER_PAGE - 1) // CLIENTS_PER_PAGE) if total else 1
        if page > total_pages:
            return RedirectResponse(
                url=f"/clients?{_clients_urlencode_params(filter_form, sort_by, sort_dir, total_pages)}",
                status_code=302,
            )

        offset = (page - 1) * CLIENTS_PER_PAGE
        stmt = _clients_apply_visibility_filters(select(Client), clauses)

        stmt = _clients_apply_sort_order(stmt, sort_by, sort_dir)
        stmt = stmt.offset(offset).limit(CLIENTS_PER_PAGE)
        res = await session.execute(stmt)
        clients = list(res.scalars().all())
        customer_ids = [c.customer_id for c in clients if c.customer_id]
        latest_status_by_customer: dict[str, dict] = {}
        if customer_ids:
            latest_dt_sq = (
                select(
                    ClientOrder.client_customer_id.label("ccid"),
                    func.max(ClientOrder.order_date).label("max_dt"),
                )
                .where(ClientOrder.client_customer_id.in_(customer_ids))
                .group_by(ClientOrder.client_customer_id)
                .subquery()
            )
            latest_q = await session.execute(
                select(
                    ClientOrder.client_customer_id,
                    ClientOrder.order_status_id,
                    ClientOrder.order_status,
                )
                .join(
                    latest_dt_sq,
                    and_(
                        ClientOrder.client_customer_id == latest_dt_sq.c.ccid,
                        ClientOrder.order_date == latest_dt_sq.c.max_dt,
                    ),
                )
            )
            for cid, sid, sname in latest_q.all():
                label = normalize_order_status_name(sname, sid)
                latest_status_by_customer[str(cid)] = {
                    "status_id": sid,
                    "name": label,
                    "tone": order_status_tone(label, sid),
                }

    now = reference_now()
    clients_with_meta = []
    for c in clients:
        keys = deserialize_interaction_keys(getattr(c, "crm_interaction_keys", None))
        proc_st = normalize_processing_status(getattr(c, "crm_processing_status", None))
        clients_with_meta.append(
            {
                "client": c,
                "m": marketing_row(c, now),
                "latest_order_status": latest_status_by_customer.get(
                    c.customer_id, {"status_id": None, "name": "—", "tone": "muted"}
                ),
                "interaction_labels": interaction_labels_for_keys(keys),
                "interaction_keys": keys,
                "processing_status": proc_st,
                "processing_status_label": processing_status_label(proc_st),
            }
        )
    export_qs = _clients_urlencode_params(filter_form, sort_by, sort_dir)
    feed_tag_codes = [opt["code"] for opt in feed_tag_options]
    clients_chips = _clients_chip_query_strings(
        filter_form, sort_by, sort_dir, feed_tag_codes=feed_tag_codes
    )
    sort_urls = _clients_build_sort_urls(filter_form, sort_by, sort_dir)

    catalog_brands: list[str] = []
    try:
        catalog_brands = await _catalog_brand_names_ordered()
    except Exception:
        logger.exception("Не вдалося завантажити бренди з каталогу товарів для фільтра клієнтів")

    marketing_managers = await _clients_marketing_managers_list()
    manager_labels = {
        (m.username or ""): ((m.display_name or m.username) or "").strip()
        for m in marketing_managers
        if m.username
    }
    async with AsyncSessionLocalClients() as session:
        lom_stmt = select(Client.last_order_manager_username).where(
            Client.last_order_manager_username.isnot(None),
            func.trim(func.coalesce(Client.last_order_manager_username, "")) != "",
        )
        hidden_clause = _clients_hidden_where()
        if hidden_clause is not None:
            lom_stmt = lom_stmt.where(hidden_clause)
        lom_vals = (await session.execute(lom_stmt)).scalars().all()
        client_geo_cities = await _client_geo_cities_ordered(session)
        client_geo_oblasts = await _client_geo_oblasts_ordered(session)
    last_order_managers = sorted({str(x).strip() for x in lom_vals if str(x or "").strip()}, key=str.casefold)

    return templates.TemplateResponse(
        "clients/clients_list.html",
        {
            "request": request,
            "clients_with_meta": clients_with_meta,
            "clients": clients,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "per_page": CLIENTS_PER_PAGE,
            "filter_form": filter_form,
            "sort_by": sort_by,
            "sort_dir": sort_dir,
            "sort_urls": sort_urls,
            "export_qs": export_qs,
            "clients_chips": clients_chips,
            "search_query": filter_form.get("q", ""),
            "catalog_brands": catalog_brands,
            "order_status_options": order_status_filter_options(),
            "client_totals": client_totals,
            "clients_nav": "list",
            "marketing_managers": marketing_managers,
            "interaction_options": INTERACTION_OPTIONS,
            "interaction_options_direct": INTERACTION_OPTIONS_DIRECT,
            "interaction_options_mass": INTERACTION_OPTIONS_MASS,
            "interaction_options_auto": INTERACTION_OPTIONS_MASS,
            "interaction_options_manual": INTERACTION_OPTIONS_DIRECT,
            "processing_status_options": CRM_PROCESSING_ACTIVE_STATUSES,
            "closure_result_options": CRM_CLOSURE_RESULT_CHOICES,
            "manager_labels": manager_labels,
            "last_order_managers": last_order_managers,
            "client_geo_cities": client_geo_cities,
            "client_geo_oblasts": client_geo_oblasts,
            "feed_tag_options": feed_tag_options,
            "clients_notice": request.query_params.get("clients_notice"),
            "clients_notice_n": request.query_params.get("clients_n"),
        },
    )



@router.get("/clients/dashboard", response_class=HTMLResponse)
async def clients_dashboard_page(request: Request, user: str = Depends(get_current_user)):
    """Колишній дашборд клієнтів — перенесено в «Звіти»."""
    q = request.url.query
    target = "/reports?report_type=clients_monthly"
    if q:
        target = f"{target}&{q}"
    return RedirectResponse(url=target, status_code=302)


@router.get("/clients/{client_id}", response_class=HTMLResponse)
async def client_detail_page(
    request: Request,
    client_id: int,
    user: str = Depends(get_current_user),
):
    now = reference_now()
    async with AsyncSessionLocalClients() as session:
        client = await _clients_get_by_id_or_404(session, client_id, with_orders=True)
        manager_notes: list[ClientManagerNote] = []
        notes_res = await session.execute(
            select(ClientManagerNote)
            .where(ClientManagerNote.client_customer_id == client.customer_id)
            .order_by(desc(ClientManagerNote.created_at))
        )
        manager_notes = list(notes_res.scalars().all())
        crm_history_res = await session.execute(
            select(ClientCrmHistory)
            .where(ClientCrmHistory.client_customer_id == client.customer_id)
            .order_by(desc(ClientCrmHistory.created_at))
            .limit(80)
        )
        crm_history = list(crm_history_res.scalars().all())

    orders = sorted(
        client.shop_orders,
        key=lambda o: (o.order_date or datetime.min, o.order_id or 0),
        reverse=True,
    )
    for o in orders:
        label = normalize_order_status_name(getattr(o, "order_status", None), getattr(o, "order_status_id", None))
        setattr(o, "order_status_label", label)
        setattr(o, "order_status_tone", order_status_tone(label, getattr(o, "order_status_id", None)))
    top_brands = top_brands_from_orders(orders)
    repurchase_hints = repurchase_reminders(orders, now)
    repurchase_fresh = [h for h in repurchase_hints if not h.get("stale")]
    repurchase_stale = [h for h in repurchase_hints if h.get("stale")]
    repurchase_show_fresh = repurchase_fresh[:10]
    repurchase_show_stale = repurchase_stale[:8]
    repurchase_more_count = max(
        0,
        len(repurchase_hints) - len(repurchase_show_fresh) - len(repurchase_show_stale),
    )
    marketing = marketing_row(client, now)

    product_ids: list[int] = []
    for o in orders:
        for ln in o.lines or []:
            if ln.product_id is not None:
                product_ids.append(int(ln.product_id))

    category_by_product: dict = {}
    if product_ids:
        try:
            async with AsyncProductsSessionLocal() as ps:
                category_by_product = await load_product_category_map(ps, product_ids)
        except Exception:
            logging.exception("load_product_category_map for client %s", client_id)
            category_by_product = {}

    last_category = last_interest_category(orders, category_by_product)

    assigned_manager_label = None
    crm_interaction_labels = interaction_labels_for_keys(
        deserialize_interaction_keys(getattr(client, "crm_interaction_keys", None))
    )

    assigned_manager_label = None
    au = getattr(client, "crm_assigned_username", None) or None
    if au:
        async with AsyncSessionLocal() as su:
            ur = await su.execute(select(WebUser).where(WebUser.username == au))
            wu = ur.scalar_one_or_none()
            if wu:
                assigned_manager_label = (wu.display_name or wu.username or "").strip() or au
            else:
                assigned_manager_label = au

    proc_status = normalize_processing_status(getattr(client, "crm_processing_status", None))
    interaction_keys = deserialize_interaction_keys(getattr(client, "crm_interaction_keys", None))
    marketing_managers = await _clients_marketing_managers_list()

    return templates.TemplateResponse(
        "clients/client_detail.html",
        {
            "request": request,
            "client": client,
            "orders": orders,
            "marketing": marketing,
            "top_brands": top_brands,
            "repurchase_hints": repurchase_hints,
            "repurchase_fresh": repurchase_fresh,
            "repurchase_stale": repurchase_stale,
            "repurchase_show_fresh": repurchase_show_fresh,
            "repurchase_show_stale": repurchase_show_stale,
            "repurchase_more_count": repurchase_more_count,
            "last_category": last_category,
            "manager_notes": manager_notes,
            "crm_history": crm_history,
            "query_notice": request.query_params.get("notice"),
            "assigned_manager_label": assigned_manager_label,
            "crm_interaction_labels": crm_interaction_labels,
            "crm_interaction_keys": interaction_keys,
            "crm_processing_status": proc_status,
            "crm_processing_status_label": processing_status_label(proc_status),
            "interaction_options_direct": INTERACTION_OPTIONS_DIRECT,
            "interaction_options_mass": INTERACTION_OPTIONS_MASS,
            "processing_status_options": CRM_PROCESSING_ACTIVE_STATUSES,
            "closure_result_options": CRM_CLOSURE_RESULT_CHOICES,
            "marketing_managers": marketing_managers,
        },
    )



@router.post("/clients/{client_id}/comments")
async def client_post_comment(
    request: Request,
    client_id: int,
    user: str = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_dependency),
    comment_body: str = Form(""),
):
    text = (comment_body or "").strip()
    if not text:
        return RedirectResponse(url=f"/clients/{client_id}?notice=comment_empty", status_code=302)
    if len(text) > 8000:
        text = text[:8000]
    author = get_operator_display(request)
    async with AsyncSessionLocalClients() as session:
        client = await _clients_get_by_id_or_404(session, client_id)
        session.add(
            ClientManagerNote(
                client_customer_id=client.customer_id,
                author_display=author,
                body=text,
                created_at=now_kyiv(),
            )
        )
        apply_client_last_touch(client, request)
        await session.commit()
    return RedirectResponse(url=f"/clients/{client_id}?notice=comment_ok", status_code=302)


def _redirect_clients_list(return_qs: str | None, extra: str | None = None) -> RedirectResponse:
    base = "/clients"
    q = _clients_safe_return_query(return_qs)
    if extra:
        q = f"{q}&{extra}" if q else extra.lstrip("&")
    loc = f"{base}?{q}" if q else base
    return RedirectResponse(url=loc, status_code=302)


def _clients_list_ajax_requested(request: Request) -> bool:
    return request.headers.get("x-clients-list-ajax", "").strip().lower() in ("1", "true", "yes")


async def _clients_marketing_manager_labels_map() -> dict[str, str]:
    managers = await _clients_marketing_managers_list()
    return {
        (m.username or "").strip(): ((m.display_name or m.username) or "").strip()
        for m in managers
        if m.username
    }



@router.post("/clients/assign-selected")
async def clients_assign_selected_post(
    request: Request,
    user: str = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_dependency),
):
    """Масове призначення менеджера обраним клієнтам (галочки на поточній сторінці)."""
    ajax = _clients_list_ajax_requested(request)
    form = await request.form()
    return_qs = form.get("return_qs")
    raw_ids = form.getlist("client_id")
    ids: list[int] = []
    for x in raw_ids:
        try:
            ids.append(int(str(x).strip()))
        except ValueError:
            continue
    ids = list(dict.fromkeys(ids))
    assign_raw = (form.get("assign_username") or "").strip()

    def _err():
        if ajax:
            return JSONResponse({"ok": False, "error": "bulk_err"}, status_code=400)
        return _redirect_clients_list(str(return_qs) if return_qs else None, "clients_notice=bulk_err")

    if not ids or not assign_raw:
        return _err()

    clear_assign = assign_raw in ("__none__", "__clear__")
    canon_username: str | None
    if clear_assign:
        canon_username = None
    else:
        managers = await _clients_marketing_managers_list()
        allowed_cf = {m.username.casefold(): m.username for m in managers if m.username}
        canon_username = allowed_cf.get(assign_raw.casefold())
        if not canon_username:
            return _err()

    if len(ids) > CLIENTS_BULK_ASSIGN_MAX:
        ids = ids[:CLIENTS_BULK_ASSIGN_MAX]

    async with AsyncSessionLocalClients() as session:
        stmt = select(Client).where(Client.id.in_(ids))
        hidden_clause = _clients_hidden_where()
        if hidden_clause is not None:
            stmt = stmt.where(hidden_clause)
        rows = list((await session.execute(stmt)).scalars().all())
        if not rows:
            return _err()
        author = get_operator_display(request)
        for row in rows:
            row.crm_assigned_username = canon_username
            apply_client_last_touch(row, request)
            record_crm_history(
                session,
                client=row,
                event_type=CRM_HISTORY_ASSIGNMENT,
                author_display=author,
                assigned_username=canon_username,
            )
        await session.commit()
        applied = len(rows)

    if ajax:
        labels = await _clients_marketing_manager_labels_map()
        updates = []
        for r in rows:
            un = r.crm_assigned_username
            updates.append(
                {
                    "id": r.id,
                    "username": un,
                    "value": "__none__" if not un else un,
                    "display": "Немає" if not un else (labels.get(un) or un),
                }
            )
        return JSONResponse({"ok": True, "applied": applied, "updates": updates})

    return _redirect_clients_list(
        str(return_qs) if return_qs else None,
        f"clients_notice=bulk_ok&clients_n={applied}",
    )



@router.post("/clients/{client_id}/assign-manager")
async def client_row_assign_manager_post(
    request: Request,
    client_id: int,
    user: str = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_dependency),
    assign_username: str = Form(""),
    return_qs: str = Form(""),
):
    """Зміна відповідального менеджера з рядка таблиці клієнтів."""
    ajax = _clients_list_ajax_requested(request)
    assign_raw = (assign_username or "").strip()

    def _err():
        if ajax:
            return JSONResponse({"ok": False, "error": "bulk_err"}, status_code=400)
        return _redirect_clients_list(str(return_qs) if return_qs else None, "clients_notice=bulk_err")

    if not assign_raw:
        return _err()
    clear_assign = assign_raw in ("__none__", "__clear__")
    canon_username: str | None
    if clear_assign:
        canon_username = None
    else:
        managers = await _clients_marketing_managers_list()
        allowed_cf = {m.username.casefold(): m.username for m in managers if m.username}
        canon_username = allowed_cf.get(assign_raw.casefold())
        if not canon_username:
            return _err()

    async with AsyncSessionLocalClients() as session:
        stmt = select(Client).where(Client.id == client_id)
        hidden_clause = _clients_hidden_where()
        if hidden_clause is not None:
            stmt = stmt.where(hidden_clause)
        res = await session.execute(stmt)
        row = res.scalar_one_or_none()
        if not row:
            return _err()
        row.crm_assigned_username = canon_username
        apply_client_last_touch(row, request)
        record_crm_history(
            session,
            client=row,
            event_type=CRM_HISTORY_ASSIGNMENT,
            author_display=get_operator_display(request),
            assigned_username=canon_username,
        )
        await session.commit()
        un = row.crm_assigned_username

    if ajax:
        labels = await _clients_marketing_manager_labels_map()
        return JSONResponse(
            {
                "ok": True,
                "client_id": client_id,
                "username": un,
                "value": "__none__" if not un else un,
                "display": "Немає" if not un else (labels.get(un) or un),
            }
        )

    return _redirect_clients_list(str(return_qs) if return_qs else None, "clients_notice=bulk_ok&clients_n=1")



@router.post("/clients/interactions-selected")
async def clients_interactions_selected_post(
    request: Request,
    user: str = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_dependency),
):
    """Масове оновлення каналів взаємодії для обраних клієнтів."""
    ajax = _clients_list_ajax_requested(request)
    form = await request.form()
    return_qs = form.get("return_qs")
    raw_ids = form.getlist("client_id")
    ids: list[int] = []
    for x in raw_ids:
        try:
            ids.append(int(str(x).strip()))
        except ValueError:
            continue
    ids = list(dict.fromkeys(ids))
    keys = interaction_keys_from_raw_list(form.getlist("interaction"))

    def _err():
        if ajax:
            return JSONResponse({"ok": False, "error": "bulk_err"}, status_code=400)
        return _redirect_clients_list(str(return_qs) if return_qs else None, "clients_notice=bulk_err")

    if not ids:
        return _err()
    if len(ids) > CLIENTS_BULK_ASSIGN_MAX:
        ids = ids[:CLIENTS_BULK_ASSIGN_MAX]
    encoded = serialize_interaction_keys(keys)

    async with AsyncSessionLocalClients() as session:
        stmt = select(Client).where(Client.id.in_(ids))
        hidden_clause = _clients_hidden_where()
        if hidden_clause is not None:
            stmt = stmt.where(hidden_clause)
        rows = list((await session.execute(stmt)).scalars().all())
        if not rows:
            return _err()
        author = get_operator_display(request)
        for row in rows:
            apply_client_interaction_keys(row, keys)
            apply_client_last_touch(row, request)
            record_crm_history(
                session,
                client=row,
                event_type=CRM_HISTORY_CHANNELS,
                author_display=author,
                interaction_keys=keys,
            )
        await session.commit()
        applied = len(rows)

    if ajax:
        return JSONResponse({"ok": True, "applied": applied, "interactions": keys})
    return _redirect_clients_list(str(return_qs) if return_qs else None, f"clients_notice=bulk_ok&clients_n={applied}")



@router.post("/clients/{client_id}/interactions")
async def client_row_interactions_post(
    request: Request,
    client_id: int,
    user: str = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_dependency),
    return_qs: str = Form(""),
    return_detail: str = Form(""),
):
    """Оновлення каналів взаємодії для одного клієнта."""
    ajax = _clients_list_ajax_requested(request)
    form = await request.form()
    keys = interaction_keys_from_raw_list(form.getlist("interaction"))

    def _err(code: str = "bulk_err"):
        if ajax:
            return JSONResponse({"ok": False, "error": code}, status_code=400)
        return _redirect_clients_list(str(return_qs) if return_qs else None, "clients_notice=bulk_err")

    async with AsyncSessionLocalClients() as session:
        stmt = select(Client).where(Client.id == client_id)
        hidden_clause = _clients_hidden_where()
        if hidden_clause is not None:
            stmt = stmt.where(hidden_clause)
        row = (await session.execute(stmt)).scalar_one_or_none()
        if not row:
            return _err()
        apply_client_interaction_keys(row, keys)
        apply_client_last_touch(row, request)
        record_crm_history(
            session,
            client=row,
            event_type=CRM_HISTORY_CHANNELS,
            author_display=get_operator_display(request),
            interaction_keys=keys,
        )
        await session.commit()

    if ajax:
        return JSONResponse(
            {
                "ok": True,
                "client_id": client_id,
                "interactions": keys,
                "interaction_labels": interaction_labels_for_keys(keys),
            }
        )
    if str(return_detail or "").strip() in ("1", "true", "yes"):
        return RedirectResponse(url=f"/clients/{client_id}?notice=crm_ok", status_code=302)
    return _redirect_clients_list(str(return_qs) if return_qs else None, "clients_notice=bulk_ok&clients_n=1")



@router.post("/clients/processing-selected")
async def clients_processing_selected_post(
    request: Request,
    user: str = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_dependency),
):
    """Масова зміна статусу опрацювання ліда."""
    ajax = _clients_list_ajax_requested(request)
    form = await request.form()
    return_qs = form.get("return_qs")
    raw_ids = form.getlist("client_id")
    ids: list[int] = []
    for x in raw_ids:
        try:
            ids.append(int(str(x).strip()))
        except ValueError:
            continue
    ids = list(dict.fromkeys(ids))
    new_status = normalize_processing_status(str(form.get("processing_status") or ""))
    closure_result = normalize_closure_result(str(form.get("closure_result") or ""))

    def _err(code: str = "bulk_err"):
        if ajax:
            return JSONResponse({"ok": False, "error": code}, status_code=400)
        return _redirect_clients_list(str(return_qs) if return_qs else None, f"clients_notice={code}")

    if not ids or new_status == CRM_STATUS_NEUTRAL:
        return _err()
    if new_status == CRM_STATUS_CLOSED and not closure_result:
        return _err("closure_required")
    if len(ids) > CLIENTS_BULK_ASSIGN_MAX:
        ids = ids[:CLIENTS_BULK_ASSIGN_MAX]

    author = get_operator_display(request)
    updates: list[dict] = []
    async with AsyncSessionLocalClients() as session:
        stmt = select(Client).where(Client.id.in_(ids))
        hidden_clause = _clients_hidden_where()
        if hidden_clause is not None:
            stmt = stmt.where(hidden_clause)
        rows = list((await session.execute(stmt)).scalars().all())
        if not rows:
            return _err()
        for row in rows:
            err = apply_processing_status_change(
                session,
                client=row,
                new_status=new_status,
                author_display=author,
                closure_result=closure_result,
            )
            if err:
                return _err(err)
            apply_client_last_touch(row, request)
            st = normalize_processing_status(row.crm_processing_status)
            keys = deserialize_interaction_keys(row.crm_interaction_keys)
            updates.append(
                {
                    "id": row.id,
                    "processing_status": st,
                    "processing_status_label": processing_status_label(st),
                    "interaction_labels": interaction_labels_for_keys(keys),
                    "interaction_keys": keys,
                }
            )
        await session.commit()
        applied = len(rows)

    if ajax:
        return JSONResponse({"ok": True, "applied": applied, "updates": updates})
    return _redirect_clients_list(str(return_qs) if return_qs else None, f"clients_notice=bulk_ok&clients_n={applied}")



@router.post("/clients/{client_id}/processing-status")
async def client_row_processing_status_post(
    request: Request,
    client_id: int,
    user: str = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_dependency),
    processing_status: str = Form(""),
    closure_result: str = Form(""),
    return_qs: str = Form(""),
    return_detail: str = Form(""),
):
    """Зміна статусу опрацювання ліда для одного клієнта."""
    ajax = _clients_list_ajax_requested(request)
    new_status = normalize_processing_status(processing_status)
    closure = normalize_closure_result(closure_result)

    def _err(code: str = "bulk_err"):
        if ajax:
            return JSONResponse({"ok": False, "error": code}, status_code=400)
        return _redirect_clients_list(str(return_qs) if return_qs else None, f"clients_notice={code}")

    if new_status == CRM_STATUS_NEUTRAL:
        return _err()
    if new_status == CRM_STATUS_CLOSED and not closure:
        return _err("closure_required")

    async with AsyncSessionLocalClients() as session:
        stmt = select(Client).where(Client.id == client_id)
        hidden_clause = _clients_hidden_where()
        if hidden_clause is not None:
            stmt = stmt.where(hidden_clause)
        row = (await session.execute(stmt)).scalar_one_or_none()
        if not row:
            return _err()
        err = apply_processing_status_change(
            session,
            client=row,
            new_status=new_status,
            author_display=get_operator_display(request),
            closure_result=closure,
        )
        if err:
            return _err(err)
        apply_client_last_touch(row, request)
        await session.commit()
        st = normalize_processing_status(row.crm_processing_status)
        keys = deserialize_interaction_keys(row.crm_interaction_keys)

    if ajax:
        return JSONResponse(
            {
                "ok": True,
                "client_id": client_id,
                "processing_status": st,
                "processing_status_label": processing_status_label(st),
                "interaction_labels": interaction_labels_for_keys(keys),
                "interaction_keys": keys,
            }
        )
    if str(return_detail or "").strip() in ("1", "true", "yes"):
        return RedirectResponse(url=f"/clients/{client_id}?notice=crm_ok", status_code=302)
    return _redirect_clients_list(str(return_qs) if return_qs else None, "clients_notice=bulk_ok&clients_n=1")



@router.post("/clients/sync")
async def clients_sync_post(
    request: Request,
    user: str = Depends(require_admin),
    _csrf: None = Depends(verify_csrf_dependency),
):
    referer = (request.headers.get("referer") or "/sync").split("?")[0] or "/sync"

    async def run_clients_sync() -> None:
        try:
            from bot.services.clients_sync import sync_clients_from_feed
            from web.integrations.crm_analytics_cache import cache_invalidate_all

            await sync_clients_from_feed()
            cache_invalidate_all()
        except Exception as exc:
            logger.error("❌ Помилка синхронізації клієнтів: %s", exc, exc_info=True)

    asyncio.create_task(run_clients_sync())
    return RedirectResponse(url=f"{referer}?clients_synced=1", status_code=302)
