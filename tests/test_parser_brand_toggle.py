"""Вимикання бренду і звіти парсера живуть у SQLite."""

from web import parser_service as ps


def _paths(tmp_path, monkeypatch):
    monkeypatch.setattr(ps.store, "DB_PATH", tmp_path / "parser.db")
    monkeypatch.setattr(ps.store, "JSON_DIR", tmp_path / "no-json")
    monkeypatch.setattr(ps, "load_catalog", lambda: [])


def test_two_disables_both_stick(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    ps.set_brand_enabled("Cos De Baha", False)
    ps.set_brand_enabled("Hadat Cosmetics", False)
    assert ps.disabled_brand_set() == {"cos de baha", "hadat cosmetics"}
    assert not ps.is_brand_enabled("Cos De Baha")
    assert not ps.is_brand_enabled("Hadat Cosmetics")
    assert ps.is_brand_enabled("Cosmy")


def test_save_settings_does_not_reenable_brands(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    ps.set_brand_enabled("Cos De Baha", False)
    ps.save_settings({"sku_workers": 4, "disabled_brands": []})
    assert not ps.is_brand_enabled("COS  DE   BAHA")
    assert "Cos De Baha" in ps.load_settings()["disabled_brands"]


def test_explicit_empty_sites_stay_off(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    ps.save_settings({"enabled_sites": []})
    assert ps.load_settings()["enabled_sites"] == []


def test_missing_sites_key_means_all(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    ps.store.write_settings({"sku_workers": 3}, None, update_sites=False)
    loaded = ps.load_settings()
    assert loaded["sku_workers"] == 3
    assert loaded["enabled_sites"]
    assert len(loaded["enabled_sites"]) > 1


def test_brand_toggle_does_not_wipe_settings(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    ps.save_settings({"sku_workers": 7, "enabled_sites": []})
    ps.set_brand_enabled("Hadat Cosmetics", False)
    loaded = ps.load_settings()
    assert loaded["sku_workers"] == 7
    assert loaded["enabled_sites"] == []
    assert not ps.is_brand_enabled("Hadat Cosmetics")


def test_seed_does_not_clobber_running_but_heals_error(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    ps._update_brand_status("Academie", state="running", error=None)
    ps._update_brand_status(
        "Sesderma",
        state="error",
        error="timeout",
        last_parsed_at="2026-10-10T12:00:00",
    )
    ps._update_brand_status("Erayba", state="done", total=2, last_parsed_at="2026-10-20T12:00:00")
    ps._seed_brand_status_from_report({
        "generated_at": "2026-10-14T12:00:00",
        "rows": [{"brand": "Academie"}, {"brand": "Sesderma"}, {"brand": "Erayba"}],
    })
    status = ps.load_brand_status()
    assert status["Academie"]["state"] == "running"
    assert status["Sesderma"]["state"] == "done"
    assert status["Sesderma"].get("error") is None
    assert status["Erayba"]["state"] == "done"
    assert status["Erayba"]["last_parsed_at"] == "2026-10-20T12:00:00"


def test_effective_brand_state_hides_stale_error(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    ps._update_brand_status(
        "Academie",
        state="error",
        error="worker timeout",
        last_parsed_at="2026-10-16T13:52:00",
    )
    ps.store.save_report(
        "full",
        {
            "generated_at": "2026-10-23T11:00:00",
            "rows": [{"brand": "Academie", "from_report": True, "found": 3, "diff_min_pct": 1.0}],
        },
    )
    monkeypatch.setattr(
        ps,
        "load_catalog",
        lambda: [{"brand": "Academie"}, {"brand": "Academie"}, {"brand": "Academie"}],
    )
    row = next(b for b in ps.list_brands() if b["name"] == "Academie")
    assert row["state"] == "done"
    assert row.get("error") is None


def test_report_roundtrip_keeps_shops(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    ps.store.save_report(
        "academie",
        {
            "generated_at": "2026-10-14T12:00:00",
            "total": 1,
            "sites": ["Makeup"],
            "rows": [
                {
                    "id": 7,
                    "brand": "Academie",
                    "name": "Cream",
                    "our_price": 100,
                    "position": "в ринку",
                    "shops": {
                        "Makeup": {
                            "status": "match",
                            "price": 90,
                            "title": "Cream",
                            "url": "https://example.test/p",
                            "match": 0.91,
                            "in_stock": True,
                            "near": [{"title": "інший", "price": 80, "match": 0.4}],
                        }
                    },
                }
            ],
        },
    )
    loaded = ps.load_brand_report("Academie")
    assert loaded["rows"][0]["id"] == 7
    shop = loaded["rows"][0]["shops"]["Makeup"]
    assert shop["price"] == 90
    assert shop["in_stock"] is True
    assert shop["near"][0]["title"] == "інший"
    assert loaded["rows"][0]["position"] == "в ринку"


def test_full_report_stats_override_older_brand_scope(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    ps.store.save_report(
        "academie",
        {
            "generated_at": "2026-10-16T13:52:00",
            "rows": [
                {
                    "brand": "Academie",
                    "from_report": True,
                    "found": 1,
                    "diff_min_pct": 2.0,
                }
            ],
        },
    )
    ps.store.save_report(
        ps.store.FULL_SCOPE,
        {
            "generated_at": "2026-10-23T11:00:00",
            "rows": [
                {
                    "brand": "Academie",
                    "from_report": True,
                    "found": 5,
                    "diff_min_pct": 1.0,
                }
            ],
        },
    )
    idx = ps._reports_stats_by_brand()
    assert idx["Academie"]["last_parsed_at"] == "2026-10-23T11:00:00"
    assert idx["Academie"]["stats"]["with_diff"] == 1


def test_reconcile_keeps_done_brands_after_full_catalog_fail(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    ps._update_brand_status("Academie", state="error", error="worker down")
    ps._update_brand_status("Anua", state="running", error=None)
    ps.store.save_report(
        ps.store.FULL_SCOPE,
        {
            "generated_at": "2026-10-23T11:00:00",
            "rows": [
                {
                    "brand": "Academie",
                    "from_report": True,
                    "found": 2,
                    "diff_min_pct": 1.0,
                }
            ],
        },
    )
    monkeypatch.setattr(
        ps,
        "load_catalog",
        lambda: [{"brand": "Academie"}, {"brand": "Anua"}],
    )
    monkeypatch.setattr(ps, "report_status", lambda: {"state": "error", "error": "fail"})
    ps._reconcile_brand_status(
        ps._reports_stats_by_brand(),
        {"Academie": 1, "Anua": 1},
        brands=["Academie", "Anua"],
        job_error="fail",
    )
    status = ps.load_brand_status()
    assert status["Academie"]["state"] == "done"
    assert status["Academie"].get("error") is None
    assert status["Anua"]["state"] == "never"
    assert status["Anua"].get("error") is None


def test_error_without_report_shows_never(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    ps._update_brand_status("FreshBrand", state="error", error="mass fail")
    monkeypatch.setattr(
        ps,
        "load_catalog",
        lambda: [{"brand": "FreshBrand"}, {"brand": "FreshBrand"}],
    )
    row = next(b for b in ps.list_brands() if b["name"] == "FreshBrand")
    assert row["state"] == "never"
    assert row.get("error") is None
    assert ps.load_brand_status()["FreshBrand"]["state"] == "never"


def test_search_history_roundtrip(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    items = ps.append_history(
        {
            "id": "abc",
            "query": "крем",
            "our_price": 10,
            "found": 1,
            "results": [{"site": "Makeup", "title": "крем", "price": 12, "match": 1, "url": "https://example.test"}],
        }
    )
    assert items[0]["query"] == "крем"
    again = ps.load_history()
    assert again[0]["results"][0]["site"] == "Makeup"
    assert again[0]["results"][0]["price"] == 12
    ps.save_history([])
    assert ps.load_history() == []
