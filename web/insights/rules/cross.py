"""Кросканальні та товарні інсайти (CROSS)."""

from __future__ import annotations

from web.ad_analytics import _pct_delta
from web.ad_channel import CHANNEL_GOOGLE_ADS, CHANNEL_META_ADS, CHANNEL_SEO
from web.insights.types import InsightContext, make_insight
from web.insights.utils import contribution_share, fmt_delta, fmt_uah_compact, significant_change


def apply_cross_rules(ctx: InsightContext) -> None:
    t = ctx.threshold
    rel = float(t("relative_change_pct"))
    driver_pct = float(t("contribution_driver_pct"))

    rev = ctx.revenue
    prev_rev = ctx.prev_revenue
    rev_delta = _pct_delta(rev, prev_rev)

    g_row = ctx.google_row
    m_row = ctx.meta_row
    s_row = ctx.seo_row

    g_rev = float(g_row.get("revenue") or 0)
    m_rev = float(m_row.get("revenue") or 0)
    s_rev = float(s_row.get("revenue") or 0)
    g_prev = float((ctx.prev.get("by_channel") or {}).get(CHANNEL_GOOGLE_ADS, {}).get("revenue", 0))
    m_prev = float((ctx.prev.get("by_channel") or {}).get(CHANNEL_META_ADS, {}).get("revenue", 0))
    s_prev = float((ctx.prev.get("by_channel") or {}).get(CHANNEL_SEO, {}).get("revenue", 0))

    g_delta = _pct_delta(g_rev, g_prev)
    m_delta = _pct_delta(m_rev, m_prev)
    s_delta = _pct_delta(s_rev, s_prev)
    total_delta = rev - prev_rev

    paid_rev = g_rev + m_rev
    paid_prev = g_prev + m_prev
    paid_delta = _pct_delta(paid_rev, paid_prev)

    # CROSS01
    if (
        g_delta is not None and s_delta is not None
        and g_delta >= rel and s_delta <= -rel
        and rev_delta is not None
        and rev_delta <= 5
    ):
        ctx.add(make_insight(
            id="CROSS01",
            category="cross",
            title="Paid росте, Organic падає",
            what_happened="Можливе перерозподілення продажів між платним і органічним каналом.",
            action="перевірити інкрементальний внесок і brand/non-brand mix",
            trend="warn",
            impact_score=82,
            signal_type="anomaly",
        ))

    # CROSS02
    if (
        paid_delta is not None
        and paid_delta >= rel
        and rev_delta is not None
        and abs(rev_delta) <= 5
        and paid_rev > 0
    ):
        ctx.add(make_insight(
            id="CROSS02",
            category="cross",
            title="Рекламний дохід росте швидше за загальний",
            what_happened=f"Дохід paid {fmt_delta(paid_delta)}, загальний дохід {fmt_delta(rev_delta)}.",
            action="перевірити інкрементальний внесок і атрибуцію",
            trend="warn",
            impact_score=80,
            signal_type="anomaly",
        ))

    # CROSS03 — компенсація каналів
    if total_delta < 0:
        losers = []
        gainers = []
        for label, cur, prev in (
            ("Google Ads", g_rev, g_prev),
            ("Meta Ads", m_rev, m_prev),
            ("SEO", s_rev, s_prev),
        ):
            d = cur - prev
            if d < 0:
                losers.append((label, d))
            elif d > 0:
                gainers.append((label, d))
        if losers and gainers:
            main_loss = min(losers, key=lambda x: x[1])
            main_gain = max(gainers, key=lambda x: x[1])
            comp = contribution_share(main_gain[1], abs(main_loss[1]))
            if comp is not None and comp >= 50:
                ctx.add(make_insight(
                    id="CROSS03",
                    category="cross",
                    title="Один канал компенсує інший",
                    what_happened=f"{main_gain[0]} компенсував {comp:.0f}% падіння {main_loss[0]}.",
                    action="перевірити, чи компенсація стійка, а не разова",
                    trend="neutral",
                    impact_score=70,
                    signal_type="opportunity",
                ))

    # CROSS04 — загальний фактор сайту (CR падає в кількох каналах)
    channels_cr_down = 0
    for row in (g_row, m_row, s_row):
        orders = int(row.get("orders") or 0)
        prev_orders = int((ctx.prev.get("by_channel") or {}).get(row.get("channel"), {}).get("orders", 0))
        od = _pct_delta(orders, prev_orders)
        if od is not None and od <= -rel:
            channels_cr_down += 1
    if channels_cr_down >= 2:
        ctx.add(make_insight(
            id="CROSS04",
            category="cross",
            title="Загальний фактор сайту",
            what_happened="Конверсії падають одночасно в кількох каналах.",
            action="перевірити сайт, пропозицію, ціни, наявність і checkout",
            trend="warn",
            impact_score=92,
            signal_type="risk",
        ))

    # CROSS06 — бренд просів у всіх каналах
    if ctx.top_brands and len(ctx.top_brands) >= 1:
        declining_brands = [
            b for b in ctx.top_brands[:5]
            if (b.get("delta_revenue_pct") or b.get("revenue_delta_pct")) is not None
            and float(b.get("delta_revenue_pct") or b.get("revenue_delta_pct") or 0) <= -rel
        ]
        if len(declining_brands) >= 2:
            names = ", ".join(f"«{b.get('brand')}»" for b in declining_brands[:3])
            ctx.add(make_insight(
                id="CROSS06",
                category="cross",
                title="Бренди просіли в аналітиці",
                what_happened=f"Падіння доходу за брендами: {names}.",
                action="перевірити попит, наявність і рекламну підтримку брендів",
                trend="down",
                impact_score=78,
                signal_type="risk",
            ))

    # CROSS08 — категорія драйвер росту
    top_channels = ctx.business.get("top_channels") or ctx.business.get("top_categories") or []
    if total_delta > 0 and top_channels:
        top = top_channels[0]
        cat_rev = float(top.get("revenue") or 0)
        share = contribution_share(cat_rev, total_delta) if total_delta else None
        ch_name = top.get("label") or top.get("name") or "—"
        if share is not None and share >= 30:
            ctx.add(make_insight(
                id="CROSS08",
                category="cross",
                title="Канал — драйвер росту",
                what_happened=f"Канал «{ch_name}» (UTM) забезпечив {share:.0f}% загального приросту доходу.",
                action="перевірити UTM-мітки, рекламні кампанії та асортимент у цьому каналі",
                trend="up",
                impact_score=72,
                signal_type="positive",
            ))

    # CROSS09 — залежність від брендів
    if ctx.top_brands and rev > 0:
        top3 = sum(float(b.get("revenue") or 0) for b in ctx.top_brands[:3])
        share = round(100 * top3 / rev, 1)
        if share >= float(t("concentration_top3_share_pct")):
            ctx.add(make_insight(
                id="CROSS09",
                category="cross",
                title="Залежність від брендів",
                what_happened=f"Три бренди формують {share:.0f}% маркетингового доходу.",
                action="оцінити ризик концентрації та диверсифікацію асортименту",
                trend="warn",
                impact_score=65,
                signal_type="risk",
            ))

    # CROSS12 — одночасне падіння paid-каналів
    if (
        g_delta is not None
        and m_delta is not None
        and g_delta <= -rel
        and m_delta <= -rel
        and ctx.paid_spend >= float(t("min_spend_uah"))
        and g_rev + m_rev > 0
    ):
        ctx.add(make_insight(
            id="CROSS12",
            category="cross",
            title="Погіршення paid-каналів",
            what_happened=f"Дохід падає одночасно в Google Ads ({fmt_delta(g_delta)}) і Meta ({fmt_delta(m_delta)}).",
            action="перевірити залучення нових покупців і CPA по сегментах",
            trend="down",
            impact_score=79,
            signal_type="risk",
        ))

    # CROSS16 — атрибуційна аномалія (лише paid CRM vs платформи)
    g_orders = int(g_row.get("orders") or 0)
    m_orders = int(m_row.get("orders") or 0)
    paid_crm_orders = g_orders + m_orders
    platform_conv = float(ctx.gads.get("conversions") or 0) + float(ctx.meta.get("purchases") or 0)
    if platform_conv > 0 and paid_crm_orders >= int(t("min_orders")):
        gap = abs(100 * (platform_conv - paid_crm_orders) / paid_crm_orders)
        if gap >= 35:
            ctx.add(make_insight(
                id="CROSS16",
                category="cross",
                title="Атрибуційна аномалія",
                what_happened=(
                    f"Платформи: {int(platform_conv)} конверсій; CRM (Google+Meta): {paid_crm_orders} замовлень "
                    f"(розбіжність {gap:.0f}%)."
                ),
                action="перевірити вікна атрибуції, дублікати та backend-замовлення",
                trend="warn",
                impact_score=83,
                signal_type="anomaly",
            ))
