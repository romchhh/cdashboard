"""
Маркетингова сегментація клієнтів Cosmy: RFM, тип, активність, канал, фільтри, експорт.
"""

from __future__ import annotations

import csv
import io
import re
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Iterable

from openpyxl import Workbook
from sqlalchemy import and_, case, desc, exists, extract, func, or_, select, nullslast
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.database.clients_models import Client, ClientFeedTag, ClientOrder, ClientOrderLine
from web.client_name_gender import gender_filter_clause
from config import CLIENTS_EXPORT_EXCLUDED_CUSTOMER_IDS
from mono.api import now_kyiv

ACTIVITY_RANGE_1_MAX_DAYS = 14
ACTIVITY_RANGE_2_MIN_DAYS = 14
ACTIVITY_RANGE_2_MAX_DAYS = 20
ACTIVITY_RANGE_3_MIN_DAYS = 20
ACTIVITY_RANGE_3_MAX_DAYS = 45
ACTIVITY_RANGE_4_MIN_DAYS = 45
ACTIVITY_RANGE_4_MAX_DAYS = 90
DEFAULT_REPURCHASE_DAYS = 30  # мін. давність останньої покупки SKU, щоб потрапив у список нагадувань
# Понад цей поріг у UI позначаємо як «давня покупка» (косметика через рік+ — слабкий сигнал для докупівлі)
REPURCHASE_STALE_DAYS = 365
# Дашборд «Топ джерел»: рядки з меншою кількістю клієнтів не показуємо.
CLIENTS_DASH_TOP_SOURCE_MIN_CLIENTS = 20


def _format_repurchase_age_ua(days: int) -> str:
    """Людяний підпис замість «1747 дн. тому»."""
    if days < 14:
        return f"{days} дн."
    if days < 30:
        return f"{days} дн."
    if days < 365:
        m = round(days / 30)
        return f"≈ {m} міс." if m > 1 else f"{days} дн."
    y, rem = divmod(days, 365)
    if rem < 60:
        return f"≈ {y} р." if y > 1 else "≈ 1 р."
    mo = rem // 30
    if mo and y:
        return f"≈ {y} р. {mo} міс."
    return f"≈ {y} р."


def reference_now() -> datetime:
    return now_kyiv()


def orders_count_effective(n: int | None) -> int:
    return int(n) if n is not None else 0


def segment_label(orders_count: int | None) -> str:
    n = orders_count_effective(orders_count)
    if n >= 10:
        return "VIP"
    if n >= 5:
        return "Лояльний"
    if n >= 2:
        return "Повторюваний"
    if n == 1:
        return "Новий / одноразовий"
    return "Без замовлень"


def days_since_last_order(last_order_date: datetime | None, now: datetime) -> int | None:
    if last_order_date is None:
        return None
    return max(0, (now.date() - last_order_date.date()).days)


def activity_label(last_order_date: datetime | None, now: datetime) -> str:
    d = days_since_last_order(last_order_date, now)
    if d is None:
        return "Невідомо"
    # Важливий пріоритет: 14–20 перевіряємо раніше за 1–14, щоб не втрачати підсегмент.
    if ACTIVITY_RANGE_2_MIN_DAYS <= d <= ACTIVITY_RANGE_2_MAX_DAYS:
        return "14–20 днів"
    if d <= ACTIVITY_RANGE_1_MAX_DAYS:
        return "1–14 днів"
    if ACTIVITY_RANGE_3_MIN_DAYS < d <= ACTIVITY_RANGE_3_MAX_DAYS:
        return "20–45 днів"
    if ACTIVITY_RANGE_4_MIN_DAYS < d <= ACTIVITY_RANGE_4_MAX_DAYS:
        return "45–90 днів"
    return "90+ днів"


def channel_label(client: Client) -> str:
    """Групування джерела для маркетингу (наближено до Meta / SEO / органіка)."""
    raw = (client.source_raw or "").lower()
    utm = (client.source_utm_source or "").lower()
    cat = (client.source_category or "").lower()
    ref = (client.source_referrer or "").lower()
    combined = f"{raw} {utm} {cat} {ref}"
    if "facebook" in combined or "instagram" in combined or "meta" in combined or "fb" in combined:
        return "Meta Ads"
    if "google" in combined and ("cpc" in combined or "ads" in cat or "ads" in raw):
        return "Google Ads"
    if "organic" in raw or "organic" in utm or cat == "organic":
        return "Органіка"
    if "seo" in combined or "search" in combined:
        return "SEO"
    if "direct" in raw or "direct" in utm:
        return "Direct"
    return client.source_raw or client.source_utm_source or "—"


def rfm_summary(client: Client, now: datetime) -> dict[str, Any]:
    """Recency (днів), Frequency (замовлень), Monetary (LTV)."""
    r = days_since_last_order(client.last_order_date, now)
    f = orders_count_effective(client.orders_count)
    m = float(client.ltv) if client.ltv is not None else 0.0
    return {
        "recency_days": r,
        "frequency": f,
        "monetary": m,
        "recency_text": f"{r} дн." if r is not None else "—",
    }


def marketing_row(client: Client, now: datetime | None = None) -> dict[str, Any]:
    now = now or reference_now()
    rfm = rfm_summary(client, now)
    return {
        "segment": segment_label(client.orders_count),
        "activity": activity_label(client.last_order_date, now),
        "channel": channel_label(client),
        "rfm": rfm,
    }


_ORDER_STATUS_TITLE_BY_ID: dict[int, str] = {
    1: "В очікуванні",
    3: "Відправлено",
    10: "Чекаємо на товар",
    13: "Готовий до відправлення",
    15: "Оброблений",
    17: "Скасовано",
    18: "Задвоєння",
    19: "Самовивіз",
    20: "Клієнт не відповідає",
    21: "Немає товару в наявності",
    22: "Не актуально",
    23: "Повернення",
    24: "Виконано",
    25: "Більше 5 днів",
    26: "Доставлено у відділення / поштомат",
}

# ID статусів з фіду, які можна обрати для місячного графіка на дашборді клієнтів
KNOWN_ORDER_STATUS_IDS: frozenset[int] = frozenset(_ORDER_STATUS_TITLE_BY_ID.keys())

# Проміжні статуси: не входять ні в «успішні», ні в «неуспішні» (KPI та стовпці графіка).
# 26 «Доставлено» — кінцевий успішний; не проміжний.
ORDER_TRANSITIONAL_STATUS_IDS: frozenset[int] = frozenset({1, 10, 13, 15, 25})

_TRANSITIONAL_TITLE_CF: frozenset[str] = frozenset(
    _ORDER_STATUS_TITLE_BY_ID[i].casefold() for i in ORDER_TRANSITIONAL_STATUS_IDS
)


def is_order_status_transitional(status_name: str | None, status_id: int | None) -> bool:
    if status_id is not None and int(status_id) in ORDER_TRANSITIONAL_STATUS_IDS:
        return True
    title_cf = normalize_order_status_name(status_name, status_id).casefold()
    return title_cf in _TRANSITIONAL_TITLE_CF


def normalize_order_status_name(status_name: str | None, status_id: int | None = None) -> str:
    by_id = _ORDER_STATUS_TITLE_BY_ID.get(int(status_id)) if status_id is not None else None
    raw = (status_name or "").strip()
    if by_id:
        return by_id
    if not raw:
        return "Невідомо"
    low = raw.casefold()
    aliases = {
        "обработан": "Оброблений",
        "оброблен": "Оброблений",
        "выполнен": "Виконано",
        "доставлен": "Доставлено у відділення / поштомат",
    }
    for k, v in aliases.items():
        if k in low:
            return v
    return raw


def order_status_tone(status_name: str | None, status_id: int | None = None) -> str:
    label = normalize_order_status_name(status_name, status_id).casefold()
    if any(x in label for x in ("виконано", "доставлено", "відправлено", "самовивіз", "оброблений")):
        return "success"
    if any(x in label for x in ("скасовано", "не актуально", "повернення", "задвоєння", "не відповідає")):
        return "danger"
    if any(x in label for x in ("чекаємо", "немає товару", "більше 5 днів")):
        return "warning"
    if "готовий до відправлення" in label:
        return "info"
    if "очікуванні" in label:
        return "muted"
    return "muted"


def classify_order_outcome(status_id: int | None, status_name: str | None) -> str:
    """Класифікація замовлення: success | unsuccessful | transitional."""
    if is_order_status_transitional(status_name, status_id):
        return "transitional"
    title = normalize_order_status_name(status_name, status_id)
    if order_status_tone(title, status_id) == "success":
        return "success"
    return "unsuccessful"


# Кінцеві «успішні» / «неуспішні» id за єдиною логікою classify_order_outcome.
ORDER_SUCCESS_STATUS_IDS: frozenset[int] = frozenset(
    sid
    for sid in _ORDER_STATUS_TITLE_BY_ID
    if classify_order_outcome(sid, _ORDER_STATUS_TITLE_BY_ID[sid]) == "success"
)
ORDER_UNSUCCESS_STATUS_IDS: frozenset[int] = frozenset(
    sid
    for sid in _ORDER_STATUS_TITLE_BY_ID
    if classify_order_outcome(sid, _ORDER_STATUS_TITLE_BY_ID[sid]) == "unsuccessful"
)


def normalize_monthly_chart_outcome(raw: str | None) -> str:
    """Фільтр місячного графіка: all | success | unsuccessful (решта → all)."""
    v = (raw or "").strip().lower()
    if v in ("success", "unsuccessful"):
        return v
    return "all"


CHART_GRANULARITY_AUTO_DAY_THRESHOLD = 45
CHART_GRANULARITY_AUTO_WEEK_THRESHOLD = 180


def normalize_chart_granularity(raw: str | None, span_days: int) -> str:
    """day | week | month | auto — auto: ≤45д по днях, ≤180д по тижнях, інакше по місяцях."""
    v = (raw or "auto").strip().lower()
    if v in ("day", "week", "month"):
        return v
    if span_days <= CHART_GRANULARITY_AUTO_DAY_THRESHOLD:
        return "day"
    if span_days <= CHART_GRANULARITY_AUTO_WEEK_THRESHOLD:
        return "week"
    return "month"


def _order_status_text_lower():
    return func.lower(func.trim(func.coalesce(ClientOrder.order_status, "")))


def _sql_text_outcome_transitional() -> Any:
    t = _order_status_text_lower()
    return or_(
        t.like("%очікуван%"),
        t.like("%чекаємо%"),
        t.like("%немає товару%"),
        t.like("%більше 5 дні%"),
        t.like("%готовий до відправлення%"),
    )


def _sql_text_outcome_success() -> Any:
    t = _order_status_text_lower()
    return and_(
        or_(
            t.like("%виконано%"),
            t.like("%доставлен%"),
            t.like("%відправлен%"),
            t.like("%самовивіз%"),
            t.like("%оброблен%"),
        ),
        ~_sql_text_outcome_transitional(),
    )


def _sql_outcome_id_or_text_fallback(known_ids: frozenset[int], text_cond: Any) -> Any:
    """Відомий status_id або (null/невідомий id + збіг тексту статусу)."""
    return or_(
        ClientOrder.order_status_id.in_(known_ids),
        and_(
            or_(
                ClientOrder.order_status_id.is_(None),
                ClientOrder.order_status_id.notin_(KNOWN_ORDER_STATUS_IDS),
            ),
            text_cond,
        ),
    )


def _sql_outcome_is_success() -> Any:
    return _sql_outcome_id_or_text_fallback(ORDER_SUCCESS_STATUS_IDS, _sql_text_outcome_success())


def _sql_outcome_is_transitional() -> Any:
    return _sql_outcome_id_or_text_fallback(
        ORDER_TRANSITIONAL_STATUS_IDS, _sql_text_outcome_transitional()
    )


def _sql_outcome_is_unsuccessful() -> Any:
    return and_(~_sql_outcome_is_success(), ~_sql_outcome_is_transitional())


