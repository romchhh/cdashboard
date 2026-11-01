"""Інсайти Meta Ads (META)."""

from __future__ import annotations

from web.ad_analytics import _pct_delta, _safe_roas
from web.insights.types import InsightContext, make_insight
from web.insights.utils import (
    contribution_share,
    fmt_currency_compact,
    fmt_delta,
    fmt_roas,
    fmt_uah_compact,
    significant_change,
)


def _classify_campaign(name: str, objective: str = "") -> str:
    blob = f"{name} {objective}".lower()
    if "remarket" in blob or "ретаргет" in blob or "warm" in blob:
        return "Remarketing"
    if "prospect" in blob or "cold" in blob or "new" in blob:
        return "Prospecting"
    return "Інше"


def apply_meta_rules(ctx: InsightContext) -> None:
    if ctx.meta.get("error") or ctx.m_spend <= 0:
        return

    t = ctx.threshold
    rel = float(t("relative_change_pct"))
    min_spend = float(t("min_spend_uah"))
    target_roas = float(t("target_roas"))
    driver_pct = float(t("contribution_driver_pct"))

    m_row = ctx.meta_row
    m_rev = float(m_row.get("revenue") or 0)
    m_prev_rev = float((ctx.prev.get("by_channel") or {}).get("meta_ads", {}).get("revenue", 0))
    m_orders = int(m_row.get("orders") or 0)
    m_prev_orders = int((ctx.prev.get("by_channel") or {}).get("meta_ads", {}).get("orders", 0))

    purchase_value = float(ctx.meta.get("purchase_value") or 0)
    prev_purchase_value = float(ctx.meta_prev.get("purchase_value") or 0)
    value_delta = _pct_delta(m_rev, m_prev_rev)

    m_roas = _safe_roas(m_rev, ctx.m_spend)
    m_prev_roas = _safe_roas(m_prev_rev, ctx.m_prev_spend)
    roas_delta = _pct_delta(m_roas or 0, m_prev_roas or 0)

    spend_delta = _pct_delta(ctx.m_spend, ctx.m_prev_spend)
    cpm = float(ctx.meta.get("cpm") or 0)
    prev_cpm = float(ctx.meta_prev.get("cpm") or 0)
    cpm_delta = _pct_delta(cpm, prev_cpm)

    ctr = float(ctx.meta.get("ctr") or 0)
    prev_ctr = float(ctx.meta_prev.get("ctr") or 0)
    ctr_delta = _pct_delta(ctr, prev_ctr)

    cpc = float(ctx.meta.get("cpc") or 0)
    prev_cpc = float(ctx.meta_prev.get("cpc") or 0)
    cpc_delta = _pct_delta(cpc, prev_cpc)

    purchases = int(ctx.meta.get("purchases") or 0)
    prev_purchases = int(ctx.meta_prev.get("purchases") or 0)
    cpa = float(ctx.meta.get("cost_per_purchase") or 0)
    prev_cpa = float(ctx.meta_prev.get("cost_per_purchase") or 0)
    cpa_delta = _pct_delta(cpa, prev_cpa)

    freq = float(ctx.meta.get("frequency") or 0)
    prev_freq = float(ctx.meta_prev.get("frequency") or 0)
    freq_delta = _pct_delta(freq, prev_freq)

    channel_rev_delta = m_rev - m_prev_rev

    # META01
    if value_delta is not None and value_delta <= -rel and ctx.m_spend >= min_spend:
        ctx.add(make_insight(
            id="META01",
            category="meta",
            title="Meta: дохід знизився",
            what_happened=f"Дохід Meta (CRM) знизився на {abs(value_delta)}%: {fmt_uah_compact(m_prev_rev)} → {fmt_uah_compact(m_rev)}.",
            action="перевірити CPM → CTR → CPC → CR по кампаніях і креативах",
            trend="down",
            impact_score=88,
            signal_type="risk",
        ))

    # META02
    if roas_delta is not None and roas_delta <= -rel and m_roas is not None:
        ctx.add(make_insight(
            id="META02",
            category="meta",
            title="Meta: ROAS знизився",
            what_happened=f"ROAS знизився з {fmt_roas(m_prev_roas)} до {fmt_roas(m_roas)}.",
            why=["перевірити CPM, CTR, CPC, LPV rate і site CR"],
            action="декомпозувати падіння по аудиторіях і креативах",
            trend="down",
            impact_score=86,
            signal_type="risk",
        ))

    # META03
    if significant_change(cpa_delta, rel) and cpa_delta is not None and cpa_delta >= rel and purchases >= int(t("min_orders")):
        ctx.add(make_insight(
            id="META03",
            category="meta",
            title="CPA покупки зріс у Meta",
            what_happened=(
                f"CPA покупки зріс на {cpa_delta}%: "
                f"{fmt_currency_compact(prev_cpa, ctx.meta.get('currency'))} → "
                f"{fmt_currency_compact(cpa, ctx.meta.get('currency'))}."
            ),
            action="відокремити аукціонний фактор CPM від креативного CTR",
            trend="down",
            impact_score=84,
            signal_type="risk",
        ))

    # META04
    if spend_delta is not None and spend_delta >= 20 and value_delta is not None and value_delta < 10:
        ctx.add(make_insight(
            id="META04",
            category="meta",
            title="Meta: витрати ростуть без доходу",
            what_happened=f"Витрати {fmt_delta(spend_delta)}, дохід {fmt_delta(value_delta)}.",
            action="перевірити аудиторії та креативи з найбільшим ростом витрат",
            trend="warn",
            impact_score=83,
            signal_type="risk",
        ))

    # META05
    if significant_change(cpm_delta, rel) and cpm_delta is not None and cpm_delta >= rel:
        ctx.add(make_insight(
            id="META05",
            category="meta",
            title="CPM зріс у Meta",
            what_happened=f"CPM зріс на {cpm_delta}%, через що подорожчало залучення трафіку.",
            action="перевірити аукціон, сезонність і конкуренцію",
            trend="down",
            impact_score=76,
            signal_type="risk",
        ))

    # META06
    if significant_change(ctr_delta, rel) and ctr_delta is not None and ctr_delta <= -rel:
        ctx.add(make_insight(
            id="META06",
            category="meta",
            title="Link CTR знизився у Meta",
            what_happened=f"CTR знизився з {prev_ctr:.2f}% до {ctr:.2f}%.",
            action="перевірити креативи, офери та релевантність аудиторій",
            trend="down",
            impact_score=74,
            signal_type="risk",
        ))

    # META07 / META08 — CPC drivers
    if significant_change(cpc_delta, rel) and cpc_delta is not None and cpc_delta >= rel:
        cpm_stable = cpm_delta is None or abs(cpm_delta) < rel
        ctr_down = ctr_delta is not None and ctr_delta <= -rel
        cpm_up = cpm_delta is not None and cpm_delta >= rel
        if cpm_stable and ctr_down:
            ctx.add(make_insight(
                id="META07",
                category="meta",
                title="CPC зріс через креатив",
                what_happened="CPC зріс переважно через зниження CTR; CPM стабільний.",
                action="перевірити креативи, перші секунди відео та офер",
                trend="down",
                impact_score=75,
                signal_type="risk",
            ))
        elif cpm_up and (ctr_delta is None or abs(ctr_delta) < rel):
            ctx.add(make_insight(
                id="META08",
                category="meta",
                title="CPC зріс через аукціон",
                what_happened="Трафік подорожчав через CPM, а не через реакцію на креатив.",
                action="перевірити аудиторії, гео та сезонність аукціону",
                trend="down",
                impact_score=74,
                signal_type="risk",
            ))

    # META11 — втома креативу
    if (
        freq >= float(t("meta_frequency_warn"))
        and ctr_delta is not None
        and ctr_delta <= -rel
        and cpa_delta is not None
        and cpa_delta >= rel
    ):
        ctx.add(make_insight(
            id="META11",
            category="meta",
            title="Втома креативу / аудиторії",
            what_happened=f"Частота {freq:.1f}; CTR {fmt_delta(ctr_delta)}, CPA {fmt_delta(cpa_delta)}.",
            action="оновити креативи або розширити аудиторію",
            trend="warn",
            impact_score=80,
            signal_type="risk",
        ))

    by_seg: dict[str, float] = {}
    for camp in ctx.meta.get("campaigns") or []:
        seg = _classify_campaign(camp.get("name") or "", camp.get("objective") or "")
        by_seg[seg] = by_seg.get(seg, 0) + float(camp.get("purchase_value") or camp.get("revenue") or 0)

    # META13 — Prospecting просів
    if channel_rev_delta < 0:
        prev_by_seg: dict[str, float] = {}
        for camp in ctx.meta_prev.get("campaigns") or []:
            seg = _classify_campaign(camp.get("name") or "", camp.get("objective") or "")
            prev_by_seg[seg] = prev_by_seg.get(seg, 0) + float(camp.get("purchase_value") or camp.get("revenue") or 0)
        for seg, cur_v in by_seg.items():
            delta = cur_v - prev_by_seg.get(seg, 0)
            share = contribution_share(delta, channel_rev_delta)
            if share is not None and share <= -driver_pct:
                ctx.add(make_insight(
                    id=f"META13_{seg}",
                    category="meta",
                    title=f"Просідання {seg} у Meta",
                    what_happened=f"Основна просадка Meta ({abs(share):.0f}%) припала на {seg}.",
                    action="перевірити аудиторії, креативи та плейсменти сегмента",
                    trend="down",
                    impact_score=81,
                    signal_type="risk",
                ))
                break

    # META15 — залежність від Remarketing
    total_seg_rev = sum(by_seg.values())
    remarketing_rev = by_seg.get("Remarketing", 0)
    remarketing_share = (100 * remarketing_rev / total_seg_rev) if total_seg_rev > 0 else 0
    if total_seg_rev > 0 and remarketing_share >= 65:
        ctx.add(make_insight(
            id="META15",
            category="meta",
            title="Залежність від Remarketing",
            what_happened=f"{remarketing_share:.0f}% доходу Meta (за даними кампаній) формує ремаркетинг.",
            action="перевірити баланс prospecting і remarketing",
            trend="warn",
            impact_score=72,
            signal_type="risk",
        ))

    # META19 — креатив без покупок (campaigns as proxy)
    bad_camps = [
        c for c in (ctx.meta.get("campaigns") or [])
        if float(c.get("spend") or 0) >= min_spend * 0.3 and int(c.get("purchases") or 0) == 0
    ]
    if bad_camps:
        waste = sum(float(c.get("spend") or 0) for c in bad_camps[:5])
        ctx.add(make_insight(
            id="META19",
            category="meta",
            title="Кампанія витрачає без покупок",
            what_happened=f"{len(bad_camps)} кампаній витратили {fmt_uah_compact(waste)} без покупок.",
            action="перевірити креативи, аудиторії та цілі кампаній",
            trend="warn",
            impact_score=76,
            signal_type="risk",
        ))

    # META23 / META24 — placements
    placements = ctx.meta.get("placements") or []
    if placements and ctx.m_spend >= min_spend:
        total_pl_spend = sum(float(p.get("spend") or 0) for p in placements) or ctx.m_spend
        worst = max(placements, key=lambda p: float(p.get("spend") or 0))
        w_spend = float(worst.get("spend") or 0)
        w_rev = float(worst.get("purchase_value") or worst.get("revenue") or 0)
        w_roas = _safe_roas(w_rev, w_spend)
        w_share = 100 * w_spend / total_pl_spend
        if w_share >= 10 and w_roas is not None and w_roas < target_roas * float(t("roas_below_target_multiplier")):
            ctx.add(make_insight(
                id="META23",
                category="meta",
                title="Placement з низькою ефективністю",
                what_happened=f"{worst.get('name', '—')} формує {w_share:.0f}% витрат з ROAS {fmt_roas(w_roas)}.",
                action="перевірити ефективність плейсментів і виключення",
                trend="warn",
                impact_score=70,
                signal_type="risk",
            ))

    # META28 — розбіжність Meta vs CRM
    if purchase_value > 0 and m_rev > 0:
        gap_pct = abs(100 * (purchase_value - m_rev) / max(m_rev, 1))
        prev_gap = abs(100 * (prev_purchase_value - m_prev_rev) / max(m_prev_rev, 1)) if m_prev_rev else 0
        if gap_pct >= 25 and gap_pct > prev_gap + 10:
            ctx.add(make_insight(
                id="META28",
                category="meta",
                title="Розбіжність Meta і CRM",
                what_happened="Розбіжність між платформою Meta і CRM суттєво зросла.",
                action="перевірити атрибуцію, UTM і вікно конверсій",
                trend="warn",
                impact_score=84,
                signal_type="anomaly",
            ))

    # META35
    if spend_delta is not None and spend_delta >= 20 and (roas_delta is None or roas_delta >= 0):
        purch_delta = _pct_delta(purchases, prev_purchases)
        if purch_delta is not None and purch_delta >= 10:
            ctx.add(make_insight(
                id="META35",
                category="meta",
                title="Ефективне масштабування Meta",
                what_happened=f"Витрати {fmt_delta(spend_delta)}, покупки {fmt_delta(purch_delta)}, ROAS стабільний або кращий.",
                action="перевірити, які сегменти дають найкращий приріст",
                trend="up",
                impact_score=68,
                signal_type="positive",
            ))
