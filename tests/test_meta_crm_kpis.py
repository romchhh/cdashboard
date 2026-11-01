"""CRM-дохід на Meta: кампанії та lookup атрибуції."""

from web.ad_reporting import _campaign_attribution_from_orders, _enrich_meta_campaigns


def test_enrich_meta_campaigns_adds_crm_revenue():
    camps = [
        {"name": "retarget", "spend": 10.0, "purchases": 2, "purchase_value": 0.0},
        {"name": "prospect", "spend": 30.0, "purchases": 1, "purchase_value": 5.0},
    ]
    attribution = {"retarget": {"revenue": 53428.0, "orders": 23}}
    rows, matched = _enrich_meta_campaigns(
        camps,
        total_crm_revenue=53428.0,
        total_meta_spend=1640.0,
        attribution=attribution,
    )
    assert matched is True
    by_name = {r["name"]: r for r in rows}
    assert by_name["retarget"]["crm_revenue"] == 53428.0
    assert by_name["retarget"]["crm_orders"] == 23
    assert by_name["retarget"]["attribution"] == "utm"
    assert by_name["prospect"]["crm_orders"] == 0
    assert by_name["prospect"]["meta_revenue"] == 5.0
    assert by_name["retarget"]["roas_pct"] != "—"


def test_campaign_attribution_alias_meta_ads():
    store = {
        "campaign_attribution": {
            "meta": {"retarget": {"revenue": 100.0, "orders": 2}},
        }
    }
    assert _campaign_attribution_from_orders(store, "meta_ads")["retarget"]["orders"] == 2
    assert _campaign_attribution_from_orders(store, "meta")["retarget"]["revenue"] == 100.0
