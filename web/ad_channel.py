"""
Класифікація маркетингових каналів для замовлень і клієнтів Cosmy.
Використовується в розділі «Аналіз реклами» для атрибуції доходу з CRM.
"""

from __future__ import annotations

import re
from typing import Any

from web.cosmy_storefront import is_cosmy_storefront_referrer

CHANNEL_GOOGLE_ADS = "google_ads"
CHANNEL_META_ADS = "meta_ads"
CHANNEL_SEO = "seo"
CHANNEL_DIRECT = "direct"
CHANNEL_EMAIL = "email"
CHANNEL_REFERRAL = "referral"
CHANNEL_OTHER = "other"

ALL_CHANNELS: tuple[str, ...] = (
    CHANNEL_GOOGLE_ADS,
    CHANNEL_META_ADS,
    CHANNEL_SEO,
    CHANNEL_DIRECT,
    CHANNEL_EMAIL,
    CHANNEL_REFERRAL,
    CHANNEL_OTHER,
)

CHANNEL_LABELS_UA: dict[str, str] = {
    CHANNEL_GOOGLE_ADS: "Google Ads",
    CHANNEL_META_ADS: "Meta Ads",
    CHANNEL_SEO: "SEO (органіка)",
    CHANNEL_DIRECT: "Прямий трафік",
    CHANNEL_EMAIL: "Email",
    CHANNEL_REFERRAL: "Реферальний",
    CHANNEL_OTHER: "Інші джерела",
}

OVERVIEW_CHANNELS: tuple[str, ...] = (
    CHANNEL_GOOGLE_ADS,
    CHANNEL_META_ADS,
    CHANNEL_SEO,
    CHANNEL_DIRECT,
    CHANNEL_OTHER,
)


def _fold(*parts: str | None) -> str:
    return " ".join((p or "").strip().lower() for p in parts if (p or "").strip())


_REFERRER_PLACEHOLDERS = frozenset({
    "",
    "missing",
    "none",
    "(none)",
    "null",
    "unknown",
    "n/a",
    "na",
    "-",
    "(direct)",
    "direct",
})

_GADS_CAMPAIGN_RE = re.compile(
    r"(?i)(^va[_\[]|[_-]va[_-]|^ob-pmax|pmax|search_brand|remarketing|performance[\s._-]?max|googleads|gads_)",
)

_MESSAGING_SOURCE_EXACT = frozenset({
    "sms", "mailings", "mailing", "newsletter", "email", "e-mail",
})

_MESSAGING_MEDIUM_EXACT = frozenset({"viber", "sms", "email", "e-mail"})


def _meaningful_referrer(ref: str | None) -> str:
    """Referer з CRM; placeholder «missing» не вважається зовнішнім сайтом."""
    r = (ref or "").strip()
    if r.casefold() in _REFERRER_PLACEHOLDERS:
        return ""
    return r


def looks_like_google_ads_campaign(campaign: str | None) -> bool:
    """Назви кампаній Cosmy Google Ads (VA_PMax, pmax_*, Search_Brand тощо)."""
    c = (campaign or "").strip()
    if not c or c in ("—", "-"):
        return False
    return bool(_GADS_CAMPAIGN_RE.search(c))


def _looks_like_messaging(us: str, um: str, combined: str) -> bool:
    u = (us or "").strip().lower()
    m = (um or "").strip().lower()
    if u in _MESSAGING_SOURCE_EXACT:
        return True
    if m in _MESSAGING_MEDIUM_EXACT:
        return True
    if "mailing" in combined or "newsletter" in combined:
        return True
    return False


_META_SOURCE_EXACT = frozenset({
    "fb", "ig", "insta", "meta", "facebook", "instagram",
    "facebook.com", "instagram.com", "m.facebook.com", "l.facebook.com",
    "lm.facebook.com", "l.instagram.com",
})


def _looks_like_meta(us: str, combined: str) -> bool:
    u = (us or "").strip().lower()
    blob = (combined or "").strip().lower()
    if u in _META_SOURCE_EXACT:
        return True
    if "igshopping" in blob.replace(" ", ""):
        return True
    if any(x in blob for x in (
        "facebook", "instagram", "fbclid", "igclid", "meta",
    )):
        return True
    if " ig " in f" {blob} " or blob.startswith("ig ") or blob.endswith(" ig"):
        return True
    return False


