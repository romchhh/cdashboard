# ParserService

Scrape-only worker для парсингу цін конкурентів Cosmy.

**Основний софт** (`CosmyFinancesBot`) тримає всю БД, UI `/parser/*`, налаштування, історію, логи і нічні крони.  
**Цей сервіс** лише скрейпить магазини і віддає результат назад по HTTP.

```
CosmyFinancesBot/          ← БД, UI, крони, бізнес-логіка
├── web/                   ← /parser/* + parser_worker_client
├── cosmyparser/           ← двигун скрейпінгу
└── ParserService/         ← цей worker (інший сервер)
```

```
Веб :8000  ──X-Parser-Key──►  ParserService :8091
  БД / UI / cron                 лише scrape jobs
```

---

## Запуск локально (з монорепо)

```bash
cd ParserService
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env          # задай PARSER_API_KEY
./start.sh                    # http://127.0.0.1:8091  (лог: logs/parser.log, auto-respawn)
# ./start.sh stop | restart | status
```

**Чому був 502:** sync-пошук тримав HTTP 85–150с → nginx обривав; паралельні scrape роздували потоки/OOM і процес помирав. Тепер усі `/parse/*` одразу `202`, scrape в одному фоні, `start.sh` піднімає процес знову.

У кореневому `.env` веба (прод):

```env
PARSER_SERVICE_URL=https://cosmyparser.telebots.site
PARSER_API_KEY=той-самий-ключ
```

Локально можна `http://127.0.0.1:8091`. Порожній `PARSER_SERVICE_URL` = скрейп у процесі веба.

---

## Перенос на інший сервер

**Обовʼязково** зібрати `runtime/` (інакше API відповість, але scrape дасть `No module named cosmy_parse`):

```bash
# у монорепо
bash ParserService/pack.sh
# скопіюй ВСЮ теку ParserService/ (разом з runtime/) на хост cosmyparser.telebots.site

cd ParserService
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # той самий PARSER_API_KEY, що в основному .env
./start.sh
```

Перевірка: `curl -H "X-Parser-Key: $KEY" https://cosmyparser.telebots.site/v1/health`  
→ `"ok": true` і `"engine": {"ready": true}`.

Каталог товарів **не** синхронізується на worker — основний сервер передає SKU у `POST /v1/parse/catalog`.

---

## Авторизація

Усі `/v1/*` (крім кореня `/`) вимагають:

```http
X-Parser-Key: <PARSER_API_KEY>
```

Локально: `PARSER_REQUIRE_API_KEY=0`.

---

## Ендпоінти

База: `http://HOST:8091`

| Method | Path | Опис |
|--------|------|------|
| `GET` | `/` | Інфо (без ключа) |
| `GET` | `/docs` | Swagger |
| `GET` | `/v1/health` | Healthcheck |
| `GET` | `/v1/sites` | Список магазинів |
| `POST` | `/v1/parse/search` | Синхронний пошук одного товару |
| `POST` | `/v1/parse/catalog` | Старт фонового парсингу списку товарів → `202 {job_id}` |
| `GET` | `/v1/jobs/{job_id}?since_rows=` | Статус + інкремент рядків / фінальний `report` |

### `POST /v1/parse/search`

```json
{ "query": "Sesderma C-Vit", "our_price": 890, "settings": { "sku_workers": 4, "enabled_sites": ["Makeup"] } }
```

→ одразу результат пошуку (results/misses). Основний сервер зберігає в історію сам.

### `POST /v1/parse/catalog`

```json
{
  "products": [ { "id": 1, "name": "...", "query": "...", "brand": "...", "our_price": 100 } ],
  "settings": { "sku_workers": 4, "min_similarity": 0.58 },
  "brand": "Sesderma"
}
```

→ `202 { "job_id": "...", "state": "running" }`

### `GET /v1/jobs/{job_id}`

Полінг з основного сервера. `since_rows` — віддати лише нові рядки.

Коли `state=done` — у відповіді є `report` (рядки без `raw`). Основний сервер пише їх у `parser.db` і Excel.

---

## Приклади curl

```bash
KEY=your-key
BASE=http://127.0.0.1:8091

curl -s -H "X-Parser-Key: $KEY" $BASE/v1/health

curl -s -X POST -H "X-Parser-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"query":"Sesderma C-Vit","our_price":890}' $BASE/v1/parse/search

curl -s -X POST -H "X-Parser-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"products":[{"id":1,"name":"Test","query":"Sesderma C-Vit","brand":"Sesderma","our_price":890}]}' \
  $BASE/v1/parse/catalog
```

---

## Діагностика магазинів

Після `pack.sh` з монорепо (двигун у `cosmyparser/`):

```bash
cd ..   # корінь CosmyFinancesBot
.venv/bin/python cosmyparser/scripts/probe_search.py "Acnemy Zitback" --model 105518
.venv/bin/python cosmyparser/scripts/match_distribution.py Acnemy Medicube Babor
```

Catalog job тримає `_LISTING_CACHE` до кінця прогону; кожні `CACHE_CLEAR_EVERY` SKU чиститься лише HTTP-кеш.

---

## Структура

```
ParserService/
├── README.md
├── .env.example
├── requirements.txt
├── pack.sh
├── run.py
├── app/
│   ├── main.py
│   ├── config.py
│   ├── auth.py
│   ├── bootstrap.py
│   ├── jobs.py          ← in-memory scrape jobs
│   └── routes_v1.py
└── runtime/             ← після pack.sh
```

Немає бізнес-БД, нічних кронів і UI — лише ephemeral work-dir для двигуна.
