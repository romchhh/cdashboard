"""Загальні маркетингові інсайти (GEN)."""

from __future__ import annotations

from web.ad_analytics import _pct_delta, _safe_cpa, _safe_roas
from web.ad_channel import CHANNEL_GOOGLE_ADS, CHANNEL_META_ADS
from web.insights.types import InsightContext, make_insight
from web.insights.utils import (
    contribution_share,
    fmt_change_line,
    fmt_delta,
    fmt_roas,
    fmt_uah_compact,
    min_volume_ok,
    significant_change,
)


def apply_general_rules(ctx: InsightContext) -> None:
    t = ctx.threshold
    rel = float(t("relative_change_pct"))
    min_ord = int(t("min_orders"))
    min_spend = float(t("min_spend_uah"))
    target_roas = float(t("target_roas"))
    driver_pct = float(t("contribution_driver_pct"))
    gap_pp = float(t("efficiency_gap_pp"))

    rev = ctx.revenue
    prev_rev = ctx.prev_revenue
    rev_delta = _pct_delta(rev, prev_rev)
    ord_count = ctx.orders_count
    prev_ord = ctx.prev_orders_count
    orders_delta = _pct_delta(ord_count, prev_ord)

    paid_spend = ctx.paid_spend
    prev_paid_spend = ctx.prev_paid_spend
    spend_delta = _pct_delta(paid_spend, prev_paid_spend)

    roas = _safe_roas(rev, paid_spend)
    prev_roas = _safe_roas(prev_rev, prev_paid_spend)
    roas_delta = _pct_delta(roas or 0, prev_roas or 0)

    cpa = _safe_cpa(paid_spend, ord_count)
    prev_cpa = _safe_cpa(prev_paid_spend, prev_ord)
    cpa_delta = _pct_delta(cpa or 0, prev_cpa or 0)

    aov = rev / ord_count if ord_count else None
    prev_aov = prev_rev / prev_ord if prev_ord else None
    aov_delta = _pct_delta(aov or 0, prev_aov or 0)

    total_rev_delta = rev - prev_rev

    # GEN01 / GEN02
    if rev > 0 and significant_change(rev_delta, rel):
        if min_volume_ok(conversions=ord_count, prev_conversions=prev_ord, min_conversions=min_ord):
            if rev_delta is not None and rev_delta <= -rel:
                why = []
                if orders_delta is not None and orders_delta <= -rel:
                    why.append(f"конверсії {fmt_delta(orders_delta)}")
                if aov_delta is not None and aov_delta <= -rel:
                    why.append(f"середній чек {fmt_delta(aov_delta)}")
                ctx.add(make_insight(
                    id="GEN01",
                    category="general",
                    title="Дохід знизився",
                    what_happened=f"Дохід знизився на {abs(rev_delta)}%: {fmt_change_line(rev, prev_rev)}.",
                    why=why or None,
                    impact=f"Абсолютна зміна: {fmt_uah_compact(total_rev_delta)}.",
                    trend="down",
                    impact_score=95,
                    signal_type="risk",
                ))
            elif rev_delta is not None and rev_delta >= rel:
                why = []
                if spend_delta is not None:
                    why.append(f"витрати {fmt_delta(spend_delta)}")
                ctx.add(make_insight(
                    id="GEN02",
                    category="general",
                    title="Дохід зріс",
                    what_happened=f"Дохід зріс на {rev_delta}%: {fmt_change_line(rev, prev_rev)} {ctx.compare_label}.",
                    why=why,
                    trend="up",
                    impact_score=88,
                    signal_type="positive",
                ))

    # GEN03 / GEN04
    if roas is not None and paid_spend >= min_spend:
        roas_below = roas < target_roas * float(t("roas_below_target_multiplier"))
        roas_declining = roas_delta is not None and roas_delta <= -5
        if roas_below and (roas_declining or roas < target_roas * 0.85):
            ctx.add(make_insight(
                id="GEN03",
                category="general",
                title="ROAS нижче цілі",
                what_happened=f"ROAS {fmt_roas(roas)} проти цілі {fmt_roas(target_roas)} ({fmt_delta(roas_delta)} {ctx.compare_label}).".strip(),
                action="перевірити ефективність каналів і кампаній з найбільшими витратами",
                trend="warn",
                impact_score=82,
                signal_type="risk",
            ))
        elif roas >= target_roas * float(t("roas_above_target_multiplier")) and (roas_delta is None or roas_delta >= 0):
            ctx.add(make_insight(
                id="GEN04",
                category="general",
                title="ROAS вище цілі",
                what_happened=f"ROAS {fmt_roas(roas)} — на {round(100 * (roas / target_roas - 1))}% вище цільового рівня.",
                action="оцінити потенціал масштабування без втрати ефективності",
                trend="up",
                impact_score=70,
                signal_type="positive",
            ))

    # GEN05
    if (
        cpa is not None
        and prev_cpa
        and significant_change(cpa_delta, rel)
        and cpa_delta is not None
        and cpa_delta >= rel
        and min_volume_ok(conversions=ord_count, prev_conversions=prev_ord, min_conversions=min_ord)
    ):
        ctx.add(make_insight(
            id="GEN05",
            category="general",
            title="CPA зріс",
            what_happened=f"CPA зріс із {fmt_uah_compact(prev_cpa)} до {fmt_uah_compact(cpa)} ({fmt_delta(cpa_delta)}).",
            action="перевірити CPC, CR і якість трафіку в платних каналах",
            trend="down",
            impact_score=84,
            signal_type="risk",
        ))

    # GEN06
    if (
        significant_change(orders_delta, rel)
        and orders_delta is not None
        and orders_delta <= -rel
        and min_volume_ok(conversions=ord_count, prev_conversions=prev_ord, min_conversions=min_ord)
    ):
        ctx.add(make_insight(
            id="GEN06",
            category="general",
            title="Конверсії знизилися",
            what_happened=f"Кількість покупок знизилася на {abs(orders_delta)}%: {prev_ord} → {ord_count}.",
            action="перевірити трафік, CR і статуси кампаній",
            trend="down",
            impact_score=86,
            signal_type="risk",
        ))

    # GEN07
    if (
        rev_delta is not None
        and rev_delta <= -rel
        and aov_delta is not None
        and aov_delta <= -rel
        and orders_delta is not None
        and rev_delta < orders_delta
        and min_volume_ok(conversions=ord_count, prev_conversions=prev_ord, min_conversions=min_ord)
    ):
        ctx.add(make_insight(
            id="GEN07",
            category="general",
            title="Середній чек знизився",
            what_happened=f"Дохід впав сильніше за замовлення: чек {fmt_delta(aov_delta)}, дохід {fmt_delta(rev_delta)}.",
            action="перевірити зміну асортименту, знижок і середньої ціни кошика",
            trend="down",
            impact_score=78,
            signal_type="risk",
        ))

    # GEN08 / GEN09
    if spend_delta is not None and rev_delta is not None and paid_spend >= min_spend:
        gap = spend_delta - rev_delta
        if gap >= gap_pp:
            ctx.add(make_insight(
                id="GEN08",
                category="general",
                title="Витрати ростуть швидше за дохід",
                what_happened=f"Витрати {fmt_delta(spend_delta)}, дохід {fmt_delta(rev_delta)}; ефективність погіршилася.",
                action="перевірити кампанії з ростом витрат без пропорційного доходу",
                trend="warn",
                impact_score=90,
                signal_type="risk",
            ))
        elif (rev_delta - spend_delta) >= gap_pp:
            ctx.add(make_insight(
                id="GEN09",
                category="general",
                title="Дохід росте швидше за витрати",
                what_happened=f"Дохід {fmt_delta(rev_delta)} при зростанні витрат на {spend_delta}%.",
                action="перевірити, чи є простір для обережного масштабування",
                trend="up",
                impact_score=72,
                signal_type="positive",
            ))

    # GEN10 / GEN11 — драйвери зміни по каналах
    if abs(total_rev_delta) > 0 and rev > 0:
        for row in ctx.overview_rows:
            ch_rev = float(row.get("revenue") or 0)
            ch_prev = float((ctx.prev.get("by_channel") or {}).get(row.get("channel"), {}).get("revenue", 0))
            ch_delta = ch_rev - ch_prev
            share = contribution_share(ch_delta, total_rev_delta)
            if share is None or abs(share) < driver_pct:
                continue
            label = row.get("label") or row.get("channel")
            if total_rev_delta > 0 and ch_delta > 0:
                ctx.add(make_insight(
                    id=f"GEN10_{row.get('channel')}",
                    category="general",
                    title="Головний драйвер росту",
                    what_happened=f"{label} забезпечив {share}% загального приросту доходу.",
                    impact=f"Внесок: {fmt_uah_compact(ch_delta)}.",
                    trend="up",
                    impact_score=75 + min(share, 20),
                    signal_type="positive",
                ))
                ctx.suppressed_entities.add(str(row.get("channel")))
            elif total_rev_delta < 0 and ch_delta < 0:
                ctx.add(make_insight(
                    id=f"GEN11_{row.get('channel')}",
                    category="general",
                    title="Головне джерело падіння",
                    what_happened=f"{label} сформував {abs(share)}% загального зниження доходу.",
                    impact=f"Втрата: {fmt_uah_compact(abs(ch_delta))}.",
                    action="перевірити ключові кампанії та сегменти всередині каналу",
                    trend="down",
                    impact_score=88 + min(abs(share), 10),
                    signal_type="risk",
                ))
                ctx.suppressed_entities.add(str(row.get("channel")))

    # GEN12 — неефективний розподіл
    imb_ratio = float(t("budget_share_imbalance_ratio"))
    for row in ctx.overview_rows:
        ch = row.get("channel")
        if ch not in (CHANNEL_GOOGLE_ADS, CHANNEL_META_ADS):
            continue
        ch_spend = float(row.get("spend") or 0)
        ch_rev = float(row.get("revenue") or 0)
        if paid_spend <= 0 or rev <= 0 or ch_spend < min_spend:
            continue
        spend_share = 100 * ch_spend / paid_spend
        rev_share = 100 * ch_rev / rev
        if rev_share > 0 and spend_share / rev_share >= imb_ratio and spend_share >= float(t("min_share_pct")):
            ctx.add(make_insight(
                id=f"GEN12_{ch}",
                category="general",
                title="Неефективний розподіл бюджету",
                what_happened=(
                    f"{row.get('label')} отримує {spend_share:.0f}% бюджету, "
                    f"але дає {rev_share:.0f}% доходу."
                ),
                action="перевірити ефективність сегментів і перерозподіл бюджету",
                trend="warn",
                impact_score=76,
                signal_type="risk",
            ))

    # GEN13 — недофінансований ефективний сегмент
    under_pct = float(t("underfunded_spend_share_pct"))
    roas_mult = float(t("roas_above_target_multiplier"))
    for row in ctx.overview_rows:
        ch = row.get("channel")
        if ch not in (CHANNEL_GOOGLE_ADS, CHANNEL_META_ADS):
            continue
        ch_spend = float(row.get("spend") or 0)
        ch_roas = float(row.get("roas") or 0)
        if paid_spend <= 0 or ch_spend <= 0:
            continue
        spend_share = 100 * ch_spend / paid_spend
        if ch_roas >= target_roas * roas_mult and spend_share < under_pct:
            ctx.add(make_insight(
                id=f"GEN13_{ch}",
                category="general",
                title="Недофінансований ефективний сегмент",
                what_happened=(
                    f"{row.get('label')}: ROAS {fmt_roas(ch_roas)} при частці бюджету лише {spend_share:.1f}%."
                ),
                action="перевірити можливість обережного масштабування",
                trend="up",
                impact_score=68,
                signal_type="opportunity",
            ))

    # GEN19 — концентрація брендів
    if ctx.top_brands and rev > 0:
        top3_share = sum(float(b.get("revenue_share_pct") or 0) for b in ctx.top_brands[:3])
        if not top3_share:
            top3_share = round(100 * sum(float(b.get("revenue") or 0) for b in ctx.top_brands[:3]) / rev, 1)
        top1_share = float(ctx.top_brands[0].get("revenue_share_pct") or round(100 * float(ctx.top_brands[0].get("revenue") or 0) / rev, 1))
        if top3_share >= float(t("concentration_top3_share_pct")) or top1_share >= float(t("concentration_top1_share_pct")):
            names = ", ".join(f"«{b.get('brand')}»" for b in ctx.top_brands[:3])
            ctx.add(make_insight(
                id="GEN19",
                category="general",
                title="Концентрація доходу",
                what_happened=f"Топ-3 бренди формують {top3_share:.0f}% доходу ({names}).",
                action="оцінити ризик залежності від обмеженого асортименту",
                trend="warn" if top3_share >= 60 else "neutral",
                impact_score=60,
                signal_type="risk" if top3_share >= 60 else "opportunity",
            ))

    # GEN24 — декомпозиція CPA
    g_clicks = int(ctx.gads.get("clicks") or 0)
    g_prev_clicks = int(ctx.gads_prev.get("clicks") or 0)
    if (
        cpa_delta is not None
        and cpa_delta >= rel
        and min_volume_ok(conversions=ord_count, prev_conversions=prev_ord, min_conversions=min_ord)
        and g_clicks >= int(t("min_clicks_cr"))
    ):
        g_orders = int(ctx.google_row.get("orders") or 0)
        g_prev_orders = int((ctx.prev.get("by_channel") or {}).get("google_ads", {}).get("orders", 0))
        g_cpc = float(ctx.gads.get("cpc") or 0)
        g_prev_cpc = float(ctx.gads_prev.get("cpc") or 0) if ctx.gads_prev.get("cpc") else None
        g_cr = 100 * g_orders / g_clicks if g_clicks else None
        g_prev_cr = 100 * g_prev_orders / g_prev_clicks if g_prev_clicks else None
        cpc_d = _pct_delta(g_cpc, g_prev_cpc or 0) if g_prev_cpc else None
        cr_d = _pct_delta(g_cr or 0, g_prev_cr or 0) if g_prev_cr else None
        why = []
        if cpc_d is not None:
            why.append(f"CPC {fmt_delta(cpc_d)}")
        if cr_d is not None:
            why.append(f"CR {fmt_delta(cr_d)}")
        if why:
            ctx.add(make_insight(
                id="GEN24",
                category="general",
                title="Декомпозиція CPA",
                what_happened=f"CPA {fmt_delta(cpa_delta)}; {'; '.join(why)}.",
                action="перевірити аукціон, релевантність і конверсію на сайті",
                trend="down",
                impact_score=77,
                signal_type="risk",
            ))
