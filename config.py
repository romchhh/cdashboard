# Єдиний файл конфігурації проєкту

import os
from os import getenv
from dotenv import load_dotenv

load_dotenv()

# --- Telegram бот ---
BOT_TOKEN = getenv("TELEGRAM_BOT_TOKEN")
_admin_raw = getenv("ADMINISTRATORS", "")
administrators = [int(x.strip()) for x in _admin_raw.strip("[]").split(",") if x.strip()]

# --- База даних ---
DATABASE_URL = getenv("DATABASE_URL", "sqlite+aiosqlite:///./databases/finance_bot.db")
# Окрема БД для продуктів Cosmy
PRODUCTS_DATABASE_URL = getenv("PRODUCTS_DATABASE_URL", "sqlite+aiosqlite:///./databases/products.db")
# Окрема БД для клієнтів / замовлень Cosmy (фід analytics_customer.xml): clients, client_orders, client_order_lines
CLIENTS_DATABASE_URL = getenv(
    "CLIENTS_DATABASE_URL",
    "sqlite+aiosqlite:///./databases/cosmy_clients.db",
)
# Резервні копії SQLite (локально + Telegram адмінам)
DB_BACKUP_DIR = getenv("DB_BACKUP_DIR", "./databases/backups")
DB_BACKUP_KEEP_COUNT = int(getenv("DB_BACKUP_KEEP_COUNT", "5"))
DB_BACKUP_TELEGRAM_MAX_BYTES = int(
    getenv("DB_BACKUP_TELEGRAM_MAX_BYTES", str(48 * 1024 * 1024)),
)

# --- Monobank / NovaPay (runtime-кеш; заповнюється з БД при старті) ---
BASE_URL_MONO = "https://api.monobank.ua"

MONO_TOKENS: dict[str, str] = {}
FOP_IBANS: dict[str, str] = {}
NOVAPAY_TOKENS: dict[str, dict] = {}

# ФОПи, відключені від агента — не показувати баланси, не синхронізувати
INACTIVE_FOP_NAMES: frozenset[str] = frozenset(
    {
        "Грабовський Артем",
        "Рудь Єлизавета",
    }
)

# Річний ліміт надходжень ФОП 3 групи (грн). Можна перевизначити в .env.
# Банківські кредити YTD, не податкова консультація.
try:
    FOP_ANNUAL_INCOME_LIMIT_UAH = float(getenv("FOP_ANNUAL_INCOME_LIMIT_UAH", "7211598") or "7211598")
except ValueError:
    FOP_ANNUAL_INCOME_LIMIT_UAH = 7_211_598.0


def fop_config_sources_by_name() -> dict[str, list[str]]:
    """
    Джерела ФОП з runtime-кешу (БД): name → ['novapay' and/or 'monobank'].
    """
    out: dict[str, list[str]] = {}

    def _add(name: str, source: str) -> None:
        key = (name or "").strip()
        if not key or key in INACTIVE_FOP_NAMES:
            return
        bucket = out.setdefault(key, [])
        if source not in bucket:
            bucket.append(source)

    for name in MONO_TOKENS:
        _add(name, "monobank")
    for name in FOP_IBANS.values():
        _add(name, "monobank")
    for name in NOVAPAY_TOKENS:
        _add(name, "novapay")
    return out


def configured_fop_names() -> list[str]:
    """Усі імена ФОП з активного runtime-кешу (з БД)."""
    return sorted(fop_config_sources_by_name().keys(), key=lambda s: s.casefold())


# --- NovaPay ---
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE_PATH = os.path.join(ROOT_DIR, ".env")
SESSIONS_DIR = os.path.join(ROOT_DIR, "sessions")
SESSION_SUFFIXES_FILE = os.path.join(SESSIONS_DIR, "suffixes.json")
SESSION_SUFFIX: dict[str, str] = {}
NOVAPAY_USER_KEY = "Єльникова"


