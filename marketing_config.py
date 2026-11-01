"""Ключі маркетингових інтеграцій (Google Ads, Meta, GSC, GA4) — окремий конфіг, не .env."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv, unset_key

from settings.paths import (
    MARKETING_CONFIG_CANONICAL,
    ROOT_DIR,
    resolve_marketing_config_path,
)

MARKETING_CONFIG_PATH = resolve_marketing_config_path()
ENV_FILE_PATH = ROOT_DIR / ".env"

MARKETING_ENV_KEYS: tuple[str, ...] = (
    "GOOGLE_ADS_DEVELOPER_TOKEN",
    "GOOGLE_ADS_CLIENT_ID",
    "GOOGLE_ADS_CLIENT_SECRET",
    "GOOGLE_ADS_REFRESH_TOKEN",
    "GOOGLE_ADS_CUSTOMER_ID",
    "GOOGLE_ADS_OAUTH_REDIRECT_URI",
    "GOOGLE_ADS_OAUTH_LOCAL_REDIRECT_URI",
    "GOOGLE_ADS_OAUTH_LOCAL_PORT",
    "META_ADS_ACCESS_TOKEN",
    "META_AD_ACCOUNT_ID",
    "META_ADS_API_VERSION",
    "GSC_SERVICE_ACCOUNT_FILE",
    "GSC_SITE_URL",
    "GSC_SITE_URL_EXTRA",
    "GA4_PROPERTY_ID",
    "GA4_SERVICE_ACCOUNT_FILE",
)


def _clean_value(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1].strip()
    return text


def _read_json_file() -> dict[str, str]:
    if not MARKETING_CONFIG_PATH.is_file():
        return {}
    try:
        raw = json.loads(MARKETING_CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): _clean_value(v) for k, v in raw.items() if _clean_value(v)}


def _write_json_file(data: dict[str, str]) -> None:
    global MARKETING_CONFIG_PATH
    target = MARKETING_CONFIG_CANONICAL
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {k: data[k] for k in MARKETING_ENV_KEYS if data.get(k)}
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    MARKETING_CONFIG_PATH = target.resolve()


def _read_env_marketing() -> dict[str, str]:
    load_dotenv(ENV_FILE_PATH, override=False)
    out: dict[str, str] = {}
    for key in MARKETING_ENV_KEYS:
        val = _clean_value(os.getenv(key, ""))
        if val:
            out[key] = val
    return out


def _remove_marketing_from_env() -> None:
    if not ENV_FILE_PATH.is_file():
        return
    for key in MARKETING_ENV_KEYS:
        if re.search(rf"^{re.escape(key)}=", ENV_FILE_PATH.read_text(encoding="utf-8"), re.M):
            try:
                unset_key(str(ENV_FILE_PATH), key)
            except OSError:
                pass
        os.environ.pop(key, None)


def ensure_marketing_config() -> dict[str, str]:
    """Завантажити marketing_config.json; при відсутності — імпорт з .env."""
    data = _read_json_file()
    if data:
        return data

    migrated = _read_env_marketing()
    if migrated:
        _write_json_file(migrated)
        _remove_marketing_from_env()
    return migrated


def load_marketing_config() -> dict[str, str]:
    return ensure_marketing_config()


def get_marketing_value(key: str, default: str = "") -> str:
    data = load_marketing_config()
    return data.get(key, default)


def set_marketing_value(key: str, value: str) -> None:
    if key not in MARKETING_ENV_KEYS:
        raise KeyError(f"Невідомий ключ маркетингового конфігу: {key}")
    data = load_marketing_config()
    cleaned = _clean_value(value)
    if cleaned:
        data[key] = cleaned
    else:
        data.pop(key, None)
    _write_json_file(data)
    if cleaned:
        os.environ[key] = cleaned
    else:
        os.environ.pop(key, None)


def apply_marketing_to_environ(data: dict[str, str]) -> None:
    for key in MARKETING_ENV_KEYS:
        val = _clean_value(data.get(key, ""))
        if val:
            os.environ[key] = val
        else:
            os.environ.pop(key, None)
