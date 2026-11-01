# Модуль `bot/` — Telegram-бот

## Точка входу

`main_bot.py` — aiogram 3, доступ лише для ID з `ADMINISTRATORS` у `.env` (`bot/filters.py` → `IsAdmin`).

Планувальник у боті **не** стартує; cron — у веб-процесі.

## Структура

### `bot/handlers/`

| Файл | Функція |
|------|---------|
| `start.py` | `/start`, головне меню, баланси з БД |
| `transactions.py` | Перегляд транзакцій, фільтри |
| `reports.py` | Звіти (cashflow, P&L тощо) |
| `balance_commands.py` | `/balance` — підтягнути баланси з API |
| `sync_commands.py` | Ручний синк транзакцій |
| `cash.py` | Готівкові операції |
| `clients.py` | Короткий доступ до CRM (якщо увімкнено в меню) |
| `admin_commands.py` | Адмін-команди (бекап БД тощо) |

### `bot/services/` — бізнес-логіка

| Сервіс | Опис |
|--------|------|
| `sync_transactions.py` | Завантаження платежів Mono + NovaPay у `finance_bot.db` |
| `balance.py` / `balance_sync.py` | Баланси з API → таблиці в БД |
| `classifier.py` | Автокатегоризація за правилами |
| `reports.py` | Агрегації для звітів |
| `fop_config.py` | Завантаження ФОП з БД у runtime `config` |
| `fop_filter.py`, `fop_income_limit.py` | Фільтри та ліміти доходу ФОП |
| `clients_sync.py` | Імпорт `analytics_customer.xml` |
| `products_sync.py` | XML-каталог Cosmy → `products.db` |
| `procurement_crm_sales.py` | Продажі з успішних CRM-замовлень для аналітики |
| `analytics.py`, `abc_analysis.py`, `sales_history.py` | Розрахунки для procurement |
| `db_backup.py` | Zip SQLite → Telegram адмінам |
| `expiry_lots.py` | Терміни придатності (якщо використовується) |
| `category_lookup.py`, `period_range.py` | Допоміжні утиліти для звітів |

### `bot/database/`

| Файл | БД |
|------|-----|
| `engine.py`, `models.py` | Фінанси (`DATABASE_URL`) |
| `clients_engine.py`, `clients_models.py` | Клієнти Cosmy |
| `products_engine.py`, `products_models.py` | Продукти / закупки |
| `init_data.py`, `categories_config.py` | Початкові категорії |
| `migrate_clients_sqlite.py` | Одноразові міграції клієнтської БД |

### `bot/keyboards/`

`inline.py` — inline-кнопки для меню бота.

### `bot/utils/`

`feed_download.py` — завантаження XML-фідів; `text_normalize.py` — нормалізація тексту для пошуку/зіставлення.

### `bot/scheduler/`

Див. [scheduler.md](scheduler.md). Код задач — `tasks.py`, реєстрація — `scheduler.py`.
