# Конфігурація та дані

## `.env` / `.env.example`

Основні групи:

| Група | Приклади змінних |
|-------|------------------|
| Telegram | `TELEGRAM_BOT_TOKEN`, `ADMINISTRATORS` |
| БД | `DATABASE_URL`, `CLIENTS_DATABASE_URL`, `PRODUCTS_DATABASE_URL` |
| Веб | `WEB_HOST`, `WEB_PORT`, `WEB_USERNAME`, `WEB_PASSWORD`, `WEB_SECRET_KEY`, `WEB_PUBLIC_BASE_URL`, `ENABLE_SCHEDULER` |
| Resend | `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, … |
| Cosmy | `COSMY_CUSTOMERS_FEED_URL`, `MARKETING_API_KEY`, `ENV` |

ФОП **не** в `.env` — лише в БД через `/fops`.

## `config.py` (корінь)

- Завантаження `.env`, константи бота та веба.
- Runtime-словники Mono/NovaPay після `refresh_fop_runtime`.
- `get_session_file`, шляхи до `sessions/`, `databases/`.
- Email unsubscribe URL, ліміти бекапу (`DB_BACKUP_*`).

## Каталоги даних

| Шлях | Зміст |
|------|--------|
| `databases/` | SQLite + `backups/` |
| `sessions/` | `novapay_session_*.json`, `.jwt_lock_*` |
| `email_templates/` | Готові HTML для тригерів |
| Кеш реклами | Шляхи з `web/integrations/ad_cache.py` (файли на диску) |

## JSON у `settings/`

Див. [settings/README.md](../../settings/README.md): `marketing.json`, `gsc_service_account.json`, `insights_thresholds.json`, приклади `*.example.json`, `deploy/`, `api/postman_mailings.json`.

## Секрети (не комітити)

`.gitignore`: `.env`, `settings/marketing.json`, `settings/gsc_service_account.json`, legacy `marketing_config.json` / `service_account.json`, `sessions/`, `databases/*.db`.

## Ролі веб-доступу

Визначені в `web/auth.py`:

- **admin** — повний доступ.
- **manager** — обмежений фінансовий UI.
- Scopes: `finance`, `marketing`, `warehouse`, `advertising` — комбінуються для користувача.

## Ініціалізація БД

При старті веба: `init_db`, `init_clients_db`, `init_products_db`.

Бот при старті: `init_db` + `init_clients_db` (продукти — за потреби через веб/cron).
