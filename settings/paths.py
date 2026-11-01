"""Шляхи до JSON-конфігів проєкту (відносно кореня репозиторію)."""

from __future__ import annotations

import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SETTINGS_DIR = ROOT_DIR / "settings"

# Маркетинг (Google Ads, Meta, GSC, GA4)
MARKETING_CONFIG_CANONICAL = SETTINGS_DIR / "marketing.json"
LEGACY_MARKETING_CONFIG = ROOT_DIR / "marketing_config.json"
MARKETING_CONFIG_EXAMPLE = SETTINGS_DIR / "marketing.example.json"

# Пороги інсайтів рекламного дашборду
INSIGHTS_THRESHOLDS_CANONICAL = SETTINGS_DIR / "insights_thresholds.json"
LEGACY_INSIGHTS_THRESHOLDS = ROOT_DIR / "marketing_insights_thresholds.json"
INSIGHTS_THRESHOLDS_EXAMPLE = SETTINGS_DIR / "insights_thresholds.example.json"

# Google Service Account (GSC / GA4)
DEFAULT_GSC_SERVICE_ACCOUNT_REL = "settings/gsc_service_account.json"
GSC_SERVICE_ACCOUNT_CANONICAL = SETTINGS_DIR / "gsc_service_account.json"
LEGACY_GSC_SERVICE_ACCOUNT = ROOT_DIR / "service_account.json"
GSC_SERVICE_ACCOUNT_EXAMPLE = SETTINGS_DIR / "gsc_service_account.example.json"


def resolve_marketing_config_path() -> Path:
    explicit = (os.getenv("MARKETING_CONFIG_PATH") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    if MARKETING_CONFIG_CANONICAL.is_file():
        return MARKETING_CONFIG_CANONICAL.resolve()
    if LEGACY_MARKETING_CONFIG.is_file():
        return LEGACY_MARKETING_CONFIG.resolve()
    return MARKETING_CONFIG_CANONICAL.resolve()


def resolve_insights_thresholds_path() -> Path:
    explicit = (os.getenv("INSIGHTS_THRESHOLDS_PATH") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    if INSIGHTS_THRESHOLDS_CANONICAL.is_file():
        return INSIGHTS_THRESHOLDS_CANONICAL.resolve()
    if LEGACY_INSIGHTS_THRESHOLDS.is_file():
        return LEGACY_INSIGHTS_THRESHOLDS.resolve()
    return INSIGHTS_THRESHOLDS_CANONICAL.resolve()


def resolve_project_path(
    rel: str | None,
    *,
    default_rel: str = DEFAULT_GSC_SERVICE_ACCOUNT_REL,
) -> Path:
    """Відносний шлях від ROOT_DIR; fallback на legacy файли в корені."""
    text = (rel or default_rel).strip() or default_rel
    candidate = Path(text)
    if candidate.is_absolute():
        return candidate.resolve()
    resolved = (ROOT_DIR / candidate).resolve()
    if resolved.is_file():
        return resolved
    for legacy in (LEGACY_GSC_SERVICE_ACCOUNT,):
        leg = legacy.resolve()
        if leg.is_file():
            return leg
    return resolved
