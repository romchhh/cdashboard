"""Налаштування API інтеграцій рекламного дашборду (marketing_config.json)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from config import (
    INTEGRATION_SETTINGS_UI_ENABLED,
    MARKETING_CONFIG_PATH,
    ROOT_DIR,
    WEB_PUBLIC_BASE_URL,
    ga4_configured,
    ga4_service_account_file,
    google_ads_configured,
    gsc_configured,
    meta_ads_configured,
    reload_marketing_config,
    set_marketing_value,
)

AUTH_ERROR_MARKERS = (
    "invalid_grant",
    "token has been expired",
    "token has expired",
    "has expired",
    "expired",
    "revoked",
    "unauthenticated",
    "authentication",
    "invalid oauth",
    "error validating access token",
    "session has expired",
    "oauth",
    "permission_denied",
    "invalid credentials",
    "access token",
    "developer_token",
    "developer token",
    "unauthenticated",
    "missing required authentication",
)

GOOGLE_ADS_VERIFY_ENV_KEYS = frozenset(
    {
        "GOOGLE_ADS_DEVELOPER_TOKEN",
        "GOOGLE_ADS_CLIENT_ID",
        "GOOGLE_ADS_CLIENT_SECRET",
        "GOOGLE_ADS_REFRESH_TOKEN",
        "GOOGLE_ADS_CUSTOMER_ID",
    }
)

SECTION_INTEGRATION_KEYS: dict[str, list[str]] = {
    "overview": ["google_ads", "meta", "gsc", "ga4", "crm"],
    "google_ads": ["google_ads"],
    "meta": ["meta"],
    "seo": ["gsc", "ga4"],
    "direct": ["ga4"],
    "other": ["ga4"],
    "business": ["google_ads", "meta", "gsc", "ga4"],
    "insights": ["google_ads", "meta", "gsc", "ga4"],
    "details": ["crm"],
}

INTEGRATION_FIELDS: dict[str, list[dict[str, Any]]] = {
    "google_ads": [
        {"env": "GOOGLE_ADS_DEVELOPER_TOKEN", "label": "Developer Token", "secret": True},
        {"env": "GOOGLE_ADS_CLIENT_ID", "label": "Client ID", "secret": False},
        {"env": "GOOGLE_ADS_CLIENT_SECRET", "label": "Client Secret", "secret": True},
        {"env": "GOOGLE_ADS_REFRESH_TOKEN", "label": "Refresh Token", "secret": True},
        {"env": "GOOGLE_ADS_CUSTOMER_ID", "label": "Customer ID", "secret": False},
    ],
    "meta": [
        {"env": "META_ADS_ACCESS_TOKEN", "label": "Access Token", "secret": True},
        {"env": "META_AD_ACCOUNT_ID", "label": "Ad Account ID", "secret": False},
        {"env": "META_ADS_API_VERSION", "label": "API Version", "secret": False},
    ],
    "gsc": [
        {"env": "GSC_SITE_URL", "label": "URL property GSC (cosmy.ua; кілька через кому)", "secret": False},
        {"env": "GSC_SITE_URL_EXTRA", "label": "Додаткові URL GSC", "secret": False},
        {"env": "GSC_MERGE_STOREFRONT_DOMAINS", "label": "Об’єднувати com.ua + ua (true/false)", "secret": False},
        {"env": "GSC_SERVICE_ACCOUNT_FILE", "label": "Service Account (шлях)", "secret": False},
        {"env": "_GSC_SERVICE_ACCOUNT_JSON", "label": "Service Account JSON", "secret": True, "type": "service_account"},
    ],
    "ga4": [
        {"env": "GA4_PROPERTY_ID", "label": "Property ID", "secret": False},
        {"env": "GA4_SERVICE_ACCOUNT_FILE", "label": "Service Account (шлях, опційно)", "secret": False},
        {"env": "_GA4_SERVICE_ACCOUNT_JSON", "label": "Service Account JSON", "secret": True, "type": "service_account"},
    ],
}

INTEGRATION_LABELS = {
    "google_ads": "Google Ads",
    "meta": "Meta Ads",
    "gsc": "Google Search Console",
    "ga4": "Google Analytics 4",
    "crm": "CRM Cosmy",
}

GOOGLE_ADS_AUTH_HELP = {
    "summary": (
        "Помилка invalid_grant означає, що refresh token недійсний (не access token — він оновлюється автоматично). "
        "Типова причина: OAuth-застосунок у Google Cloud у статусі Testing (refresh token живе ~7 днів), "
        "або доступ відкликано / змінено пароль Google-акаунта."
    ),
    "steps": [
        "Google Cloud Console → APIs & Services → OAuth consent screen → перевірте Publishing status.",
        "Якщо Testing: додайте email у Test users АБО переведіть застосунок у Production (для scope adwords може знадобитись verification).",
        "Перевірте myaccount.google.com/permissions — чи не відкликано доступ застосунку.",
        "Отримайте новий refresh token: запустіть <code>python scripts/google_ads_oauth_local.py</code> (localhost) або кнопку «Підключити через Google» у налаштуваннях.",
        "Після підключення дані Google Ads оновляться автоматично.",
    ],
    "links": [
        {"label": "OAuth consent screen", "url": "https://console.cloud.google.com/apis/credentials/consent"},
        {"label": "OAuth 2.0 Playground", "url": "https://developers.google.com/oauthplayground"},
        {"label": "Дозволи Google-акаунта", "url": "https://myaccount.google.com/permissions"},
    ],
}


def google_ads_auth_help() -> dict:
    """Інструкція з підстановкою публічного URL для OAuth consent screen."""
    from config import WEB_PUBLIC_BASE_URL

    base = WEB_PUBLIC_BASE_URL or "https://your-domain.example.com"
    help = dict(GOOGLE_ADS_AUTH_HELP)
    help["links"] = list(GOOGLE_ADS_AUTH_HELP["links"]) + [
        {"label": "Головна застосунку (OAuth)", "url": f"{base}/legal"},
        {"label": "Політика конфіденційності", "url": f"{base}/privacy"},
        {"label": "Умови використання", "url": f"{base}/terms"},
    ]
    return help


def mask_secret(value: str | None, *, visible: int = 4) -> str:
    text = (value or "").strip()
    if not text:
        return "—"
    if len(text) <= visible * 2:
        return "•" * len(text)
    return f"{'•' * 8}{text[-visible:]}"


def classify_integration_status(*, configured: bool, error: str | None) -> str:
    """ok | auth_error | api_error | off"""
    if not configured:
        return "off"
    if not error:
        return "ok"
    err = error.lower()
    if "не налаштовано" in err or "marketing_config" in err:
        return "off"
    if any(marker in err for marker in AUTH_ERROR_MARKERS):
        return "auth_error"
    return "api_error"


def _config_value(env_key: str) -> str:
    from marketing_config import load_marketing_config

    return load_marketing_config().get(env_key, "")


def _service_account_path(integration_key: str) -> str:
    if integration_key == "ga4":
        return ga4_service_account_file()
    from marketing_config import load_marketing_config

    from settings.paths import DEFAULT_GSC_SERVICE_ACCOUNT_REL

    return load_marketing_config().get("GSC_SERVICE_ACCOUNT_FILE", DEFAULT_GSC_SERVICE_ACCOUNT_REL)


def _service_account_exists(path: str) -> bool:
    return bool(path and os.path.isfile(path))


def _integration_configured(key: str) -> bool:
    if key == "google_ads":
        return google_ads_configured()
    if key == "meta":
        return meta_ads_configured()
    if key == "gsc":
        return gsc_configured()
    if key == "ga4":
        return ga4_configured()
    if key == "crm":
        return True
    return False


def build_integration_row(
    key: str,
    *,
    error: str | None = None,
    cache_updated: str | None = None,
) -> dict[str, Any]:
    configured = _integration_configured(key)
    status = classify_integration_status(configured=configured, error=error)
    return {
        "key": key,
        "label": INTEGRATION_LABELS.get(key, key),
        "configured": configured,
        "ok": status == "ok",
        "status": status,
        "error": error,
        "updated": cache_updated,
    }


def build_integration_settings_detail(
    key: str,
    *,
    panel_row: dict[str, Any] | None = None,
    public_base: str | None = None,
) -> dict[str, Any]:
    """Дані для модалки налаштувань однієї інтеграції."""
    panel_row = panel_row or {}
    status = panel_row.get("status") or classify_integration_status(
        configured=_integration_configured(key),
        error=panel_row.get("error"),
    )
    fields_out: list[dict[str, Any]] = []

    if key == "crm":
        return {
            "key": key,
            "label": INTEGRATION_LABELS["crm"],
            "status": "ok",
            "configured": True,
            "error": None,
            "updated": panel_row.get("updated"),
            "fields": [],
            "note": "CRM підключається через синхронізацію з Cosmy (фід + API). API-ключів немає — натисніть «CRM» у панелі інструментів.",
            "editable": False,
        }

    for field in INTEGRATION_FIELDS.get(key, []):
        env_key = field["env"]
        if field.get("type") == "service_account":
            sa_path = _service_account_path(key)
            fields_out.append({
                "env": env_key,
                "label": field["label"],
                "secret": True,
                "type": "service_account",
                "path": sa_path,
                "preview": mask_secret(sa_path) if sa_path else "—",
                "file_exists": _service_account_exists(sa_path),
                "placeholder": "Вставте JSON service account, щоб замінити файл",
            })
            continue
        raw = _config_value(env_key)
        fields_out.append({
            "env": env_key,
            "label": field["label"],
            "secret": bool(field.get("secret")),
            "type": "env",
            "preview": mask_secret(raw) if field.get("secret") else (raw or "—"),
            "has_value": bool(raw),
            "placeholder": "Залиште порожнім, щоб не змінювати",
        })

    result = {
        "key": key,
        "label": INTEGRATION_LABELS.get(key, key),
        "status": status,
        "configured": _integration_configured(key),
        "error": panel_row.get("error"),
        "updated": panel_row.get("updated"),
        "fields": fields_out,
        "editable": True,
        "env_file": str(MARKETING_CONFIG_PATH),
        "auth_help": google_ads_auth_help() if key == "google_ads" and status == "auth_error" else None,
    }

    if key == "google_ads":
        from marketing_config import load_marketing_config
        from web.integrations.google_ads_oauth import oauth_prerequisites_ok, resolve_oauth_redirect_uri

        m = load_marketing_config()
        redirect = resolve_oauth_redirect_uri(request_base=public_base or WEB_PUBLIC_BASE_URL or "")
        client_id = (m.get("GOOGLE_ADS_CLIENT_ID") or "").strip()
        client_hint = ""
        if client_id:
            client_hint = f"{client_id[:20]}…{client_id[-30:]}" if len(client_id) > 55 else client_id
        result["oauth"] = {
            "available": oauth_prerequisites_ok(),
            "connect_url": "/mkt-report/oauth/google-ads/start",
            "redirect_uri": redirect,
            "javascript_origin": redirect.rsplit("/", 1)[0] if redirect else None,
            "client_id_hint": client_hint,
            "needs_public_base": not bool(
                public_base or WEB_PUBLIC_BASE_URL or m.get("GOOGLE_ADS_OAUTH_REDIRECT_URI")
            ),
            "google_credentials_url": "https://console.cloud.google.com/apis/credentials",
        }
        from web.integrations.google_ads_oauth_local import local_oauth_meta

        result["oauth"]["local"] = local_oauth_meta()

    return result


def build_section_integration_settings(
    section: str,
    panel_rows: list[dict[str, Any]],
    *,
    public_base: str | None = None,
) -> dict[str, Any]:
    keys = SECTION_INTEGRATION_KEYS.get(section, [])
    by_key = {row["key"]: row for row in panel_rows}
    integrations = [
        build_integration_settings_detail(k, panel_row=by_key.get(k, {}), public_base=public_base)
        for k in keys
    ]
    alerts: list[dict[str, str]] = []
    has_auth_issue = False
    for item in integrations:
        st = item.get("status")
        if st == "auth_error":
            has_auth_issue = True
            msg = item.get("error") or "Токен або ключ прострочений."
            if item.get("key") == "google_ads" and msg:
                low = msg.lower()
                if "invalid_grant" in low:
                    msg = "Refresh token недійсний (invalid_grant). Оновіть GOOGLE_ADS_REFRESH_TOKEN у marketing_config.json."
                elif "developer token" in low or "developer_token" in low:
                    msg = (
                        "Developer Token недійсний. Оновіть GOOGLE_ADS_DEVELOPER_TOKEN у marketing_config.json "
                        "(Google Ads → Tools → API Center)."
                    )
            alerts.append({
                "key": item["key"],
                "label": item["label"],
                "message": msg,
                "severity": "auth_error",
            })
        elif st == "off" and item["key"] != "crm":
            alerts.append({
                "key": item["key"],
                "label": item["label"],
                "message": "Інтеграція не налаштована — додайте ключі в marketing_config.json.",
                "severity": "off",
            })
    return {
        "integrations": integrations,
        "alerts": alerts,
        "has_auth_issue": has_auth_issue,
        "has_issues": bool(alerts),
        "settings_ui_enabled": INTEGRATION_SETTINGS_UI_ENABLED,
    }


def _resolve_service_account_target(integration_key: str, explicit_path: str | None = None) -> Path:
    from settings.paths import DEFAULT_GSC_SERVICE_ACCOUNT_REL

    rel = (
        explicit_path or _service_account_path(integration_key) or DEFAULT_GSC_SERVICE_ACCOUNT_REL
    ).strip()
    target = Path(rel)
    if not target.is_absolute():
        target = Path(ROOT_DIR) / target
    target = target.resolve()
    root = Path(ROOT_DIR).resolve()
    if root not in target.parents and target != root:
        raise ValueError("Недозволений шлях до service account файлу")
    return target


def update_integration_credentials(integration_key: str, values: dict[str, str]) -> tuple[list[str], str | None]:
    """Оновлює marketing_config.json та service account. Повертає (список полів, перевірка)."""
    if integration_key not in INTEGRATION_FIELDS and integration_key != "crm":
        raise ValueError(f"Невідома інтеграція: {integration_key}")
    if integration_key == "crm":
        raise ValueError("CRM не має API-ключів для оновлення")

    old_values: dict[str, str] = {}
    if integration_key == "google_ads":
        for field in INTEGRATION_FIELDS.get(integration_key, []):
            env_key = field["env"]
            if env_key in GOOGLE_ADS_VERIFY_ENV_KEYS and (values.get(env_key) or "").strip():
                old_values[env_key] = _config_value(env_key)

    updated: list[str] = []
    updated_env_keys: list[str] = []
    for field in INTEGRATION_FIELDS.get(integration_key, []):
        env_key = field["env"]
        raw = (values.get(env_key) or "").strip()
        if not raw:
            continue
        if field.get("type") == "service_account":
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Невалідний JSON service account: {exc}") from exc
            if not isinstance(parsed, dict) or "client_email" not in parsed:
                raise ValueError("JSON service account має містити client_email")
            path_hint = (values.get("GSC_SERVICE_ACCOUNT_FILE") or values.get("GA4_SERVICE_ACCOUNT_FILE") or "").strip()
            target = _resolve_service_account_target(integration_key, path_hint or None)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
            updated.append(field["label"])
            continue
        set_marketing_value(env_key, raw)
        updated_env_keys.append(env_key)
        updated.append(field["label"])

    reload_marketing_config()

    verified_msg: str | None = None
    if integration_key == "google_ads" and any(k in GOOGLE_ADS_VERIFY_ENV_KEYS for k in updated_env_keys):
        from web.integrations.google_ads import verify_google_ads_credentials

        try:
            verified_msg = verify_google_ads_credentials()
        except ValueError:
            for env_key in updated_env_keys:
                if env_key in old_values:
                    set_marketing_value(env_key, old_values[env_key])
            reload_marketing_config()
            raise

    return updated, verified_msg
