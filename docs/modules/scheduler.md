# Планувальник (APScheduler)

## Де увімкнено

`web/main.py` → `bot/scheduler/scheduler.setup_scheduler()` якщо `ENABLE_SCHEDULER` не `0/false/no/off`.

Timezone: `Europe/Kiev`.

## Розклад (актуальний)

| ID / задача | Коли | Функція в `tasks.py` |
|-------------|------|----------------------|
| Синхронізація транзакцій | Кожні **15 хв** (lookback 31 день) | `sync_all_transactions` |
| Синхронізація балансів | Кожні **30 хв** | `sync_balances` |
| JWT NovaPay | Кожні **6 год** + **10:15** і **22:15** | `refresh_novapay_tokens` |
| Щоденний звіт у Telegram | **09:00** | `daily_report` |
| Непідтверджені транзакції | Кожну **годину** | `check_pending_transactions` |
| Продукти Cosmy (повний цикл) | **03:00** | `sync_products_from_feed` |
| Клієнти Cosmy | **05:00** | `sync_clients_from_feed` |
| Кеш реклами (повний) | **02:00** | `refresh_ad_cache` |
| Кеш реклами (поточний період) | Кожні **6 год** | `refresh_ad_cache_recent` |
| YTD ФОП + звірка Mono | Неділя **04:15** | `sync_fop_ytd_and_reconcile` |
| Бекап БД → Telegram | **01:30** | `backup_databases_to_telegram` |
| Парсер брендів (ніч) | **01:00–07:00** пн–пт, черга **03:05**, drain **1 хв** + ланцюжок | `rotate_parser_brands` |

Перед синком транзакцій і refresh NovaPay:

- `reload_fops_runtime_from_db()`
- для JWT-ФОП: `ensure_novapay_jwt_sessions()` (не legacy refresh).

## Налаштування job

`max_instances=1`, `coalesce=True` для важких задач — уникнення накладення синків.

## Ручний запуск

- Веб: `/sync` (транзакції).
- Бот: команди в `sync_commands.py`.
- Бекап: `scripts/run_db_backup.py` або `/backup_db` у боті.

## Деплой

- **Один** процес uvicorn з `ENABLE_SCHEDULER=1`.
- Решта воркерів: `ENABLE_SCHEDULER=0`.
- Після змін ФОП у `/fops` cron підхопить токени з БД без рестарту (reload у задачах).
