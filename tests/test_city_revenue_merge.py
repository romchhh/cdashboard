"""Обʼєднання міст за доходом (Київ + Киев)."""

from web.client_marketing import merge_city_revenue_rows


def test_merge_kyiv_variants():
    rows = merge_city_revenue_rows({
        "Київ": 200_387.0,
        "Киев": 49_057.0,
        "Одеса": 42_615.0,
    })
    assert rows[0]["name"] == "Київ"
    assert rows[0]["revenue"] == 249_444.0
    assert rows[1]["name"] == "Одеса"


def test_share_pct_after_merge():
    rows = merge_city_revenue_rows({"Київ": 100.0, "Киев": 100.0, "Львів": 100.0})
    kyiv = next(r for r in rows if r["name"] == "Київ")
    assert kyiv["share_pct"] == round(100 * 200 / 300, 1)
