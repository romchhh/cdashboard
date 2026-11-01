"""Тести глосарію показників рекламного дашборду."""

from web.ad_metric_help import LABEL_TO_METRIC_KEY, METRIC_HELP, metric_help, metric_key_for_label


def test_metric_help_by_key():
    text = metric_help("roas")
    assert text is not None
    assert "ROAS" in text
    assert "Дохід" in text


def test_metric_help_by_ukrainian_label():
    text = metric_help("Витрати")
    assert text is not None
    assert "Google Ads" in text or "Meta" in text


def test_metric_key_for_label():
    assert metric_key_for_label("CRM замовлення") == "crm_orders"


def test_all_label_mappings_have_help_text():
    for label, key in LABEL_TO_METRIC_KEY.items():
        assert key in METRIC_HELP, f"Missing METRIC_HELP for {label} -> {key}"
