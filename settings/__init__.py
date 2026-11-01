"""JSON-конфіги та шляхи (див. settings/README.md)."""

from settings.paths import (
    DEFAULT_GSC_SERVICE_ACCOUNT_REL,
    INSIGHTS_THRESHOLDS_CANONICAL,
    MARKETING_CONFIG_CANONICAL,
    SETTINGS_DIR,
    resolve_insights_thresholds_path,
    resolve_marketing_config_path,
    resolve_project_path,
)

__all__ = [
    "SETTINGS_DIR",
    "MARKETING_CONFIG_CANONICAL",
    "INSIGHTS_THRESHOLDS_CANONICAL",
    "DEFAULT_GSC_SERVICE_ACCOUNT_REL",
    "resolve_marketing_config_path",
    "resolve_insights_thresholds_path",
    "resolve_project_path",
]
