"""CRUD витрат SEO (SQLite)."""

from datetime import date

import web.integrations.seo_expenses as seo_mod
from web.integrations.seo_expenses import (
    SeoExpenseError,
    create_seo_expense,
    delete_seo_expense,
    list_seo_expenses,
    sum_seo_expenses_uah,
    update_seo_expense,
)


def test_create_list_sum_delete(tmp_path, monkeypatch):
    db_path = tmp_path / "seo_test.db"
    monkeypatch.setattr(seo_mod, "clients_db_path", lambda: db_path)

    row = create_seo_expense(
        expense_date="2026-09-10",
        amount_uah="1500",
        description="Копірайт статей",
    )
    assert row["amount_uah"] == 1500.0
    assert row["description"] == "Копірайт статей"

    create_seo_expense(expense_date="2026-09-20", amount_uah=2500, description="Лінкбілдинг")

    items = list_seo_expenses(date_from="2026-09-01", date_to="2026-09-30")
    assert len(items) == 2
    total = sum_seo_expenses_uah(date(2026, 9, 1), date(2026, 9, 30))
    assert total == 4000.0

    updated = update_seo_expense(
        row["id"],
        expense_date="2026-09-11",
        amount_uah=1800,
        description="Копірайт + редактура",
    )
    assert updated["amount_uah"] == 1800.0

    delete_seo_expense(row["id"])
    remaining = list_seo_expenses()
    assert len(remaining) == 1
    assert db_path.is_file()


def test_validation_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(seo_mod, "clients_db_path", lambda: tmp_path / "seo.db")
    try:
        create_seo_expense(expense_date="2026-09-01", amount_uah=0, description="test")
        assert False, "expected error"
    except SeoExpenseError:
        pass
    try:
        create_seo_expense(expense_date="2026-09-01", amount_uah=100, description="")
        assert False, "expected error"
    except SeoExpenseError:
        pass


def test_migrate_legacy_json(tmp_path, monkeypatch):
    db_path = tmp_path / "clients.db"
    json_path = tmp_path / "seo_expenses.json"
    json_path.write_text(
        '[{"id":"legacy-1","expense_date":"2026-08-01","amount_uah":999,"description":"old"}]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(seo_mod, "clients_db_path", lambda: db_path)
    monkeypatch.setattr(seo_mod, "_LEGACY_JSON_PATH", json_path)

    items = list_seo_expenses()
    assert len(items) == 1
    assert items[0]["id"] == "legacy-1"
    assert items[0]["amount_uah"] == 999.0
