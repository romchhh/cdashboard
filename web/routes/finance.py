"""Finance routes."""

from web.deps import *  # noqa: F403, F401
from web.clients_query import *  # noqa: F403, F401

import asyncio
from fastapi import APIRouter

router = APIRouter()

@router.get("/", response_class=HTMLResponse)
async def index(request: Request, user: str = Depends(get_current_user)):
    role = getattr(request.state, "role", ROLE_ADMIN)
    access = getattr(request.state, "access", normalize_access(None, role))
    return RedirectResponse(url=default_home_path(access, role))



@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user: str = Depends(get_current_user)):
    role = getattr(request.state, "role", "admin")
    period = request.query_params.get("period", "month")
    date_from_str = request.query_params.get("date_from_str", "")
    date_to_str = request.query_params.get("date_to_str", "")
    date_single_str = request.query_params.get("date_single_str", "")

    single = parse_single_day(date_single_str)
    custom = parse_custom_period(date_from_str, date_to_str) if not single else None
    if single:
        date_from, date_to, period_name = single
    elif custom:
        date_from, date_to, period_name = custom
    else:
        date_from, date_to, period_name = period_dates(period)

    async with AsyncSessionLocal() as session:
        balances = await get_latest_balances(session)
        pl_period = await get_pl(session, date_from, date_to, None)
        
        # Отримуємо ID категорій "Готівка" / "Зняття Готівки" та "Богдан" для відображення
        cash_cat_result = await session.execute(
            select(Category.id).where(Category.name.in_(CASH_MOVEMENT_CATEGORY_NAMES))
        )
        cash_category_ids = [row[0] for row in cash_cat_result.all()]
        bogdan_cat_result = await session.execute(
            select(Category.id).where(Category.name == "Богдан")
        )
        bogdan_category_id = bogdan_cat_result.scalar_one_or_none()

        employee_cat_result = await session.execute(
            select(Category.id).where(Category.name == EMPLOYEE_EXPENSE_CATEGORY_NAME)
        )
        employee_category_id = employee_cat_result.scalar_one_or_none()
    
    # Отримуємо дані про заморожений капітал (Procurement Copilot)
    procurement_data = {}
    try:
        from bot.services.abc_analysis import reports_service
        warehouse_analytics = await reports_service.get_warehouse_analytics()
        procurement_data = {
            "total_frozen_capital": warehouse_analytics.get("total_frozen_capital", 0),
            "total_products": warehouse_analytics.get("total_products", 0),
            "out_of_stock_products": warehouse_analytics.get("out_of_stock_products", 0),
            "total_stock_qty": warehouse_analytics.get("total_stock_qty", 0),
        }
    except Exception as e:
        logger.error(f"Помилка отримання даних Procurement Copilot: {e}")
        procurement_data = {
            "total_frozen_capital": 0,
            "total_products": 0,
            "out_of_stock_products": 0,
            "total_stock_qty": 0,
        }

    clients_stats: dict[str, float | int] | None = None
    if getattr(request.state, "access_marketing", False):
        try:
            from bot.database.clients_models import Client, ClientOrder

            async with AsyncSessionLocalClients() as cs:
                hidden = _clients_hidden_where()
                total_stmt = select(func.count()).select_from(Client)
                reg_stmt = select(func.count()).select_from(Client).where(Client.is_registered.is_(True))
                ltv_stmt = select(func.coalesce(func.sum(Client.ltv), 0))
                if hidden is not None:
                    total_stmt = total_stmt.where(hidden)
                    reg_stmt = reg_stmt.where(hidden)
                    ltv_stmt = ltv_stmt.where(hidden)
                total_c = await cs.execute(total_stmt)
                reg_c = await cs.execute(reg_stmt)
                ord_stmt = select(func.count()).select_from(ClientOrder)
                if CLIENTS_EXPORT_EXCLUDED_CUSTOMER_IDS:
                    ord_stmt = ord_stmt.where(
                        ClientOrder.client_customer_id.notin_(CLIENTS_EXPORT_EXCLUDED_CUSTOMER_IDS)
                    )
                ord_c = await cs.execute(ord_stmt)
                ltv_sum = await cs.execute(ltv_stmt)
            tc = int(total_c.scalar() or 0)
            rc = int(reg_c.scalar() or 0)
            clients_stats = {
                "total": tc,
                "registered": rc,
                "guests": max(0, tc - rc),
                "orders_rows": int(ord_c.scalar() or 0),
                "sum_ltv": float(ltv_sum.scalar() or 0),
            }
        except Exception as e:
            logger.warning("Дашборд: не вдалося завантажити статистику клієнтів: %s", e)
            clients_stats = None

    async with AsyncSessionLocal() as session:
        # Виключаємо переміщення коштів ("Готівка", "Зняття Готівки") та "Богдан"
        top_expenses_result = await session.execute(
            select(
                Category.id,
                Category.name,
                func.sum(Transaction.amount).label("total")
            )
            .select_from(Transaction)
            .join(Category, Transaction.category_id == Category.id)
            .where(
                and_(
                    Transaction.transaction_type == TransactionType.EXPENSE,
                    Transaction.status == TransactionStatus.CONFIRMED,
                    Transaction.date >= date_from,
                    Transaction.date <= date_to,
                    (Category.id.notin_(cash_category_ids) if cash_category_ids else True),
                    Category.name != "Богдан",
                    (Category.id != employee_category_id if (role == ROLE_MANAGER and employee_category_id) else True),
                )
            )
            .group_by(Category.id, Category.name)
            .order_by(func.abs(func.sum(Transaction.amount)).desc())
        )
        top_expense_categories = [
            {"id": row[0], "name": row[1], "amount": abs(float(row[2]))}
            for row in top_expenses_result.all()
        ]

        recent_txs = await session.execute(
            select(Transaction)
            .options(selectinload(Transaction.category), selectinload(Transaction.fop))
            .where(
                and_(
                    Transaction.status == TransactionStatus.CONFIRMED,
                    Transaction.date >= date_from,
                    Transaction.date <= date_to,
                )
            )
            .order_by(Transaction.date.desc())
            .limit(5)
        )
        recent_transactions = recent_txs.scalars().all()

        pending_count = await session.execute(
            select(func.count(Transaction.id)).where(
                or_(
                    Transaction.status == TransactionStatus.PENDING,
                    Transaction.category_id.is_(None),
                )
            )
        )
        pending = pending_count.scalar() or 0

        # Розрахунок балансу готівки (всі транзакції до date_to включно)
        # Враховуємо:
        # 1. Транзакції з source == CASH (операції з готівкою)
        # 2. Переміщення з карток в готівку (категорія "Готівка", expense) - збільшують баланс
        cash_balance_conditions = [
            Transaction.source == TransactionSource.CASH,
            Transaction.date <= date_to,
        ]
        sum_cash_income = await session.execute(
            select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                and_(*cash_balance_conditions, Transaction.transaction_type == TransactionType.INCOME)
            )
        )
        sum_cash_expense = await session.execute(
            select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                and_(*cash_balance_conditions, Transaction.transaction_type == TransactionType.EXPENSE)
            )
        )
        # Баланс готівки рахуємо ТІЛЬКИ за операціями source == CASH
        # Категорії "Готівка" / "Зняття Готівки" не пересувають гроші в касу автоматично
        cash_balance = float(sum_cash_income.scalar() or 0) - float(sum_cash_expense.scalar() or 0)
        
        # Розрахунок доходів та витрат з готівки за період (виключаючи переміщення коштів)
        cash_period_conditions_income = [
            Transaction.source == TransactionSource.CASH,
            Transaction.transaction_type == TransactionType.INCOME,
            Transaction.date >= date_from,
            Transaction.date <= date_to,
        ]
        cash_period_conditions_expense = [
            Transaction.source == TransactionSource.CASH,
            Transaction.transaction_type == TransactionType.EXPENSE,
            Transaction.date >= date_from,
            Transaction.date <= date_to,
        ]
        
        # Виключаємо переміщення коштів ("Готівка", "Зняття Готівки")
        if cash_category_ids:
            cash_period_conditions_income.append(Transaction.category_id.notin_(cash_category_ids))
            cash_period_conditions_expense.append(Transaction.category_id.notin_(cash_category_ids))
        
        cash_income_period = await session.execute(
            select(func.coalesce(func.sum(Transaction.amount), 0)).where(and_(*cash_period_conditions_income))
        )
        cash_expense_period = await session.execute(
            select(func.coalesce(func.sum(Transaction.amount), 0)).where(and_(*cash_period_conditions_expense))
        )
        cash_income_amount = float(cash_income_period.scalar() or 0)
        cash_expense_amount = abs(float(cash_expense_period.scalar() or 0))

        fop_limits = []
        fop_limits_summary = None
        if role != "manager":
            from bot.services.fop_income_limit import load_ytd_income_by_fop, summarize_fop_limits

            fop_limits = await load_ytd_income_by_fop(session)
            fop_limits_summary = summarize_fop_limits(fop_limits)

    mono = [b for b in balances if b["source"] == "monobank"]
    nova = [b for b in balances if b["source"] == "novapay"]
    total_uah = sum(
        float(b["balance"]) for b in balances
        if (b.get("currency") or "UAH").upper() == "UAH"
    ) + cash_balance
    total_mono_uah = sum(
        float(b["balance"]) for b in mono
        if (b.get("currency") or "UAH").upper() == "UAH"
    )
    total_nova_uah = sum(
        float(b["balance"]) for b in nova
        if (b.get("currency") or "UAH").upper() == "UAH"
    )
    latest = max((b["recorded_at"] for b in balances), default=None)

    display_date_from = date_from_str or (date_from.strftime("%Y-%m-%d") if not date_single_str else "")
    display_date_to = date_to_str or (date_to.strftime("%Y-%m-%d") if not date_single_str else "")
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "mono_balances": mono,
            "nova_balances": nova,
            "total_uah": total_uah,
            "total_mono_uah": total_mono_uah,
            "total_nova_uah": total_nova_uah,
            "cash_balance": cash_balance,
            "cash_income_period": cash_income_amount,
            "cash_expense_period": cash_expense_amount,
            "latest": latest,
            "pl_period": pl_period,
            "period_name": period_name,
            "date_from": date_from,
            "date_to": date_to,
            "period": period,
            "date_from_str": display_date_from,
            "date_to_str": display_date_to,
            "date_single_str": date_single_str or "",
            "top_expense_categories": top_expense_categories,
            "recent_transactions": recent_transactions,
            "pending_count": pending,
            "cash_category_ids": cash_category_ids,
            "bogdan_category_id": bogdan_category_id,
            "procurement_data": procurement_data,
            "clients_stats": clients_stats,
            "fop_limits": fop_limits if role != "manager" else [],
            "fop_limits_summary": fop_limits_summary if role != "manager" else None,
        },
    )



