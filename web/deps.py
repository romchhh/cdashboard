"""
Спільні залежності веб-роутів: шаблони, константи, хелпери, реекспорт моделей і сервісів.
Роутери імпортують звідси замість globals().update(web.main).
"""

from __future__ import annotations

import html
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from fastapi import BackgroundTasks, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, delete, desc, func, nullslast, or_, select
from sqlalchemy.orm import selectinload

from bot.database.clients_engine import AsyncSessionLocalClients
from bot.database.clients_models import (
    Client,
    ClientCrmHistory,
    ClientManagerNote,
    ClientOrder,
    EmailCampaign,
)
from bot.database.engine import AsyncSessionLocal
from bot.database.models import (
    Category,
    ClassificationRule,
    FOP,
    Transaction,
    TransactionSource,
    TransactionStatus,
    TransactionType,
    WebUser,
)
from bot.database.products_engine import AsyncProductsSessionLocal
from bot.database.products_models import (
    ProcurementAlert,
    Product,
    ProductAnalytics,
    ProductBrand,
    ProductCategory,
    ProductOption,
)
from bot.keyboards.inline import get_fop_list
from bot.services.balance_sync import get_latest_balances
from bot.services.category_lookup import get_sales_category_id
from bot.services.period_range import day_end, day_start, period_dates
from bot.services.reports import (
    compare_periods,
    get_breakdown,
    get_cashflow,
    get_daily_totals,
    get_pl,
)
from bot.utils.text_normalize import normalize_product_text
from config import CLIENTS_EXPORT_EXCLUDED_CUSTOMER_IDS
from mono.api import now_kyiv
from web.security import verify_csrf_dependency
from web.auth import (
    ROLE_ADMIN,
    ROLE_MANAGER,
    default_home_path,
    get_current_user,
    get_operator_display,
    normalize_access,
    require_admin,
)
from web.client_crm import (
    CRM_CLOSURE_RESULT_CHOICES,
    CRM_HISTORY_ASSIGNMENT,
    CRM_HISTORY_CHANNELS,
    CRM_HISTORY_CLOSED,
    CRM_HISTORY_STATUS,
    CRM_PROCESSING_ACTIVE_STATUSES,
    CRM_STATUS_CLOSED,
    CRM_STATUS_COMMUNICATED,
    CRM_STATUS_IN_PROGRESS,
    CRM_STATUS_NEUTRAL,
    INTERACTION_OPTIONS,
    INTERACTION_OPTIONS_AUTO,
    INTERACTION_OPTIONS_DIRECT,
    INTERACTION_OPTIONS_MANUAL,
    INTERACTION_OPTIONS_MASS,
    apply_client_interaction_keys,
    apply_client_last_touch,
    apply_processing_status_change,
    closure_result_label,
    format_crm_history_items,
    deserialize_interaction_keys,
    interaction_keys_from_raw_list,
    interaction_labels_for_keys,
    interaction_short_label,
    normalize_closure_result,
    normalize_processing_status,
    processing_status_label,
    record_crm_history,
    serialize_interaction_keys,
)
from web.client_marketing import (
    build_all_orders_csv_bytes,
    build_all_orders_xlsx_bytes,
    build_client_orders_csv_bytes,
    build_client_orders_xlsx_bytes,
    build_clients_csv_bytes_async,
    build_clients_xlsx_bytes,
    last_interest_category,
    load_clients_base_totals,
    load_feed_tag_filter_options,
    load_clients_dashboard_snapshot,
    load_product_category_map,
    marketing_row,
    normalize_monthly_chart_outcome,
    normalize_order_status_name,
    order_status_filter_options,
    order_status_tone,
    parse_client_filter_dates,
    reference_now,
    repurchase_reminders,
    top_brands_from_orders,
)
from web.clients_query import clients_filter_has, clients_filter_list
from web.constants import PENDING_PER_PAGE
from web.email_delivery import resend_config_error_ua, send_marketing_emails
from web.manager_crm_stats import crm_event_type_label

BASE_DIR = Path(__file__).resolve().parent
EMAIL_TEMPLATES_DIR = BASE_DIR.parent / "email_templates"

CASH_MOVEMENT_CATEGORY_NAMES = ("Готівка", "Зняття Готівки", "Зняття готівки")
EMPLOYEE_EXPENSE_CATEGORY_NAME = "Працівники"

logger = logging.getLogger(__name__)


def _fmt_ua_thousands(value, signed=False):
    if value is None:
        return "—"
    try:
        x = float(value)
    except (TypeError, ValueError):
        return str(value)
    n = int(round(abs(x)))
    s = f"{n:,}".replace(",", ".")
    if signed:
        return ("+" if x >= 0 else "-") + s
    return ("-" if x < 0 else "") + s


def _fmt_ua_compact(value):
    """Скорочений вигляд для KPI: млн / тис. при великих значеннях."""
    if value is None:
        return "—"
    try:
        x = float(value)
    except (TypeError, ValueError):
        return str(value)
    sign = "-" if x < 0 else ""
    abs_x = abs(x)
    if abs_x >= 1_000_000:
        millions = abs_x / 1_000_000
        if millions >= 100:
            s = f"{millions:.0f}"
        elif millions >= 10:
            s = f"{millions:.1f}".rstrip("0").rstrip(".")
        else:
            s = f"{millions:.2f}".rstrip("0").rstrip(".")
        return sign + s.replace(".", ",") + " млн"
    if abs_x >= 100_000:
        thousands = abs_x / 1_000
        s = f"{thousands:.0f}" if thousands >= 100 else f"{thousands:.1f}".rstrip("0").rstrip(".")
        return sign + s.replace(".", ",") + " тис"
    return _fmt_ua_thousands(value)


