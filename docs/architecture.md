# Архітектура

## Призначення

Єдина система для Cosmy:

1. **Фінанси** — транзакції Monobank + NovaPay, категорії, звіти, готівка, ліміти ФОП.
2. **Клієнти та маркетинг** — CRM з XML-фіду, аналітика, реклама (Google/Meta/GSC), email через Resend.
3. **Закупки (Procurement)** — каталог, залишки, ABC, рекомендації з продажів CRM.

## Процеси

```
┌─────────────┐     ┌──────────────────────────────────────┐
│ main_bot.py │     │ main_web.py (FastAPI)                 │
│  aiogram    │     │  routes + templates + static          │
└──────┬──────┘     │  lifespan: init DB + APScheduler      │
       │            └───────────────┬────────────────────────┘
       │                            │
       └────────────┬───────────────┘
                    ▼
         bot/services/*  +  web/* (логіка UI)
                    │
       ┌────────────┼────────────┐
       ▼            ▼            ▼
 finance_bot.db  cosmy_clients.db  products.db
       │            │            │
       └────────────┴────────────┘
                    │
       ┌────────────┴────────────┐
       ▼                         ▼
  mono/api.py              novapay/auth.py + sessions/
  Monobank REST            NovaPay SOAP (JWT)
```

## Три бази SQLite

| Файл (типово `databases/`) | Моделі | Хто пише |
|----------------------------|--------|----------|
| `finance_bot.db` | Транзакції, категорії, ФОП, баланси, готівка | Синк банків, веб/бот |
| `cosmy_clients.db` | Клієнти, замовлення, рядки | `clients_sync`, фід Cosmy |
| `products.db` | SKU, залишки, аналітика закупок | `products_sync`, procurement |

URL задаються в `.env`: `DATABASE_URL`, `CLIENTS_DATABASE_URL`, `PRODUCTS_DATABASE_URL`.

## Де живе cron

Планувальник **запускається разом із веб-апкою** (`web/main.py` → `bot/scheduler/scheduler.py`), якщо `ENABLE_SCHEDULER=1`.

На другому воркері uvicorn встановіть `ENABLE_SCHEDULER=0`, щоб не дублювати синк і оновлення JWT NovaPay.

## Конфігурація ФОП і NovaPay

- Токени Mono та refresh/certificate NovaPay — у **таблиці `fops`** (редагування: веб `/fops`).
- Runtime-кеш: `config.NOVAPAY_TOKENS`, `MONO_*` — оновлюється через `bot/services/fop_config.refresh_fop_runtime`.
- JWT у файлах `sessions/novapay_session_<suffix>.json` (окремо на кожен ФОП).

## Маркетингові ключі

Google Ads, Meta, GSC — у `settings/marketing.json` (не в `.env`). Зразок: `settings/marketing.example.json`.
