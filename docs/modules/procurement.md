# Procurement — склад і закупки

## Дані

- БД: `products.db` (`PRODUCTS_DATABASE_URL`).
- Моделі: `bot/database/products_models.py`.
- Імпорт каталогу: `bot/services/products_sync.py` (XML-фід Cosmy).

## Нічний пайплайн (cron `sync_products_from_feed`, 03:00)

Послідовність у `bot/scheduler/tasks.py`:

1. `sync_cosmy_products` — оновлення SKU, залишків, цін  
2. `sync_clients_from_feed` — CRM (може дублювати 05:00 job, але в складі потрібні свіжі замовлення)  
3. `apply_success_crm_sales` — продажі лише успішних статусів  
4. `collect_daily_sales_snapshot` — зріз продажів  
5. `calculate_all_analytics` — агрегати  
6. `calculate_abc_analysis` — ABC-класи  

Пороги (fast mover, dead stock тощо) — таблиця `procurement_settings`, читається при розрахунку.

## Веб-UI

Роутер: `web/routes/products.py`.

Сторінки в `web/templates/procurement/`:

- Дашборд, топ SKU, рекомендації, clearance  
- Бренди, маржі, алерти  
- Налаштування закупок  
- Пошук товару: `static/procurement-product-search.js`

Доступ: scope **warehouse** (`SCOPE_WAREHOUSE`).

## Сервіси (bot)

| Файл | Роль |
|------|------|
| `analytics.py` | Загальна аналітика продажів/залишків |
| `abc_analysis.py` | ABC |
| `sales_history.py` | Історія продажів, злиття з CRM |
| `procurement_crm_sales.py` | Прив’язка CRM-продажів до SKU |

## Окремий підпроєкт `cosmyparser/`

Не імпортується основним ботом. Локальний Flask + скрипти парсингу цін/каталогу конкурентів. Див. [scripts-and-tools.md](scripts-and-tools.md).
