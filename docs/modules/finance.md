# Інтеграції фінансів: Monobank, NovaPay, синк

## `mono/`

| Файл | Опис |
|------|------|
| `config.py` | Токени з runtime `config` (з БД ФОП) |
| `api.py` | HTTP-клієнт Monobank (виписки, рахунки) |

Використовується з `bot/services/sync_transactions.py` та `balance.py`.

## `novapay/`

SOAP API (zeep), WSDL з `novapay/config.py`.

| Файл | Опис |
|------|------|
| `auth.py` | JWT (`UserAuthenticationJWT`), legacy refresh, `ensure_jwt_session`, `renew_jwt_for_fop` |
| `session_lock.py` | `fcntl`-lock на ФОП — один процес оновлює refresh за раз |
| `api_auth.py` | Параметри `jwt` / `principal` для SOAP-викликів |
| `accounts.py` | Рахунки, виписки, платежі |
| `refresh_token.py` | Legacy `RefreshUserAuthentication` |
| `token_store.py` | `persist_novapay_tokens_sync` → таблиця `fops` |
| `config.py` | `SESSIONS_DIR`, `get_session_file(fop_name)` |

### Життєвий цикл JWT-сесії

1. У `/fops` зберігаються **refresh token**, **login API**, **public certificate** (PEM).
2. `UserAuthenticationJWT` повертає JWT + часто **новий** refresh і certificate → пишуться в БД і в `sessions/*.json`.
3. `ensure_jwt_session`: якщо до `expiration` більше ніж `REFRESH_BEFORE_HOURS` (2 год) — JWT з файлу; інакше renew.
4. Cron: `refresh_novapay_tokens` + перед кожним `sync_all_transactions` — `ensure_novapay_jwt_sessions` + `reload_fops_runtime_from_db`.

### Типові причини «збиваються» сесії

- Паралельний renew без lock (виправлено `session_lock.py`).
- Два процеси з `ENABLE_SCHEDULER=1` (дубль cron).
- Повторна вставка **старого** refresh після ротації в кабінеті NovaPay.
- Розсинхрон БД і `sessions/` на сервері (різні інстанси без спільного диска).

### Ручні інструменти

- `init_novapay_session.py` — CLI вхід / renew.
- `scripts/novapay_jwt_smoke.py` — швидка перевірка JWT.
- `scripts/novapay_payments_probe.py` — сирий XML платежів для діагностики.

## Синхронізація транзакцій

`bot/services/sync_transactions.py` (`TransactionSyncService`):

- Період lookback (за замовч. 31 день у cron).
- Дедуплікація за external id.
- Класифікація через `classifier.py`.
- NovaPay: потрібен валідний JWT у `sessions/` для кожного ФОП.

## Веб

- `/fops` — `web/routes/fops.py` + `web/fop_admin.py`
- `/sync` — ручний запуск синку (той самий сервіс, що cron)

## Кореневий `config.py`

Єдине місце для `.env`, шляхів до БД, `NOVAPAY_TOKENS`, публічних URL, Resend, Cosmy feed URL тощо. Після змін у БД ФОП викликайте `refresh_fop_runtime` (cron робить це автоматично).
