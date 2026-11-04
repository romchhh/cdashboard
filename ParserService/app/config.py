"""Конфіг worker-а парсингу (без бізнес-БД основного софта).

Секрети й мережа — з .env. Ліміти памʼяті/паралелізму — тільки тут.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_HERE = Path(__file__).resolve().parent
_SERVICE_ROOT = _HERE.parent  # ParserService/
_CANDIDATE_ROOTS = [
    Path(os.getenv("PARSER_PROJECT_ROOT", "")).expanduser() if os.getenv("PARSER_PROJECT_ROOT") else None,
    _SERVICE_ROOT / "runtime",
    _SERVICE_ROOT.parent,  # CosmyFinancesBot monorepo
]
PROJECT_ROOT = next(
    (p for p in _CANDIDATE_ROOTS if p and (p / "cosmyparser").is_dir()),
    _SERVICE_ROOT.parent,
)

load_dotenv(PROJECT_ROOT / ".env", override=False)
load_dotenv(_SERVICE_ROOT / ".env", override=True)

HOST = os.getenv("PARSER_HOST", "0.0.0.0")
PORT = int(os.getenv("PARSER_PORT", "8091"))
API_KEY = (os.getenv("PARSER_API_KEY") or "").strip()
REQUIRE_API_KEY = os.getenv("PARSER_REQUIRE_API_KEY", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)

# --- Ліміти проти OOM (Linux "Killed") / nginx 502. Міняти тут, не в .env. ---
# Catalog: N товарів паралельно; кожен товар → до MAX_SITE_WORKERS магазинів.
MAX_SKU_WORKERS = 2
MAX_SITE_WORKERS = 4
MAX_RUNNING_JOBS = 1
# Чистити HTTP/listing кеш кожні N готових товарів.
CACHE_CLEAR_EVERY = 5
# Скільки завершених job тримати в RAM (рядки звіту важкі).
MAX_JOBS_KEPT = 4
# TTL завершеного job (сек) — потім викидаємо з памʼяті.
JOB_TTL_SEC = 45 * 60
# Чекати вільний scrape-gate (сек), замість миттєвого 409.
SCRAPE_GATE_WAIT_SEC = 120
# Макс. товарів у одному catalog-запиті (захист від гігантського payload).
MAX_CATALOG_PRODUCTS = 500
# Watchdog: пауза перед рестартом після падіння.
RESPAWN_DELAY_SEC = 3

WORK_DIR = Path(
    os.getenv("PARSER_WORK_DIR", str(_SERVICE_ROOT / "work"))
).expanduser()
WORK_DIR.mkdir(parents=True, exist_ok=True)
JOBS_DIR = WORK_DIR / "jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)