@router.get("/statistics", response_class=HTMLResponse)
async def statistics_page(request: Request, user: str = Depends(get_current_user)):
    """Сторінка статистики (область «Фінанси»)."""
    if not getattr(request.state, "access_finance", True):
        raise HTTPException(status_code=403, detail="Доступ заборонено")
    role = getattr(request.state, "role", ROLE_ADMIN)
    period = request.query_params.get("period", "month")
    date_from_str = request.query_params.get("date_from_str", "")
    date_to_str = request.query_params.get("date_to_str", "")
    date_single_str = request.query_params.get("date_single_str", "")

    single = parse_single_day(date_single_str)
    custom = parse_custom_period(date_from_str, date_to_str) if not single else None
    if single:
        date_from, date_to, period_name = single
    elif custom:
        date_from, date_to, period_name = custom
    else:
        date_from, date_to, period_name = period_dates(period)

    async with AsyncSessionLocal() as session:
        pl_period = await get_pl(session, date_from, date_to, None)
        cash_cat_result = await session.execute(
            select(Category.id).where(Category.name.in_(CASH_MOVEMENT_CATEGORY_NAMES))
        )
        cash_category_ids = [row[0] for row in cash_cat_result.all()]

        # Усі категорії витрат за період: від більшого до меншого (desc); id для посилання на транзакції
        # Виключаємо переміщення коштів ("Готівка", "Зняття Готівки") та "Богдан"
        top_expenses_result = await session.execute(
            select(
                Category.id,
                Category.name,
                func.sum(Transaction.amount).label("total")
            )
            .select_from(Transaction)
            .join(Category, Transaction.category_id == Category.id)
            .where(
                and_(
                    Transaction.transaction_type == TransactionType.EXPENSE,
                    Transaction.status == TransactionStatus.CONFIRMED,
                    Transaction.date >= date_from,
                    Transaction.date <= date_to,
                    (Category.id.notin_(cash_category_ids) if cash_category_ids else True),
                    Category.name != "Богдан",
                )
            )
            .group_by(Category.id, Category.name)
            .order_by(func.abs(func.sum(Transaction.amount)).desc())
        )
        top_expense_categories = [
            {"id": row[0], "name": row[1], "amount": abs(float(row[2]))}
            for row in top_expenses_result.all()
        ]

        expense_chart_daily = await get_daily_totals(
            session,
            date_from,
            date_to,
            None,
            include_cash=True,
            flow="expense",
        )
        try:
            expense_chart_json = json.dumps(expense_chart_daily, ensure_ascii=False, default=str)
        except Exception:
            expense_chart_json = "[]"

        # Кількість непідтверджених транзакцій
        pending_result = await session.execute(
            select(func.count(Transaction.id)).where(
                or_(
                    Transaction.status == TransactionStatus.PENDING,
                    Transaction.category_id.is_(None),
                )
            )
        )
        pending = pending_result.scalar() or 0

    display_date_from = date_from_str or (date_from.strftime("%Y-%m-%d") if not single and not custom else "")
    display_date_to = date_to_str or (date_to.strftime("%Y-%m-%d") if not single and not custom else "")
    return templates.TemplateResponse(
        "statistics.html",
        {
            "request": request,
            "pl_period": pl_period,
            "period_name": period_name,
            "date_from": date_from,
            "date_to": date_to,
            "period": period,
            "date_from_str": display_date_from,
            "date_to_str": display_date_to,
            "date_single_str": date_single_str or "",
            "top_expense_categories": top_expense_categories,
            "expense_chart_daily": expense_chart_daily,
            "expense_chart_json": expense_chart_json,
            "pending_count": pending,
            "is_manager": role == "manager",
            "cash_category_ids": cash_category_ids,
        },
    )



@router.post("/statistics", response_class=HTMLResponse)
async def statistics_post(
    request: Request,
    user: str = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_dependency),
    period: str = Form("month"),
    date_from_str: str = Form(""),
    date_to_str: str = Form(""),
):
    form = await request.form()
    raw = form.getlist("fop_ids")
    include_cash = "cash" in raw
    fop_raw = [x for x in raw if x != "cash"]
    async with AsyncSessionLocal() as session:
        fop_filter_values = await resolve_fop_filter_for_totals(
            session, fop_raw, include_cash=include_cash
        )
    custom = parse_custom_period(date_from_str, date_to_str)
    date_from, date_to, period_name = custom if custom else period_dates(period)

    async with AsyncSessionLocal() as session:
        from bot.services.fop_filter import load_fop_filter_options

        fop_filter_options = await load_fop_filter_options(session)
        fops = await get_fop_list(session)
        fop_sources = await get_fop_sources(session, [f.id for f in fops])
        pl = await get_pl(
            session, date_from, date_to, fop_filter_values=fop_filter_values
        )
        breakdown = await get_breakdown(
            session, date_from, date_to, fop_filter_values=fop_filter_values
        )
    result = {
        "period_name": period_name,
        "income": pl["income"],
        "expense": pl["expense"],
        "profit": pl["profit"],
        "margin": pl["margin"],
        "categories": breakdown["categories"],
        "total_expense": breakdown["total_expense"],
    }
    display_date_from = date_from_str or (date_from.strftime("%Y-%m-%d") if not custom else "")
    display_date_to = date_to_str or (date_to.strftime("%Y-%m-%d") if not custom else "")
    return templates.TemplateResponse(
        "statistics.html",
        {
            "request": request,
            "fops": fops,
            "fop_sources": fop_sources,
            "result": result,
            "period": period,
            "date_from_str": display_date_from,
            "date_to_str": display_date_to,
            "fop_filter_options": fop_filter_options,
            "selected_fop_ids": raw,
            "include_cash": include_cash,
        },
    )



CLIENT_REPORT_TYPES = frozenset({"clients_mkt", "clients_monthly"})


async def _build_clients_report(
    report_type: str,
    monthly_outcome: str | None = None,
    monthly_from: str | None = None,
    monthly_to: str | None = None,
    chart_granularity: str | None = None,
) -> dict:
    outcome = normalize_monthly_chart_outcome(monthly_outcome)
    async with AsyncSessionLocalClients() as session:
        dash = await load_clients_dashboard_snapshot(
            session,
            monthly_chart_outcome=outcome,
            monthly_date_from=monthly_from,
            monthly_date_to=monthly_to,
            chart_granularity=chart_granularity,
        )
    title = (
        "Клієнти — аналітика"
        if report_type == "clients_mkt"
        else "Замовлення та виручка"
    )
    return {"type": report_type, "title": title, "dash": dash}


@router.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request, user: str = Depends(require_admin)):
    report_type = (request.query_params.get("report_type") or "").strip()
    result = None
    if report_type in CLIENT_REPORT_TYPES:
        result = await _build_clients_report(
            report_type,
            monthly_outcome=request.query_params.get("monthly_outcome"),
            monthly_from=request.query_params.get("monthly_from"),
            monthly_to=request.query_params.get("monthly_to"),
            chart_granularity=request.query_params.get("chart_granularity"),
        )
    async with AsyncSessionLocal() as session:
        from bot.services.fop_filter import load_fop_filter_options

        fop_filter_options = await load_fop_filter_options(session)
        fops = await get_fop_list(session)
        fop_sources = await get_fop_sources(session, [f.id for f in fops])
    return templates.TemplateResponse(
        "reports.html",
        {
            "request": request,
            "fops": fops,
            "fop_sources": fop_sources,
            "fop_filter_options": fop_filter_options,
            "result": result,
            "include_cash": False,
            "selected_fop_ids": [],
            "pl_flow": "all",
            "report_type": report_type or "",
        },
    )



