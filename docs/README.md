# Документація CosmyFinancesBot

Короткий опис модулів проєкту. Деталі — у файлах нижче.

## Зміст

| Документ | Що всередині |
|----------|----------------|
| [architecture.md](architecture.md) | Як пов’язані бот, веб, БД, cron, зовнішні API |
| [modules/bot.md](modules/bot.md) | Telegram-бот: handlers, services, БД |
| [modules/web.md](modules/web.md) | FastAPI: маршрути, ролі, безпека |
| [modules/finance.md](modules/finance.md) | Monobank, NovaPay, синк транзакцій, ФОП |
| [modules/marketing.md](modules/marketing.md) | Реклама, CRM-клієнти, email-розсилки |
| [modules/procurement.md](modules/procurement.md) | Склад, закупки, продукти Cosmy |
| [modules/scheduler.md](modules/scheduler.md) | APScheduler: розклад і задачі |
| [modules/config-and-data.md](modules/config-and-data.md) | `.env`, `config.py`, SQLite, сесії |
| [modules/scripts-and-tools.md](modules/scripts-and-tools.md) | CLI-скрипти, cosmyparser, тести |

## Точки входу

| Команда | Призначення |
|---------|-------------|
| `python main_bot.py` | Telegram-бот (тільки адміни з `ADMINISTRATORS`) |
| `./start.sh` / `./restart.sh` | Веб-панель і Telegram-бот (планувальник у веб-процесі, за `ENABLE_SCHEDULER`) |
| `python init_novapay_session.py` | Ручна ініціалізація JWT NovaPay (CLI) |

## Додатково в корені репозиторію

- `README.md` — швидкий старт
- `MAILING_API.md` — API розсилок для зовнішніх інтеграцій
- `settings/README.md` — JSON-конфіги маркетингу, GSC, пороги інсайтів
- `.env.example` — змінні середовища