def classify_marketing_channel(
    *,
    utm_source: str | None = None,
    utm_medium: str | None = None,
    utm_campaign: str | None = None,
    source_raw: str | None = None,
    source_category: str | None = None,
    source_utm_source: str | None = None,
    source_utm_medium: str | None = None,
    source_utm_campaign: str | None = None,
    referer: str | None = None,
    source_referrer: str | None = None,
) -> str:
    """
    Визначає канал атрибуції замовлення.
    Пріоритет: UTM замовлення → UTM/джерело клієнта → referer.
    """
    us = (utm_source or source_utm_source or "").strip()
    um = (utm_medium or source_utm_medium or "").strip()
    raw = (source_raw or "").strip()
    cat = (source_category or "").strip()
    ref = _meaningful_referrer(referer or source_referrer)
    camp = (utm_campaign or source_utm_campaign or "").strip()

    combined = _fold(us, um, raw, cat, camp, ref)
    um_l = um.lower()

    if any(x in combined for x in ("gclid", "gbraid", "wbraid", "googleads")):
        return CHANNEL_GOOGLE_ADS

    if looks_like_google_ads_campaign(camp):
        return CHANNEL_GOOGLE_ADS

    if _looks_like_messaging(us, um, combined):
        return CHANNEL_EMAIL

    if _looks_like_meta(us, combined):
        if um_l in ("cpc", "paid", "ppc", "cpm") or "ads" in combined or "paid" in combined:
            return CHANNEL_META_ADS
        if "organic" not in combined:
            return CHANNEL_META_ADS

    # Cosmy Meta: utm_medium=cpm без google у джерелі (навіть якщо source обрізаний).
    if um_l == "cpm" and "google" not in combined and "youtube" not in combined:
        return CHANNEL_META_ADS

    if "google" in combined:
        if um.lower() in ("cpc", "paid", "ppc") or "ads" in cat.lower() or "ads" in raw.lower():
            return CHANNEL_GOOGLE_ADS
        if "organic" in combined or "organic" in um.lower() or cat.lower() == "organic":
            return CHANNEL_SEO
        if um.lower() in ("cpc", "paid"):
            return CHANNEL_GOOGLE_ADS

    if um.lower() == "email" or "newsletter" in combined or "mailing" in combined:
        return CHANNEL_EMAIL

    if um.lower() == "referral" or ("referral" in combined and "organic" not in combined):
        return CHANNEL_REFERRAL

    if "organic" in combined or "organic" in um.lower() or cat.lower() == "organic":
        return CHANNEL_SEO

    if "seo" in combined or ("search" in combined and "cpc" not in combined and "paid" not in combined):
        return CHANNEL_SEO

    if um_l in ("cpc", "paid", "ppc") and us:
        if any(x in us.lower() for x in ("google", "adwords", "gads", "youtube")):
            return CHANNEL_GOOGLE_ADS
        return CHANNEL_OTHER

    if "direct" in combined or (not us and not um and not ref and raw.lower() in ("direct", "(direct)", "none")):
        return CHANNEL_DIRECT

    if not us and not um and not ref and not raw:
        return CHANNEL_DIRECT

    if ref and not is_cosmy_storefront_referrer(ref):
        return CHANNEL_REFERRAL

    return CHANNEL_OTHER


def channel_label_ua(channel_id: str) -> str:
    return CHANNEL_LABELS_UA.get(channel_id, channel_id or "—")


def classify_order_channel(order: Any, client: Any | None = None) -> str:
    """Канал для замовлення з урахуванням полів order + fallback на client."""
    c = client
    return classify_marketing_channel(
        utm_source=getattr(order, "utm_source", None),
        utm_medium=getattr(order, "utm_medium", None),
        utm_campaign=getattr(order, "utm_campaign", None),
        referer=getattr(order, "referer", None),
        source_raw=getattr(c, "source_raw", None) if c else None,
        source_category=getattr(c, "source_category", None) if c else None,
        source_utm_source=getattr(c, "source_utm_source", None) if c else None,
        source_utm_medium=getattr(c, "source_utm_medium", None) if c else None,
        source_utm_campaign=getattr(c, "source_utm_campaign", None) if c else None,
        source_referrer=getattr(c, "source_referrer", None) if c else None,
    )


def other_channel_group(channel_id: str) -> bool:
    """Чи входить канал у блок «Інші джерела» на огляді."""
    return channel_id in (CHANNEL_EMAIL, CHANNEL_REFERRAL, CHANNEL_OTHER)


UTM_CLASSIFICATION_RULES_UA: tuple[dict[str, str], ...] = (
    {
        "channel": "Google Ads",
        "channel_id": CHANNEL_GOOGLE_ADS,
        "rules": (
            "gclid / gbraid / wbraid у referer; "
            "назва utm_campaign як у Google Ads (VA_PMax, pmax_*, Search_Brand…); "
            "google / youtube + utm_medium=cpc|paid|ppc; "
            "utm_source містить google|adwords|gads + paid"
        ),
    },
    {
        "channel": "Meta Ads",
        "channel_id": CHANNEL_META_ADS,
        "rules": (
            "facebook / instagram / fb / ig / meta + cpc|cpm|paid; "
            "utm_medium=cpm без google у джерелі; "
            "fbclid / igclid у referer"
        ),
    },
    {
        "channel": "SEO (органіка)",
        "channel_id": CHANNEL_SEO,
        "rules": (
            "utm_medium=organic або source_category=organic; "
            "google + organic у UTM/referer; "
            "seo / search без cpc|paid"
        ),
    },
    {
        "channel": "Прямий трафік",
        "channel_id": CHANNEL_DIRECT,
        "rules": (
            "немає utm_source і utm_medium, немає зовнішнього referer; "
            "source_raw=direct; type-in / закладки"
        ),
    },
    {
        "channel": "Email",
        "channel_id": CHANNEL_EMAIL,
        "rules": (
            "utm_medium=email; newsletter / mailing; "
            "sms / mailings / viber (розсилки Cosmy)"
        ),
    },
    {
        "channel": "Реферальний",
        "channel_id": CHANNEL_REFERRAL,
        "rules": (
            "utm_medium=referral; "
            "зовнішній referer (не cosmy.com.ua / cosmy.ua); «missing» у фіді не рахується referer"
        ),
    },
    {
        "channel": "Інші джерела",
        "channel_id": CHANNEL_OTHER,
        "rules": (
            "utm_medium=cpc|paid без google/meta у source; "
            "нестандартні UTM; все, що не підпало під правила вище"
        ),
    },
)


def utm_classification_rules_ua() -> list[dict[str, str]]:
    """Правила атрибуції UTM → канал для підказок у UI."""
    return [dict(r) for r in UTM_CLASSIFICATION_RULES_UA]