@router.post("/reports", response_class=HTMLResponse)
async def reports_post(
    request: Request,
    user: str = Depends(require_admin),
    _csrf: None = Depends(verify_csrf_dependency),
    report_type: str = Form("cf"),
    period: str = Form("month"),
    date_from_str: str = Form(""),
    date_to_str: str = Form(""),
    date_single_str: str = Form(""),
    date_from_str_2: str = Form(""),
    date_to_str_2: str = Form(""),
):
    form = await request.form()
    raw = form.getlist("fop_ids")
    include_cash = form.get("include_cash") == "1"
    async with AsyncSessionLocal() as session:
        fop_filter_values = await resolve_fop_filter_for_totals(
            session, raw, include_cash=include_cash
        )
    fop_ids_clean = fop_filter_values_to_legacy_ids(raw)
    pl_flow_raw = (form.get("pl_flow") or "all").strip()
    pl_flow = pl_flow_raw if pl_flow_raw in ("income", "expense") else "all"

    if report_type in CLIENT_REPORT_TYPES:
        result = await _build_clients_report(
            report_type,
            monthly_outcome=form.get("monthly_outcome"),
            monthly_from=(form.get("monthly_from") or "").strip() or None,
            monthly_to=(form.get("monthly_to") or "").strip() or None,
            chart_granularity=form.get("chart_granularity"),
        )
        async with AsyncSessionLocal() as session:
            from bot.services.fop_filter import load_fop_filter_options

            fop_filter_options = await load_fop_filter_options(session)
            fops = await get_fop_list(session)
            fop_sources = await get_fop_sources(session, [f.id for f in fops])
        return templates.TemplateResponse(
            "reports.html",
            {
                "request": request,
                "fops": fops,
                "fop_sources": fop_sources,
                "fop_filter_options": fop_filter_options,
                "result": result,
                "chart_daily": [],
                "chart_daily_json": "[]",
                "period": period,
                "report_type": report_type,
                "period_name": "",
                "date_from_str": "",
                "date_to_str": "",
                "report_query_from": "",
                "report_query_to": "",
                "date_single_str": "",
                "date_from_str_2": "",
                "date_to_str_2": "",
                "selected_fop_ids": [],
                "include_cash": include_cash,
                "pl_flow": pl_flow,
                "report_fop_filter_label": "",
            },
        )

    if not fop_filter_values:
        fop_ids_or_none = None
    else:
        fop_ids_or_none = fop_ids_clean or []
    single = parse_single_day(date_single_str) if report_type != "cmp" else None
    custom = parse_custom_period(date_from_str, date_to_str) if not single else None
    date_from, date_to, period_name = single if single else (custom if custom else period_dates(period))

    async with AsyncSessionLocal() as session:
        fops = await get_fop_list(session)
        if report_type == "cf":
            data = await get_cashflow(
                session,
                date_from,
                date_to,
                None,
                include_cash=include_cash,
                flow=pl_flow,
                fop_filter_values=fop_filter_values,
            )
            result = {
                "title": f"CASHFLOW за {period_name}",
                "type": "cf",
                "income": data["income"],
                "expense": data["expense"],
                "net": data["net"],
            }
        elif report_type == "pl":
            data = await get_pl(
                session,
                date_from,
                date_to,
                None,
                include_cash=include_cash,
                flow=pl_flow,
                fop_filter_values=fop_filter_values,
            )
            expense_sorted = sorted(data["expense_by_category"].items(), key=lambda x: x[1], reverse=True)[:15]
            result = {
                "title": f"P&L за {period_name}",
                "type": "pl",
                "income": data["income"],
                "expense": data["expense"],
                "profit": data["profit"],
                "margin": data["margin"],
                "income_by_fop": data["income_by_fop"],
                "expense_by_category": dict(expense_sorted),
                "expense_categories": data.get("expense_categories") or [],
                "cash_expense_total": data.get("cash_expense_total", 0),
                "cash_withdrawal_total": data.get("cash_withdrawal_total", 0),
                "bogdan_total": data.get("bogdan_total", 0),
            }
        elif report_type == "bd":
            data = await get_breakdown(
                session,
                date_from,
                date_to,
                None,
                include_cash=include_cash,
                flow=pl_flow,
                fop_filter_values=fop_filter_values,
            )
            result = {
                "title": f"Витрати за категоріями — {period_name}",
                "type": "bd",
                "categories": data["categories"],
                "total_expense": data["total_expense"],
            }
        elif report_type == "cmp":
            custom2 = parse_custom_period(date_from_str_2, date_to_str_2)
            if custom2:
                p2_from, p2_to, p2_name = custom2
                p1_name = f"{date_from.strftime('%d.%m.%Y')} – {date_to.strftime('%d.%m.%Y')}"
                p2_name = f"{p2_from.strftime('%d.%m.%Y')} – {p2_to.strftime('%d.%m.%Y')}"
            elif parse_custom_period(date_from_str, date_to_str):
                delta = date_to - date_from
                p2_to = date_from - timedelta(seconds=1)
                p2_from = p2_to - delta
                p1_name = f"{date_from.strftime('%d.%m.%Y')} – {date_to.strftime('%d.%m.%Y')}"
                p2_name = f"{p2_from.strftime('%d.%m.%Y')} – {p2_to.strftime('%d.%m.%Y')}"
            elif period == "today":
                p2_to = date_from - timedelta(seconds=1)
                p2_from = (date_from - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                p1_name, p2_name = "Сьогодні", "Вчора"
            elif period == "yesterday":
                p2_to = date_from - timedelta(seconds=1)
                p2_from = p2_to.replace(hour=0, minute=0, second=0, microsecond=0)
                p1_name, p2_name = "Вчора", "Передвчора"
            elif period == "week":
                delta = date_to - date_from
                p2_to = date_from - timedelta(seconds=1)
                p2_from = p2_to - delta
                p1_name = f"{date_from.strftime('%d.%m')} – {date_to.strftime('%d.%m')}"
                p2_name = f"{p2_from.strftime('%d.%m')} – {p2_to.strftime('%d.%m')}"
            elif period == "year":
                p2_to = date_from - timedelta(days=1)
                p2_from = p2_to.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
                p1_name = str(date_from.year)
                p2_name = str(p2_from.year)
            elif period == "quarter":
                from bot.services.period_range import (
                    previous_tax_quarter,
                    tax_quarter,
                    tax_quarter_label,
                    tax_quarter_start,
                )

                q = tax_quarter(date_from)
                prev_year, prev_q = previous_tax_quarter(date_from.year, q)
                p2_from = tax_quarter_start(prev_year, prev_q)
                p2_to = date_from - timedelta(seconds=1)
                p1_name = tax_quarter_label(date_from.year, q)
                p2_name = tax_quarter_label(prev_year, prev_q)
            else:
                p2_to = date_from - timedelta(days=1)
                p2_from = p2_to.replace(day=1)
                p1_name = date_from.strftime("%B %Y")
                p2_name = p2_from.strftime("%B %Y")
            data = await compare_periods(
                session,
                date_from,
                date_to,
                p2_from,
                p2_to,
                None,
                include_cash=include_cash,
                flow=pl_flow,
                fop_filter_values=fop_filter_values,
            )
            chart_daily_p1 = await get_daily_totals(
                session,
                date_from,
                date_to,
                None,
                include_cash=include_cash,
                flow=pl_flow,
                fop_filter_values=fop_filter_values,
            )
            chart_daily_p2 = await get_daily_totals(
                session,
                p2_from,
                p2_to,
                None,
                include_cash=include_cash,
                flow=pl_flow,
                fop_filter_values=fop_filter_values,
            )
            # Серіалізуємо дані для графіків
            chart_daily_p1_json = json.dumps(chart_daily_p1, ensure_ascii=False, default=str) if chart_daily_p1 else "[]"
            chart_daily_p2_json = json.dumps(chart_daily_p2, ensure_ascii=False, default=str) if chart_daily_p2 else "[]"
            result = {
                "title": f"Порівняння: {p1_name} vs {p2_name}",
                "type": "cmp",
                "p1_name": p1_name,
                "p2_name": p2_name,
                "period1": data["period1"],
                "period2": data["period2"],
                "income_change": data["income_change"],
                "expense_change": data["expense_change"],
                "profit_change": data["profit_change"],
                "chart_daily_p1": chart_daily_p1,
                "chart_daily_p2": chart_daily_p2,
                "chart_daily_p1_json": chart_daily_p1_json,
                "chart_daily_p2_json": chart_daily_p2_json,
            }
        else:
            result = None
        
        # Генеруємо дані для графіка для всіх типів звітів, крім порівняння періодів
        chart_daily = []
        chart_daily_json = "[]"
        if result and result.get("type") != "cmp":
            chart_daily = await get_daily_totals(
                session,
                date_from,
                date_to,
                None,
                include_cash=include_cash,
                flow=pl_flow,
                fop_filter_values=fop_filter_values,
            )
            # Серіалізуємо дані в JSON в Python для надійності
            try:
                chart_daily_json = json.dumps(chart_daily, ensure_ascii=False, default=str)
            except Exception:
                chart_daily_json = "[]"
        from bot.services.fop_filter import load_fop_filter_options

        fop_filter_options = await load_fop_filter_options(session)
        fops = await get_fop_list(session)
        fop_sources = await get_fop_sources(session, [f.id for f in fops])
    display_date_from = date_from_str or (date_from.strftime("%Y-%m-%d") if not single and not custom else "")
    display_date_to = date_to_str or (date_to.strftime("%Y-%m-%d") if not single and not custom else "")
    report_query_from = date_from.strftime("%Y-%m-%d")
    report_query_to = date_to.strftime("%Y-%m-%d")
    return templates.TemplateResponse(
        "reports.html",
        {
            "request": request,
            "fops": fops,
            "fop_sources": fop_sources,
            "fop_filter_options": fop_filter_options,
            "result": result,
            "chart_daily": chart_daily,
            "chart_daily_json": chart_daily_json,
            "period": period,
            "report_type": report_type,
            "period_name": period_name,
            "date_from_str": display_date_from,
            "date_to_str": display_date_to,
            "report_query_from": report_query_from,
            "report_query_to": report_query_to,
            "date_single_str": date_single_str or "",
            "date_from_str_2": date_from_str_2 or "",
            "date_to_str_2": date_to_str_2 or "",
            "selected_fop_ids": [str(x) for x in raw],
            "include_cash": include_cash,
            "pl_flow": pl_flow,
            "report_fop_filter_label": report_fop_filter_label(
                fops,
                None if not fop_filter_values else (fop_ids_clean or []),
                fop_ids_clean,
                include_cash,
                fop_filter_options=fop_filter_options,
                selected_values=[str(x) for x in raw],
            ),
        },
    )


PER_PAGE = 50



@router.get("/transactions", response_class=HTMLResponse)
async def transactions_page(request: Request, user: str = Depends(get_current_user)):
    # Порожній period у query (наприклад після POST з очищеним hidden) ламає period_dates — трактуємо як month
    _raw_period = (request.query_params.get("period") or "").strip()
    period = _raw_period if _raw_period else "month"
    try:
        page = max(1, int(request.query_params.get("page", 1)))
    except ValueError:
        page = 1
    date_from_str = (request.query_params.get("date_from_str") or "").strip() or None
    date_to_str = (request.query_params.get("date_to_str") or "").strip() or None
    date_single_str = request.query_params.get("date_single_str")
    category_id_param = request.query_params.get("category_id")
    category_id = int(category_id_param) if category_id_param and str(category_id_param).isdigit() else None
    async with AsyncSessionLocal() as session:
        from bot.services.fop_filter import load_fop_filter_options

        fop_filter_options = await load_fop_filter_options(session)
        fops = await get_fop_list(session)
        fop_sources = await get_fop_sources(session, [f.id for f in fops])
        # Отримуємо ID категорій "Готівка" / "Зняття Готівки" та "Богдан" для виключення з витрат
        cash_cat_result = await session.execute(
            select(Category.id).where(Category.name.in_(CASH_MOVEMENT_CATEGORY_NAMES))
        )
        cash_category_ids = [row[0] for row in cash_cat_result.all()]
        bogdan_cat_result = await session.execute(
            select(Category.id).where(Category.name == "Богдан")
        )
        bogdan_category_id = bogdan_cat_result.scalar_one_or_none()
    date_from, date_to = None, None
    if date_single_str:
        try:
            day = datetime.strptime(date_single_str.strip(), "%Y-%m-%d")
            date_from = day.replace(hour=0, minute=0, second=0, microsecond=0)
            date_to = day.replace(hour=23, minute=59, second=59, microsecond=999999)
        except ValueError:
            pass
    if (not date_from or not date_to) and date_from_str and date_to_str:
        try:
            date_from = datetime.strptime(date_from_str, "%Y-%m-%d")
            date_to = datetime.strptime(date_to_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        except ValueError:
            pass
    if not date_from or not date_to:
        date_from, date_to, _ = period_dates(period)

    # Базові умови для фільтрації транзакцій (без типу транзакції)
    base_conditions = [
        Transaction.date >= date_from,
        Transaction.date <= date_to,
        Transaction.status == TransactionStatus.CONFIRMED,
    ]
    # Не показуємо в загальному списку операції «Переміщення в готівку»:
    # це всі доходи (INCOME) з категоріями "Готівка"/"Зняття Готівки" — як по картках, так і дзеркальні в касі.
    #
    # ВАЖЛИВО: якщо користувач явно фільтрує по цій категорії, тоді ці транзакції треба показувати.
    if cash_category_ids and not (category_id and category_id in cash_category_ids):
        base_conditions.append(
            ~and_(
                Transaction.category_id.in_(cash_category_ids),
                Transaction.transaction_type == TransactionType.INCOME,
            )
        )
    
    # Умови для відображення транзакцій (з урахуванням фільтрів)
    conditions = [*base_conditions]
    tx_type = request.query_params.get("tx_type", "all")
    if tx_type == "income":
        conditions.append(Transaction.transaction_type == TransactionType.INCOME)
    elif tx_type == "expense":
        conditions.append(Transaction.transaction_type == TransactionType.EXPENSE)
        # Виключаємо транзакції з категоріями переміщення коштів та "Богдан" з витрат
        # Але якщо користувач явно вибрав одну з цих категорій — не виключаємо.
        if cash_category_ids and not (category_id and category_id in cash_category_ids):
            conditions.append(Transaction.category_id.notin_(cash_category_ids))
        if bogdan_category_id and category_id != bogdan_category_id:
            conditions.append(Transaction.category_id != bogdan_category_id)

    # Фільтр по ФОПах + окремий псевдо-ФОП "cash" (готівка)
    fop_ids_raw = request.query_params.getlist("fop_ids")

    async with AsyncSessionLocal() as session:
        fop_filter_for_totals = await resolve_fop_filter_for_totals(session, fop_ids_raw)
        fop_scope = transaction_fop_scope_condition(fop_filter_for_totals)
        if fop_scope is not None:
            conditions.append(fop_scope)
            base_conditions.append(fop_scope)
        if category_id:
            conditions.append(Transaction.category_id == category_id)
            base_conditions.append(Transaction.category_id == category_id)

        count_result = await session.execute(
            select(func.count(Transaction.id)).where(and_(*conditions))
        )
        total_count = count_result.scalar() or 0

        # Підсумки: None = усі ФОПи включно з архівом (історичні транзакції не губляться)
        if fop_filter_for_totals:
            pl_result = await get_pl(
                session,
                date_from,
                date_to,
                None,
                category_id,
                fop_filter_values=fop_filter_for_totals,
            )
        else:
            pl_result = await get_pl(
                session,
                date_from,
                date_to,
                None,
                category_id,
            )
        summary_income = pl_result["income"]
        summary_expense = pl_result["expense"]
        summary_net = summary_income - summary_expense
        
        # Окремо рахуємо переміщення з картки в готівку та переведення коштів з get_pl()
        transfer_amount = pl_result.get("cash_withdrawal_total", 0.0)
        bogdan_amount = pl_result.get("bogdan_total", 0.0)
        offset = (page - 1) * PER_PAGE
        result = await session.execute(
            select(Transaction)
            .options(selectinload(Transaction.category), selectinload(Transaction.fop))
            .where(and_(*conditions))
            .order_by(Transaction.date.desc())
            .offset(offset)
            .limit(PER_PAGE)
        )
        transactions = result.scalars().all()

    total_pages = (total_count + PER_PAGE - 1) // PER_PAGE if total_count else 1
    async with AsyncSessionLocal() as session:
        cat_result = await session.execute(
            select(Category).order_by(Category.is_expense.desc(), Category.name)
        )
        categories = cat_result.scalars().all()
        pending_result = await session.execute(
            select(func.count(Transaction.id)).where(
                or_(
                    Transaction.status == TransactionStatus.PENDING,
                    Transaction.category_id.is_(None),
                )
            )
        )
        pending_count = int(pending_result.scalar() or 0)

    q = request.query_params
    def pagination_url(p: int) -> str:
        params = [f"period={period}", f"tx_type={tx_type}", f"page={p}"]
        if date_single_str:
            params.append(f"date_single_str={date_single_str}")
        elif date_from_str and date_to_str:
            params.append(f"date_from_str={date_from_str}")
            params.append(f"date_to_str={date_to_str}")
        for fid in q.getlist("fop_ids"):
            params.append(f"fop_ids={fid}")
        if category_id:
            params.append(f"category_id={category_id}")
        return "/transactions?" + "&".join(params)

    selected_category = None
    if category_id:
        async with AsyncSessionLocal() as session:
            cat_result = await session.execute(select(Category).where(Category.id == category_id))
            selected_category = cat_result.scalar_one_or_none()

    return templates.TemplateResponse(
        "transactions.html",
        {
            "request": request,
            "fops": fops,
            "fop_sources": fop_sources,
            "fop_filter_options": fop_filter_options,
            "categories": categories,
            "transactions": transactions,
            "period": period,
            "tx_type": tx_type,
            "date_from": date_from,
            "date_to": date_to,
            "page": page,
            "total_pages": total_pages,
            "total_count": total_count,
            "per_page": PER_PAGE,
            "pagination_url": pagination_url,
            "selected_fop_ids": request.query_params.getlist("fop_ids"),
            "current_query": request.url.query,
            "date_single_str": date_single_str or "",
            "date_from_str": date_from_str or "",
            "date_to_str": date_to_str or "",
            "category_id": category_id,
            "selected_category": selected_category,
            "summary_income": summary_income,
            "summary_expense": summary_expense,
            "summary_net": summary_net,
            "transfer_amount": transfer_amount,
            "bogdan_amount": bogdan_amount,
            "cash_category_ids": cash_category_ids,
            "bogdan_category_id": bogdan_category_id,
            "pending_count": pending_count,
        },
    )



@router.post("/transactions")
async def transactions_post(
    request: Request,
    user: str = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_dependency),
    period: str = Form("month"),
    tx_type: str = Form("all"),
    date_from_str: str = Form(""),
    date_to_str: str = Form(""),
    date_single_str: str = Form(""),
    category_id: str = Form(""),
):
    form = await request.form()
    raw = form.getlist("fop_ids")
    period_norm = (period or "").strip() or "month"
    params = [f"period={period_norm}", f"tx_type={tx_type}", "page=1"]
    if date_single_str and date_single_str.strip():
        params.append(f"date_single_str={date_single_str.strip()}")
    elif date_from_str and date_to_str:
        params.append(f"date_from_str={date_from_str}")
        params.append(f"date_to_str={date_to_str}")
    for value in raw:
        val = str(value).strip()
        if val:
            params.append(f"fop_ids={val}")
    if category_id and str(category_id).isdigit():
        params.append(f"category_id={category_id}")
    return RedirectResponse(url="/transactions?" + "&".join(params), status_code=302)



@router.post("/transactions/{tx_id:int}/category")
async def transaction_set_category(
    request: Request,
    tx_id: int,
    user: str = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_dependency),
    category_id: str = Form(""),
):
    referer = request.headers.get("referer") or "/transactions"
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Transaction).where(Transaction.id == tx_id)
        )
        tx = result.scalar_one_or_none()
        if not tx:
            raise HTTPException(status_code=404, detail="Транзакцію не знайдено")
        cat_id = int(category_id) if category_id and str(category_id).isdigit() else None
        if cat_id is not None:
            res = await session.execute(select(Category).where(Category.id == cat_id))
            cat = res.scalar_one_or_none()
            if not cat:
                cat_id = None
            else:
                # Якщо вибрана категорія «Готівка» і це витрата по картці, створюємо
                # ОДНУ дзеркальну транзакцію в касі (source=CASH, INCOME), але лише якщо
                # такої ще немає.
                if cat.name == "Готівка" and tx.transaction_type == TransactionType.EXPENSE and tx.source != TransactionSource.CASH:
                    existing_cash = await session.execute(
                        select(Transaction).where(
                            Transaction.source == TransactionSource.CASH,
                            Transaction.transaction_type == TransactionType.INCOME,
                            Transaction.category_id == cat.id,
                            Transaction.amount == abs(float(tx.amount)),
                            Transaction.date == tx.date,
                        )
                    )
                    if not existing_cash.scalar_one_or_none():
                        cash_tx = Transaction(
                            amount=abs(float(tx.amount)),
                            transaction_type=TransactionType.INCOME,
                            date=tx.date,
                            source=TransactionSource.CASH,
                            fop_id=None,
                            category_id=cat.id,
                            status=TransactionStatus.CONFIRMED,
                            description=((tx.description or "") + " (переміщення в готівку)").strip(),
                            operator_username=get_operator_display(request),
                        )
                        session.add(cash_tx)
        else:
            # Якщо категорію знято і транзакція була готівкою через категорію, можна залишити як є
            pass
        tx.category_id = cat_id
        tx.status = TransactionStatus.CONFIRMED
        tx.operator_username = get_operator_display(request)
        await session.commit()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return Response(status_code=204)
    return RedirectResponse(url=referer, status_code=302)



