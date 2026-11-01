"""Інсайти Google Ads (GAD)."""

from __future__ import annotations

from web.ad_analytics import _pct_delta, _safe_roas
from web.insights.types import InsightContext, make_insight
from web.insights.utils import (
    contribution_share,
    fmt_delta,
    fmt_roas,
    fmt_uah_compact,
    min_volume_ok,
    significant_change,
)


def _campaign_type_label(name: str) -> str:
    n = (name or "").upper()
    if "PMAX" in n or "PERFORMANCE" in n:
        return "PMax"
    if "SHOPPING" in n:
        return "Shopping"
    if "SEARCH" in n:
        return "Search"
    return "Інше"


def apply_google_ads_rules(ctx: InsightContext) -> None:
    if ctx.gads.get("error") or ctx.g_spend <= 0:
        return

    t = ctx.threshold
    rel = float(t("relative_change_pct"))
    min_spend = float(t("min_spend_uah"))
    target_roas = float(t("target_roas"))
    driver_pct = float(t("contribution_driver_pct"))

    g_row = ctx.google_row
    g_rev = float(g_row.get("revenue") or 0)
    g_prev_rev = float((ctx.prev.get("by_channel") or {}).get("google_ads", {}).get("revenue", 0))
    g_orders = int(g_row.get("orders") or 0)
    g_prev_orders = int((ctx.prev.get("by_channel") or {}).get("google_ads", {}).get("orders", 0))

    g_value = float(ctx.gads.get("conversions_value") or 0)
    g_prev_value = float(ctx.gads_prev.get("conversions_value") or 0)
    # Для інсайтів про дохід пріоритет — CRM-атрибуція каналу.
    value_delta = _pct_delta(g_rev, g_prev_rev)
    ads_value_delta = _pct_delta(g_value, g_prev_value) if g_value > 0 and g_prev_value > 0 else None

    g_roas = _safe_roas(g_rev, ctx.g_spend)
    g_prev_roas = _safe_roas(g_prev_rev, ctx.g_prev_spend)
    roas_delta = _pct_delta(g_roas or 0, g_prev_roas or 0)

    spend_delta = _pct_delta(ctx.g_spend, ctx.g_prev_spend)
    clicks = int(ctx.gads.get("clicks") or 0)
    prev_clicks = int(ctx.gads_prev.get("clicks") or 0)
    clicks_delta = _pct_delta(clicks, prev_clicks)
    conversions = float(ctx.gads.get("conversions") or 0)
    prev_conversions = float(ctx.gads_prev.get("conversions") or 0)
    conv_delta = _pct_delta(conversions, prev_conversions)

    cpc = float(ctx.gads.get("cpc") or 0)
    prev_cpc = float(ctx.gads_prev.get("cpc") or 0)
    cpc_delta = _pct_delta(cpc, prev_cpc)

    ctr = float(ctx.gads.get("ctr") or 0)
    prev_ctr = float(ctx.gads_prev.get("ctr") or 0)
    ctr_delta = _pct_delta(ctr, prev_ctr)
    impressions = int(ctx.gads.get("impressions") or 0)

    channel_rev_delta = g_rev - g_prev_rev

    # GAD01
    if (
        value_delta is not None
        and value_delta <= -rel
        and ctx.g_spend >= min_spend
        and min_volume_ok(
            conversions=g_orders,
            prev_conversions=g_prev_orders,
            min_conversions=int(t("min_orders")),
            require_both_periods=False,
        )
    ):
        ctx.add(make_insight(
            id="GAD01",
            category="google_ads",
            title="Google Ads: дохід знизився",
            what_happened=f"Дохід Google Ads (CRM) знизився на {abs(value_delta)}%: {fmt_uah_compact(g_prev_rev)} → {fmt_uah_compact(g_rev)}.",
            action="перевірити кампанії, товари та пошукові запити з найбільшим падінням",
            trend="down",
            impact_score=88,
            signal_type="risk",
        ))

    # GAD02
    if (
        roas_delta is not None
        and roas_delta <= -rel
        and g_roas is not None
        and ctx.g_spend >= min_spend
    ):
        ctx.add(make_insight(
            id="GAD02",
            category="google_ads",
            title="Google Ads: ROAS знизився",
            what_happened=f"ROAS знизився з {fmt_roas(g_prev_roas)} до {fmt_roas(g_roas)} ({fmt_delta(roas_delta)}).",
            why=["перевірити CPC, CR, середній чек і mix кампаній"],
            action="декомпозувати падіння ROAS по типах кампаній і товарах",
            trend="down",
            impact_score=86,
            signal_type="risk",
        ))

    # GAD03 / GAD04
    if spend_delta is not None and spend_delta >= 20 and ctx.g_spend >= min_spend:
        eff_value_delta = ads_value_delta if ads_value_delta is not None else value_delta
        if eff_value_delta is not None and (eff_value_delta < 10 or (roas_delta is not None and roas_delta <= -rel)):
            ctx.add(make_insight(
                id="GAD03",
                category="google_ads",
                title="Неефективне масштабування Google Ads",
                what_happened=f"Витрати {fmt_delta(spend_delta)}, ROAS {fmt_delta(roas_delta)}.",
                action="перевірити, які кампанії отримали додатковий бюджет без росту доходу",
                trend="warn",
                impact_score=84,
                signal_type="risk",
            ))
        elif eff_value_delta is not None and eff_value_delta >= spend_delta - 5 and (roas_delta is None or abs(roas_delta) < rel):
            ctx.add(make_insight(
                id="GAD04",
                category="google_ads",
                title="Ефективне масштабування Google Ads",
                what_happened=f"Витрати {fmt_delta(spend_delta)}, ROAS утримався на рівні {fmt_roas(g_roas)}.",
                action="перевірити, чи є простір для подальшого обережного масштабування",
                trend="up",
                impact_score=70,
                signal_type="positive",
            ))

    # GAD07
    if significant_change(cpc_delta, rel) and cpc_delta is not None and cpc_delta >= rel and clicks >= int(t("min_clicks_cr")):
        ctx.add(make_insight(
            id="GAD07",
            category="google_ads",
            title="CPC зріс у Google Ads",
            what_happened=f"CPC зріс на {cpc_delta}% і може бути фактором падіння ROAS.",
            action="перевірити конкуренцію в аукціоні, ставки та Quality Score",
            trend="down",
            impact_score=74,
            signal_type="risk",
        ))

    # GAD08
    if (
        significant_change(ctr_delta, rel)
        and ctr_delta is not None
        and ctr_delta <= -rel
        and impressions >= int(t("min_clicks_ctr"))
    ):
        ctx.add(make_insight(
            id="GAD08",
            category="google_ads",
            title="CTR знизився у Google Ads",
            what_happened=f"CTR знизився з {prev_ctr:.2f}% до {ctr:.2f}% ({fmt_delta(ctr_delta)}).",
            action="перевірити оголошення, релевантність і типи кампаній",
            trend="down",
            impact_score=72,
            signal_type="risk",
        ))

    # GAD09
    if (
        significant_change(conv_delta, rel)
        and conv_delta is not None
        and conv_delta <= -rel
        and clicks >= int(t("min_clicks_cr"))
    ):
        cr = 100 * conversions / clicks if clicks else 0
        prev_cr = 100 * prev_conversions / prev_clicks if prev_clicks else 0
        ctx.add(make_insight(
            id="GAD09",
            category="google_ads",
            title="CR знизився у Google Ads",
            what_happened=f"CR знизився з {prev_cr:.1f}% до {cr:.1f}% ({fmt_delta(conv_delta)} конверсій).",
            action="перевірити landing pages, наявність товарів і трекінг",
            trend="down",
            impact_score=80,
            signal_type="risk",
        ))

    # GAD10 / GAD11
    if clicks_delta is not None and conv_delta is not None and clicks >= int(t("min_clicks_cr")):
        if clicks_delta >= rel and conv_delta <= -10:
            ctx.add(make_insight(
                id="GAD10",
                category="google_ads",
                title="Більше кліків без продажів",
                what_happened=f"Кліки {fmt_delta(clicks_delta)}, конверсії {fmt_delta(conv_delta)}.",
                action="перевірити якість трафіку, запити та релевантність оголошень",
                trend="warn",
                impact_score=78,
                signal_type="risk",
            ))
        elif clicks_delta <= -rel and clicks > 0 and prev_clicks > 0:
            cr_now = 100 * conversions / clicks
            cr_prev = 100 * prev_conversions / prev_clicks
            cr_improved = _pct_delta(cr_now, cr_prev)
            if cr_improved is not None and cr_improved >= rel:
                ctx.add(make_insight(
                    id="GAD11",
                    category="google_ads",
                    title="Менше кліків, краща якість",
                    what_happened=f"Кліки {fmt_delta(clicks_delta)}, CR {cr_prev:.1f}% → {cr_now:.1f}%.",
                    trend="up",
                    impact_score=65,
                    signal_type="positive",
                ))

    # GAD12 — просідання типу кампанії
    if channel_rev_delta < 0:
        by_type: dict[str, float] = {}
        prev_by_type: dict[str, float] = {}
        for camp in ctx.gads.get("campaigns") or []:
            label = _campaign_type_label(camp.get("name") or camp.get("type") or "")
            by_type[label] = by_type.get(label, 0) + float(camp.get("conversion_value") or camp.get("revenue") or 0)
        for camp in ctx.gads_prev.get("campaigns") or []:
            label = _campaign_type_label(camp.get("name") or camp.get("type") or "")
            prev_by_type[label] = prev_by_type.get(label, 0) + float(camp.get("conversion_value") or camp.get("revenue") or 0)
        for label, cur_v in by_type.items():
            delta = cur_v - prev_by_type.get(label, 0)
            share = contribution_share(delta, channel_rev_delta)
            if share is not None and share <= -driver_pct:
                ctx.add(make_insight(
                    id=f"GAD12_{label}",
                    category="google_ads",
                    title="Просідання типу кампанії",
                    what_happened=f"{abs(share):.0f}% падіння Google Ads припало на {label}.",
                    action=f"перевірити кампанії типу {label} і їхні товарні/пошукові сегменти",
                    trend="down",
                    impact_score=82,
                    signal_type="risk",
                ))
                break

    # GAD20 — запити без конверсій (keywords)
    kw_threshold = float(t("query_zero_conv_spend_uah"))
    bad_keywords = [
        k for k in (ctx.gads.get("keywords") or [])
        if float(k.get("spend") or 0) >= kw_threshold and int(k.get("conversions") or 0) == 0
    ]
    if bad_keywords:
        total_waste = sum(float(k.get("spend") or 0) for k in bad_keywords[:10])
        ctx.add(make_insight(
            id="GAD20",
            category="google_ads",
            title="Пошукові запити витрачають без результату",
            what_happened=f"{len(bad_keywords)} запитів витратили {fmt_uah_compact(total_waste)} без конверсій.",
            action="перевірити мінус-слова, релевантність і тип відповідності",
            trend="warn",
            impact_score=75,
            signal_type="risk",
        ))

    # GAD27
    sis = ctx.gads.get("search_impression_share")
    prev_sis = ctx.gads_prev.get("search_impression_share")
    if sis is not None and prev_sis is not None:
        sis_delta = float(sis) - float(prev_sis)
        if abs(sis_delta) >= 10:
            direction = "знизилася" if sis_delta < 0 else "зросла"
            ctx.add(make_insight(
                id="GAD27",
                category="google_ads",
                title="Зміна частки показів у Search",
                what_happened=f"Частка показів {direction} з {float(prev_sis):.0f}% до {float(sis):.0f}%.",
                action="перевірити бюджет, ставки та обмеження показів",
                trend="down" if sis_delta < 0 else "up",
                impact_score=70,
                signal_type="risk" if sis_delta < 0 else "opportunity",
            ))

    # GAD28
    if (
        roas_delta is not None
        and abs(roas_delta) < rel
        and spend_delta is not None
        and value_delta is not None
        and spend_delta <= -20
        and value_delta <= -20
        and ctx.g_spend >= min_spend
    ):
        ctx.add(make_insight(
            id="GAD28",
            category="google_ads",
            title="Втрата обсягу при стабільному ROAS",
            what_happened="Ефективність стабільна, але канал втратив обсяг через скорочення витрат і доходу.",
            action="перевірити бюджети, статуси кампаній і обмеження показів",
            trend="warn",
            impact_score=73,
            signal_type="risk",
        ))

    # GAD29 / GAD30 — кампанії vs ціль
    campaigns = ctx.gads.get("campaigns") or []
    if campaigns and ctx.g_spend >= min_spend:
        for camp in sorted(campaigns, key=lambda c: -float(c.get("spend") or 0))[:5]:
            c_spend = float(camp.get("spend") or 0)
            c_val = float(camp.get("conversion_value") or 0)
            c_roas = _safe_roas(c_val, c_spend)
            if c_spend < min_spend * 0.2:
                continue
            share = 100 * c_spend / ctx.g_spend
            name = (camp.get("name") or "—")[:40]
            if c_roas is not None and c_roas < target_roas and share >= float(t("min_share_pct")):
                ctx.add(make_insight(
                    id=f"GAD29_{camp.get('id') or name[:20]}",
                    category="google_ads",
                    title="Кампанія нижче цілі ROAS",
                    what_happened=f"«{name}» має ROAS {fmt_roas(c_roas)} проти цілі {fmt_roas(target_roas)}.",
                    action="перевірити структуру кампанії, товари та пошукові сегменти",
                    trend="warn",
                    impact_score=68,
                    signal_type="risk",
                ))
                break
            if c_roas is not None and c_roas >= target_roas * float(t("roas_above_target_multiplier")) and share < float(t("underfunded_spend_share_pct")):
                ctx.add(make_insight(
                    id=f"GAD30_{camp.get('id') or name[:20]}",
                    category="google_ads",
                    title="Кампанія вище цілі з малою часткою",
                    what_happened=f"«{name}» має ROAS {fmt_roas(c_roas)} при частці бюджету {share:.1f}%.",
                    action="перевірити потенціал обережного масштабування",
                    trend="up",
                    impact_score=64,
                    signal_type="opportunity",
                ))
                break

    # GAD25 — розбіжність Ads vs CRM
    if g_value > 0 and g_rev > 0 and g_orders >= int(t("min_orders")):
        gap_pct = abs(100 * (g_value - g_rev) / max(g_rev, 1))
        prev_gap = abs(100 * (g_prev_value - g_prev_rev) / max(g_prev_rev, 1)) if g_prev_rev else 0
        if gap_pct >= 25 and gap_pct > prev_gap + 10:
            ctx.add(make_insight(
                id="GAD25",
                category="google_ads",
                title="Можлива зміна атрибуції або тегів",
                what_happened="Розбіжність між Google Ads і CRM суттєво зросла.",
                action="перевірити GA4, UTM і теги конверсій",
                trend="warn",
                impact_score=85,
                signal_type="anomaly",
            ))
