"""Домени вітрини Cosmy (старий cosmy.com.ua + новий cosmy.ua)."""

from __future__ import annotations

import re
from urllib.parse import urlparse

# GSC property URLs (https prefix, без trailing slash — як у Search Console).
GSC_STOREFRONT_PAIR: tuple[str, ...] = ("https://cosmy.ua", "https://cosmy.com.ua")

_COSMY_STORE_HOST_SUFFIXES: tuple[str, ...] = (
    "cosmy.com.ua",
    "cosmy.ua",
)

_INTERNAL_REFERRER_MARKERS: tuple[str, ...] = (
    "webpkgcache.com",
)


def storefront_host_from_url(url: str | None) -> str | None:
    if not url or not str(url).strip():
        return None
    text = str(url).strip()
    if text.lower() in ("missing", "none", "(none)", "—"):
        return None
    parsed = urlparse(text if "://" in text else f"https://{text}")
    host = (parsed.hostname or "").lower().strip()
    return host or None


def is_cosmy_storefront_referrer(url: str | None) -> bool:
    """Чи referer належить вітрині Cosmy (обидва домени) або внутрішньому кешу Google."""
    if not url:
        return False
    low = str(url).lower()
    for marker in _INTERNAL_REFERRER_MARKERS:
        if marker in low:
            return True
    host = storefront_host_from_url(url)
    if not host:
        return False
    for suffix in _COSMY_STORE_HOST_SUFFIXES:
        if host == suffix or host.endswith(f".{suffix}"):
            return True
    return False


def _split_url_tokens(raw: str) -> list[str]:
    parts = re.split(r"[,;\n]+", raw or "")
    out: list[str] = []
    for part in parts:
        u = part.strip()
        if u:
            out.append(u.rstrip("/"))
    return out


def normalize_gsc_site_url(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    if not u:
        return u
    if not u.startswith("http"):
        u = f"https://{u}"
    return u


def gsc_site_urls_from_config(
    primary: str | None,
    extra: str | None = None,
    *,
    include_storefront_pair: bool = False,
) -> list[str]:
    """Список GSC siteUrl для запитів API (дедуп).

    Другий домен (com.ua ↔ ua) додається лише з include_storefront_pair=True
    (або явно в GSC_SITE_URL через кому), щоб не бити в 403 після міграції на cosmy.ua.
    """
    urls: list[str] = []
    for token in _split_url_tokens(primary or ""):
        urls.append(normalize_gsc_site_url(token))
    for token in _split_url_tokens(extra or ""):
        urls.append(normalize_gsc_site_url(token))

    if not urls:
        urls = [normalize_gsc_site_url(u) for u in GSC_STOREFRONT_PAIR]
    elif include_storefront_pair:
        normalized = {normalize_gsc_site_url(u) for u in urls}
        pair_norm = {normalize_gsc_site_url(u) for u in GSC_STOREFRONT_PAIR}
        if normalized & pair_norm:
            for u in GSC_STOREFRONT_PAIR:
                nu = normalize_gsc_site_url(u)
                if nu not in normalized:
                    urls.append(nu)

    seen: set[str] = set()
    ordered: list[str] = []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            ordered.append(u)
    return ordered