@router.get("/categories", response_class=HTMLResponse)
async def categories_page(request: Request, user: str = Depends(get_current_user)):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Category).order_by(Category.is_expense.desc(), Category.name)
        )
        categories = result.scalars().all()
        # Кількість транзакцій по категоріях (для кнопки «Видалити»)
        count_result = await session.execute(
            select(Transaction.category_id, func.count(Transaction.id))
            .where(Transaction.category_id.isnot(None))
            .group_by(Transaction.category_id)
        )
        tx_counts = {row[0]: row[1] for row in count_result.all()}
    return templates.TemplateResponse(
        "categories.html",
        {
            "request": request,
            "categories": categories,
            "tx_counts": tx_counts,
            "error": request.query_params.get("error"),
            "success": request.query_params.get("success"),
        },
    )



@router.get("/categories/add", response_class=HTMLResponse)
async def category_add_page(request: Request, user: str = Depends(get_current_user)):
    return templates.TemplateResponse("category_edit.html", {"request": request, "category": None})



@router.post("/categories/add")
async def category_add_post(
    request: Request,
    user: str = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_dependency),
    name: str = Form(...),
    is_expense: str = Form(""),
):
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Category).where(Category.name == name.strip()))
        if res.scalar_one_or_none():
            return templates.TemplateResponse(
                "category_edit.html",
                {"request": request, "category": None, "error": "Категорія з такою назвою вже існує"},
            )
        cat = Category(name=name.strip(), is_expense=(is_expense == "1"))
        session.add(cat)
        await session.commit()
    return RedirectResponse(url="/categories", status_code=302)



