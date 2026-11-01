"""Інсайти SEO — лише затверджений набір правил."""

from __future__ import annotations

from collections import defaultdict

from web.ad_analytics import _pct_delta
from web.insights.types import InsightContext, make_insight
from web.insights.utils import (
    contribution_share,
    fmt_change_line,
    fmt_delta,
    min_volume_ok,
    significant_change,
)


def _page_category(url: str) -> str:
    u = (url or "").lower()
    if "/category" in u or "/katalog" in u:
        return "Категорії"
    if "/product" in u or "product_id" in u:
        return "Товари"
    if u in ("/", ""):
        return "Головна"
    return "Інше"


def apply_seo_rules(ctx: InsightContext) -> None:
    if ctx.gsc.get("error"):
        return

    t = ctx.threshold
    rel = float(t("relative_change_pct"))
    min_ord = int(t("min_orders"))
    pos_stable = float(t("position_stable_max_change"))
    idx_drop = float(t("indexing_impressions_drop_pct"))

    seo_row = ctx.seo_row
    seo_rev = float(seo_row.get("revenue") or 0)
    seo_prev_rev = float((ctx.prev.get("by_channel") or {}).get("seo", {}).get("revenue", 0))
    seo_ord = int(seo_row.get("orders") or 0)
    seo_prev_ord = int((ctx.prev.get("by_channel") or {}).get("seo", {}).get("orders", 0))

    ga4_org = ctx.ga4_organic
    ga4_prev = ctx.ga4_organic_prev
    ga4_rev = float(ga4_org.get("revenue") or 0)
    ga4_prev_rev = float(ga4_prev.get("revenue") or 0)
    organic_rev = seo_rev or ga4_rev
    organic_prev_rev = seo_prev_rev or ga4_prev_rev

    clicks = int(ctx.gsc.get("clicks") or 0)
    prev_clicks = int(ctx.gsc_prev.get("clicks") or 0)
    clicks_delta = _pct_delta(clicks, prev_clicks)

    impressions = int(ctx.gsc.get("impressions") or 0)
    prev_impressions = int(ctx.gsc_prev.get("impressions") or 0)
    impr_delta = _pct_delta(impressions, prev_impressions)

    position = float(ctx.gsc.get("position") or 0)
    prev_position = float(ctx.gsc_prev.get("position") or 0)
    position_delta = position - prev_position if position and prev_position else None

    ctr = float(ctx.gsc.get("ctr") or 0)
    prev_ctr = float(ctx.gsc_prev.get("ctr") or 0)
    ctr_delta = _pct_delta(ctr, prev_ctr)

    sessions = int(ga4_org.get("sessions") or 0) or clicks
    prev_sessions = int(ga4_prev.get("sessions") or 0) or prev_clicks
    sessions_delta = _pct_delta(sessions, prev_sessions)

    rev_delta = _pct_delta(organic_rev, organic_prev_rev)
    ord_delta = _pct_delta(seo_ord, seo_prev_ord)
    clicks_total_delta = clicks - prev_clicks
    traffic_stable = (
        (clicks_delta is not None and abs(clicks_delta) < rel)
        or (sessions_delta is not None and abs(sessions_delta) < rel)
    )

    # SEO01 — падіння доходу зі зміною трафіку
    if (
        significant_change(rev_delta, rel)
        and rev_delta is not None
        and rev_delta <= -rel
        and organic_rev > 0
        and not traffic_stable
        and min_volume_ok(conversions=seo_ord, prev_conversions=seo_prev_ord, min_conversions=min_ord)
    ):
        ctx.add(make_insight(
            id="SEO01",
            category="seo",
            title="Органічний дохід знизився",
            what_happened=f"Дохід Organic Search знизився на {abs(rev_delta)}%: {fmt_change_line(organic_rev, organic_prev_rev)}.",
            action="розкласти на sessions × CR × AOV; перевірити landing pages і категорії",
            trend="down",
            impact_score=90,
            signal_type="risk",
        ))

    # SEO02
    if (
        significant_change(ord_delta, rel)
        and ord_delta is not None
        and ord_delta <= -rel
        and min_volume_ok(conversions=seo_ord, prev_conversions=seo_prev_ord, min_conversions=min_ord)
    ):
        ctx.add(make_insight(
            id="SEO02",
            category="seo",
            title="Органічні конверсії знизилися",
            what_happened=f"Кількість органічних покупок знизилася на {abs(ord_delta)}%: {seo_prev_ord} → {seo_ord}.",
            action="перевірити CR на landing pages, наявність і ціни",
            trend="down",
            impact_score=88,
            signal_type="risk",
        ))

    # SEO03
    if significant_change(clicks_delta, rel) and clicks_delta is not None and clicks_delta <= -rel:
        ctx.add(make_insight(
            id="SEO03",
            category="seo",
            title="Кліки GSC знизилися",
            what_happened=f"Органічні кліки знизилися на {abs(clicks_delta)}%: {prev_clicks:,} → {clicks:,}.".replace(",", " "),
            action="розкласти на impressions × CTR; перевірити позиції та сторінки",
            trend="down",
            impact_score=84,
            signal_type="risk",
        ))

    # SEO04
    if significant_change(impr_delta, rel) and impr_delta is not None and impr_delta <= -rel:
        ctx.add(make_insight(
            id="SEO04",
            category="seo",
            title="Покази знизилися",
            what_happened=f"Видимість у пошуку знизилася на {abs(impr_delta)}%: {prev_impressions:,} → {impressions:,}.".replace(",", " "),
            action="перевірити індексацію, позиції та втрату сторінок",
            trend="down",
            impact_score=82,
            signal_type="risk",
        ))

    # SEO05
    if position_delta is not None and position_delta >= 0.8 and prev_position > 0:
        ctx.add(make_insight(
            id="SEO05",
            category="seo",
            title="Позиція погіршилася",
            what_happened=f"Середня зважена позиція змінилася з {prev_position:.1f} до {position:.1f}.",
            action="перевірити конкуренцію, контент і технічні зміни",
            trend="down",
            impact_score=80,
            signal_type="risk",
        ))

    # SEO06
    if (
        significant_change(ctr_delta, rel)
        and ctr_delta is not None
        and ctr_delta <= -rel
        and position_delta is not None
        and abs(position_delta) <= pos_stable
        and impressions >= int(t("min_clicks_ctr"))
    ):
        ctx.add(make_insight(
            id="SEO06",
            category="seo",
            title="CTR знизився за стабільної позиції",
            what_happened=f"Позиції стабільні, але CTR знизився з {prev_ctr:.2f}% до {ctr:.2f}%.",
            action="перевірити titles, descriptions і SERP-фічі",
            trend="down",
            impact_score=76,
            signal_type="risk",
        ))

    # SEO10 — трафік стабільний, дохід падає (пріоритетніший за SEO01)
    if (
        organic_rev > 0
        and organic_prev_rev > 0
        and significant_change(rev_delta, rel)
        and rev_delta is not None
        and rev_delta <= -rel
        and traffic_stable
        and min_volume_ok(conversions=seo_ord, prev_conversions=seo_prev_ord, min_conversions=min_ord)
    ):
        ctx.add(make_insight(
            id="SEO10",
            category="seo",
            title="Трафік стабільний, дохід падає",
            what_happened="Кліки або сесії стабільні, але органічний дохід знизився.",
            why=["ймовірна проблема після переходу: CR або середній чек"],
            action="перевірити landing pages, ціни, наявність і UX",
            trend="down",
            impact_score=85,
            signal_type="risk",
        ))

    # SEO15 — категорія втратила видимість
    if clicks_total_delta < 0:
        pages = ctx.gsc.get("pages") or []
        prev_pages = {p.get("key"): p for p in (ctx.gsc_prev.get("pages") or [])}
        cat_delta: dict[str, float] = defaultdict(float)
        for page in pages:
            key = page.get("key") or ""
            cur_cl = float(page.get("clicks") or 0)
            prev_cl = float((prev_pages.get(key) or {}).get("clicks") or 0)
            cat_delta[_page_category(key)] += cur_cl - prev_cl
        worst_cat = min(cat_delta.items(), key=lambda x: x[1], default=None)
        if worst_cat:
            share = contribution_share(worst_cat[1], float(clicks_total_delta))
            if share is not None and share <= -30:
                ctx.add(make_insight(
                    id="SEO15",
                    category="seo",
                    title="Категорія втратила видимість",
                    what_happened=f"Сегмент «{worst_cat[0]}» сформував {abs(share):.0f}% падіння SEO кліків.",
                    action="перевірити сторінки категорії, індексацію та позиції",
                    trend="down",
                    impact_score=78,
                    signal_type="risk",
                ))

    # SEO16 — landing page просіла
    pages = ctx.gsc.get("pages") or []
    prev_pages_map = {p.get("key"): p for p in (ctx.gsc_prev.get("pages") or [])}
    page_candidates = []
    for page in pages:
        key = page.get("key") or "—"
        cur_cl = int(page.get("clicks") or 0)
        prev_cl = int((prev_pages_map.get(key) or {}).get("clicks") or 0)
        if prev_cl < 20:
            continue
        cl_d = _pct_delta(cur_cl, prev_cl)
        if cl_d is not None and cl_d <= -20:
            page_candidates.append((key, cl_d, cur_cl, prev_cl))
    if page_candidates:
        key, cl_d, cur_cl, prev_cl = max(page_candidates, key=lambda x: abs(x[1]))
        ctx.add(make_insight(
            id="SEO16",
            category="seo",
            title="Landing page просіла",
            what_happened=f"Сторінка втратила {abs(cl_d)}% кліків: {prev_cl} → {cur_cl} ({key[:60]}).",
            action="перевірити контент, технічний стан і позиції сторінки",
            trend="down",
            impact_score=77,
            signal_type="risk",
        ))

    # SEO22 — зміна URL у видачі
    query_pages_cur: dict[str, str] = {}
    for q in ctx.gsc.get("queries") or []:
        if q.get("page"):
            query_pages_cur[q.get("key") or ""] = q.get("page") or ""
    query_pages_prev: dict[str, str] = {}
    for q in ctx.gsc_prev.get("queries") or []:
        if q.get("page"):
            query_pages_prev[q.get("key") or ""] = q.get("page") or ""
    url_changes = []
    for query, cur_url in query_pages_cur.items():
        prev_url = query_pages_prev.get(query)
        if prev_url and cur_url and prev_url != cur_url:
            impr = next((float(q.get("impressions") or 0) for q in (ctx.gsc.get("queries") or []) if q.get("key") == query), 0)
            if impr >= 50:
                url_changes.append((query, prev_url, cur_url, impr))
    if url_changes:
        query, prev_url, cur_url, _ = max(url_changes, key=lambda x: x[3])
        ctx.add(make_insight(
            id="SEO22",
            category="seo",
            title="Зміна URL у видачі",
            what_happened=f"Для запиту «{query[:50]}» Google показує іншу сторінку.",
            why=[f"було: {prev_url[:50]}", f"стало: {cur_url[:50]}"],
            action="перевірити канонікали, редиректи та релевантність сторінок",
            trend="warn",
            impact_score=74,
            signal_type="anomaly",
        ))

    # SEO28 — проблема індексації
    prev_pages_list = ctx.gsc_prev.get("pages") or []
    if prev_pages_list and pages:
        prev_impr_by_page = {p.get("key"): float(p.get("impressions") or 0) for p in prev_pages_list}
        dropped = 0
        for key, prev_impr in prev_impr_by_page.items():
            if prev_impr < 30:
                continue
            cur_page = next((p for p in pages if p.get("key") == key), None)
            cur_impr = float((cur_page or {}).get("impressions") or 0)
            if cur_impr <= prev_impr * (1 - idx_drop / 100):
                dropped += 1
        if dropped >= 3:
            ctx.add(make_insight(
                id="SEO28",
                category="seo",
                title="Проблема індексації",
                what_happened=f"Різке падіння показів у {dropped} сторінок одночасно.",
                action="перевірити індексацію, robots, sitemap і технічні зміни",
                trend="warn",
                impact_score=86,
                signal_type="anomaly",
            ))
