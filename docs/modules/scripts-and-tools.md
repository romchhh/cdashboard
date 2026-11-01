# Скрипти, cosmyparser, тести

## `scripts/`

| Скрипт | Призначення |
|--------|-------------|
| `run_db_backup.py` | Ручний бекап SQLite (те саме, що cron о 01:30) |
| `restore_db_backup.py` | Відновлення трьох SQLite з `databases/backups/cosmy_db_backup_*.zip` |
| `google_ads_oauth_cli.py` | Локальний OAuth Google Ads (`--daemon` для кнопки в UI) |
| `novapay_payments_probe.py` | Діагностика XML платежів NovaPay |
| `novapay_jwt_smoke.py` | Перевірка JWT для ФОП |
| `generate_email_templates.py` | Збірка HTML з `web/email_templates_src/` |
| `audit_utm_channels.py` | Аудит UTM / каналів |

`start.sh` і `restart.sh` у корені — запуск і перезапуск веб-панелі та Telegram-бота.

## `init_novapay_session.py`

Інтерактивне або напівавтоматичне створення сесії NovaPay у `sessions/`.

## `cosmyparser/` (окремий інструмент)

| Файл | Опис |
|------|------|
| `app.py` | Невеликий веб-UI (Flask) |
| `cosmy_parse.py` | Парсинг каталогу |
| `catalog_report.py` | Звіти по цінах |
| `scripts/probe_*.py` | Дослідницькі скрипти сайтів |
| `data/` | JSON-каталоги, логи (локально) |

Власне venv: `cosmyparser/.venv` — не частина основного `requirements.txt`.

## `tests/`

Pytest у корені проєкту. Запуск:

```bash
.venv/bin/python -m pytest tests/ -q
```

Групи:

- `test_novapay_*`, `test_mono_*` — банки  
- `test_mailing_*` — розсилки  
- `test_ad_*`, `test_google_*`, `test_meta_*` — реклама  
- `test_procurement_*`, `test_products*` — склад  
- `test_db_backup.py` — бекап  

Файли `tests/migrate_*.py` — одноразові міграції (не unit-тести).

## `deploy/`

`settings/deploy/nginx-mailings-body-size.conf.example` — приклад `client_max_body_size` для великих форм розсилок (+ опційно `WEB_FORM_MAX_PART_MB` у коді).

## Postman

`settings/api/postman_mailings.json` — колекція для API розсилок.