@router.get("/categories/{category_id:int}/edit", response_class=HTMLResponse)
async def category_edit_page(
    request: Request,
    category_id: int,
    user: str = Depends(get_current_user),
):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Category).options(selectinload(Category.rules)).where(Category.id == category_id)
        )
        category = result.scalar_one_or_none()
    if not category:
        raise HTTPException(status_code=404, detail="Категорію не знайдено")
    return templates.TemplateResponse(
        "category_edit.html",
        {"request": request, "category": category, "error": request.query_params.get("error")},
    )



@router.post("/categories/{category_id:int}/edit")
async def category_edit_post(
    request: Request,
    category_id: int,
    user: str = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_dependency),
    name: str = Form(...),
    is_expense: str = Form(""),
):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Category).options(selectinload(Category.rules)).where(Category.id == category_id)
        )
        category = result.scalar_one_or_none()
        if not category:
            raise HTTPException(status_code=404, detail="Категорію не знайдено")
        res = await session.execute(
            select(Category).where(Category.name == name.strip(), Category.id != category_id)
        )
        if res.scalar_one_or_none():
            return templates.TemplateResponse(
                "category_edit.html",
                {"request": request, "category": category, "error": "Категорія з такою назвою вже існує"},
            )
        category.name = name.strip()
        category.is_expense = (is_expense == "1")
        await session.commit()
    return RedirectResponse(url="/categories", status_code=302)



@router.post("/categories/{category_id:int}/rules/add")
async def category_rule_add_post(
    request: Request,
    category_id: int,
    user: str = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_dependency),
    keyword: str = Form(""),
    counterparty_pattern: str = Form(""),
):
    keyword = keyword.strip() if keyword else ""
    counterparty_pattern = counterparty_pattern.strip() if counterparty_pattern else ""
    if not keyword and not counterparty_pattern:
        return RedirectResponse(
            url=f"/categories/{category_id}/edit?error=rule_empty",
            status_code=302,
        )
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Category).where(Category.id == category_id))
        if not result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Категорію не знайдено")
        rule = ClassificationRule(
            category_id=category_id,
            keyword=keyword or None,
            counterparty_pattern=counterparty_pattern or None,
        )
        session.add(rule)
        await session.commit()
    return RedirectResponse(url=f"/categories/{category_id}/edit", status_code=302)



@router.get("/categories/{category_id:int}/rules/{rule_id:int}/edit", response_class=HTMLResponse)
async def category_rule_edit_page(
    request: Request,
    category_id: int,
    rule_id: int,
    user: str = Depends(get_current_user),
):
    async with AsyncSessionLocal() as session:
        cat_result = await session.execute(select(Category).where(Category.id == category_id))
        category = cat_result.scalar_one_or_none()
        if not category:
            raise HTTPException(status_code=404, detail="Категорію не знайдено")
        rule_result = await session.execute(
            select(ClassificationRule).where(
                ClassificationRule.id == rule_id,
                ClassificationRule.category_id == category_id,
            )
        )
        rule = rule_result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Правило не знайдено")
    return templates.TemplateResponse(
        "rule_edit.html",
        {
            "request": request,
            "category": category,
            "rule": rule,
            "error": request.query_params.get("error"),
        },
    )



@router.post("/categories/{category_id:int}/rules/{rule_id:int}/edit")
async def category_rule_edit_post(
    request: Request,
    category_id: int,
    rule_id: int,
    user: str = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_dependency),
    keyword: str = Form(""),
    counterparty_pattern: str = Form(""),
):
    keyword = keyword.strip() if keyword else ""
    counterparty_pattern = counterparty_pattern.strip() if counterparty_pattern else ""
    if not keyword and not counterparty_pattern:
        return RedirectResponse(
            url=f"/categories/{category_id}/rules/{rule_id}/edit?error=rule_empty",
            status_code=302,
        )
    async with AsyncSessionLocal() as session:
        rule_result = await session.execute(
            select(ClassificationRule).where(
                ClassificationRule.id == rule_id,
                ClassificationRule.category_id == category_id,
            )
        )
        rule = rule_result.scalar_one_or_none()
        if not rule:
            raise HTTPException(status_code=404, detail="Правило не знайдено")
        rule.keyword = keyword or None
        rule.counterparty_pattern = counterparty_pattern or None
        await session.commit()
    return RedirectResponse(url=f"/categories/{category_id}/edit", status_code=302)



@router.post("/categories/{category_id:int}/rules/{rule_id:int}/delete")
async def category_rule_delete_post(
    request: Request,
    category_id: int,
    rule_id: int,
    user: str = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_dependency),
):
    async with AsyncSessionLocal() as session:
        rule_result = await session.execute(
            select(ClassificationRule).where(
                ClassificationRule.id == rule_id,
                ClassificationRule.category_id == category_id,
            )
        )
        rule = rule_result.scalar_one_or_none()
        if not rule:
            raise HTTPException(status_code=404, detail="Правило не знайдено")
        await session.delete(rule)
        await session.commit()
    return RedirectResponse(url=f"/categories/{category_id}/edit", status_code=302)



