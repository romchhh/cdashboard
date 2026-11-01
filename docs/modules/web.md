# Модуль `web/` — веб-панель (FastAPI)

## Ядро

| Файл | Роль |
|------|------|
| `main.py` | FastAPI app, middleware (Host, rate limit), lifespan (БД + scheduler), монтування static |
| `auth.py` | Cookie-сесії, ролі admin / manager, scopes (finance, marketing, warehouse, advertising) |
| `deps.py` | Залежності для роутів (сесія, шаблони) |
| `security.py` | Заголовки безпеки, rate limit, ліміти multipart (розсилки) |
| `passwords.py` | Хеші паролів менеджерів |

Доступ до шляхів обмежується в `main.py` (`_scopes_required_for_path`) та в окремих роутерах.

## Маршрути `web/routes/`

| Роутер | Префікс / розділ |
|--------|------------------|
| `auth.py` | Логін / вихід |
| `finance.py` | Дашборд, транзакції, звіти, статистика, готівка, sync, pending |
| `fops.py` | CRUD ФОП, NovaPay refresh (делегує `fop_admin.py`) |
| `managers.py` | Менеджери веб-доступу |
| `clients.py` | Список клієнтів, картка, дашборд CRM |
| `emails.py` | Розсилки, API Resend, jobs, unsubscribe |
| `products.py` | Procurement UI (склад, бренди, алерти, налаштування) |
| `advertising.py` | Аналіз реклами, інтеграції, кеш |
| `legal.py` | Публічні сторінки (terms, privacy) |

## Фінанси та ФОП (не в `routes/`)

| Файл | Опис |
|------|------|
| `fop_admin.py` | Збереження Mono/NovaPay у БД, `refresh_session_quiet`, `ensure_jwt_session` |

## Клієнти та CRM

| Файл | Опис |
|------|------|
| `clients_query.py` | SQL/фільтри списку клієнтів |
| `client_crm.py` | Деталі замовлень, метрики |
| `client_marketing.py` | Сегменти, UTM, маркетингові зрізи |
| `client_export_audit.py` | Журнал експортів |
| `client_name_gender.py` | Допоміжна класифікація імен |
| `manager_crm_stats.py` | Статистика для ролі менеджера |

## Реклама та аналітика

| Файл | Опис |
|------|------|
| `ad_reporting.py` | Зведені KPI, порівняння періодів |
| `ad_analytics.py` | Дані для сторінок аналітики |
| `ad_attribution.py`, `ad_channel.py` | UTM / канали |
| `ad_insights.py` | Текстові інсайти для UI |
| `ad_metric_help.py` | Підказки до метрик |
| `insights/` | Правила інсайтів (`engine.py`, `rules/*`) |

Інтеграції API — у `web/integrations/` (див. [marketing.md](marketing.md)).

## Email / розсилки

| Файл | Опис |
|------|------|
| `mailing_jobs.py` | Фонові job’и розсилки |
| `mailing_body_staging.py` | Staging великого HTML (обхід 413) |
| `mailing_helpers.py`, `mailing_api_payload.py` | Валідація payload |
| `mailing_recipients_upload.py` | CSV одержувачів |
| `mailing_resume.py` | Продовження перерваних job |
| `mailing_delivery_log.py` | Лог доставки |
| `mailing_unsubscribe.py` | Відписки |
| `email_delivery.py` | Виклики Resend |
| `cosmy_email.py` | Транзакційні листи (замовлення, welcome) |

Детальніше API — `MAILING_API.md` у корені.

## Procurement (допоміжні)

| Файл | Опис |
|------|------|
| `procurement_margin.py` | Маржа в таблицях |
| `procurement_table_sort.py`, `procurement_table_filters.py` | Сортування/фільтри в шаблонах |

## Шаблони та static

- `templates/finance/` — фінансовий UI  
- `templates/clients/` — клієнти, розсилки, реклама  
- `templates/procurement/` — склад  
- `templates/shared/`, `templates/legal/` — база, публічні сторінки  
- `static/js/`, `static/css/` — фронтенд без окремого SPA-білду  

## Публічні endpoint’и

- `/unsubscribe` — відписка від листів  
- `/legal/*` — політики  
- Зовнішні API розсилок — під `MARKETING_API_KEY` (див. `web/security.py`, `ENV=production`)
