"""Пороги та цілі для правил інсайтів."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DEFAULT_THRESHOLDS: dict[str, float | int] = {
    "relative_change_pct": 15,
    "min_share_pct": 1,
    "min_conversions_per_period": 10,
    "min_clicks_cr": 100,
    "min_clicks_ctr": 500,
    "min_spend_uah": 3000,
    "min_orders": 3,
    "efficiency_gap_pp": 15,
    "contribution_driver_pct": 40,
    "budget_share_imbalance_ratio": 1.5,
    "underfunded_spend_share_pct": 5,
    "roas_above_target_multiplier": 1.2,
    "roas_below_target_multiplier": 0.7,
    "target_roas": 3.0,
    "concentration_top3_share_pct": 60,
    "concentration_top1_share_pct": 30,
    "position_stable_max_change": 0.5,
    "indexing_impressions_drop_pct": 30,
    "z_score_anomaly": 2,
    "meta_frequency_warn": 2.5,
    "query_zero_conv_spend_uah": 500,
    "product_zero_conv_spend_uah": 200,
}

from settings.paths import resolve_insights_thresholds_path

_THRESHOLDS_PATH = resolve_insights_thresholds_path()


def load_insight_thresholds() -> dict[str, float | int]:
    data: dict[str, Any] = {}
    if _THRESHOLDS_PATH.is_file():
        try:
            data = json.loads(_THRESHOLDS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    merged = dict(_DEFAULT_THRESHOLDS)
    for key, value in data.items():
        if key in merged and isinstance(value, (int, float)):
            merged[key] = value
    return merged