@router.post("/categories/{category_id:int}/delete")
async def category_delete_post(
    request: Request,
    category_id: int,
    user: str = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_dependency),
):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Category).where(Category.id == category_id))
        category = result.scalar_one_or_none()
        if not category:
            raise HTTPException(status_code=404, detail="Категорію не знайдено")

        # Якщо по категорії ще є транзакції — не даємо видалити
        count_result = await session.execute(
            select(func.count(Transaction.id)).where(Transaction.category_id == category_id)
        )
        if (count_result.scalar() or 0) > 0:
            return RedirectResponse(url="/categories?error=has_transactions", status_code=302)

        # Видаляємо пов'язані правила класифікації, щоб не порушити NOT NULL у classification_rules.category_id
        await session.execute(
            delete(ClassificationRule).where(ClassificationRule.category_id == category_id)
        )

        await session.delete(category)
        await session.commit()
    return RedirectResponse(url="/categories", status_code=302)


# --- Менеджери (тільки для адміна) ---


@router.get("/pending", response_class=HTMLResponse)
async def pending_page(request: Request, user: str = Depends(get_current_user)):
    try:
        page = max(1, int(request.query_params.get("page", 1)))
    except (ValueError, TypeError):
        page = 1
    async with AsyncSessionLocal() as session:
        fops = await get_fop_list(session)
        cat_result = await session.execute(
            select(Category).order_by(Category.is_expense.desc(), Category.name)
        )
        categories = cat_result.scalars().all()
        cash_cat_result = await session.execute(
            select(Category.id).where(Category.name.in_(CASH_MOVEMENT_CATEGORY_NAMES))
        )
        cash_category_ids = [row[0] for row in cash_cat_result.all()]
        bogdan_cat_result = await session.execute(
            select(Category.id).where(Category.name == "Богдан")
        )
        bogdan_category_id = bogdan_cat_result.scalar_one_or_none()
        count_result = await session.execute(
            select(func.count())
            .select_from(Transaction)
            .where(
                or_(
                    Transaction.status == TransactionStatus.PENDING,
                    Transaction.category_id.is_(None),
                )
            )
        )
        total_count = count_result.scalar() or 0
        offset = (page - 1) * PENDING_PER_PAGE
        result = await session.execute(
            select(Transaction)
            .options(selectinload(Transaction.category), selectinload(Transaction.fop))
            .where(
                or_(
                    Transaction.status == TransactionStatus.PENDING,
                    Transaction.category_id.is_(None),
                )
            )
            .order_by(Transaction.date.desc())
            .offset(offset)
            .limit(PENDING_PER_PAGE)
        )
        transactions = result.scalars().all()
    total_pages = (total_count + PENDING_PER_PAGE - 1) // PENDING_PER_PAGE if total_count else 1

    def pagination_url(p: int) -> str:
        return f"/pending?page={p}"

    return templates.TemplateResponse(
        "pending.html",
        {
            "request": request,
            "fops": fops,
            "categories": categories,
            "transactions": transactions,
            "total_count": total_count,
            "page": page,
            "cash_category_ids": cash_category_ids,
            "bogdan_category_id": bogdan_category_id,
            "total_pages": total_pages,
            "pagination_url": pagination_url,
        },
    )


SYNC_PERIOD_DAYS = {"day": 1, "week": 7, "month": 31, "quarter": 90, "year": 0}



@router.get("/sync", response_class=HTMLResponse)
async def sync_page(request: Request, user: str = Depends(require_admin)):
    return templates.TemplateResponse(
        "sync.html",
        {
            "request": request,
            "sync_periods": list(SYNC_PERIOD_DAYS.keys()),
            "products_synced_ok": request.query_params.get("synced") == "1",
            "analytics_calculated_ok": request.query_params.get("analytics_calculated") == "1",
            "clients_synced_ok": request.query_params.get("clients_synced") == "1",
            "ad_sync_started": request.query_params.get("ad_sync_started") == "1",
            "error": request.query_params.get("error"),
        },
    )



@router.post("/sync")
async def sync_post(
    request: Request,
    user: str = Depends(require_admin),
    _csrf: None = Depends(verify_csrf_dependency),
    period: str = Form("month"),
):
    """
    Запуск синхронізації транзакцій і балансів.
    Важкий імпорт Моно/NovaPay іде в окремий asyncio-task, відповідь віддається одразу.
    """
    from bot.scheduler.tasks import sync_all_transactions, sync_balances, sync_fop_ytd_and_reconcile
    from bot.services.period_range import days_since_year_start

    if period == "year":
        days = days_since_year_start()
    else:
        days = SYNC_PERIOD_DAYS.get(period, 31)

    async def run_sync_jobs() -> None:
        try:
            if period == "year":
                await sync_fop_ytd_and_reconcile()
            else:
                await sync_all_transactions(days=days)
            await sync_balances()
        except Exception as exc:
            logger.error("Фонова синхронізація транзакцій: %s", exc, exc_info=True)

    asyncio.create_task(run_sync_jobs())

    return RedirectResponse(url="/dashboard?synced=1", status_code=302)


# --- Ручні витрати поза картами ---

MANUAL_EXPENSE_PER_PAGE = 50


def _manual_expense_date_range(
    period: str,
    date_from_str: str | None,
    date_to_str: str | None,
    date_single_str: str | None,
) -> tuple[datetime | None, datetime | None, str]:
    """Діапазон дат для списку витрат поза картами. period=all — без обмеження дат."""
    if (period or "").strip() == "all":
        return None, None, "Усі записи"
    single = parse_single_day(date_single_str or "")
    if single:
        return single
    custom = parse_custom_period(date_from_str or "", date_to_str or "") if not single else None
    if custom:
        return custom
    return period_dates(period or "month")


def _manual_expense_redirect_url(
    *,
    success: str | None = None,
    error: str | None = None,
    period: str | None = None,
    date_from_str: str | None = None,
    date_to_str: str | None = None,
    date_single_str: str | None = None,
    page: int | None = None,
) -> str:
    from urllib.parse import urlencode

    params: dict[str, str] = {}
    if success:
        params["success"] = success
    if error:
        params["error"] = error
    if period:
        params["period"] = period
    if date_from_str:
        params["date_from_str"] = date_from_str
    if date_to_str:
        params["date_to_str"] = date_to_str
    if date_single_str:
        params["date_single_str"] = date_single_str
    if page and page > 1:
        params["page"] = str(page)
    qs = urlencode(params)
    return "/manual-expense" + (f"?{qs}" if qs else "")


def _manual_expense_filter_from_form(
    filter_period: str = "month",
    filter_date_from_str: str = "",
    filter_date_to_str: str = "",
    filter_date_single_str: str = "",
    filter_page: str = "1",
) -> dict:
    """Параметри фільтра з POST-форми (після збереження / редагування)."""
    return {
        "period": (filter_period or "month").strip() or "month",
        "date_from_str": (filter_date_from_str or "").strip(),
        "date_to_str": (filter_date_to_str or "").strip(),
        "date_single_str": (filter_date_single_str or "").strip(),
        "page": max(1, int(filter_page)) if str(filter_page or "").isdigit() else 1,
    }


