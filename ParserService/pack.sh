#!/usr/bin/env bash
# Збирає переносимий scrape-worker: ParserService + runtime (cosmyparser + мінімум web stubs).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SVC="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:-$SVC/runtime}"

echo "Pack ParserService runtime → $OUT"
rm -rf "$OUT"
mkdir -p "$OUT/web" "$OUT/cosmyparser"

# Двигун потрібен parser_store лише як ephemeral (під час scrape); бізнес-дані не живуть на worker.
cp "$ROOT/web/parser_store.py" "$OUT/web/"
printf '%s\n' '"""Parser worker runtime package."""' > "$OUT/web/__init__.py"

rsync -a --delete \
  --exclude '.venv' --exclude '__pycache__' --exclude '*.pyc' --exclude '.DS_Store' \
  --exclude 'parser' \
  "$ROOT/cosmyparser/" "$OUT/cosmyparser/"

mkdir -p "$OUT/bot/database"
cat > "$OUT/config.py" <<'PY'
import os
PRODUCTS_DATABASE_URL = os.getenv("PRODUCTS_DATABASE_URL", "")
PY
: > "$OUT/bot/__init__.py"
: > "$OUT/bot/database/__init__.py"

cat > "$OUT/bot/database/products_engine.py" <<'PY'
"""Stub: worker не читає products.db — SKU приходять у POST /v1/parse/catalog."""
from contextlib import asynccontextmanager

async def init_products_db():
    return None

@asynccontextmanager
async def AsyncProductsSessionLocal():
    raise RuntimeError("Products DB недоступна на scrape-worker")
    yield  # pragma: no cover
PY

cat > "$OUT/bot/database/products_models.py" <<'PY'
class Product:  # pragma: no cover
    options = None
    brand_name = None
    name = None
    product_id = None
    has_options = False
    selling_price_catalog = None
    selling_price_order = None
    model = None
    model_internal = None
    sku = None
    image_url = None
PY

echo "OK. Скопіюй всю теку ParserService/ (з runtime/) на інший сервер."
echo "Запуск: cd ParserService && python -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/python run.py"
