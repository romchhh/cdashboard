"""Набори правил інсайтів."""

from web.insights.rules.cross import apply_cross_rules
from web.insights.rules.general import apply_general_rules
from web.insights.rules.google_ads import apply_google_ads_rules
from web.insights.rules.meta import apply_meta_rules
from web.insights.rules.seo import apply_seo_rules

__all__ = [
    "apply_general_rules",
    "apply_google_ads_rules",
    "apply_meta_rules",
    "apply_seo_rules",
    "apply_cross_rules",
]