def _day_to_label_ua(ymd: str) -> str:
    try:
        d = datetime.strptime(str(ymd).strip()[:10], "%Y-%m-%d")
        return d.strftime("%d.%m.%Y")
    except ValueError:
        return str(ymd)


def _week_to_label_ua(ymd: str) -> str:
    """Підпис тижня за датою понеділка: «05–11.05.2026»."""
    try:
        start = datetime.strptime(str(ymd).strip()[:10], "%Y-%m-%d")
        end = start + timedelta(days=6)
        if start.year == end.year and start.month == end.month:
            return f"{start.strftime('%d')}–{end.strftime('%d.%m.%Y')}"
        if start.year == end.year:
            return f"{start.strftime('%d.%m')}–{end.strftime('%d.%m.%Y')}"
        return f"{start.strftime('%d.%m.%Y')}–{end.strftime('%d.%m.%Y')}"
    except ValueError:
        return str(ymd)


def _sql_week_start_monday():
    """Понеділок тижня для order_date (SQLite: %w неділя=0)."""
    from sqlalchemy import Integer, cast

    dow = cast(func.strftime("%w", ClientOrder.order_date), Integer)
    days_from_monday = func.mod(dow + 6, 7)
    return func.date(ClientOrder.order_date, func.printf("-%d days", days_from_monday))


async def _load_orders_chart_buckets(
    session: AsyncSession,
    date_bounds: Any,
    outcome: str,
    granularity: str,
    deny_o: Any | None,
) -> list[dict[str, Any]]:
    """Агрегація замовлень/виручки по днях, тижнях або місяцях. Виручка в полі revenue — лише успішні."""
    _succ_cond = _sql_outcome_is_success()
    _trans_cond = _sql_outcome_is_transitional()
    _unsucc_cond = _sql_outcome_is_unsuccessful()

    if granularity == "day":
        group_cols = [func.strftime("%Y-%m-%d", ClientOrder.order_date)]
    elif granularity == "week":
        group_cols = [_sql_week_start_monday()]
    else:
        group_cols = [
            extract("year", ClientOrder.order_date),
            extract("month", ClientOrder.order_date),
        ]

    def _parse_bucket_row(r) -> tuple[str, str, int] | None:
        """Повертає (ym_key, label_ua, value_offset) або None."""
        if granularity == "day":
            bucket_key = str(r[0] or "")
            if not bucket_key:
                return None
            return bucket_key, _day_to_label_ua(bucket_key), 1
        if granularity == "week":
            bucket_key = str(r[0] or "")[:10]
            if not bucket_key:
                return None
            return bucket_key, _week_to_label_ua(bucket_key), 1
        yv, mv = r[0], r[1]
        if yv is None or mv is None:
            return None
        yi, mi = int(yv), int(mv)
        if not (1 <= mi <= 12):
            return None
        ym = f"{yi}-{mi:02d}"
        return ym, _ym_to_label_ua(ym), 2

    def _base_select(extra_group: list):
        return (
            select(
                *extra_group,
                func.coalesce(func.sum(case((_succ_cond, 1), else_=0)), 0).label("n_succ"),
                func.coalesce(func.sum(case((_unsucc_cond, 1), else_=0)), 0).label("n_unsucc"),
                func.coalesce(func.sum(case((_trans_cond, 1), else_=0)), 0).label("n_trans"),
                func.coalesce(
                    func.sum(case((_succ_cond, func.coalesce(ClientOrder.order_total, 0.0)), else_=0.0)),
                    0.0,
                ).label("rev_succ"),
                func.coalesce(
                    func.sum(case((_unsucc_cond, func.coalesce(ClientOrder.order_total, 0.0)), else_=0.0)),
                    0.0,
                ).label("rev_unsucc"),
                func.coalesce(
                    func.sum(case((_trans_cond, func.coalesce(ClientOrder.order_total, 0.0)), else_=0.0)),
                    0.0,
                ).label("rev_trans"),
                func.count(func.distinct(ClientOrder.client_customer_id)).label("n_buyers"),
            )
            .select_from(ClientOrder)
            .where(date_bounds)
            .group_by(*extra_group)
            .order_by(*extra_group)
        )

    buckets: list[dict[str, Any]] = []

    if outcome == "all":
        stmt = _base_select(group_cols)
        if deny_o is not None:
            stmt = stmt.where(deny_o)
        rows = (await session.execute(stmt)).all()
        for r in rows:
            parsed = _parse_bucket_row(r)
            if not parsed:
                continue
            ym, label, offset = parsed
            ns = int(r[offset] or 0)
            nu = int(r[offset + 1] or 0)
            nt = int(r[offset + 2] or 0)
            if ns + nu + nt == 0:
                continue
            rs = float(r[offset + 3] or 0.0)
            ru = float(r[offset + 4] or 0.0)
            rt = float(r[offset + 5] or 0.0)
            n_buyers = int(r[offset + 6] or 0)
            tot_o = ns + nu + nt
            buckets.append(
                {
                    "ym": ym,
                    "label_ua": label,
                    "orders_success": ns,
                    "orders_unsuccessful": nu,
                    "orders_transitional": nt,
                    "orders": tot_o,
                    "revenue": rs,
                    "revenue_success": rs,
                    "revenue_unsuccessful": ru,
                    "revenue_transitional": rt,
                    "revenue_total_all_statuses": rs + ru + rt,
                    "distinct_buyers": n_buyers,
                    "avg_check_uah": round(rs / ns, 2) if ns else None,
                }
            )
    else:
        if granularity in ("day", "week"):
            bucket_col = group_cols[0]
            stmt = (
                select(
                    bucket_col.label("bucket"),
                    func.count(ClientOrder.id),
                    func.coalesce(func.sum(ClientOrder.order_total), 0.0),
                    func.count(func.distinct(ClientOrder.client_customer_id)),
                )
                .select_from(ClientOrder)
                .where(date_bounds)
                .group_by(bucket_col)
                .order_by(bucket_col)
            )
        else:
            _yr = extract("year", ClientOrder.order_date)
            _mo = extract("month", ClientOrder.order_date)
            stmt = (
                select(
                    _yr,
                    _mo,
                    func.count(ClientOrder.id),
                    func.coalesce(func.sum(ClientOrder.order_total), 0.0),
                    func.count(func.distinct(ClientOrder.client_customer_id)),
                )
                .select_from(ClientOrder)
                .where(date_bounds)
                .group_by(_yr, _mo)
                .order_by(_yr, _mo)
            )
        if deny_o is not None:
            stmt = stmt.where(deny_o)
        if outcome == "success":
            stmt = stmt.where(_succ_cond)
        elif outcome == "unsuccessful":
            stmt = stmt.where(_unsucc_cond)
        rows = (await session.execute(stmt)).all()
        for r in rows:
            parsed = _parse_bucket_row(r)
            if not parsed:
                continue
            ym, label, offset = parsed
            od = int(r[offset] or 0)
            rev = float(r[offset + 1] or 0.0)
            n_buyers = int(r[offset + 2] or 0)
            if od == 0:
                continue
            entry: dict[str, Any] = {
                "ym": ym,
                "label_ua": label,
                "orders": od,
                "revenue": rev,
                "distinct_buyers": n_buyers,
            }
            if outcome == "success":
                entry["orders_success"] = od
                entry["orders_unsuccessful"] = 0
                entry["orders_transitional"] = 0
                entry["revenue_success"] = rev
                entry["revenue_unsuccessful"] = 0.0
                entry["avg_check_uah"] = round(rev / od, 2) if od else None
            else:
                entry["orders_success"] = 0
                entry["orders_unsuccessful"] = od
                entry["orders_transitional"] = 0
                entry["revenue_success"] = 0.0
                entry["revenue_unsuccessful"] = rev
                entry["avg_check_uah"] = None
            buckets.append(entry)

    return buckets


def order_status_filter_options() -> list[dict[str, Any]]:
    return [{"id": sid, "name": name} for sid, name in sorted(_ORDER_STATUS_TITLE_BY_ID.items())]


def _parse_date_param(s: str | None) -> datetime | None:
    if not s or not str(s).strip():
        return None
    s = str(s).strip()[:10]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


# Місячний графік на дашборді клієнтів: межі діапазону order_date (Київ, naive).
MONTHLY_CHART_HARD_MIN = datetime(2015, 1, 1, 0, 0, 0)
MONTHLY_CHART_MAX_SPAN_DAYS = 366 * 15 + 200  # ~15 років


def _end_of_calendar_day(d: datetime) -> datetime:
    return d.replace(hour=23, minute=59, second=59, microsecond=999999)