def _fmt_phone_ua_display(raw: str | None) -> str:
    if raw is None:
        return "—"
    s = str(raw).strip()
    if not s:
        return "—"
    d = "".join(c for c in s if c.isdigit())
    if len(d) >= 12 and d.startswith("380"):
        nsn = d[3:12]
        if len(nsn) == 9:
            if not nsn.startswith("0"):
                grp = "0" + nsn[:2]
                mid, t1, t2 = nsn[2:5], nsn[5:7], nsn[7:9]
            else:
                grp, mid, t1, t2 = nsn[:3], nsn[3:6], nsn[6:8], nsn[8:10]
            return f"+38 ({grp}) {mid}-{t1}-{t2}"
    if len(d) == 10 and d.startswith("0"):
        return f"+38 ({d[:3]}) {d[3:6]}-{d[6:8]}-{d[8:10]}"
    return s


templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.loader.searchpath.extend(
    [
        str(BASE_DIR / "templates" / "shared"),
        str(BASE_DIR / "templates" / "finance"),
        str(BASE_DIR / "templates" / "clients"),
        str(BASE_DIR / "templates" / "procurement"),
    ]
)
templates.env.filters["fmt_ua"] = _fmt_ua_thousands
templates.env.filters["fmt_ua_compact"] = _fmt_ua_compact
templates.env.filters["fmt_phone_ua"] = _fmt_phone_ua_display
templates.env.filters["html_unescape"] = lambda s: html.unescape(s) if isinstance(s, str) else s
templates.env.filters["normalize_product_text"] = normalize_product_text
templates.env.filters["processing_status_label"] = processing_status_label
templates.env.filters["closure_result_label"] = closure_result_label
templates.env.filters["crm_event_type_label"] = crm_event_type_label
templates.env.globals["clients_filter_has"] = clients_filter_has
templates.env.globals["clients_filter_list"] = clients_filter_list
templates.env.globals["interaction_short_label"] = interaction_short_label
from web.procurement_margin import format_margin_pct, sku_margin_pct, sku_purchase_price

templates.env.globals["sku_margin_pct"] = sku_margin_pct
templates.env.globals["sku_purchase_price"] = sku_purchase_price
templates.env.filters["format_margin_pct"] = format_margin_pct


def static_asset_version(rel_path: str) -> str:
    """mtime файлу в /static — щоб браузер не тримав старий JS/CSS."""
    path = (BASE_DIR / "static" / rel_path.lstrip("/")).resolve()
    try:
        return str(int(path.stat().st_mtime))
    except OSError:
        return "1"


templates.env.globals["static_asset_version"] = static_asset_version

from web.ad_metric_help import metric_help, metric_key_for_label

templates.env.globals["metric_help"] = metric_help
templates.env.globals["metric_key_for_label"] = metric_key_for_label


def form_checkbox_on(val: str | None) -> bool:
    if val is None:
        return False
    return str(val).strip().lower() in ("1", "on", "true", "yes")


async def procurement_unread_alerts_count() -> int:
    async with AsyncProductsSessionLocal() as session:
        res = await session.execute(
            select(func.count(ProcurementAlert.id)).where(ProcurementAlert.is_read == False)
        )
        return res.scalar() or 0


def parse_custom_period(
    date_from_str: str | None, date_to_str: str | None
) -> tuple[datetime, datetime, str] | None:
    if not date_from_str or not date_to_str:
        return None
    try:
        date_from = day_start(datetime.strptime(date_from_str.strip(), "%Y-%m-%d"))
        date_to = day_end(datetime.strptime(date_to_str.strip(), "%Y-%m-%d"))
        if date_from > date_to:
            date_from, date_to = date_to, date_from
        period_name = f"{date_from.strftime('%d.%m.%Y')} – {date_to.strftime('%d.%m.%Y')}"
        return date_from, date_to, period_name
    except ValueError:
        return None


def parse_single_day(date_single_str: str | None) -> tuple[datetime, datetime, str] | None:
    if not date_single_str or not date_single_str.strip():
        return None
    try:
        day = datetime.strptime(date_single_str.strip(), "%Y-%m-%d")
        date_from = day.replace(hour=0, minute=0, second=0, microsecond=0)
        date_to = day.replace(hour=23, minute=59, second=59, microsecond=999999)
        period_name = day.strftime("%d.%m.%Y") + " (один день)"
        return date_from, date_to, period_name
    except ValueError:
        return None


def report_fop_filter_label(
    fops,
    fop_ids_or_none: list[int] | None,
    fop_ids_clean: list[int],
    include_cash: bool,
    *,
    fop_filter_options: list | None = None,
    selected_values: list[str] | None = None,
) -> str:
    if fop_filter_options is not None and selected_values is not None:
        return report_fop_filter_label_from_options(
            fop_filter_options, selected_values, include_cash
        )
    if fop_ids_or_none is None:
        return "Усі ФОПи та готівка"
    if fop_ids_or_none == []:
        return "Лише готівка"
    names_by_id = {int(f.id): fop_display_name(f.name) for f in fops}
    parts = [names_by_id.get(fid, str(fid)) for fid in fop_ids_clean]
    label = ", ".join(parts) if parts else "Обрані ФОПи"
    if include_cash:
        label += " + готівка"
    return label


from bot.services.fop_filter import (  # noqa: E402
    build_fop_filter_options,
    fop_display_name,
    fop_filter_values_to_legacy_ids,
    get_fop_sources,
    report_fop_filter_label_from_options,
    reports_fop_filter_values,
    resolve_fop_filter_for_totals,
    transaction_fop_scope_condition,
)


_procurement_unread_alerts_count = procurement_unread_alerts_count
_form_checkbox_on = form_checkbox_on

