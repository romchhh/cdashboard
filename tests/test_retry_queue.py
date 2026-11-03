"""Повтор магазинів не має перетворювати бренд на тисячі «нових товарів»."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cosmyparser"))

from catalog_report import _shop_needs_retry, trim_retry_queue


def _item(idx: int, site: str):
    return (idx, {"id": idx}, site)


def test_slow_site_is_not_retried():
    assert _shop_needs_retry({
        "status": "unavailable",
        "reason": "магазин відповідає занадто повільно — пропущено в цьому прогоні",
    }) is False


def test_timeout_is_retried():
    assert _shop_needs_retry({
        "status": "unavailable",
        "reason": "не відповів за 85 с",
    }) is True
    assert _shop_needs_retry({"status": "not_found", "reason": "товар не знайдено"}) is False


def test_widespread_site_is_probed_not_repeated_per_sku():
    queue = [_item(i, "Profistyle") for i in range(92)]
    queue += [_item(i, "Krasota.shop") for i in range(3)]
    trimmed = trim_retry_queue(queue, 92)
    assert sum(1 for item in trimmed if item[2] == "Profistyle") == 2
    assert sum(1 for item in trimmed if item[2] == "Krasota.shop") == 3


def test_sparse_timeouts_stay():
    queue = [_item(i, "Pleka") for i in range(5)]
    assert trim_retry_queue(queue, 92) == queue