@router.get("/manual-expense", response_class=HTMLResponse)
async def manual_expense_page(request: Request, user: str = Depends(get_current_user)):
    """Сторінка для додавання витрат поза межами карт і готівки"""
    period = request.query_params.get("period") or "month"
    date_from_str = request.query_params.get("date_from_str", "")
    date_to_str = request.query_params.get("date_to_str", "")
    date_single_str = request.query_params.get("date_single_str", "")
    try:
        page = max(1, int(request.query_params.get("page", 1)))
    except ValueError:
        page = 1

    date_from, date_to, period_name = _manual_expense_date_range(
        period, date_from_str, date_to_str, date_single_str
    )

    async with AsyncSessionLocal() as session:
        expense_cats = await session.execute(
            select(Category).where(Category.is_expense == True).order_by(Category.name)
        )
        expense_categories = expense_cats.scalars().all()

        base_conditions = [
            Transaction.source == TransactionSource.MANUAL,
            Transaction.transaction_type == TransactionType.EXPENSE,
        ]
        if date_from is not None and date_to is not None:
            base_conditions.extend([
                Transaction.date >= date_from,
                Transaction.date <= date_to,
            ])
        base_where = and_(*base_conditions)

        count_result = await session.execute(
            select(func.count(Transaction.id)).where(base_where)
        )
        total_count = int(count_result.scalar() or 0)
        total_pages = max(1, (total_count + MANUAL_EXPENSE_PER_PAGE - 1) // MANUAL_EXPENSE_PER_PAGE)
        if page > total_pages:
            page = total_pages

        sum_result = await session.execute(
            select(func.coalesce(func.sum(Transaction.amount), 0)).where(base_where)
        )
        period_total = float(sum_result.scalar() or 0)

        offset = (page - 1) * MANUAL_EXPENSE_PER_PAGE
        list_result = await session.execute(
            select(Transaction)
            .options(selectinload(Transaction.category))
            .where(base_where)
            .order_by(Transaction.date.desc())
            .offset(offset)
            .limit(MANUAL_EXPENSE_PER_PAGE)
        )
        manual_expenses = list_result.scalars().all()

    success = request.query_params.get("success")
    error = request.query_params.get("error")
    return templates.TemplateResponse(
        "manual_expense.html",
        {
            "request": request,
            "expense_categories": expense_categories,
            "manual_expenses": manual_expenses,
            "period": period,
            "period_name": period_name,
            "date_from": date_from,
            "date_to": date_to,
            "date_from_str": date_from_str,
            "date_to_str": date_to_str,
            "date_single_str": date_single_str,
            "page": page,
            "total_pages": total_pages,
            "total_count": total_count,
            "period_total": period_total,
            "success": success,
            "error": error,
        },
    )



@router.post("/manual-expense")
async def manual_expense_post(
    request: Request,
    user: str = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_dependency),
    amount: str = Form(...),
    category_id: str = Form(...),
    description: str = Form(""),
    datetime_tx: str = Form(""),
    filter_period: str = Form("month"),
    filter_date_from_str: str = Form(""),
    filter_date_to_str: str = Form(""),
    filter_date_single_str: str = Form(""),
    filter_page: str = Form("1"),
):
    """Додати витрату поза межами карт і готівки"""
    filt = _manual_expense_filter_from_form(
        filter_period=filter_period,
        filter_date_from_str=filter_date_from_str,
        filter_date_to_str=filter_date_to_str,
        filter_date_single_str=filter_date_single_str,
        filter_page=filter_page,
    )
    try:
        amount_val = float(amount.replace(",", ".").replace(" ", "").strip())
    except (ValueError, AttributeError):
        return RedirectResponse(
            url=_manual_expense_redirect_url(error="amount", **filt), status_code=302
        )
    if amount_val <= 0:
        return RedirectResponse(
            url=_manual_expense_redirect_url(error="amount", **filt), status_code=302
        )
    cat_id = int(category_id) if category_id and str(category_id).isdigit() else None
    if not cat_id:
        return RedirectResponse(
            url=_manual_expense_redirect_url(error="category", **filt), status_code=302
        )
    tx_date = parse_tx_datetime(datetime_tx)

    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Category).where(Category.id == cat_id, Category.is_expense == True))
        if not res.scalar_one_or_none():
            return RedirectResponse(
                url=_manual_expense_redirect_url(error="category", **filt), status_code=302
            )
        operator = get_operator_display(request)
        tx = Transaction(
            amount=amount_val,
            transaction_type=TransactionType.EXPENSE,
            date=tx_date,
            source=TransactionSource.MANUAL,
            fop_id=None,
            category_id=cat_id,
            status=TransactionStatus.CONFIRMED,
            description=(description or "").strip() or None,
            operator_username=operator,
        )
        session.add(tx)
        await session.commit()
    return RedirectResponse(
        url=_manual_expense_redirect_url(success="1", **filt), status_code=302
    )



@router.post("/manual-expense/{tx_id:int}/edit")
async def manual_expense_edit_post(
    request: Request,
    tx_id: int,
    user: str = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_dependency),
    amount: str = Form(...),
    category_id: str = Form(...),
    description: str = Form(""),
    datetime_tx: str = Form(""),
    filter_period: str = Form("month"),
    filter_date_from_str: str = Form(""),
    filter_date_to_str: str = Form(""),
    filter_date_single_str: str = Form(""),
    filter_page: str = Form("1"),
):
    """Редагувати витрату поза картами"""
    filt = _manual_expense_filter_from_form(
        filter_period=filter_period,
        filter_date_from_str=filter_date_from_str,
        filter_date_to_str=filter_date_to_str,
        filter_date_single_str=filter_date_single_str,
        filter_page=filter_page,
    )
    try:
        amount_val = float(amount.replace(",", ".").replace(" ", "").strip())
    except (ValueError, AttributeError):
        return RedirectResponse(
            url=_manual_expense_redirect_url(error="amount", **filt), status_code=302
        )
    if amount_val <= 0:
        return RedirectResponse(
            url=_manual_expense_redirect_url(error="amount", **filt), status_code=302
        )
    cat_id = int(category_id) if category_id and str(category_id).isdigit() else None
    if not cat_id:
        return RedirectResponse(
            url=_manual_expense_redirect_url(error="category", **filt), status_code=302
        )
    tx_date = parse_tx_datetime(datetime_tx)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Transaction).where(Transaction.id == tx_id, Transaction.source == TransactionSource.MANUAL)
        )
        tx = result.scalar_one_or_none()
        if not tx:
            return RedirectResponse(
                url=_manual_expense_redirect_url(error="notfound", **filt), status_code=302
            )

        res = await session.execute(select(Category).where(Category.id == cat_id, Category.is_expense == True))
        if not res.scalar_one_or_none():
            return RedirectResponse(
                url=_manual_expense_redirect_url(error="category", **filt), status_code=302
            )

        tx.amount = amount_val
        tx.category_id = cat_id
        tx.date = tx_date
        tx.description = (description or "").strip() or None
        tx.operator_username = get_operator_display(request)
        await session.commit()
    return RedirectResponse(
        url=_manual_expense_redirect_url(success="edited", **filt), status_code=302
    )



@router.post("/manual-expense/{tx_id:int}/delete")
async def manual_expense_delete_post(
    request: Request,
    tx_id: int,
    user: str = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_dependency),
    filter_period: str = Form("month"),
    filter_date_from_str: str = Form(""),
    filter_date_to_str: str = Form(""),
    filter_date_single_str: str = Form(""),
    filter_page: str = Form("1"),
):
    """Видалити витрату поза картами"""
    filt = _manual_expense_filter_from_form(
        filter_period=filter_period,
        filter_date_from_str=filter_date_from_str,
        filter_date_to_str=filter_date_to_str,
        filter_date_single_str=filter_date_single_str,
        filter_page=filter_page,
    )
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Transaction).where(Transaction.id == tx_id, Transaction.source == TransactionSource.MANUAL)
        )
        tx = result.scalar_one_or_none()
        if not tx:
            return RedirectResponse(
                url=_manual_expense_redirect_url(error="notfound", **filt), status_code=302
            )
        await session.delete(tx)
        await session.commit()
    return RedirectResponse(
        url=_manual_expense_redirect_url(success="deleted", **filt), status_code=302
    )


# --- Готівка: внесення в касу / винесення з каси + історія ---

CASH_PER_PAGE = 50


def parse_tx_datetime(datetime_str: str | None) -> datetime:
    """Парсинг дати та часу для операції (одне поле datetime-local); якщо порожньо — поточний момент (Київ)."""
    if not datetime_str or not datetime_str.strip():
        return now_kyiv()
    s = datetime_str.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return now_kyiv()