def _load_session_suffix_overrides() -> None:
    """Підвантажити suffix з sessions/suffixes.json (legacy + нові ФОП з БД)."""
    import json

    if os.path.isfile(SESSION_SUFFIXES_FILE):
        try:
            with open(SESSION_SUFFIXES_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for name, suffix in data.items():
                    if name and suffix:
                        SESSION_SUFFIX[str(name).strip()] = str(suffix).strip()
        except Exception:
            pass


_load_session_suffix_overrides()


def _persist_session_suffixes() -> None:
    import json

    os.makedirs(SESSIONS_DIR, exist_ok=True)
    with open(SESSION_SUFFIXES_FILE, "w", encoding="utf-8") as f:
        json.dump(SESSION_SUFFIX, f, ensure_ascii=False, indent=2)


def _slug_session_suffix(name: str) -> str:
    """Короткий унікальний suffix для файлу сесії."""
    mapping = {
        "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e",
        "є": "ye", "ж": "zh", "з": "z", "и": "y", "і": "i", "ї": "yi", "й": "y",
        "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
        "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch",
        "ш": "sh", "щ": "shch", "ь": "", "ю": "yu", "я": "ya",
        "А": "a", "Б": "b", "В": "v", "Г": "h", "Ґ": "g", "Д": "d", "Е": "e",
        "Є": "ye", "Ж": "zh", "З": "z", "И": "y", "І": "i", "Ї": "yi", "Й": "y",
        "К": "k", "Л": "l", "М": "m", "Н": "n", "О": "o", "П": "p", "Р": "r",
        "С": "s", "Т": "t", "У": "u", "Ф": "f", "Х": "kh", "Ц": "ts", "Ч": "ch",
        "Ш": "sh", "Щ": "shch", "Ь": "", "Ю": "yu", "Я": "ya",
    }
    raw = "".join(mapping.get(ch, ch if ch.isascii() and ch.isalnum() else "") for ch in (name or ""))
    raw = raw.lower().strip() or "fop"
    return raw[:8]


def ensure_session_suffix(name: str) -> str:
    """Повернути (і за потреби створити) suffix для NovaPay-сесії ФОП."""
    key = (name or "").strip()
    if not key:
        return "default"
    if key in SESSION_SUFFIX:
        return SESSION_SUFFIX[key]
    base = _slug_session_suffix(key)
    used = set(SESSION_SUFFIX.values())
    suffix = base
    n = 2
    while suffix in used or suffix == "default":
        suffix = f"{base}{n}"[:12]
        n += 1
    SESSION_SUFFIX[key] = suffix
    _persist_session_suffixes()
    return suffix


def get_session_file(user_key=None):
    """Шлях до файлу сесії NovaPay для user_key."""
    key = user_key if user_key is not None else NOVAPAY_USER_KEY
    key = (key or "").strip()
    suffix = SESSION_SUFFIX.get(key)
    if not suffix:
        suffix = ensure_session_suffix(key) if key else "default"
    return os.path.join(SESSIONS_DIR, f"novapay_session_{suffix}.json")


def mono_iban_for_fop(name: str) -> str | None:
    """IBAN Monobank для ФОП (з runtime-кешу БД)."""
    key = (name or "").strip()
    if not key:
        return None
    for iban, n in FOP_IBANS.items():
        if (n or "").strip() == key:
            return iban
    return None


def reload_fop_runtime_config() -> None:
    """Legacy: runtime оновлюється через bot.services.fop_config.refresh_fop_runtime."""
    pass


def _env_set(key: str, value: str) -> None:
    from dotenv import set_key

    os.environ[key] = value
    if os.path.isfile(ENV_FILE_PATH) or os.path.isdir(os.path.dirname(ENV_FILE_PATH)):
        set_key(ENV_FILE_PATH, key, value, quote_mode="always")


def _env_unset(key: str) -> None:
    from dotenv import unset_key

    os.environ.pop(key, None)
    if os.path.isfile(ENV_FILE_PATH):
        unset_key(ENV_FILE_PATH, key)


WSDL_URL = "https://business.novapay.ua/Services/ClientAPIService.svc?wsdl"
SESSION_VALIDITY_HOURS = 24
REFRESH_BEFORE_HOURS = 2
LOG_API_RESPONSES = True

# --- Веб-апка (FastAPI) ---
WEB_USERNAME = getenv("WEB_USERNAME", "admin")
WEB_PASSWORD = getenv("WEB_PASSWORD", "admin")  # Обовʼязково змініть у .env
WEB_MANAGER_USERNAME = getenv("WEB_MANAGER_USERNAME", "manager")
WEB_MANAGER_PASSWORD = getenv("WEB_MANAGER_PASSWORD", "manager")
WEB_SECRET_KEY = getenv("WEB_SECRET_KEY", "change-me-in-production")
# True = cookie тільки по HTTPS (рекомендовано для продакшену). False = для локального HTTP.
WEB_SECURE_COOKIE = getenv("WEB_SECURE_COOKIE", "false").lower() in ("1", "true", "yes")
# Дозволені Host для доступу. Якщо задано — доступ тільки за цим доменом (не за IP).
# Кілька значень через кому, напр.: "fin.example.com" або "fin.example.com,www.fin.example.com"
_WEB_ALLOWED_RAW = getenv("WEB_ALLOWED_HOSTS", "").strip()
WEB_ALLOWED_HOSTS: tuple[str, ...] = tuple(
    h.strip().lower() for h in _WEB_ALLOWED_RAW.split(",") if h.strip()
)

# Перевірка сильного секрету при запуску веб-апки (викликати з main.py)
def check_web_secret_key():
    """Попередження або виняток, якщо WEB_SECRET_KEY слабкий (для продакшену)."""
    if not WEB_SECRET_KEY or WEB_SECRET_KEY == "change-me-in-production":
        if os.getenv("ENV", "development") == "production":
            raise ValueError(
                "WEB_SECRET_KEY має бути встановлений у .env і відрізнятися від 'change-me-in-production'. "
                "Згенеруйте: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
            )
    if len(WEB_SECRET_KEY) < 32:
        import logging
        logging.getLogger(__name__).warning(
            "WEB_SECRET_KEY занадто короткий (мін. 32 символи). Рекомендовано згенерувати сильний ключ."
        )
    if os.getenv("ENV", "development").strip().lower() == "production":
        import logging

        log = logging.getLogger(__name__)
        if not MARKETING_API_KEY:
            log.warning("MARKETING_API_KEY не задано — API розсилок буде відхиляти запити в production.")
        if not WEB_PUBLIC_BASE_URL:
            log.warning("WEB_PUBLIC_BASE_URL не задано — посилання відписки в листах можуть бути некоректними.")
        if not WEB_ALLOWED_HOSTS:
            log.warning("WEB_ALLOWED_HOSTS порожній — рекомендовано обмежити Host (напр. cosmyfinances.telebots.site).")

# --- Бот: сповіщення та бюджети ---
DAILY_REPORT_TIME = "09:00"
LARGE_TRANSACTION_THRESHOLD = 50000

# --- Фід клієнтів Cosmy (OpenCart analytics) ---
COSMY_CUSTOMERS_FEED_URL = getenv(
    "COSMY_CUSTOMERS_FEED_URL",
    "https://cosmy.com.ua/price/analytics_customer.xml",
)
# customer_id з фіду — чорний список: не в експортах, не в списку клієнтів у вебі,
# виключені з маркетингового дашборду та зведених KPI клієнтів на головному дашборді (разом із їхніми замовленнями).
CLIENTS_EXPORT_EXCLUDED_CUSTOMER_IDS: frozenset[str] = frozenset(
    {
        "phone_f00bff27d6d6cb8735bc153340d938cc548ab88",
        "phone_f00bff27d6d6cb8735bc153340d938cc548ab881",
        "phone_96d20a1bd60878c47f8929c1e6dd25b43b0ba6d1",
        "phone_b9bcff8807e009deae8821c2943cd3d79cd277e2",
        "phone_36a71ab58f9545fb2160c1d813ac87ed609b702e",
        "phone_376be6ad2e297b7d7032b3d9c18fc13f89c174c6",
    }
)
# Опційно: публічний URL веб-панелі для посилання з Telegram («Клієнти»), напр. https://fin.example.com
WEB_PUBLIC_BASE_URL = getenv("WEB_PUBLIC_BASE_URL", "").strip().rstrip("/")
# Публічні юридичні сторінки (Google OAuth consent screen)
LEGAL_APP_NAME = getenv("LEGAL_APP_NAME", "Cosmy Agent").strip() or "Cosmy Agent"
LEGAL_COMPANY_NAME = getenv("LEGAL_COMPANY_NAME", "Cosmy").strip() or "Cosmy"
LEGAL_CONTACT_EMAIL = getenv("LEGAL_CONTACT_EMAIL", "support@cosmy.com.ua").strip() or "support@cosmy.com.ua"
# API-ключ для публічних email API (welcome, order, abandoned-cart, abandoned-payment). Якщо порожній — без перевірки ключа.
MARKETING_API_KEY = getenv("MARKETING_API_KEY", "").strip()

# --- Парсер цін: scrape-worker на іншому сервері (опційно) ---
# У .env лише URL і ключ. Таймаути/ретраї — константи нижче.
PARSER_SERVICE_URL = getenv("PARSER_SERVICE_URL", "").strip().rstrip("/")
# Другий парсер на цій машині (ParserService). Порожньо = вимкнено.
# Разом із PARSER_SERVICE_URL можна вести два бренди одночасно: віддалений API і локальний.
PARSER_LOCAL_URL = getenv("PARSER_LOCAL_URL", "http://127.0.0.1:8091").strip().rstrip("/")
PARSER_API_KEY = getenv("PARSER_API_KEY", "").strip()
# Старт job / health (сек).
PARSER_SERVICE_TIMEOUT = 45.0
# Макс. очікування одного catalog/search job (сек).
PARSER_JOB_MAX_WAIT = 7200.0
# Один poll HTTP timeout — короткий, щоб не вбити job через «заїкання» worker під scrape.
PARSER_POLL_TIMEOUT = 15.0
# Скільки timeout/502 поспіль терпимо, перш ніж здатися (job на worker ще може жити).
PARSER_POLL_MAX_STREAK = 20
# Якщо worker заданий — не падати на локальний scrape (валить веб-процес).
PARSER_FALLBACK_LOCAL = False

# --- Resend для email-розсилок ---
RESEND_API_BASE = getenv("RESEND_API_BASE", "https://api.resend.com").strip()
RESEND_API_KEY = getenv("RESEND_API_KEY", "").strip()
RESEND_FROM_EMAIL = getenv("RESEND_FROM_EMAIL", "").strip()
RESEND_FROM_NAME = getenv("RESEND_FROM_NAME", "Cosmy")
# Транзакційні листи (замовлення, кошик, оплата). Якщо порожньо — RESEND_FROM_EMAIL.
RESEND_FROM_ORDERS = getenv("RESEND_FROM_ORDERS", "").strip() or RESEND_FROM_EMAIL
# Reply-To для всіх листів (напр. support@cosmy.com.ua).
RESEND_REPLY_TO = getenv("RESEND_REPLY_TO", "").strip()
# URL відписки для масових розсилок (шаблон, не в .env).
# Плейсхолдери: {base} — WEB_PUBLIC_BASE_URL, {token} — підписаний токен, {email} — адреса.
EMAIL_UNSUBSCRIBE_URL = "{base}/index.php?route=promo/unsubscribe&t={token}"
RESEND_USER_AGENT = getenv("RESEND_USER_AGENT", "cosmy-finances-bot/1.0").strip()

# --- Маркетинг: Google Ads, Meta Ads, Search Console (marketing_config.json, не .env) ---
from marketing_config import (
    MARKETING_CONFIG_PATH,
    MARKETING_ENV_KEYS,
    ensure_marketing_config,
    load_marketing_config,
    set_marketing_value,
)

_marketing_cfg = ensure_marketing_config()


def _mget(key: str, default: str = "") -> str:
    return load_marketing_config().get(key, default)


GOOGLE_ADS_DEVELOPER_TOKEN = _mget("GOOGLE_ADS_DEVELOPER_TOKEN")
GOOGLE_ADS_CLIENT_ID = _mget("GOOGLE_ADS_CLIENT_ID")
GOOGLE_ADS_CLIENT_SECRET = _mget("GOOGLE_ADS_CLIENT_SECRET")
GOOGLE_ADS_REFRESH_TOKEN = _mget("GOOGLE_ADS_REFRESH_TOKEN")
GOOGLE_ADS_CUSTOMER_ID = _mget("GOOGLE_ADS_CUSTOMER_ID")
GOOGLE_ADS_OAUTH_REDIRECT_URI = _mget("GOOGLE_ADS_OAUTH_REDIRECT_URI")
GOOGLE_ADS_OAUTH_LOCAL_REDIRECT_URI = _mget(
    "GOOGLE_ADS_OAUTH_LOCAL_REDIRECT_URI", "http://localhost:8765"
)
GOOGLE_ADS_OAUTH_LOCAL_PORT = int(_mget("GOOGLE_ADS_OAUTH_LOCAL_PORT", "8765") or "8765")

META_ADS_ACCESS_TOKEN = _mget("META_ADS_ACCESS_TOKEN")
META_AD_ACCOUNT_ID = _mget("META_AD_ACCOUNT_ID")
META_ADS_API_VERSION = _mget("META_ADS_API_VERSION", "v21.0")

from settings.paths import DEFAULT_GSC_SERVICE_ACCOUNT_REL, resolve_project_path

GSC_SERVICE_ACCOUNT_FILE = _mget("GSC_SERVICE_ACCOUNT_FILE", DEFAULT_GSC_SERVICE_ACCOUNT_REL)
GSC_SITE_URL = _mget("GSC_SITE_URL", "https://cosmy.ua")

GA4_PROPERTY_ID = _mget("GA4_PROPERTY_ID")
GA4_SERVICE_ACCOUNT_FILE = _mget("GA4_SERVICE_ACCOUNT_FILE")


def ga4_service_account_file() -> str:
    m = load_marketing_config()
    rel = m.get("GA4_SERVICE_ACCOUNT_FILE") or m.get(
        "GSC_SERVICE_ACCOUNT_FILE", DEFAULT_GSC_SERVICE_ACCOUNT_REL
    )
    return str(resolve_project_path(rel))


def ga4_configured() -> bool:
    from pathlib import Path

    m = load_marketing_config()
    return bool(m.get("GA4_PROPERTY_ID") and Path(ga4_service_account_file()).is_file())


def google_ads_configured() -> bool:
    m = load_marketing_config()
    return bool(
        m.get("GOOGLE_ADS_DEVELOPER_TOKEN")
        and m.get("GOOGLE_ADS_CLIENT_ID")
        and m.get("GOOGLE_ADS_CLIENT_SECRET")
        and m.get("GOOGLE_ADS_REFRESH_TOKEN")
        and m.get("GOOGLE_ADS_CUSTOMER_ID")
    )


def meta_ads_configured() -> bool:
    m = load_marketing_config()
    return bool(m.get("META_ADS_ACCESS_TOKEN") and m.get("META_AD_ACCOUNT_ID"))


def gsc_configured() -> bool:
    m = load_marketing_config()
    sa_file = resolve_project_path(m.get("GSC_SERVICE_ACCOUNT_FILE"))
    return bool(m.get("GSC_SITE_URL") and sa_file.is_file())


# Тимчасово приховано UI редагування ключів (ключі лише з marketing_config.json на сервері).
INTEGRATION_SETTINGS_UI_ENABLED = getenv("INTEGRATION_SETTINGS_UI_ENABLED", "0").lower() in (
    "1",
    "true",
    "yes",
)


def set_env_variable(key: str, value: str) -> None:
    """Оновлення змінної: маркетинг — у marketing_config.json, інше — у .env."""
    if key in MARKETING_ENV_KEYS:
        set_marketing_value(key, value.strip())
        reload_marketing_config()
        return
    _env_set(key, value.strip())


def reload_marketing_config() -> None:
    """Перечитати маркетингові ключі з marketing_config.json."""
    global GOOGLE_ADS_DEVELOPER_TOKEN, GOOGLE_ADS_CLIENT_ID, GOOGLE_ADS_CLIENT_SECRET
    global GOOGLE_ADS_REFRESH_TOKEN, GOOGLE_ADS_CUSTOMER_ID
    global GOOGLE_ADS_OAUTH_REDIRECT_URI, GOOGLE_ADS_OAUTH_LOCAL_REDIRECT_URI, GOOGLE_ADS_OAUTH_LOCAL_PORT
    global META_ADS_ACCESS_TOKEN, META_AD_ACCOUNT_ID, META_ADS_API_VERSION
    global GSC_SERVICE_ACCOUNT_FILE, GSC_SITE_URL, GA4_PROPERTY_ID, GA4_SERVICE_ACCOUNT_FILE

    m = load_marketing_config()
    GOOGLE_ADS_DEVELOPER_TOKEN = m.get("GOOGLE_ADS_DEVELOPER_TOKEN", "")
    GOOGLE_ADS_CLIENT_ID = m.get("GOOGLE_ADS_CLIENT_ID", "")
    GOOGLE_ADS_CLIENT_SECRET = m.get("GOOGLE_ADS_CLIENT_SECRET", "")
    GOOGLE_ADS_REFRESH_TOKEN = m.get("GOOGLE_ADS_REFRESH_TOKEN", "")
    GOOGLE_ADS_CUSTOMER_ID = m.get("GOOGLE_ADS_CUSTOMER_ID", "")
    GOOGLE_ADS_OAUTH_REDIRECT_URI = m.get("GOOGLE_ADS_OAUTH_REDIRECT_URI", "")
    GOOGLE_ADS_OAUTH_LOCAL_REDIRECT_URI = m.get(
        "GOOGLE_ADS_OAUTH_LOCAL_REDIRECT_URI", "http://localhost:8765"
    )
    GOOGLE_ADS_OAUTH_LOCAL_PORT = int(m.get("GOOGLE_ADS_OAUTH_LOCAL_PORT", "8765") or "8765")
    META_ADS_ACCESS_TOKEN = m.get("META_ADS_ACCESS_TOKEN", "")
    META_AD_ACCOUNT_ID = m.get("META_AD_ACCOUNT_ID", "")
    META_ADS_API_VERSION = m.get("META_ADS_API_VERSION", "v21.0")
    GSC_SERVICE_ACCOUNT_FILE = m.get("GSC_SERVICE_ACCOUNT_FILE", DEFAULT_GSC_SERVICE_ACCOUNT_REL)
    GSC_SITE_URL = m.get("GSC_SITE_URL", "https://cosmy.ua")
    GA4_PROPERTY_ID = m.get("GA4_PROPERTY_ID", "")
    GA4_SERVICE_ACCOUNT_FILE = m.get("GA4_SERVICE_ACCOUNT_FILE", "")


def refresh_marketing_config() -> dict[str, str]:
    """Перечитати marketing_config.json у runtime (після deploy або зміни файлу)."""
    from marketing_config import apply_marketing_to_environ

    reload_marketing_config()
    data = load_marketing_config()
    apply_marketing_to_environ(data)
    return data