def resolve_monthly_chart_order_range(
    now: datetime,
    raw_from: str | None,
    raw_to: str | None,
    data_order_min: datetime | None,
) -> tuple[datetime, datetime, dict[str, Any]]:
    """
    Повертає (start_inclusive, end_inclusive, meta) для фільтра order_date.
    За замовчуванням — той самий відсік що раніше: 550 дн. до 1-го числа поточного місяця.
    """
    default_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) - timedelta(days=550)
    pf = _parse_date_param(raw_from)
    pt = _parse_date_param(raw_to)
    if pf and pt and pf.replace(hour=0, minute=0, second=0, microsecond=0) > pt.replace(
        hour=0, minute=0, second=0, microsecond=0
    ):
        pf, pt = pt, pf

    clamped_to_feed = False
    clipped_span = False
    effective_custom = bool(pf or pt)

    explicit_range = bool(pf and pt)

    if not pf and not pt:
        start = default_start.replace(hour=0, minute=0, second=0, microsecond=0)
        end_inc = now
        effective_custom = False
    elif pf and not pt:
        start = pf.replace(hour=0, minute=0, second=0, microsecond=0)
        end_inc = now
    elif pt and not pf:
        end_inc = min(_end_of_calendar_day(pt.replace(hour=0, minute=0, second=0, microsecond=0)), now)
        start = default_start.replace(hour=0, minute=0, second=0, microsecond=0)
        if start > end_inc:
            start = (end_inc - timedelta(days=550)).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start = pf.replace(hour=0, minute=0, second=0, microsecond=0)
        end_inc = min(_end_of_calendar_day(pt.replace(hour=0, minute=0, second=0, microsecond=0)), now)

    start = max(start, MONTHLY_CHART_HARD_MIN)
    # Явний діапазон «з–по» не зсуваємо до min order_date у фіді (користувач бачить обрані дати).
    if data_order_min and not explicit_range:
        dm = data_order_min.replace(hour=0, minute=0, second=0, microsecond=0)
        if dm > start:
            start = dm
            clamped_to_feed = True

    if start > end_inc:
        start = default_start.replace(hour=0, minute=0, second=0, microsecond=0)
        end_inc = now
        effective_custom = False
        clamped_to_feed = False

    span_days = (end_inc.date() - start.date()).days
    if span_days > MONTHLY_CHART_MAX_SPAN_DAYS:
        start = (end_inc - timedelta(days=MONTHLY_CHART_MAX_SPAN_DAYS)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        start = max(start, MONTHLY_CHART_HARD_MIN)
        if data_order_min and not explicit_range:
            dm = data_order_min.replace(hour=0, minute=0, second=0, microsecond=0)
            if dm > start:
                start = dm
        clipped_span = True

    notes: list[str] = []
    if clipped_span:
        notes.append("Період обмежено максимальною глибиною (~15 років).")
    if clamped_to_feed:
        notes.append("Початок не раніше мінімального order_date у фіді.")

    meta = {
        "from_iso": start.strftime("%Y-%m-%d"),
        "to_iso": end_inc.strftime("%Y-%m-%d"),
        "from_display": start.strftime("%d.%m.%Y"),
        "to_display": end_inc.strftime("%d.%m.%Y"),
        "max_date_iso": now.strftime("%Y-%m-%d"),
        "is_custom": effective_custom,
        "clipped_span": clipped_span,
        "clamped_start_to_feed": clamped_to_feed,
        "notes_ua": " ".join(notes) if notes else None,
    }
    return start, end_inc, meta


def _segment_filter_clause(segment: str) -> Any | None:
    seg = (segment or "").strip().lower()
    if seg == "new":
        return func.coalesce(Client.orders_count, 0) == 1
    if seg == "repeat":
        return and_(
            func.coalesce(Client.orders_count, 0) >= 2,
            func.coalesce(Client.orders_count, 0) < 5,
        )
    if seg == "loyal":
        return and_(
            func.coalesce(Client.orders_count, 0) >= 5,
            func.coalesce(Client.orders_count, 0) < 10,
        )
    if seg == "vip":
        return func.coalesce(Client.orders_count, 0) >= 10
    return None


def _activity_filter_clause(activity: str, now: datetime) -> Any | None:
    act = (activity or "").strip().lower()
    d14 = now - timedelta(days=14)
    d20 = now - timedelta(days=20)
    d45 = now - timedelta(days=45)
    d90 = now - timedelta(days=90)
    if act == "r1_14":
        return and_(Client.last_order_date.isnot(None), Client.last_order_date >= d14)
    if act == "r14_20":
        return and_(
            Client.last_order_date.isnot(None),
            Client.last_order_date <= d14,
            Client.last_order_date >= d20,
        )
    if act == "r20_45":
        return and_(
            Client.last_order_date.isnot(None),
            Client.last_order_date < d20,
            Client.last_order_date >= d45,
        )
    if act == "r45_90":
        return and_(
            Client.last_order_date.isnot(None),
            Client.last_order_date < d45,
            Client.last_order_date >= d90,
        )
    if act == "r90_plus":
        return or_(Client.last_order_date.is_(None), Client.last_order_date < d90)
    return None


def _multi_or_clause(parts: list[Any]) -> Any | None:
    clean = [p for p in parts if p is not None]
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    return or_(*clean)


def build_client_where_clauses(
    *,
    q_clean: str,
    orders_min: int | None,
    orders_max: int | None,
    ltv_min: float | None,
    ltv_max: float | None,
    order_total_min: float | None,
    order_total_max: float | None,
    first_from: datetime | None,
    first_to: datetime | None,
    last_from: datetime | None,
    last_to: datetime | None,
    segment: str | list[str] | None,
    activity: str | list[str] | None,
    feed_tag: str | list[str] | None,
    brand_contains_list: list[str] | None,
    product_contains: str,
    product_id: int | None,
    order_statuses: list[str] | None,
    interaction_exclude_keys: list[str] | None,
    registered: bool | None,
    email_mode: str | None,
    characteristics_mode: str | None,
    last_order_manager_filter: str | None,
    now: datetime,
    assigned_username_filter: str | None = None,
    geo_cities: list[str] | None = None,
    geo_oblasts: list[str] | None = None,
    gender: str | None = None,
) -> list[Any]:
    clauses: list[Any] = []
    if registered is True:
        clauses.append(Client.is_registered.is_(True))
    elif registered is False:
        clauses.append(Client.is_registered.is_(False))

    _em = (email_mode or "").strip().lower()
    if _em == "with":
        clauses.append(Client.email.isnot(None))
        clauses.append(func.trim(func.coalesce(Client.email, "")) != "")
    elif _em == "without":
        clauses.append(
            or_(
                Client.email.is_(None),
                func.trim(func.coalesce(Client.email, "")) == "",
            )
        )

    _cm = (characteristics_mode or "").strip().lower()
    if _cm == "with":
        clauses.append(Client.feed_characteristics.isnot(None))
        clauses.append(func.trim(func.coalesce(Client.feed_characteristics, "")) != "")
    elif _cm == "without":
        clauses.append(
            or_(
                Client.feed_characteristics.is_(None),
                func.trim(func.coalesce(Client.feed_characteristics, "")) == "",
            )
        )

    city_clause = geo_city_filter_clause(geo_cities)
    if city_clause is not None:
        clauses.append(city_clause)
    oblast_clause = geo_oblast_filter_clause(geo_oblasts)
    if oblast_clause is not None:
        clauses.append(oblast_clause)

    gender_clause = gender_filter_clause(gender)
    if gender_clause is not None:
        clauses.append(gender_clause)

    if q_clean:
        term = f"%{q_clean}%"
        clauses.append(
            or_(
                Client.firstname.ilike(term),
                Client.lastname.ilike(term),
                Client.telephone.ilike(term),
                Client.email.ilike(term),
                Client.customer_id.ilike(term),
                Client.geo_city.ilike(term),
            )
        )
    if orders_min is not None:
        clauses.append(func.coalesce(Client.orders_count, 0) >= orders_min)
    if orders_max is not None:
        clauses.append(func.coalesce(Client.orders_count, 0) <= orders_max)
    if ltv_min is not None:
        clauses.append(Client.ltv.isnot(None))
        clauses.append(Client.ltv >= ltv_min)
    if ltv_max is not None:
        clauses.append(Client.ltv.isnot(None))
        clauses.append(Client.ltv <= ltv_max)

    status_labels = [normalize_order_status_name(x) for x in (order_statuses or []) if str(x).strip()]
    status_labels_cf = [x.casefold() for x in status_labels]
    # У фіді часто заповнений лише order_status_id або order_status іншою мовою — шукаємо і по id.
    status_order_cond: Any | None = None
    if status_labels_cf:
        status_cf_set = frozenset(status_labels_cf)
        matching_status_ids = [
            sid
            for sid, title in _ORDER_STATUS_TITLE_BY_ID.items()
            if normalize_order_status_name(title, sid).casefold() in status_cf_set
        ]
        parts: list[Any] = [func.lower(func.coalesce(ClientOrder.order_status, "")).in_(status_labels_cf)]
        if matching_status_ids:
            parts.append(ClientOrder.order_status_id.in_(matching_status_ids))
        status_order_cond = or_(*parts) if len(parts) > 1 else parts[0]

    def _order_base_conds() -> list[Any]:
        conds: list[Any] = [ClientOrder.client_customer_id == Client.customer_id]
        if status_order_cond is not None:
            conds.append(status_order_cond)
        return conds

    if order_total_min is not None or order_total_max is not None:
        ot_conds = [
            *_order_base_conds(),
            ClientOrder.order_total.isnot(None),
        ]
        if order_total_min is not None:
            ot_conds.append(ClientOrder.order_total >= order_total_min)
        if order_total_max is not None:
            ot_conds.append(ClientOrder.order_total <= order_total_max)
        clauses.append(
            exists(select(1).select_from(ClientOrder).where(*ot_conds))
        )

    if first_from is not None:
        clauses.append(Client.first_order_date.isnot(None))
        clauses.append(Client.first_order_date >= first_from)
    if first_to is not None:
        end = first_to + timedelta(days=1)
        clauses.append(Client.first_order_date.isnot(None))
        clauses.append(Client.first_order_date < end)
    if last_from is not None:
        clauses.append(Client.last_order_date.isnot(None))
        clauses.append(Client.last_order_date >= last_from)
    if last_to is not None:
        end = last_to + timedelta(days=1)
        clauses.append(Client.last_order_date.isnot(None))
        clauses.append(Client.last_order_date < end)

    segments_raw = segment if isinstance(segment, list) else ([segment] if segment else [])
    segments = [str(s).strip().lower() for s in segments_raw if str(s).strip()]
    segment_clause = _multi_or_clause([_segment_filter_clause(s) for s in segments])
    if segment_clause is not None:
        clauses.append(segment_clause)

    activities_raw = activity if isinstance(activity, list) else ([activity] if activity else [])
    activities = [str(a).strip().lower() for a in activities_raw if str(a).strip()]
    activity_clause = _multi_or_clause([_activity_filter_clause(a, now) for a in activities])
    if activity_clause is not None:
        clauses.append(activity_clause)

    feed_tags_raw = feed_tag if isinstance(feed_tag, list) else ([feed_tag] if feed_tag else [])
    feed_tags = [str(t).strip().lower() for t in feed_tags_raw if str(t).strip()]
    feed_tag_clause = _multi_or_clause(
        [
            exists(
                select(1)
                .select_from(ClientFeedTag)
                .where(
                    ClientFeedTag.client_customer_id == Client.customer_id,
                    ClientFeedTag.code == code,
                )
            )
            for code in feed_tags
        ]
    )
    if feed_tag_clause is not None:
        clauses.append(feed_tag_clause)

    if brand_contains_list:
        brands_clean = [str(b).strip() for b in brand_contains_list if str(b).strip()]
        brands_cf = [b.casefold() for b in brands_clean]
        if brands_cf:
            clauses.append(
                exists(
                    select(1)
                    .select_from(ClientOrder)
                    .join(ClientOrderLine, ClientOrderLine.order_row_id == ClientOrder.id)
                    .where(
                        *_order_base_conds(),
                        ClientOrderLine.brand.isnot(None),
                        func.lower(ClientOrderLine.brand).in_(brands_cf),
                    )
                )
            )

    pc = (product_contains or "").strip()
    if pc:
        pt = f"%{pc}%"
        clauses.append(
            exists(
                select(1)
                .select_from(ClientOrder)
                .join(ClientOrderLine, ClientOrderLine.order_row_id == ClientOrder.id)
                .where(
                    *_order_base_conds(),
                    ClientOrderLine.name.isnot(None),
                    ClientOrderLine.name.ilike(pt),
                )
            )
        )

    if product_id is not None:
        clauses.append(
            exists(
                select(1)
                .select_from(ClientOrder)
                .join(ClientOrderLine, ClientOrderLine.order_row_id == ClientOrder.id)
                .where(
                    *_order_base_conds(),
                    ClientOrderLine.product_id == product_id,
                )
            )
        )

    if status_labels_cf and not any([order_total_min is not None, order_total_max is not None, pc, product_id is not None, brand_contains_list]):
        clauses.append(
            exists(
                select(1)
                .select_from(ClientOrder)
                .where(*_order_base_conds())
            )
        )

    for ik in (interaction_exclude_keys or []):
        iks = str(ik).strip()
        if not iks:
            continue
        clauses.append(
            or_(
                Client.crm_interaction_keys.is_(None),
                func.trim(func.coalesce(Client.crm_interaction_keys, "")) == "",
                func.lower(func.coalesce(Client.crm_interaction_keys, "")).not_like(f'%"{iks.lower()}"%'),
            )
        )

    af = (assigned_username_filter or "").strip()
    if af == "__none__":
        clauses.append(
            or_(
                Client.crm_assigned_username.is_(None),
                func.trim(func.coalesce(Client.crm_assigned_username, "")) == "",
            )
        )
    elif af:
        clauses.append(Client.crm_assigned_username == af)

    lom = (last_order_manager_filter or "").strip()
    if lom == "__none__":
        clauses.append(
            or_(
                Client.last_order_manager_username.is_(None),
                func.trim(func.coalesce(Client.last_order_manager_username, "")) == "",
            )
        )
    elif lom:
        clauses.append(Client.last_order_manager_username == lom)

    return clauses


def parse_client_filter_dates(
    first_from_s: str | None,
    first_to_s: str | None,
    last_from_s: str | None,
    last_to_s: str | None,
) -> tuple[datetime | None, datetime | None, datetime | None, datetime | None]:
    return (
        _parse_date_param(first_from_s),
        _parse_date_param(first_to_s),
        _parse_date_param(last_from_s),
        _parse_date_param(last_to_s),
    )


def _clients_export_headers(include_email: bool) -> list[str]:
    """Заголовки експорту клієнтів (CSV/XLSX). Колонка Email лише за фільтром «З email»."""
    h = ["Прізвище", "Ім'я"]
    if include_email:
        h.append("Email")
    h.append("Телефон")
    return h


def _client_export_row_values(c: Client, include_email: bool) -> list[Any]:
    row: list[Any] = [c.lastname or "", c.firstname or ""]
    if include_email:
        row.append(c.email or "")
    row.append(c.telephone or "")
    return row


def top_brands_from_orders(orders: Iterable[Any]) -> list[tuple[str, int]]:
    c: Counter[str] = Counter()
    for o in orders:
        for ln in getattr(o, "lines", None) or []:
            b = (getattr(ln, "brand", None) or "").strip()
            if b:
                c[b] += int(getattr(ln, "quantity", None) or 1)
    return c.most_common(5)


def repurchase_reminders(orders: Iterable[Any], now: datetime | None = None) -> list[dict[str, Any]]:
    """
    Евристика: остання покупка product_id ≥ DEFAULT_REPURCHASE_DAYS тому — ідея для нагадування про докупівлю.
    Без норми витрати з каталогу: не «термін придатності», а орієнтир для маркетингу.

    Сортування: спочатку позиції з «свіжішою» давністю останньої покупки (31–365 дн.),
    потім дуже давні — щоб зверху не опинялись покупки 5+ років тому.
    """
    now = now or reference_now()
    by_pid: dict[int, list[tuple[datetime | None, str, str]]] = {}
    for o in sorted(orders, key=lambda x: x.order_date or datetime.min):
        od = o.order_date
        for ln in getattr(o, "lines", None) or []:
            pid = getattr(ln, "product_id", None)
            if pid is None:
                continue
            name = (getattr(ln, "name", None) or "").strip() or f"#{pid}"
            brand = (getattr(ln, "brand", None) or "").strip()
            by_pid.setdefault(int(pid), []).append((od, name, brand))
    hints: list[dict[str, Any]] = []
    for pid, events in by_pid.items():
        last_dt, name, brand = events[-1]
        if not last_dt:
            continue
        days = (now.date() - last_dt.date()).days
        if days >= DEFAULT_REPURCHASE_DAYS:
            stale = days >= REPURCHASE_STALE_DAYS
            title = name
            if brand and brand.lower() not in name.lower():
                title = f"{brand} — {name}"
            hints.append(
                {
                    "product_id": pid,
                    "name": name,
                    "title": title,
                    "brand": brand or None,
                    "days_since": days,
                    "age_label": _format_repurchase_age_ua(days),
                    "stale": stale,
                }
            )
    hints.sort(key=lambda x: (x["stale"], x["days_since"]))
    return hints[:40]


async def load_product_category_map(session_products: AsyncSession, product_ids: list[int]) -> dict[int, str]:
    if not product_ids:
        return {}
    uniq = sorted({int(x) for x in product_ids if x is not None})[:8000]
    if not uniq:
        return {}
    from bot.database.products_models import Product

    res = await session_products.execute(
        select(Product.product_id, Product.category_name).where(Product.product_id.in_(uniq))
    )
    out: dict[int, str] = {}
    for pid, cat in res.all():
        if pid is not None and cat:
            out[int(pid)] = str(cat).strip()
    return out


def _clients_export_allow_clause():
    """Умова SQL: клієнти, яких не виключаємо з експортів."""
    if not CLIENTS_EXPORT_EXCLUDED_CUSTOMER_IDS:
        return None
    return Client.customer_id.notin_(CLIENTS_EXPORT_EXCLUDED_CUSTOMER_IDS)


def _clients_orders_exclude_clause():
    """Умова SQL: замовлення клієнтів не з чорного списку (те саме, що й для експортів/списку)."""
    if not CLIENTS_EXPORT_EXCLUDED_CUSTOMER_IDS:
        return None
    return ClientOrder.client_customer_id.notin_(CLIENTS_EXPORT_EXCLUDED_CUSTOMER_IDS)


def last_interest_category(
    orders: Iterable[Any], category_by_product: dict[int, str]
) -> str:
    """Категорія з останнього замовлення (перший рядок з відомим product_id)."""
    for o in sorted(orders, key=lambda x: x.order_date or datetime.min, reverse=True):
        for ln in getattr(o, "lines", None) or []:
            pid = getattr(ln, "product_id", None)
            if pid is None:
                continue
            cat = category_by_product.get(int(pid))
            if cat:
                return cat
    return "—"


async def build_clients_xlsx_bytes(session: AsyncSession, clauses: list[Any], *, include_email: bool = False) -> bytes:
    wb = Workbook(write_only=True)
    ws = wb.create_sheet(title="Клієнти")
    ws.append(_clients_export_headers(include_email))
    offset = 0
    batch = 2500
    allow = _clients_export_allow_clause()
    while True:
        stmt = select(Client)
        wheres: list[Any] = []
        if clauses:
            wheres.extend(clauses)
        if allow is not None:
            wheres.append(allow)
        if wheres:
            stmt = stmt.where(*wheres)
        stmt = stmt.order_by(Client.id).offset(offset).limit(batch)
        res = await session.execute(stmt)
        rows = list(res.scalars().all())
        if not rows:
            break
        for c in rows:
            ws.append(_client_export_row_values(c, include_email))
        offset += batch
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


async def build_clients_csv_bytes_async(session: AsyncSession, clauses: list[Any], *, include_email: bool = False) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    w.writerow(_clients_export_headers(include_email))
    offset = 0
    batch = 2500
    allow = _clients_export_allow_clause()
    while True:
        stmt = select(Client)
        wheres: list[Any] = []
        if clauses:
            wheres.extend(clauses)
        if allow is not None:
            wheres.append(allow)
        if wheres:
            stmt = stmt.where(*wheres)
        stmt = stmt.order_by(Client.id).offset(offset).limit(batch)
        res = await session.execute(stmt)
        rows = list(res.scalars().all())
        if not rows:
            break
        for c in rows:
            w.writerow(_client_export_row_values(c, include_email))
        offset += batch
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


ORDER_EXPORT_HEADERS = [
    "ID замовлення",
    "Дата",
    "Сума",
    "Статус ID",
    "Статус",
    "Менеджер",
    "Товар",
    "product_id",
    "К-сть",
    "Ціна",
    "Бренд",
]


async def build_client_orders_csv_bytes(session: AsyncSession, customer_id: str) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    w.writerow(ORDER_EXPORT_HEADERS)
    if customer_id in CLIENTS_EXPORT_EXCLUDED_CUSTOMER_IDS:
        return ("\ufeff" + buf.getvalue()).encode("utf-8")
    res = await session.execute(
        select(ClientOrder)
        .where(ClientOrder.client_customer_id == customer_id)
        .options(selectinload(ClientOrder.lines))
        .order_by(nullslast(ClientOrder.order_date.desc()), ClientOrder.order_id.desc())
    )
    orders = list(res.scalars().unique().all())
    for o in orders:
        od = o.order_date.strftime("%Y-%m-%d %H:%M") if o.order_date else ""
        total = round(float(o.order_total), 2) if o.order_total is not None else ""
        for ln in o.lines or []:
            w.writerow(
                [
                    o.order_id,
                    od,
                    total,
                    o.order_status_id or "",
                    normalize_order_status_name(o.order_status, o.order_status_id),
                    o.manager_username or "",
                    (getattr(ln, "name", None) or "")[:500],
                    ln.product_id or "",
                    ln.quantity or "",
                    round(float(ln.price), 2) if ln.price is not None else "",
                    ln.brand or "",
                ]
            )
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


async def build_client_orders_xlsx_bytes(session: AsyncSession, customer_id: str) -> bytes:
    wb = Workbook(write_only=True)
    ws = wb.create_sheet(title="Замовлення")
    ws.append(ORDER_EXPORT_HEADERS)
    if customer_id in CLIENTS_EXPORT_EXCLUDED_CUSTOMER_IDS:
        bio = io.BytesIO()
        wb.save(bio)
        return bio.getvalue()
    res = await session.execute(
        select(ClientOrder)
        .where(ClientOrder.client_customer_id == customer_id)
        .options(selectinload(ClientOrder.lines))
        .order_by(nullslast(ClientOrder.order_date.desc()), ClientOrder.order_id.desc())
    )
    orders = list(res.scalars().unique().all())
    for o in orders:
        od = o.order_date.strftime("%Y-%m-%d %H:%M") if o.order_date else ""
        total = round(float(o.order_total), 2) if o.order_total is not None else ""
        for ln in o.lines or []:
            ws.append(
                [
                    o.order_id,
                    od,
                    total,
                    o.order_status_id or "",
                    normalize_order_status_name(o.order_status, o.order_status_id),
                    o.manager_username or "",
                    (getattr(ln, "name", None) or "")[:500],
                    ln.product_id or "",
                    ln.quantity or "",
                    round(float(ln.price), 2) if ln.price is not None else "",
                    ln.brand or "",
                ]
            )
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


async def build_all_orders_csv_bytes(session: AsyncSession, clauses: list[Any]) -> bytes:
    """Усі рядки замовлень для клієнтів, що відповідають фільтрам (один JOIN-запит)."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    w.writerow(["customer_id"] + ORDER_EXPORT_HEADERS)
    allow = _clients_export_allow_clause()
    stmt = (
        select(
            Client.customer_id,
            ClientOrder.order_id,
            ClientOrder.order_date,
            ClientOrder.order_total,
            ClientOrder.order_status_id,
            ClientOrder.order_status,
            ClientOrder.manager_username,
            ClientOrderLine.name,
            ClientOrderLine.product_id,
            ClientOrderLine.quantity,
            ClientOrderLine.price,
            ClientOrderLine.brand,
        )
        .select_from(Client)
        .join(ClientOrder, ClientOrder.client_customer_id == Client.customer_id)
        .join(ClientOrderLine, ClientOrderLine.order_row_id == ClientOrder.id)
    )
    wheres_csv: list[Any] = []
    if clauses:
        wheres_csv.extend(clauses)
    if allow is not None:
        wheres_csv.append(allow)
    if wheres_csv:
        stmt = stmt.where(*wheres_csv)
    stmt = stmt.order_by(Client.customer_id, nullslast(ClientOrder.order_date.desc()))
    res = await session.execute(stmt)
    for row in res.all():
        cid, oid, odt, tot, sid, sname, mgr_name, name, pid, qty, price, brand = row
        od = odt.strftime("%Y-%m-%d %H:%M") if odt else ""
        tot_s = round(float(tot), 2) if tot is not None else ""
        w.writerow(
            [
                cid,
                oid,
                od,
                tot_s,
                sid or "",
                normalize_order_status_name(sname, sid),
                mgr_name or "",
                (name or "")[:500],
                pid or "",
                qty or "",
                round(float(price), 2) if price is not None else "",
                brand or "",
            ]
        )
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


async def build_all_orders_xlsx_bytes(session: AsyncSession, clauses: list[Any]) -> bytes:
    wb = Workbook(write_only=True)
    ws = wb.create_sheet(title="Замовлення")
    ws.append(["customer_id"] + ORDER_EXPORT_HEADERS)
    allow = _clients_export_allow_clause()
    stmt = (
        select(
            Client.customer_id,
            ClientOrder.order_id,
            ClientOrder.order_date,
            ClientOrder.order_total,
            ClientOrder.order_status_id,
            ClientOrder.order_status,
            ClientOrder.manager_username,
            ClientOrderLine.name,
            ClientOrderLine.product_id,
            ClientOrderLine.quantity,
            ClientOrderLine.price,
            ClientOrderLine.brand,
        )
        .select_from(Client)
        .join(ClientOrder, ClientOrder.client_customer_id == Client.customer_id)
        .join(ClientOrderLine, ClientOrderLine.order_row_id == ClientOrder.id)
    )
    wheres_x: list[Any] = []
    if clauses:
        wheres_x.extend(clauses)
    if allow is not None:
        wheres_x.append(allow)
    if wheres_x:
        stmt = stmt.where(*wheres_x)
    stmt = stmt.order_by(Client.customer_id, nullslast(ClientOrder.order_date.desc()))
    res = await session.execute(stmt)
    for row in res.all():
        cid, oid, odt, tot, sid, sname, mgr_name, name, pid, qty, price, brand = row
        od = odt.strftime("%Y-%m-%d %H:%M") if odt else ""
        tot_s = round(float(tot), 2) if tot is not None else ""
        ws.append(
            [
                cid,
                oid,
                od,
                tot_s,
                sid or "",
                normalize_order_status_name(sname, sid),
                mgr_name or "",
                (name or "")[:500],
                pid or "",
                qty or "",
                round(float(price), 2) if price is not None else "",
                brand or "",
            ]
        )
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def _dash_pct(part: float | int, whole: float | int) -> float:
    if not whole:
        return 0.0
    return round(100.0 * float(part) / float(whole), 1)


# Відомі дублікати назв (UA / RU / лат.) — у «Топ міст» один рядок, сума клієнтів; у таблиці лише одна укр. назва.
_CITY_GROUP_PRIMARY: dict[str, str] = {
    "grp:kyiv": "Київ",
    "grp:odesa": "Одеса",
    "grp:kharkiv": "Харків",
    "grp:dnipro": "Дніпро",
    "grp:lviv": "Львів",
    "grp:zaporizhzhia": "Запоріжжя",
    "grp:kryvyirih": "Кривий Ріг",
    "grp:mykolaiv": "Миколаїв",
    "grp:vinnytsia": "Вінниця",
    "grp:chernihiv": "Чернігів",
    "grp:cherkasy": "Черкаси",
    "grp:sumy": "Суми",
    "grp:rivne": "Рівне",
    "grp:ternopil": "Тернопіль",
    "grp:ivano-frankivsk": "Івано-Франківськ",
    "grp:lutsk": "Луцьк",
    "grp:uzhhorod": "Ужгород",
    "grp:kropyvnytskyi": "Кропивницький",
    "grp:chernivtsi": "Чернівці",
    "grp:zhytomyr": "Житомир",
    "grp:khmelnytskyi": "Хмельницький",
    "grp:mariupol": "Маріуполь",
    "grp:kherson": "Херсон",
    "grp:poltava": "Полтава",
    "grp:donetsk": "Донецьк",
    "grp:luhansk": "Луганськ",
    "grp:kamianske": "Кам'янське",
    "grp:nikopol": "Нікополь",
    "grp:bilatserkva": "Біла Церква",
    "grp:kramatorsk": "Краматорськ",
    "grp:sloviansk": "Слов'янськ",
    "grp:berdiansk": "Бердянськ",
    "grp:melitopol": "Мелітополь",
    "grp:pavlohrad": "Павлоград",
    "grp:mukachevo": "Мукачево",
    "grp:severodonetsk": "Сєвєродонецьк",
}

_CITY_ALIAS_TO_GROUP: dict[str, str] = {}


def _register_city_aliases(group_id: str, *aliases: str) -> None:
    for a in aliases:
        _CITY_ALIAS_TO_GROUP[a.casefold()] = group_id


_register_city_aliases(
    "grp:kyiv",
    "київ",
    "киев",
    "kyiv",
    "kiev",
)
_register_city_aliases(
    "grp:odesa",
    "одеса",
    "одесса",
    "odesa",
    "odessa",
)
_register_city_aliases(
    "grp:kharkiv",
    "харків",
    "харьков",
    "kharkiv",
    "kharkov",
)
_register_city_aliases(
    "grp:dnipro",
    "дніпро",
    "днепр",
    "dnipro",
    "dnepr",
    "dnipropetrovsk",
    "дніпропетровськ",
)
_register_city_aliases(
    "grp:lviv",
    "львів",
    "lviv",
    "lvov",
    "львов",
)
_register_city_aliases(
    "grp:zaporizhzhia",
    "запоріжжя",
    "zaporizhzhia",
    "zaporizhzhya",
    "zaporozhye",
    "запорожье",
    "zaporozhe",
)
_register_city_aliases(
    "grp:kryvyirih",
    "кривий ріг",
    "kryvyi rih",
    "kryvyi rig",
    "krivoy rog",
    "кривой рог",
    "kryvyy rih",
)
_register_city_aliases(
    "grp:mykolaiv",
    "миколаїв",
    "mykolaiv",
    "nikolaev",
    "николаев",
    "nikolayev",
    "mykolayiv",
)
_register_city_aliases(
    "grp:vinnytsia",
    "вінниця",
    "vinnytsia",
    "vinnitsa",
    "винница",
)
_register_city_aliases(
    "grp:chernihiv",
    "чернігів",
    "chernihiv",
    "chernigov",
    "чернигов",
)
_register_city_aliases(
    "grp:cherkasy",
    "черкаси",
    "cherkasy",
    "cherkassy",
    "черкассы",
)
_register_city_aliases(
    "grp:sumy",
    "суми",
    "sumy",
    "сумы",
)
_register_city_aliases(
    "grp:rivne",
    "рівне",
    "rivne",
    "rovno",
    "ровно",
)
_register_city_aliases(
    "grp:ternopil",
    "тернопіль",
    "ternopil",
    "ternopol",
    "тернополь",
)
_register_city_aliases(
    "grp:ivano-frankivsk",
    "івано-франківськ",
    "ivano-frankivsk",
    "ивано-франковск",
    "ivano frankivsk",
)
_register_city_aliases(
    "grp:lutsk",
    "луцьк",
    "lutsk",
    "луцк",
)
_register_city_aliases(
    "grp:uzhhorod",
    "ужгород",
    "uzhhorod",
    "uzhgorod",
)
_register_city_aliases(
    "grp:kropyvnytskyi",
    "кропивницький",
    "kropyvnytskyi",
    "kirovohrad",
    "kirovograd",
    "кировоград",
    "кіровоград",
)
_register_city_aliases(
    "grp:chernivtsi",
    "чернівці",
    "chernivtsi",
    "chernovtsy",
    "черновцы",
)
_register_city_aliases(
    "grp:zhytomyr",
    "житомир",
    "zhytomyr",
    "zhitomir",
)
_register_city_aliases(
    "grp:khmelnytskyi",
    "хмельницький",
    "khmelnytskyi",
    "khmelnitsky",
    "khmelnytsky",
    "хмельницкий",
)
_register_city_aliases(
    "grp:mariupol",
    "маріуполь",
    "mariupol",
    "мариуполь",
)
_register_city_aliases(
    "grp:kherson",
    "херсон",
    "kherson",
)
_register_city_aliases(
    "grp:poltava",
    "полтава",
    "poltava",
)
_register_city_aliases(
    "grp:donetsk",
    "донецьк",
    "donetsk",
    "донецк",
)
_register_city_aliases(
    "grp:luhansk",
    "луганськ",
    "luhansk",
    "луганск",
    "lugansk",
)
_register_city_aliases(
    "grp:kamianske",
    "кам'янське",
    "камянське",
    "kamianske",
    "kamenskoe",
    "каменское",
    "dniprodzerzhynsk",
    "дніпродзержинськ",
    "днепродзержинск",
)
_register_city_aliases(
    "grp:nikopol",
    "нікополь",
    "nikopol",
    "никополь",
)
_register_city_aliases(
    "grp:bilatserkva",
    "біла церква",
    "bila tserkva",
    "belaya tserkov",
    "белая церковь",
)
_register_city_aliases(
    "grp:kramatorsk",
    "краматорськ",
    "kramatorsk",
    "краматорск",
)
_register_city_aliases(
    "grp:sloviansk",
    "слов'янськ",
    "словянськ",
    "sloviansk",
    "slavyansk",
    "славянск",
)
_register_city_aliases(
    "grp:berdiansk",
    "бердянськ",
    "berdiansk",
    "berdyansk",
    "бердянск",
)
_register_city_aliases(
    "grp:melitopol",
    "мелітополь",
    "melitopol",
    "мелитополь",
)
_register_city_aliases(
    "grp:pavlohrad",
    "павлоград",
    "pavlohrad",
)
_register_city_aliases(
    "grp:severodonetsk",
    "сєвєродонецьк",
    "северодонецк",
    "severodonetsk",
    "sieverodonetsk",
)
_register_city_aliases(
    "grp:mukachevo",
    "мукачево",
    "mukachevo",
    "мукачеве",
    "mukacheve",
)
# Київська агломерація — окремі групи (UA/RU/лат.), фільтр об'єднує варіанти написання.
_CITY_GROUP_PRIMARY["grp:irpin"] = "Ірпінь"
_CITY_GROUP_PRIMARY["grp:brovary"] = "Бровари"
_CITY_GROUP_PRIMARY["grp:bucha"] = "Буча"
_CITY_GROUP_PRIMARY["grp:sofiivska-borshch"] = "Софіївська Борщагівка"
_CITY_GROUP_PRIMARY["grp:petropavlivska-borshch"] = "Петропавлівська Борщагівка"
_CITY_GROUP_PRIMARY["grp:vyshneve"] = "Вишневе"
_CITY_GROUP_PRIMARY["grp:boryspil"] = "Бориспіль"
_CITY_GROUP_PRIMARY["grp:vyshhorod"] = "Вишгород"
_CITY_GROUP_PRIMARY["grp:boyarka"] = "Боярка"
_CITY_GROUP_PRIMARY["grp:obukhiv"] = "Обухів"
_CITY_GROUP_PRIMARY["grp:fastiv"] = "Фастів"
_CITY_GROUP_PRIMARY["grp:vasylkiv"] = "Васильків"
_CITY_GROUP_PRIMARY["grp:kriukivshchyna"] = "Крюківщина"
_CITY_GROUP_PRIMARY["grp:hatne"] = "Гатне"
_register_city_aliases("grp:irpin", "ірпінь", "ирпень", "irpin")
_register_city_aliases("grp:brovary", "бровари", "бровары", "brovary")
_register_city_aliases("grp:bucha", "буча", "bucha")
_register_city_aliases(
    "grp:sofiivska-borshch",
    "софіївська борщагівка",
    "софиевская борщаговка",
)
_register_city_aliases(
    "grp:petropavlivska-borshch",
    "петропавлівська борщагівка",
    "петропавловская борщаговка",
)
_register_city_aliases("grp:vyshneve", "вишневе", "вишневое", "vyshneve")
_register_city_aliases("grp:boryspil", "бориспіль", "борисполь", "boryspil")
_register_city_aliases("grp:vyshhorod", "вишгород", "vyshhorod", "vyshgorod")
_register_city_aliases("grp:boyarka", "боярка", "boyarka")
_register_city_aliases("grp:obukhiv", "обухів", "обухов", "obukhiv")
_register_city_aliases("grp:fastiv", "фастів", "fastiv", "фастов")
_register_city_aliases("grp:vasylkiv", "васильків", "васильков", "vasylkiv")
_register_city_aliases("grp:kriukivshchyna", "крюківщина", "крюковщина", "kriukivshchyna")
_register_city_aliases("grp:hatne", "гатне", "hatne")

_CITY_PREFIX_RE = re.compile(
    r"^(?:м\.?|г\.?|с\.?|село|місто|город)\s+",
    re.IGNORECASE,
)
_KYIV_BRANCH_NUM_RE = re.compile(r"^(?:київ|киев|kyiv|kiev)\s+\d", re.IGNORECASE)
_KYIV_PAREN_RE = re.compile(r"\((?:київ|киев|kyiv|kiev)\)\s*$", re.IGNORECASE)


def _city_fold(raw: str) -> str:
    return raw.casefold().replace("’", "'").replace("`", "'")


def _city_primary_token(cf: str) -> str:
    """Перший фрагмент назви без префікса м./г./с. і хвоста після коми."""
    s = _CITY_PREFIX_RE.sub("", cf.strip(), count=1).strip()
    if "," in s:
        s = s.split(",", 1)[0].strip()
    return s


def resolve_city_group_id(raw: str | None) -> str | None:
    """
    grp:… для відомих міст; __empty__ якщо порожньо; None — окреме «сире» місто.
    Ураховує м. Київ, киев 31 (відділення НП), Пуща-Водиця (Київ) тощо.
    """
    s = (raw or "").strip()
    if not s:
        return "__empty__"
    cf = _city_fold(s)
    mapped = _CITY_ALIAS_TO_GROUP.get(cf)
    if mapped:
        return mapped
    token = _city_primary_token(cf)
    if token and token != cf:
        mapped = _CITY_ALIAS_TO_GROUP.get(token)
        if mapped:
            return mapped
    if _KYIV_BRANCH_NUM_RE.match(token or cf):
        return "grp:kyiv"
    if _KYIV_PAREN_RE.search(cf):
        return "grp:kyiv"
    return None


def city_group_id_for_sql(raw: str | None) -> str:
    """Повертає grp:… / __empty__ / порожній рядок (для SQLite UDF)."""
    gid = resolve_city_group_id(raw)
    if gid in (None, ""):
        return ""
    return gid


def _city_canonical_group_key(raw: str | None) -> str:
    s = (raw or "").strip()
    if not s:
        return "__empty__"
    gid = resolve_city_group_id(s)
    if gid and gid.startswith("grp:"):
        return gid
    return f"raw:{s.casefold()}"


def _city_group_display_name(group_key: str, labels: set[str]) -> str:
    if group_key == "__empty__":
        return "Не вказано"
    primary = _CITY_GROUP_PRIMARY.get(group_key)
    if primary:
        return primary
    if not labels:
        return "—"

    def _prefer_cyrillic(s: str) -> bool:
        return any(
            ("А" <= c <= "Я" or "а" <= c <= "я") or c in "ІіЇїЄєҐґ"
            for c in s
        )

    lst = sorted(
        labels,
        key=lambda t: (not _prefer_cyrillic(t), len(t.strip()), t.casefold()),
    )
    return lst[0].strip()


def _merge_top_cities_rows(
    city_raw: list[tuple[Any, int]],
    total_clients: int,
    limit: int = 12,
) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for city, cnt in city_raw:
        raw = (city or "").strip()
        key = _city_canonical_group_key(raw)
        if key not in groups:
            groups[key] = {"count": 0, "labels": set()}
        g = groups[key]
        g["count"] += int(cnt)
        if raw:
            g["labels"].add(raw)
    rows: list[tuple[str, int]] = []
    for key, g in groups.items():
        name = _city_group_display_name(key, g["labels"])
        rows.append((name, g["count"]))
    rows.sort(key=lambda x: -x[1])
    out: list[dict[str, Any]] = []
    for name, c in rows[:limit]:
        out.append({"name": name, "count": c, "pct": _dash_pct(c, total_clients)})
    return out


def merge_city_revenue_rows(
    city_revenues: dict[str, float],
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Топ міст за доходом з обʼєднанням варіантів назви (Київ + Киев тощо)."""
    groups: dict[str, dict[str, Any]] = {}
    for raw_city, rev in city_revenues.items():
        raw = (raw_city or "").strip()
        if not raw or raw.casefold() in ("невідомо", "не вказано"):
            key = "__empty__"
        else:
            key = _city_canonical_group_key(raw)
        if key not in groups:
            groups[key] = {"revenue": 0.0, "labels": set()}
        g = groups[key]
        g["revenue"] += float(rev or 0.0)
        if raw and key != "__empty__":
            g["labels"].add(raw)
    rows: list[tuple[str, float]] = []
    for key, g in groups.items():
        if key == "__empty__":
            continue
        name = _city_group_display_name(key, g["labels"])
        rows.append((name, g["revenue"]))
    rows.sort(key=lambda x: -x[1])
    total = sum(v for _, v in rows) or 1.0
    return [
        {
            "name": name,
            "revenue": round(val, 2),
            "share_pct": round(100 * val / total, 1),
        }
        for name, val in rows[:limit]
    ]


def list_canonical_city_names_from_raw(
    city_raw: list[tuple[Any, int]],
    *,
    limit: int = 500,
) -> list[str]:
    """Канонічні назви міст для фільтра (як у «Топ міст» на дашборді)."""
    merged = _merge_top_cities_rows(city_raw, max(sum(int(c) for _, c in city_raw), 1), limit=limit)
    return [str(r["name"]) for r in merged]


_OBLAST_CANONICAL_BY_TOKEN: dict[str, str] = {
    "вінницька": "Вінницька область",
    "волинська": "Волинська область",
    "дніпропетровська": "Дніпропетровська область",
    "донецька": "Донецька область",
    "житомирська": "Житомирська область",
    "закарпатська": "Закарпатська область",
    "запорізька": "Запорізька область",
    "івано-франківська": "Івано-Франківська область",
    "київська": "Київська область",
    "кіровоградська": "Кіровоградська область",
    "луганська": "Луганська область",
    "львівська": "Львівська область",
    "миколаївська": "Миколаївська область",
    "одеська": "Одеська область",
    "полтавська": "Полтавська область",
    "рівненська": "Рівненська область",
    "сумська": "Сумська область",
    "тернопільська": "Тернопільська область",
    "харківська": "Харківська область",
    "херсонська": "Херсонська область",
    "хмельницька": "Хмельницька область",
    "черкаська": "Черкаська область",
    "чернівецька": "Чернівецька область",
    "чернігівська": "Чернігівська область",
    "крим": "АР Крим",
}
_OBLAST_TOKEN_RE = re.compile(r"(?:^|[,\s(])([а-яіїєґ'’`-]{4,})\s+обл(?:асть)?\.?", flags=re.IGNORECASE)


def normalize_geo_city_for_sql(raw: str | None) -> str:
    """Unicode casefold + trim для geo_city у SQLite (вбудований lower() не працює з кирилицею)."""
    return (raw or "").strip().casefold()


def extract_geo_oblast_label(raw_city: str | None) -> str:
    """Витягнути назву області з geo_city; якщо не знайдено — порожній рядок."""
    raw = (raw_city or "").strip()
    if not raw:
        return ""
    s = raw.casefold().replace("’", "'").replace("`", "'")
    match = _OBLAST_TOKEN_RE.search(s)
    if not match:
        return ""
    token = match.group(1).strip(" ,.)(")
    return _OBLAST_CANONICAL_BY_TOKEN.get(token, "")


def list_geo_oblast_names_from_raw(city_raw: list[tuple[Any, int]]) -> list[str]:
    """Список областей, які реально трапляються у geo_city."""
    counts: dict[str, int] = {}
    for city, cnt in city_raw:
        name = extract_geo_oblast_label(str(city or ""))
        if not name:
            continue
        counts[name] = counts.get(name, 0) + int(cnt or 0)
    return [k for k, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def canonical_geo_city_filter_label(raw: str | None) -> str:
    """Канонічна підпис для фільтра / URL (Киев → Київ)."""
    s = (raw or "").strip()
    if not s:
        return ""
    if s == "Не вказано":
        return s
    key = _city_canonical_group_key(s)
    if key == "__empty__":
        return "Не вказано"
    primary = _CITY_GROUP_PRIMARY.get(key)
    if primary:
        return primary
    return _city_group_display_name(key, {s})


def _city_group_id_for_canonical(canonical: str) -> str | None:
    """grp:… | __empty__ | None (окреме «сире» місто)."""
    name = (canonical or "").strip()
    if not name:
        return None
    if name == "Не вказано":
        return "__empty__"
    cf = name.casefold()
    for gid, primary in _CITY_GROUP_PRIMARY.items():
        if primary.casefold() == cf:
            return gid
    return _CITY_ALIAS_TO_GROUP.get(cf)


def geo_city_filter_clause(canonical_cities: list[str] | None) -> Any | None:
    """Фільтр за канонічними назвами міст (Київ об'єднує Киев, kyiv, м. Київ тощо)."""
    selected = [str(x).strip() for x in (canonical_cities or []) if str(x).strip()]
    if not selected:
        return None
    geo_gid = func.client_geo_city_group(func.coalesce(Client.geo_city, ""))
    geo_norm = func.client_geo_city_norm(func.coalesce(Client.geo_city, ""))
    parts: list[Any] = []
    for name in selected:
        group_id = _city_group_id_for_canonical(name)
        if group_id == "__empty__":
            parts.append(or_(Client.geo_city.is_(None), geo_norm == ""))
            continue
        if group_id and group_id.startswith("grp:"):
            parts.append(geo_gid == group_id)
            continue
        parts.append(geo_norm == name.casefold())
    if not parts:
        return None
    return or_(*parts) if len(parts) > 1 else parts[0]


def geo_oblast_filter_clause(oblasts: list[str] | None) -> Any | None:
    """Фільтр клієнтів за областями, витягнутими з geo_city."""
    selected = [str(x).strip() for x in (oblasts or []) if str(x).strip()]
    if not selected:
        return None
    parts: list[Any] = []
    for name in selected:
        parts.append(func.client_geo_oblast(func.coalesce(Client.geo_city, "")) == name)
    return or_(*parts) if len(parts) > 1 else parts[0]


_UA_MONTHS_SHORT = (
    "січ.",
    "лют.",
    "бер.",
    "квіт.",
    "трав.",
    "черв.",
    "лип.",
    "серп.",
    "вер.",
    "жовт.",
    "лист.",
    "груд.",
)


def _ym_to_label_ua(ym: str) -> str:
    """2025-03 → «бер. 2025» (звичний для UA короткий підпис)."""
    try:
        y, m = str(ym).strip().split("-", 1)
        mi = int(m)
        if 1 <= mi <= 12:
            return f"{_UA_MONTHS_SHORT[mi - 1]} {y}"
    except (ValueError, IndexError):
        pass
    return str(ym)


async def load_feed_tag_filter_options(session: AsyncSession) -> list[dict[str, Any]]:
    """Унікальні мітки з фіду для фільтра «Мітки» на /clients."""
    from bot.services.clients_feed_channels import sort_feed_tag_filter_options

    rows = (
        await session.execute(
            select(
                ClientFeedTag.code,
                ClientFeedTag.label,
                func.count(func.distinct(ClientFeedTag.client_customer_id)).label("clients_count"),
            )
            .group_by(ClientFeedTag.code, ClientFeedTag.label)
            .order_by(
                desc(func.count(func.distinct(ClientFeedTag.client_customer_id))),
                ClientFeedTag.label.asc(),
            )
        )
    ).all()
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for code, label, clients_count in rows:
        c = str(code or "").strip().lower()
        if not c or c in seen:
            continue
        seen.add(c)
        out.append(
            {
                "code": c,
                "label": (str(label or c).strip() or c),
                "clients_count": int(clients_count or 0),
            }
        )
    return sort_feed_tag_filter_options(out)


async def load_clients_base_totals(session: AsyncSession) -> dict[str, Any]:
    """Лаконічні підсумки бази клієнтів (для списку клієнтів)."""
    allow_c = _clients_export_allow_clause()
    stmt = select(
        func.count(Client.id).label("n_total"),
        func.count(Client.id).filter(Client.is_registered.is_(True)).label("n_reg"),
        func.count(Client.id).filter(Client.is_registered.is_(False)).label("n_guest"),
        func.count(Client.id).filter(and_(Client.email.isnot(None), Client.email != "")).label("n_email"),
    )
    if allow_c is not None:
        stmt = stmt.where(allow_c)
    row = (await session.execute(stmt)).one()
    total = int(row.n_total or 0)
    n_reg = int(row.n_reg or 0)
    n_guest = int(row.n_guest or 0)
    n_email = int(row.n_email or 0)
    return {
        "clients": total,
        "registered": n_reg,
        "guests": n_guest,
        "pct_registered": _dash_pct(n_reg, total),
        "pct_guests": _dash_pct(n_guest, total),
        "with_email": n_email,
        "pct_email": _dash_pct(n_email, total),
    }


async def load_clients_dashboard_snapshot(
    session: AsyncSession,
    monthly_chart_outcome: str | None = None,
    monthly_date_from: str | None = None,
    monthly_date_to: str | None = None,
    chart_granularity: str | None = None,
) -> dict[str, Any]:
    """
    Агрегована статистика для маркетингового дашборду клієнтів (клієнти + замовлення + рядки).
    Усі дати порівнюються з reference_now() (Київ).
    monthly_chart_outcome — all | success | unsuccessful: фільтр лише для місячного графіка
    (успішні = ті самі order_status_id, що дають тон success, як у блоці «Статуси замовлень»).
    monthly_date_from / monthly_date_to — діапазон order_date для місячного графіка та для блоків нижче
    (сегменти, активність, топ міст і джерел, таблиця статусів, сума шт у рядках). Формат YYYY-MM-DD.
    KPI-картки зверху (усього клієнтів, email, статуси замовлень у цифрах, ковзні вікна) — без змін, по всій базі.
    """
    now = reference_now()
    d30 = now - timedelta(days=30)
    d90 = now - timedelta(days=90)

    monthly_outcome = normalize_monthly_chart_outcome(monthly_chart_outcome)

    allow_c = _clients_export_allow_clause()
    deny_o = _clients_orders_exclude_clause()

    stmt_totals = select(
        func.count(Client.id).label("n_total"),
        func.count(Client.id).filter(Client.is_registered.is_(True)).label("n_reg"),
        func.count(Client.id).filter(Client.is_registered.is_(False)).label("n_guest"),
        func.count(Client.id).filter(and_(Client.email.isnot(None), Client.email != "")).label("n_email"),
        func.count(Client.id).filter(and_(Client.telephone.isnot(None), Client.telephone != "")).label(
            "n_phone"
        ),
        func.max(Client.feed_generated_at).label("feed_max"),
        func.max(Client.last_order_date).label("max_last_order"),
        func.min(Client.first_order_date).label("min_first_order"),
        func.count(Client.id).filter(
            or_(Client.source_utm_source.isnot(None), Client.source_utm_campaign.isnot(None))
        ).label("n_utm_client"),
    )
    if allow_c is not None:
        stmt_totals = stmt_totals.where(allow_c)
    row = (await session.execute(stmt_totals)).one()

    total = int(row.n_total or 0)
    n_reg = int(row.n_reg or 0)
    n_guest = int(row.n_guest or 0)
    n_email = int(row.n_email or 0)
    n_phone = int(row.n_phone or 0)

    stmt_orow = select(
        func.count(ClientOrder.id).label("oc"),
        func.min(ClientOrder.order_date).label("omin"),
        func.max(ClientOrder.order_date).label("omax"),
    ).select_from(ClientOrder)
    if deny_o is not None:
        stmt_orow = stmt_orow.where(deny_o)
    orow = (await session.execute(stmt_orow)).one()

    orders_in_db = int(orow.oc or 0)
    data_order_min: datetime | None = orow.omin if orow.omin else None
    m_start, m_end, monthly_range_meta = resolve_monthly_chart_order_range(
        now, monthly_date_from, monthly_date_to, data_order_min
    )
    monthly_date_bounds = and_(
        ClientOrder.order_date.isnot(None),
        ClientOrder.order_date >= m_start,
        ClientOrder.order_date <= m_end,
    )

    stmt_utm = select(func.count()).select_from(ClientOrder).where(
        ClientOrder.utm_source.isnot(None), ClientOrder.utm_source != ""
    )
    if deny_o is not None:
        stmt_utm = stmt_utm.where(deny_o)
    orders_with_utm = int((await session.execute(stmt_utm)).scalar() or 0)

    # KPI «Статуси замовлень» — усі замовлення в БД (як раніше).
    stmt_status_global = (
        select(ClientOrder.order_status_id, ClientOrder.order_status, func.count(ClientOrder.id))
        .select_from(ClientOrder)
        .group_by(ClientOrder.order_status_id, ClientOrder.order_status)
        .order_by(desc(func.count(ClientOrder.id)))
    )
    if deny_o is not None:
        stmt_status_global = stmt_status_global.where(deny_o)
    status_raw_global = (await session.execute(stmt_status_global)).all()
    status_counts: dict[str, int] = {
        "success": 0,
        "warning": 0,
        "danger": 0,
        "info": 0,
        "muted": 0,
        "transitional": 0,
    }
    n_status_transitional = 0
    n_status_success = 0
    n_status_unsuccess = 0
    for sid, sname, cnt in status_raw_global:
        c = int(cnt or 0)
        if is_order_status_transitional(sname, sid):
            n_status_transitional += c
            status_counts["transitional"] = status_counts.get("transitional", 0) + c
            continue
        title = normalize_order_status_name(sname, sid)
        tone = order_status_tone(title, sid)
        status_counts[tone] = status_counts.get(tone, 0) + c
        if tone == "success":
            n_status_success += c
        else:
            n_status_unsuccess += c
    n_classified = n_status_success + n_status_unsuccess
    status_summary = {
        "total": orders_in_db,
        "successful": n_status_success,
        "unsuccessful": n_status_unsuccess,
        "transitional": n_status_transitional,
        "pct_successful": _dash_pct(n_status_success, n_classified),
        "pct_unsuccessful": _dash_pct(n_status_unsuccess, n_classified),
        "pct_transitional": _dash_pct(n_status_transitional, orders_in_db),
    }

    # Таблиця «Статуси замовлень» + пончики/топи нижче — той самий діапазон, що й місячний графік.
    stmt_status_period = (
        select(ClientOrder.order_status_id, ClientOrder.order_status, func.count(ClientOrder.id))
        .select_from(ClientOrder)
        .where(monthly_date_bounds)
        .group_by(ClientOrder.order_status_id, ClientOrder.order_status)
        .order_by(desc(func.count(ClientOrder.id)))
    )
    if deny_o is not None:
        stmt_status_period = stmt_status_period.where(deny_o)
    status_raw_period = (await session.execute(stmt_status_period)).all()
    orders_in_period = sum(int(r[2] or 0) for r in status_raw_period)
    status_pct_whole = orders_in_period if orders_in_period > 0 else 1
    merged_status: dict[str, dict[str, Any]] = {}
    for sid, sname, cnt in status_raw_period:
        c = int(cnt or 0)
        title = normalize_order_status_name(sname, sid)
        key = title.casefold()
        if key not in merged_status:
            merged_status[key] = {"name": title, "count": 0, "sids": []}
        else:
            if sid is not None and int(sid) in KNOWN_ORDER_STATUS_IDS:
                merged_status[key]["name"] = title
        merged_status[key]["count"] += c
        if sid is not None:
            merged_status[key]["sids"].append(int(sid))
    status_rows = []
    for _k, m in sorted(merged_status.items(), key=lambda it: -it[1]["count"])[:12]:
        rep_sid = min(m["sids"]) if m["sids"] else None
        status_rows.append(
            {
                "status_id": rep_sid,
                "name": m["name"],
                "count": m["count"],
                "pct": _dash_pct(m["count"], status_pct_whole),
                "tone": order_status_tone(m["name"], rep_sid),
            }
        )
    top_order_statuses = status_rows

    oc_sq = (
        select(
            ClientOrder.client_customer_id.label("cid"),
            func.count(ClientOrder.id).label("n_po"),
        )
        .select_from(ClientOrder)
        .where(monthly_date_bounds)
        .group_by(ClientOrder.client_customer_id)
    )
    if deny_o is not None:
        oc_sq = oc_sq.where(deny_o)
    oc_sq = oc_sq.subquery()
    n_po = func.coalesce(oc_sq.c.n_po, 0)
    seg_expr = case(
        (n_po == 0, "Без замовлень"),
        (n_po == 1, "1 замовлення"),
        (n_po < 5, "2–4 замовлення"),
        (n_po < 10, "5–9 замовлень"),
        else_="10+ (VIP)",
    )
    stmt_seg = (
        select(seg_expr.label("seg"), func.count(Client.id))
        .select_from(Client)
        .outerjoin(oc_sq, oc_sq.c.cid == Client.customer_id)
        .group_by(seg_expr)
    )
    if allow_c is not None:
        stmt_seg = stmt_seg.where(allow_c)
    seg_raw = (await session.execute(stmt_seg)).all()
    seg_order = [
        "Без замовлень",
        "1 замовлення",
        "2–4 замовлення",
        "5–9 замовлень",
        "10+ (VIP)",
    ]
    seg_map = {str(r[0]): int(r[1]) for r in seg_raw}
    segments = [
        {"name": name, "count": seg_map.get(name, 0), "pct": _dash_pct(seg_map.get(name, 0), total)}
        for name in seg_order
    ]

    ref_end = m_end
    d14p = ref_end - timedelta(days=14)
    d20p = ref_end - timedelta(days=20)
    d45p = ref_end - timedelta(days=45)
    d90p = ref_end - timedelta(days=90)
    last_sq = (
        select(
            ClientOrder.client_customer_id.label("cid"),
            func.max(ClientOrder.order_date).label("last_d"),
        )
        .select_from(ClientOrder)
        .where(monthly_date_bounds)
        .group_by(ClientOrder.client_customer_id)
    )
    if deny_o is not None:
        last_sq = last_sq.where(deny_o)
    last_sq = last_sq.subquery()
    act_expr = case(
        (last_sq.c.last_d.is_(None), "Немає замовлень у періоді"),
        (and_(last_sq.c.last_d <= d14p, last_sq.c.last_d >= d20p), "14–20 днів"),
        (last_sq.c.last_d >= d14p, "1–14 днів"),
        (and_(last_sq.c.last_d < d20p, last_sq.c.last_d >= d45p), "20–45 днів"),
        (and_(last_sq.c.last_d < d45p, last_sq.c.last_d >= d90p), "45–90 днів"),
        else_="90+ днів",
    )
    stmt_act = (
        select(act_expr.label("act"), func.count(Client.id))
        .select_from(Client)
        .outerjoin(last_sq, last_sq.c.cid == Client.customer_id)
        .group_by(act_expr)
    )
    if allow_c is not None:
        stmt_act = stmt_act.where(allow_c)
    act_raw = (await session.execute(stmt_act)).all()
    act_map = {str(r[0]): int(r[1]) for r in act_raw}
    act_order = [
        "1–14 днів",
        "14–20 днів",
        "20–45 днів",
        "45–90 днів",
        "90+ днів",
        "Немає замовлень у періоді",
    ]
    activity = [
        {"name": name, "count": act_map.get(name, 0), "pct": _dash_pct(act_map.get(name, 0), total)}
        for name in act_order
    ]

    # Графік замовлень/виручки: денна / тижнева / місячна агрегація; виручка — лише успішні.
    span_days = max(0, (m_end.date() - m_start.date()).days)
    chart_gran = normalize_chart_granularity(chart_granularity, span_days)
    monthly = await _load_orders_chart_buckets(
        session,
        monthly_date_bounds,
        monthly_outcome,
        chart_gran,
        deny_o,
    )
    _monthly_outcome_label_ua = {
        "all": "Усі замовлення",
        "success": "Лише успішні",
        "unsuccessful": "Лише неуспішні",
    }
    tot_m_o = sum(int(x["orders"]) for x in monthly)
    tot_m_r = sum(float(x["revenue"]) for x in monthly)
    tot_m_r_unsucc = 0.0
    if monthly_outcome == "all":
        tot_m_succ = sum(int(x.get("orders_success", 0)) for x in monthly)
        tot_m_unsucc = sum(int(x.get("orders_unsuccessful", 0)) for x in monthly)
        tot_m_trans = sum(int(x.get("orders_transitional", 0)) for x in monthly)
        tot_m_r_unsucc = sum(float(x.get("revenue_unsuccessful", 0)) for x in monthly)
        avg_check = round(tot_m_r / tot_m_succ, 2) if tot_m_succ else None
        pct_unsucc = _dash_pct(tot_m_unsucc, tot_m_o)
    elif monthly_outcome == "success":
        tot_m_succ, tot_m_unsucc, tot_m_trans = tot_m_o, 0, 0
        avg_check = round(tot_m_r / tot_m_o, 2) if tot_m_o else None
        pct_unsucc = 0.0
    else:
        tot_m_succ, tot_m_unsucc, tot_m_trans = 0, tot_m_o, 0
        tot_m_r_unsucc = tot_m_r
        avg_check = round(tot_m_r / tot_m_o, 2) if tot_m_o else None
        pct_unsucc = _dash_pct(tot_m_unsucc, tot_m_o) if tot_m_o else 0.0
    period_summary = {
        "orders": tot_m_o,
        "orders_success": tot_m_succ,
        "orders_unsuccessful": tot_m_unsucc,
        "orders_transitional": tot_m_trans,
        "revenue": tot_m_r,
        "revenue_unsuccessful": tot_m_r_unsucc,
        "pct_orders_unsuccessful": pct_unsucc,
        "avg_check_uah": avg_check,
        "buckets": len(monthly),
        "months": len(monthly),
        "granularity": chart_gran,
    }
    y_low = data_order_min.year if data_order_min else (now.year - 10)
    y_low = max(MONTHLY_CHART_HARD_MIN.year, min(y_low, now.year))
    monthly_year_options = list(range(now.year, y_low - 1, -1))
    monthly_chart_meta = {
        **monthly_range_meta,
        "months_with_orders": len(monthly),
        "period_summary": period_summary,
        "year_presets": monthly_year_options,
        "granularity": chart_gran,
        "span_days": span_days,
    }

    d7 = now - timedelta(days=7)
    d14 = now - timedelta(days=14)
    d180 = now - timedelta(days=180)
    d365 = now - timedelta(days=365)
    stmt_period = select(
        func.count(ClientOrder.id)
        .filter(and_(ClientOrder.order_date.isnot(None), ClientOrder.order_date >= d7))
        .label("c7"),
        func.coalesce(
            func.sum(ClientOrder.order_total).filter(
                and_(ClientOrder.order_date.isnot(None), ClientOrder.order_date >= d7)
            ),
            0.0,
        ).label("r7"),
        func.count(func.distinct(ClientOrder.client_customer_id))
        .filter(and_(ClientOrder.order_date.isnot(None), ClientOrder.order_date >= d7))
        .label("db7"),
        func.count(ClientOrder.id)
        .filter(and_(ClientOrder.order_date.isnot(None), ClientOrder.order_date >= d14))
        .label("c14"),
        func.coalesce(
            func.sum(ClientOrder.order_total).filter(
                and_(ClientOrder.order_date.isnot(None), ClientOrder.order_date >= d14)
            ),
            0.0,
        ).label("r14"),
        func.count(func.distinct(ClientOrder.client_customer_id))
        .filter(and_(ClientOrder.order_date.isnot(None), ClientOrder.order_date >= d14))
        .label("db14"),
        func.count(ClientOrder.id)
        .filter(and_(ClientOrder.order_date.isnot(None), ClientOrder.order_date >= d30))
        .label("c30"),
        func.coalesce(
            func.sum(ClientOrder.order_total).filter(
                and_(ClientOrder.order_date.isnot(None), ClientOrder.order_date >= d30)
            ),
            0.0,
        ).label("r30"),
        func.count(func.distinct(ClientOrder.client_customer_id))
        .filter(and_(ClientOrder.order_date.isnot(None), ClientOrder.order_date >= d30))
        .label("db30"),
        func.count(ClientOrder.id)
        .filter(and_(ClientOrder.order_date.isnot(None), ClientOrder.order_date >= d90))
        .label("c90"),
        func.coalesce(
            func.sum(ClientOrder.order_total).filter(
                and_(ClientOrder.order_date.isnot(None), ClientOrder.order_date >= d90)
            ),
            0.0,
        ).label("r90"),
        func.count(func.distinct(ClientOrder.client_customer_id))
        .filter(and_(ClientOrder.order_date.isnot(None), ClientOrder.order_date >= d90))
        .label("db90"),
        func.count(ClientOrder.id)
        .filter(and_(ClientOrder.order_date.isnot(None), ClientOrder.order_date >= d180))
        .label("c180"),
        func.coalesce(
            func.sum(ClientOrder.order_total).filter(
                and_(ClientOrder.order_date.isnot(None), ClientOrder.order_date >= d180)
            ),
            0.0,
        ).label("r180"),
        func.count(func.distinct(ClientOrder.client_customer_id))
        .filter(and_(ClientOrder.order_date.isnot(None), ClientOrder.order_date >= d180))
        .label("db180"),
        func.count(ClientOrder.id)
        .filter(and_(ClientOrder.order_date.isnot(None), ClientOrder.order_date >= d365))
        .label("c365"),
        func.coalesce(
            func.sum(ClientOrder.order_total).filter(
                and_(ClientOrder.order_date.isnot(None), ClientOrder.order_date >= d365)
            ),
            0.0,
        ).label("r365"),
        func.count(func.distinct(ClientOrder.client_customer_id))
        .filter(and_(ClientOrder.order_date.isnot(None), ClientOrder.order_date >= d365))
        .label("db365"),
    ).select_from(ClientOrder)
    if deny_o is not None:
        stmt_period = stmt_period.where(deny_o)
    period_row = (await session.execute(stmt_period)).one()
    pr = period_row._mapping
    orders_by_period = [
        {
            "label": "Останні 7 дн.",
            "days": 7,
            "orders": int(pr["c7"] or 0),
            "revenue": float(pr["r7"] or 0),
            "distinct_buyers": int(pr["db7"] or 0),
        },
        {
            "label": "Останні 14 дн.",
            "days": 14,
            "orders": int(pr["c14"] or 0),
            "revenue": float(pr["r14"] or 0),
            "distinct_buyers": int(pr["db14"] or 0),
        },
        {
            "label": "Останні 30 дн.",
            "days": 30,
            "orders": int(pr["c30"] or 0),
            "revenue": float(pr["r30"] or 0),
            "distinct_buyers": int(pr["db30"] or 0),
        },
        {
            "label": "Останні 90 дн.",
            "days": 90,
            "orders": int(pr["c90"] or 0),
            "revenue": float(pr["r90"] or 0),
            "distinct_buyers": int(pr["db90"] or 0),
        },
        {
            "label": "Останні 180 дн.",
            "days": 180,
            "orders": int(pr["c180"] or 0),
            "revenue": float(pr["r180"] or 0),
            "distinct_buyers": int(pr["db180"] or 0),
        },
        {
            "label": "Останні 365 дн.",
            "days": 365,
            "orders": int(pr["c365"] or 0),
            "revenue": float(pr["r365"] or 0),
            "distinct_buyers": int(pr["db365"] or 0),
        },
    ]
    for p in orders_by_period:
        o = int(p["orders"] or 0)
        r = float(p["revenue"] or 0.0)
        p["avg_check_uah"] = (r / o) if o else 0.0

    _p30 = next((x for x in orders_by_period if x["days"] == 30), None)
    _p90 = next((x for x in orders_by_period if x["days"] == 90), None)
    orders_30 = int(_p30["orders"] or 0) if _p30 else 0
    orders_90 = int(_p90["orders"] or 0) if _p90 else 0
    distinct_buyers_90 = int(_p90["distinct_buyers"] or 0) if _p90 else 0

    stmt_city = (
        select(Client.geo_city, func.count(func.distinct(Client.id)))
        .select_from(Client)
        .join(ClientOrder, ClientOrder.client_customer_id == Client.customer_id)
        .where(monthly_date_bounds)
        .group_by(Client.geo_city)
    )
    if allow_c is not None:
        stmt_city = stmt_city.where(allow_c)
    if deny_o is not None:
        stmt_city = stmt_city.where(deny_o)
    city_raw = (await session.execute(stmt_city)).all()
    stmt_city_den = (
        select(func.count(func.distinct(Client.id)))
        .select_from(Client)
        .join(ClientOrder, ClientOrder.client_customer_id == Client.customer_id)
        .where(monthly_date_bounds)
    )
    if allow_c is not None:
        stmt_city_den = stmt_city_den.where(allow_c)
    if deny_o is not None:
        stmt_city_den = stmt_city_den.where(deny_o)
    n_city_clients_period = int((await session.execute(stmt_city_den)).scalar() or 0)
    top_cities = _merge_top_cities_rows(city_raw, max(n_city_clients_period, 1), limit=12)

    _src_cnt = func.count(func.distinct(Client.id))
    stmt_src = (
        select(Client.source_raw, _src_cnt)
        .select_from(Client)
        .join(ClientOrder, ClientOrder.client_customer_id == Client.customer_id)
        .where(monthly_date_bounds)
        .group_by(Client.source_raw)
        .having(_src_cnt >= CLIENTS_DASH_TOP_SOURCE_MIN_CLIENTS)
        .order_by(desc(_src_cnt))
        .limit(12)
    )
    if allow_c is not None:
        stmt_src = stmt_src.where(allow_c)
    if deny_o is not None:
        stmt_src = stmt_src.where(deny_o)
    src_raw = (await session.execute(stmt_src)).all()
    _src_den = max(n_city_clients_period, 1)
    top_sources = []
    for src, cnt in src_raw:
        label = (src or "").strip() or "Порожньо"
        c = int(cnt)
        top_sources.append({"name": label, "count": c, "pct": _dash_pct(c, _src_den)})

    stmt_mgr = (
        select(ClientOrder.manager_username, func.count(ClientOrder.id))
        .select_from(ClientOrder)
        .where(monthly_date_bounds)
        .group_by(ClientOrder.manager_username)
        .order_by(desc(func.count(ClientOrder.id)))
        .limit(12)
    )
    if deny_o is not None:
        stmt_mgr = stmt_mgr.where(deny_o)
    mgr_raw = (await session.execute(stmt_mgr)).all()
    mgr_den = max(sum(int(cnt or 0) for _, cnt in mgr_raw), 1)
    top_order_managers = []
    for mgr_name, cnt in mgr_raw:
        label = (str(mgr_name or "").strip()) or "Не вказано"
        c = int(cnt or 0)
        top_order_managers.append({"name": label, "count": c, "pct": _dash_pct(c, mgr_den)})

    stmt_lines = (
        select(func.coalesce(func.sum(ClientOrderLine.quantity), 0))
        .select_from(ClientOrderLine)
        .join(ClientOrder, ClientOrderLine.order_row_id == ClientOrder.id)
        .where(monthly_date_bounds)
    )
    if deny_o is not None:
        stmt_lines = stmt_lines.where(deny_o)
    line_qty = int((await session.execute(stmt_lines)).scalar() or 0)

    return {
        "generated_at": now.strftime("%d.%m.%Y %H:%M"),
        "totals": {
            "clients": total,
            "registered": n_reg,
            "guests": n_guest,
            "pct_registered": _dash_pct(n_reg, total),
            "pct_guests": _dash_pct(n_guest, total),
            "with_email": n_email,
            "with_phone": n_phone,
            "pct_email": _dash_pct(n_email, total),
            "pct_phone": _dash_pct(n_phone, total),
            "utm_fields_on_clients": int(row.n_utm_client or 0),
        },
        "orders": {
            "last_30d": orders_30,
            "last_90d": orders_90,
            "distinct_buyers_90d": distinct_buyers_90,
            "orders_with_utm": orders_with_utm,
            "status_counts": status_counts,
            "status_summary": status_summary,
        },
        "dates": {
            "feed_generated_max": row.feed_max.strftime("%d.%m.%Y %H:%M") if row.feed_max else None,
            "first_order_min": row.min_first_order.strftime("%d.%m.%Y") if row.min_first_order else None,
            "last_order_max_client": row.max_last_order.strftime("%d.%m.%Y") if row.max_last_order else None,
            "order_date_min": orow.omin.strftime("%d.%m.%Y") if orow.omin else None,
            "order_date_max": orow.omax.strftime("%d.%m.%Y %H:%M") if orow.omax else None,
        },
        "segments": segments,
        "activity": activity,
        "monthly": monthly,
        "monthly_chart_meta": monthly_chart_meta,
        "monthly_chart_filter": {
            "outcome": monthly_outcome,
            "is_active": monthly_outcome != "all",
            "label_ua": _monthly_outcome_label_ua.get(monthly_outcome, _monthly_outcome_label_ua["all"]),
        },
        "orders_by_period": orders_by_period,
        "top_cities": top_cities,
        "top_sources": top_sources,
        "top_order_managers": top_order_managers,
        "top_order_statuses": top_order_statuses,
        "lines": {
            "quantity_sum": line_qty,
        },
    }
