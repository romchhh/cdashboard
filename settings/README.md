# Налаштування проєкту (JSON)

Секрети та інтеграції **не** зберігаються в `.env` (окрім одноразової міграції). Усе нижче — у цій папці.

## Швидкий старт

```bash
cp settings/marketing.example.json settings/marketing.json
cp settings/gsc_service_account.example.json settings/gsc_service_account.json
# опційно — пороги інсайтів (є дефолти в коді):
cp settings/insights_thresholds.example.json settings/insights_thresholds.json
```

Заповніть `settings/marketing.json` і вставте реальний JSON ключа в `settings/gsc_service_account.json`.

## Файли

| Файл | У git | Опис |
|------|-------|------|
| `marketing.example.json` | так | Зразок ключів Google Ads / Meta / GSC / GA4 |
| `marketing.json` | **ні** | Робочий конфіг маркетингу |
| `gsc_service_account.example.json` | так | Зразок Service Account Google |
| `gsc_service_account.json` | **ні** | Ключ для Search Console і (за замовч.) GA4 |
| `insights_thresholds.example.json` | так | Пороги для текстових інсайтів у дашборді |
| `insights_thresholds.json` | так* | Локальні перевизначення порогів |

\*Якщо файлу немає — використовуються вбудовані дефолти в `web/insights/config.py`.

## Змінні середовища

| Змінна | За замовчуванням |
|--------|------------------|
| `MARKETING_CONFIG_PATH` | `settings/marketing.json` |
| `INSIGHTS_THRESHOLDS_PATH` | `settings/insights_thresholds.json` |

## Сумісність (старі шляхи)

Якщо ще лежать у **корені** репозиторію — продовжать працювати, поки не перенесете:

- `marketing_config.json` → `settings/marketing.json`
- `marketing_insights_thresholds.json` → `settings/insights_thresholds.json`
- `service_account.json` → `settings/gsc_service_account.json`

Після збереження ключів через веб-адмінку новий конфіг пишеться в `settings/marketing.json`.

## Інше в цій папці

- `deploy/` — приклади nginx для продакшену
- `api/` — Postman-колекція для API розсилок

Логіка читання/запису: `marketing_config.py` (корінь проєкту), шляхи: `settings/paths.py`.
