# Маркетинг: реклама, інтеграції, розсилки

## Конфіг

- `settings/marketing.json` — ключі Google Ads, Meta, GSC, GA (`marketing_config.py`, `settings/paths.py`).
- `settings/insights_thresholds.json` — пороги для інсайтів (зразок: `insights_thresholds.example.json`).
- `settings/gsc_service_account.json` — Service Account Google (GSC/GA4).
- Локальний OAuth Google Ads: `scripts/google_ads_oauth_cli.py` + `web/integrations/google_ads_oauth_local.py`.

## `web/integrations/`

| Модуль | API |
|--------|-----|
| `google_ads.py` | Звіти Google Ads |
| `google_ads_oauth.py` | OAuth flow у проді |
| `google_search_console.py` | GSC |
| `google_analytics.py` | GA4 (де використовується) |
| `meta_ads.py` | Meta Marketing API |
| `ad_cache.py` | Файловий кеш відповідей (швидкий UI) |
| `ad_integration_settings.py` | UI налаштувань інтеграцій |
| `crm_analytics_cache.py` | Кеш CRM-метрик для реклами |
| `crm_auto_sync.py` | Допоміжний синк при відкритті дашбордів |
| `fx_rates.py` | Курси для зведених звітів |
| `seo_expenses.py` | SEO-витрати в звітах |
| `*_metrics_check.py` | Перевірка підключення ключів |

Нічний повний кеш: cron `refresh_ad_cache` (02:00). Поточний місяць: `refresh_ad_cache_recent` (кожні 6 год).

## Сторінки UI

- `/clients/advertising`, `/clients/ad-analytics`, `/clients/marketing-report` — JS: `advertising.js`, `ad-analytics.js`, `mkt-dashboard.js`.
- Доступ: scope **advertising** або admin.

## Email (Resend)

Змінні в `.env`: `RESEND_API_KEY`, `RESEND_FROM_*`, `RESEND_REPLY_TO`.

- Панель розсилок: `web/routes/emails.py`, UI `mailings.html` + `mailings.js`.
- Великі HTML/CSV: staging (`mailing_body_staging.py`), не слати CSV при `target_mode=all`.
- Jobs: `mailing_jobs.py` (фон, checkpoint, resume).
- Публічне API: див. `MAILING_API.md`.

## CRM-клієнти

- Фід: `COSMY_CUSTOMERS_FEED_URL` (за замовч. `analytics_customer.xml`).
- Cron: `sync_clients_from_feed` (05:00).
- Логіка імпорту: `bot/services/clients_sync.py`.
- Веб-запити: `web/clients_query.py`, `web/client_crm.py`.

## `web/cosmy_storefront.py`

Допоміжні URL/дані магазину для листів і промо (deeplink, таймери).

## `email_templates/`

HTML-шаблони; генерація з джерел: `scripts/generate_email_templates.py`, `web/email_templates_src/`.