@router.get("/cash", response_class=HTMLResponse)
async def cash_page(request: Request, user: str = Depends(get_current_user)):
    period = request.query_params.get("period", "month")
    try:
        page = max(1, int(request.query_params.get("page", 1)))
    except ValueError:
        page = 1
    date_from_str = request.query_params.get("date_from_str")
    date_to_str = request.query_params.get("date_to_str")
    date_single_str = request.query_params.get("date_single_str")
    date_from, date_to = None, None
    if date_single_str:
        try:
            day = datetime.strptime(date_single_str.strip(), "%Y-%m-%d")
            date_from = day.replace(hour=0, minute=0, second=0, microsecond=0)
            date_to = day.replace(hour=23, minute=59, second=59, microsecond=999999)
        except ValueError:
            pass
    if (not date_from or not date_to) and date_from_str and date_to_str:
        try:
            date_from = datetime.strptime(date_from_str, "%Y-%m-%d")
            date_to = datetime.strptime(date_to_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        except ValueError:
            pass
    if not date_from or not date_to:
        date_from, date_to, _ = period_dates(period)

    now = now_kyiv()
    async with AsyncSessionLocal() as session:
        # Отримуємо ID категорій "Готівка" / "Зняття Готівки" для показу переміщень з карток
        cash_cat_result = await session.execute(
            select(Category.id).where(Category.name.in_(CASH_MOVEMENT_CATEGORY_NAMES))
        )
        cash_category_ids = [row[0] for row in cash_cat_result.all()]
        
        # Умови для відображення транзакцій:
        # тільки операції з готівкою (source == CASH)
        conditions = [
            Transaction.date >= date_from,
            Transaction.date <= date_to,
            Transaction.source == TransactionSource.CASH,
        ]
        
        # Залишок готівки в касі станом на кінець обраного періоду (date_to)
        # Враховуємо тільки:
        # 1. Транзакції з source == CASH (операції з готівкою)
        balance_conditions = [
            Transaction.source == TransactionSource.CASH,
            Transaction.date <= date_to,
        ]
        sum_bal_income = await session.execute(
            select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                and_(*balance_conditions, Transaction.transaction_type == TransactionType.INCOME)
            )
        )
        sum_bal_expense = await session.execute(
            select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                and_(*balance_conditions, Transaction.transaction_type == TransactionType.EXPENSE)
            )
        )
        # Баланс готівки рахуємо лише за source == CASH
        # Категорії "Готівка" / "Зняття Готівки" не додають/зменшують баланс каси
        cash_balance_now = float(sum_bal_income.scalar() or 0) - float(sum_bal_expense.scalar() or 0)
        is_balance_today = date_to.date() == now.date()
        cash_balance_label = "На поточний момент" if is_balance_today else f"Станом на {date_to.strftime('%d.%m.%Y')}"

        count_result = await session.execute(
            select(func.count(Transaction.id)).where(and_(*conditions))
        )
        total_count = count_result.scalar() or 0
        
        # Підрахунок підсумків: використовуємо get_pl() для узгодженості з дашбордом
        # Але тільки для транзакцій з готівкою (source == CASH) та переміщень (категорія "Готівка")
        pl_result = await get_pl(session, date_from, date_to, None, None)
        
        # Для сторінки готівки показуємо:
        # - Доходи з готівки (source == CASH, income)
        # - Витрати з готівки (source == CASH, expense) 
        # - Переміщення з карток в готівку (категорія "Готівка", expense) окремо, без включення в доходи/витрати
        income_conditions = [
            Transaction.date >= date_from,
            Transaction.date <= date_to,
            Transaction.source == TransactionSource.CASH,
            Transaction.transaction_type == TransactionType.INCOME
        ]
        expense_conditions = [
            Transaction.date >= date_from,
            Transaction.date <= date_to,
            Transaction.source == TransactionSource.CASH,
            Transaction.transaction_type == TransactionType.EXPENSE
        ]
        sum_income = await session.execute(
            select(func.coalesce(func.sum(Transaction.amount), 0)).where(and_(*income_conditions))
        )
        sum_expense = await session.execute(
            select(func.coalesce(func.sum(Transaction.amount), 0)).where(and_(*expense_conditions))
        )
        summary_income = float(sum_income.scalar() or 0)
        summary_expense = float(sum_expense.scalar() or 0)
        
        # Переміщення з карток в готівку рахуємо окремо (cash_withdrawal_total),
        # але НЕ включаємо ні в доходи, ні у витрати, щоб не спотворювати P&L по готівці.
        cash_withdrawal_total = pl_result.get("cash_withdrawal_total", 0.0)

        summary_net = summary_income - summary_expense
        offset = (page - 1) * CASH_PER_PAGE
        result = await session.execute(
            select(Transaction)
            .options(selectinload(Transaction.category), selectinload(Transaction.fop))
            .where(and_(*conditions))
            .order_by(Transaction.date.desc())
            .offset(offset)
            .limit(CASH_PER_PAGE)
        )
        cash_transactions = result.scalars().all()
        expense_cats = await session.execute(
            select(Category).where(Category.is_expense == True).order_by(Category.name)
        )
        expense_categories = expense_cats.scalars().all()

    total_pages = (total_count + CASH_PER_PAGE - 1) // CASH_PER_PAGE if total_count else 1
    today_ymd = now.strftime("%Y-%m-%d")
    q = request.query_params

    def pagination_url(p: int) -> str:
        params = [f"period={period}", f"page={p}"]
        if date_single_str:
            params.append(f"date_single_str={date_single_str}")
        elif date_from_str and date_to_str:
            params.append(f"date_from_str={date_from_str}&date_to_str={date_to_str}")
        return "/cash?" + "&".join(params)

    success = request.query_params.get("success")
    error = request.query_params.get("error")
    return templates.TemplateResponse(
        "cash.html",
        {
            "request": request,
            "expense_categories": expense_categories,
            "cash_transactions": cash_transactions,
            "cash_balance_now": cash_balance_now,
            "cash_balance_label": cash_balance_label,
            "date_from": date_from,
            "date_to": date_to,
            "period": period,
            "page": page,
            "total_pages": total_pages,
            "total_count": total_count,
            "per_page": CASH_PER_PAGE,
            "summary_income": summary_income,
            "summary_expense": summary_expense,
            "summary_net": summary_net,
            "cash_withdrawal_total": cash_withdrawal_total,
            "pagination_url": pagination_url,
            "date_single_str": date_single_str or "",
            "date_from_str": date_from_str or "",
            "date_to_str": date_to_str or "",
            "today_ymd": today_ymd,
            "success": success,
            "error": error,
        },
    )



@router.post("/cash")
async def cash_filter_post(
    request: Request,
    user: str = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_dependency),
    period: str = Form("month"),
    date_from_str: str = Form(""),
    date_to_str: str = Form(""),
    date_single_str: str = Form(""),
):
    params = [f"period={period}", "page=1"]
    if date_single_str and date_single_str.strip():
        params.append(f"date_single_str={date_single_str.strip()}")
    elif date_from_str and date_to_str:
        params.append(f"date_from_str={date_from_str}")
        params.append(f"date_to_str={date_to_str}")
    return RedirectResponse(url="/cash?" + "&".join(params), status_code=302)



@router.post("/cash/in")
async def cash_in_post(
    request: Request,
    user: str = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_dependency),
    amount: str = Form(...),
    description: str = Form(""),
    datetime_tx: str = Form(""),
):
    try:
        amount_val = float(amount.replace(",", ".").replace(" ", "").strip())
    except (ValueError, AttributeError):
        return RedirectResponse(url="/cash?error=amount", status_code=302)
    if amount_val <= 0:
        return RedirectResponse(url="/cash?error=amount", status_code=302)
    tx_date = parse_tx_datetime(datetime_tx)

    async with AsyncSessionLocal() as session:
        sales_cat_id = await get_sales_category_id(session)
        if not sales_cat_id:
            return RedirectResponse(url="/cash?error=no_sales_category", status_code=302)
        operator = get_operator_display(request)
        tx = Transaction(
            amount=amount_val,
            transaction_type=TransactionType.INCOME,
            date=tx_date,
            source=TransactionSource.CASH,
            fop_id=None,
            category_id=sales_cat_id,
            status=TransactionStatus.CONFIRMED,
            description=(description or "").strip() or None,
            operator_username=operator,
        )
        session.add(tx)
        await session.commit()
    return RedirectResponse(url="/cash?success=in", status_code=302)



@router.post("/cash/out")
async def cash_out_post(
    request: Request,
    user: str = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_dependency),
    amount: str = Form(...),
    category_id: str = Form(...),
    description: str = Form(""),
    datetime_tx: str = Form(""),
):
    try:
        amount_val = float(amount.replace(",", ".").replace(" ", "").strip())
    except (ValueError, AttributeError):
        return RedirectResponse(url="/cash?error=amount", status_code=302)
    if amount_val <= 0:
        return RedirectResponse(url="/cash?error=amount", status_code=302)
    cat_id = int(category_id) if category_id and str(category_id).isdigit() else None
    if not cat_id:
        return RedirectResponse(url="/cash?error=category", status_code=302)
    tx_date = parse_tx_datetime(datetime_tx)

    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Category).where(Category.id == cat_id, Category.is_expense == True))
        if not res.scalar_one_or_none():
            return RedirectResponse(url="/cash?error=category", status_code=302)
        operator = get_operator_display(request)
        tx = Transaction(
            amount=amount_val,
            transaction_type=TransactionType.EXPENSE,
            date=tx_date,
            source=TransactionSource.CASH,
            fop_id=None,
            category_id=cat_id,
            status=TransactionStatus.CONFIRMED,
            description=(description or "").strip() or None,
            operator_username=operator,
        )
        session.add(tx)
        await session.commit()
    return RedirectResponse(url="/cash?success=out", status_code=302)



@router.post("/cash/{tx_id:int}/edit")
async def cash_edit_post(
    request: Request,
    tx_id: int,
    user: str = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_dependency),
    amount: str = Form(...),
    description: str = Form(""),
    datetime_tx: str = Form(""),
    category_id: str = Form(""),
):
    """Редагувати операцію готівки (тільки source=CASH)."""
    try:
        amount_val = float(amount.replace(",", ".").replace(" ", "").strip())
    except (ValueError, AttributeError):
        return RedirectResponse(url="/cash?error=amount", status_code=302)
    if amount_val <= 0:
        return RedirectResponse(url="/cash?error=amount", status_code=302)
    tx_date = parse_tx_datetime(datetime_tx)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Transaction).where(Transaction.id == tx_id, Transaction.source == TransactionSource.CASH)
        )
        tx = result.scalar_one_or_none()
        if not tx:
            return RedirectResponse(url="/cash?error=notfound", status_code=302)
        if tx.transaction_type == TransactionType.EXPENSE:
            cat_id = int(category_id) if category_id and str(category_id).isdigit() else None
            if not cat_id:
                return RedirectResponse(url="/cash?error=category", status_code=302)
            res = await session.execute(select(Category).where(Category.id == cat_id, Category.is_expense == True))
            if not res.scalar_one_or_none():
                return RedirectResponse(url="/cash?error=category", status_code=302)
            tx.category_id = cat_id
        tx.amount = amount_val
        tx.date = tx_date
        tx.description = (description or "").strip() or None
        tx.operator_username = get_operator_display(request)
        await session.commit()
    return RedirectResponse(url="/cash?success=edited", status_code=302)



@router.post("/cash/{tx_id:int}/delete")
async def cash_delete_post(
    request: Request,
    tx_id: int,
    user: str = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf_dependency),
):
    """Видалити операцію готівки (тільки source=CASH)."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Transaction).where(Transaction.id == tx_id, Transaction.source == TransactionSource.CASH)
        )
        tx = result.scalar_one_or_none()
        if not tx:
            return RedirectResponse(url="/cash?error=notfound", status_code=302)
        await session.delete(tx)
        await session.commit()
    return RedirectResponse(url="/cash?success=deleted", status_code=302)

