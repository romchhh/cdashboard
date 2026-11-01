"""Керування ФОП у веб-адмінці: БД + сесії NovaPay."""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select

from bot.database.models import FOP, Transaction, TransactionSource
from config import (
    INACTIVE_FOP_NAMES,
    NOVAPAY_TOKENS,
    SESSION_SUFFIX,
    get_session_file,
)
from bot.services.fop_config import (
    refresh_fop_runtime,
    set_mono_credentials,
    set_novapay_credentials,
    sources_from_fop_row,
)
from novapay.auth import (
    delete_session,
    peek_session_status,
    refresh_session_quiet,
    renew_jwt_for_fop,
)

# short-lived pending OTP challenges (in-memory)
_PENDING_LOCK = threading.Lock()
_PENDING_OTP: dict[str, dict] = {}
_PENDING_TTL_SEC = 10 * 60


def _purge_pending() -> None:
    now = time.time()
    dead = [k for k, v in _PENDING_OTP.items() if v.get("expires_at", 0) < now]
    for k in dead:
        _PENDING_OTP.pop(k, None)


def store_pending_otp(*, fop_name: str, temp_principal: str, code_operation_otp: str) -> str:
    token = uuid.uuid4().hex
    with _PENDING_LOCK:
        _purge_pending()
        _PENDING_OTP[token] = {
            "fop_name": fop_name,
            "temp_principal": temp_principal,
            "code_operation_otp": code_operation_otp,
            "expires_at": time.time() + _PENDING_TTL_SEC,
        }
    return token


def pop_pending_otp(token: str) -> dict | None:
    with _PENDING_LOCK:
        _purge_pending()
        return _PENDING_OTP.pop((token or "").strip(), None)


def peek_pending_otp(token: str) -> dict | None:
    with _PENDING_LOCK:
        _purge_pending()
        return _PENDING_OTP.get((token or "").strip())


def session_status_for_name(name: str) -> dict:
    path = get_session_file(name)
    status = peek_session_status(session_file=path)
    status["fop_name"] = name
    status["suffix"] = SESSION_SUFFIX.get(name, "default")
    return status


async def build_fop_rows(session) -> list[dict]:
    """Список активних ФОП для адмін-таблиці (конфіг і джерела — з БД)."""
    from bot.services.fop_filter import get_fop_sources

    result = await session.execute(
        select(FOP).where(FOP.is_active.is_(True)).order_by(FOP.name)
    )
    active_fops = [
        f for f in result.scalars().all() if f.name not in INACTIVE_FOP_NAMES
    ]
    fop_sources = await get_fop_sources(session, [f.id for f in active_fops], env_only=False)

    rows: list[dict] = []
    for f in active_fops:
        db_sources = sources_from_fop_row(f)
        sources = list(fop_sources.get(f.id) or [])
        has_mono = "monobank" in sources
        has_novapay = "novapay" in sources
        has_novapay_config = "novapay" in db_sources
        session_info = session_status_for_name(f.name) if has_novapay_config else None
        refresh_raw = (f.novapay_refresh_token or "").strip()
        has_real_refresh = bool(refresh_raw and refresh_raw != "session-managed")
        cert_raw = (f.novapay_certificate or "").strip()
        refresh_preview = ""
        if has_real_refresh:
            tail = refresh_raw[-8:] if len(refresh_raw) > 8 else refresh_raw
            refresh_preview = f"…{tail}"
        rows.append(
            {
                "id": f.id,
                "name": f.name,
                "is_active": bool(f.is_active),
                "sources": sources,
                "has_mono": has_mono,
                "has_novapay": has_novapay,
                "has_novapay_config": has_novapay_config,
                "novapay_has_refresh": has_real_refresh,
                "novapay_has_cert": bool(cert_raw),
                "novapay_refresh_preview": refresh_preview,
                "session": session_info,
            }
        )
    rows.sort(key=lambda r: r["name"].casefold())
    return rows


async def _fop_transaction_stats(session, fop_ids: list[int]) -> dict[int, dict]:
    if not fop_ids:
        return {}
    rows = (
        await session.execute(
            select(
                Transaction.fop_id,
                Transaction.source,
                func.count(Transaction.id),
            )
            .where(Transaction.fop_id.in_(fop_ids))
            .group_by(Transaction.fop_id, Transaction.source)
        )
    ).all()
    out: dict[int, dict] = {}
    for fop_id, source, cnt in rows:
        fid = int(fop_id)
        bucket = out.setdefault(
            fid,
            {
                "transactions_total": 0,
                "has_mono": False,
                "has_novapay": False,
                "has_cash": False,
            },
        )
        bucket["transactions_total"] += int(cnt or 0)
        if source == TransactionSource.MONOBANK:
            bucket["has_mono"] = True
        elif source == TransactionSource.NOVAPAY:
            bucket["has_novapay"] = True
        elif source == TransactionSource.CASH:
            bucket["has_cash"] = True
    return out


async def build_archived_fop_rows(session) -> list[dict]:
    """Архів: деактивовані ФОПи (soft-delete). Транзакції лишаються в БД."""
    result = await session.execute(
        select(FOP).where(FOP.is_active.is_(False)).order_by(FOP.name)
    )
    fops = list(result.scalars().all())
    stats = await _fop_transaction_stats(session, [f.id for f in fops])

    rows: list[dict] = []
    for f in fops:
        if f.name in INACTIVE_FOP_NAMES:
            continue
        tx_stats = stats.get(f.id) or {
            "transactions_total": 0,
            "has_mono": False,
            "has_novapay": False,
            "has_cash": False,
        }
        rows.append(
            {
                "id": f.id,
                "name": f.name,
                "created_at": f.created_at,
                "transactions_total": tx_stats["transactions_total"],
                "has_mono": tx_stats["has_mono"],
                "has_novapay": tx_stats["has_novapay"],
                "has_cash": tx_stats["has_cash"],
            }
        )
    rows.sort(key=lambda r: r["name"].casefold())
    return rows


async def restore_fop(session, fop_id: int) -> dict:
    """Повернути ФОП у активний список (токени лишаються в БД)."""
    fop = (
        await session.execute(select(FOP).where(FOP.id == fop_id))
    ).scalar_one_or_none()
    if not fop:
        raise ValueError("ФОП не знайдено")
    if fop.is_active:
        raise ValueError(f"ФОП «{fop.name}» уже активний")
    active_conflict = (
        await session.execute(
            select(FOP.id).where(FOP.name == fop.name, FOP.is_active.is_(True))
        )
    ).scalar_one_or_none()
    if active_conflict:
        raise ValueError(f"Активний ФОП з назвою «{fop.name}» уже існує")

    tx_count = (
        await session.execute(
            select(func.count()).select_from(Transaction).where(Transaction.fop_id == fop.id)
        )
    ).scalar_one()

    fop.is_active = True
    await session.commit()
    await refresh_fop_runtime(session)
    return {"name": fop.name, "transactions_kept": int(tx_count or 0)}


async def ensure_fop_db_row(session, name: str) -> FOP:
    name = name.strip()
    existing = (
        await session.execute(select(FOP).where(FOP.name == name))
    ).scalar_one_or_none()
    if existing:
        if not existing.is_active:
            existing.is_active = True
            await session.commit()
        return existing
    row = FOP(name=name, is_active=True)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def list_mono_accounts(token: str) -> dict:
    """
    Рахунки Monobank за токеном для випадаючого списку.
    Повертає {ok, error?, client_name?, accounts: [{iban, type, balance, currency, label, id}]}.
    """
    token = (token or "").strip()
    if not token:
        return {"ok": False, "error": "Вставте токен Monobank", "accounts": []}

    from mono.api import format_amount, get_client_info

    data, err, _status = get_client_info(token)
    if not data:
        return {"ok": False, "error": err or "Не вдалося отримати рахунки", "accounts": []}

    type_labels = {
        "fop": "ФОП",
        "black": "Чорна картка",
        "white": "Біла картка",
        "platinum": "Platinum",
        "iron": "Iron",
        "yellow": "Yellow",
        "eAid": "єПідтримка",
    }
    currency_map = {980: "UAH", 840: "USD", 978: "EUR"}

    accounts = []
    for acc in data.get("accounts") or []:
        iban = (acc.get("iban") or "").strip()
        if not iban:
            continue
        acc_type = str(acc.get("type") or "")
        currency_code = int(acc.get("currencyCode") or 980)
        currency = currency_map.get(currency_code, str(currency_code))
        try:
            balance = format_amount(acc.get("balance") or 0)
        except Exception:
            balance = 0
        type_ua = type_labels.get(acc_type, acc_type or "рахунок")
        label = f"{type_ua} · {iban} · {balance:,.2f} {currency}".replace(",", " ")
        accounts.append(
            {
                "id": acc.get("id"),
                "iban": iban,
                "type": acc_type,
                "balance": balance,
                "currency": currency,
                "label": label,
                "is_fop": acc_type == "fop",
            }
        )

    # ФОП-рахунки зверху
    accounts.sort(key=lambda a: (0 if a.get("is_fop") else 1, a.get("iban") or ""))

    client_name = (data.get("name") or "").strip() or None
    if not accounts:
        return {
            "ok": False,
            "error": "У токені немає рахунків з IBAN",
            "client_name": client_name,
            "accounts": [],
        }
    return {"ok": True, "error": None, "client_name": client_name, "accounts": accounts}


async def add_mono_fop(session, *, name: str, token: str, iban: str | None = None) -> FOP:
    name = (name or "").strip()
    token = (token or "").strip()
    iban = (iban or "").strip().replace(" ", "") or None
    if not name or not token:
        raise ValueError("Вкажіть ім'я ФОП і токен Monobank")
    if not iban:
        raise ValueError("Оберіть IBAN рахунку для моніторингу зі списку")

    accounts = await list_mono_accounts(token)
    if not accounts.get("ok"):
        raise ValueError(accounts.get("error") or "Токен Monobank невалідний")
    allowed = {a["iban"] for a in accounts.get("accounts") or []}
    if iban not in allowed:
        raise ValueError("Обраний IBAN не належить цьому токену. Оновіть список рахунків.")

    return await set_mono_credentials(session, name=name, token=token, iban=iban)


async def add_novapay_fop(
    session,
    *,
    name: str,
    refresh: str | None = None,
    cert: str | None = None,
    login: str | None = None,
) -> FOP:
    name = (name or "").strip()
    if not name:
        raise ValueError("Вкажіть ім'я ФОП NovaPay")
    return await set_novapay_credentials(session, name=name, refresh=refresh, cert=cert, login=login)


async def update_novapay_fop(
    session,
    *,
    fop_name: str,
    refresh: str,
    cert: str | None = None,
    login: str | None = None,
    reset_session: bool = True,
) -> dict:
    """
    Оновити Refresh Token / certificate / login NovaPay для існуючого ФОП.
    За замовчуванням видаляє файл сесії — потрібен новий JWT.
    """
    fop_name = (fop_name or "").strip()
    refresh_val = (refresh or "").strip()
    if not fop_name:
        raise ValueError("Не вказано ФОП")
    if not refresh_val:
        raise ValueError("Вкажіть новий Refresh Token з кабінету NovaPay")

    fop = (
        await session.execute(select(FOP).where(FOP.name == fop_name, FOP.is_active.is_(True)))
    ).scalar_one_or_none()
    if not fop:
        raise ValueError(f"Активний ФОП «{fop_name}» не знайдено")
    db_sources = sources_from_fop_row(fop)
    if "novapay" not in db_sources and fop_name not in NOVAPAY_TOKENS:
        raise ValueError(f"ФОП «{fop_name}» не налаштований для NovaPay")

    await set_novapay_credentials(
        session, name=fop_name, refresh=refresh_val, cert=cert, login=login
    )

    session_cleared = False
    if reset_session:
        try:
            delete_session(session_file=get_session_file(fop_name))
            session_cleared = True
        except Exception:
            pass

    return {
        "name": fop_name,
        "session_cleared": session_cleared,
        "message": (
            "Облікові дані оновлено. Натисніть «Створити JWT-сесію»."
            if session_cleared
            else "Облікові дані NovaPay оновлено."
        ),
    }


async def delete_fop(session, fop_id: int) -> dict:
    """
    Прибрати ФОП зі списку / конфігу без видалення транзакцій.
    Рядок у БД лишається (is_active=False), fop_id у транзакціях не чіпаємо — статистика зберігається.
    """
    from sqlalchemy import func

    from bot.database.models import Transaction

    fop = (
        await session.execute(select(FOP).where(FOP.id == fop_id))
    ).scalar_one_or_none()
    if not fop:
        raise ValueError("ФОП не знайдено")
    name = fop.name

    tx_count = (
        await session.execute(
            select(func.count()).select_from(Transaction).where(Transaction.fop_id == fop.id)
        )
    ).scalar_one()

    # сесія NovaPay більше не потрібна для синку
    if "novapay" in sources_from_fop_row(fop) or name in NOVAPAY_TOKENS:
        try:
            delete_session(session_file=get_session_file(name))
        except Exception:
            pass

    # лише деактивація — credentials лишаються в БД для можливого відновлення
    fop.is_active = False
    await session.commit()
    await refresh_fop_runtime(session)
    return {"name": name, "transactions_kept": int(tx_count or 0)}


def start_novapay_session(
    fop_name: str,
    login: str | None = None,
    password: str | None = None,
    *,
    force: bool = False,
) -> dict:
    """UserAuthenticationJWT (password ігнорується — legacy OTP вимкнено NovaPay)."""
    from novapay.config import get_session_file

    fop_name = (fop_name or "").strip()
    if not fop_name:
        return {"ok": False, "error": "Не вказано ФОП"}
    if fop_name not in NOVAPAY_TOKENS:
        return {"ok": False, "error": f"ФОП «{fop_name}» немає NovaPay у конфігу"}

    session_path = get_session_file(fop_name)
    existing = peek_session_status(session_file=session_path)
    if (
        not force
        and existing.get("ok")
        and existing.get("auth_mode") == "jwt"
    ):
        return {
            "ok": True,
            "fop_name": fop_name,
            "message": (
                f"Сесія вже активна ({existing.get('message')}). "
                "Новий JWT не запитували — не витрачайте refresh. "
                "Якщо потрібен новий токен з кабінету: спочатку «Токен / сесія» → зберегти refresh, потім знову JWT."
            ),
            "expiration": existing.get("expiration"),
        }

    done = renew_jwt_for_fop(fop_name, login=(login or "").strip() or None)
    if not done.get("ok"):
        return {"ok": False, "error": done.get("error") or "Помилка JWT"}
    status = session_status_for_name(fop_name)
    return {
        "ok": True,
        "fop_name": fop_name,
        "message": status.get("message") or done.get("message"),
        "expiration": status.get("expiration") or done.get("expiration"),
    }


def finish_novapay_session(challenge: str, otp: str) -> dict:
    return {
        "ok": False,
        "error": "OTP більше не підтримується. Створіть сесію через JWT (refresh + login + certificate).",
    }


def refresh_novapay_session(fop_name: str) -> dict:
    fop_name = (fop_name or "").strip()
    if fop_name not in NOVAPAY_TOKENS:
        return {"ok": False, "error": f"ФОП «{fop_name}» немає NovaPay у конфігу"}
    return refresh_session_quiet(
        session_file=get_session_file(fop_name),
        fop_name=fop_name,
    )


def format_now() -> str:
    return datetime.now().strftime("%d.%m.%Y %H:%M")


def _mono_fop_account(client_info: dict, fop_name: str) -> dict | None:
    from config import mono_iban_for_fop

    preferred_iban = mono_iban_for_fop(fop_name)
    if preferred_iban:
        for account in client_info.get("accounts") or []:
            if (account.get("iban") or "").strip() == preferred_iban:
                return account
    for account in client_info.get("accounts") or []:
        if account.get("type") == "fop":
            return account
    accounts = client_info.get("accounts") or []
    return accounts[0] if accounts else None


def probe_mono_recent_transactions(fop_name: str, *, limit: int = 5) -> dict:
    """Останні транзакції Monobank без запису в БД (для перевірки токена)."""
    import time

    from mono.api import format_amount, get_client_info, get_statement, utc_timestamp_to_kyiv
    from mono.config import MONO_TOKENS

    fop_name = (fop_name or "").strip()
    token = MONO_TOKENS.get(fop_name)
    if not token:
        return {
            "source": "monobank",
            "ok": False,
            "error": "Немає токена Monobank для цього ФОП",
            "transactions": [],
        }

    client_info, error_reason, _status = get_client_info(token)
    if not client_info:
        return {
            "source": "monobank",
            "ok": False,
            "error": error_reason or "Не вдалося отримати дані Monobank",
            "transactions": [],
        }

    account = _mono_fop_account(client_info, fop_name)
    if not account:
        return {
            "source": "monobank",
            "ok": False,
            "error": "Не знайдено рахунок ФОП у токені Monobank",
            "transactions": [],
        }

    to_time = int(time.time())
    from_time = to_time - 7 * 24 * 60 * 60
    try:
        raw = get_statement(token, account["id"], from_time, to_time)
    except Exception as exc:
        return {
            "source": "monobank",
            "ok": False,
            "error": str(exc),
            "transactions": [],
        }
    rows: list[dict] = []
    for tx in raw[:limit]:
        amount_raw = int(tx.get("amount") or 0)
        rows.append({
            "date": utc_timestamp_to_kyiv(int(tx["time"])).strftime("%d.%m.%Y %H:%M"),
            "amount": round(abs(format_amount(amount_raw)), 2),
            "type": "income" if amount_raw > 0 else "expense",
            "counterparty": (tx.get("counterName") or "").strip() or "—",
            "description": (tx.get("description") or tx.get("comment") or "").strip() or "—",
        })

    iban = (account.get("iban") or "").strip()
    return {
        "source": "monobank",
        "ok": True,
        "error": None,
        "account": iban or None,
        "fetched": len(raw),
        "transactions": rows,
        "message": f"Отримано {len(raw)} транзакцій за 7 днів" + (f" (IBAN …{iban[-8:]})" if iban else ""),
    }


def probe_novapay_recent_transactions(fop_name: str, *, limit: int = 5) -> dict:
    """Останні проведені платежі NovaPay без запису в БД."""
    from novapay.accounts import get_accounts_list, get_clients_list, get_payments_list, unwrap_zeep_list
    from novapay.auth import create_client, ensure_jwt_session, load_session
    from novapay.config import NOVAPAY_TOKENS, get_session_file
    from novapay.menu import _parse_payments_xml, is_importable_novapay_payment

    from mono.api import now_kyiv

    fop_name = (fop_name or "").strip()
    if fop_name not in NOVAPAY_TOKENS:
        return {
            "source": "novapay",
            "ok": False,
            "error": "NovaPay не налаштовано для цього ФОП",
            "transactions": [],
        }

    session_file = get_session_file(fop_name)
    client = create_client()
    principal = ensure_jwt_session(fop_name, session_file=session_file)
    if not principal:
        principal, _ = load_session(session_file=session_file)
    if not principal:
        return {
            "source": "novapay",
            "ok": False,
            "error": "Немає JWT-сесії NovaPay — оновіть refresh + certificate + login і створіть сесію",
            "transactions": [],
        }

    from novapay.accounts import novapay_response_error
    from novapay.auth import credentials_for_fop, renew_jwt_for_fop

    def _clients_for_token(token: str):
        resp = get_clients_list(client, token)
        items = unwrap_zeep_list(getattr(resp, "clients", None), ["Clients", "item", "Item"])
        return resp, items, novapay_response_error(resp)

    try:
        clients_resp, clients, api_err = _clients_for_token(principal)
        if not clients and credentials_for_fop(fop_name):
            renewed = renew_jwt_for_fop(fop_name, session_file=session_file)
            if renewed.get("ok") and renewed.get("jwt"):
                principal = renewed["jwt"]
                clients_resp, clients, api_err = _clients_for_token(principal)
        if not clients:
            err = api_err or "NovaPay: підприємства не отримані"
            return {"source": "novapay", "ok": False, "error": err, "transactions": []}

        first_client = clients[0]
        client_id = getattr(first_client, "id", None) or getattr(first_client, "Id", None)
        acc_resp = get_accounts_list(client, principal, client_id)
        accounts = unwrap_zeep_list(getattr(acc_resp, "accounts", None), ["Account", "Accounts", "item", "Item"])
        if not accounts:
            return {"source": "novapay", "ok": False, "error": "NovaPay: рахунки не отримані", "transactions": []}

        first_account = accounts[0]
        account_id = getattr(first_account, "id", None) or getattr(first_account, "Id", None)

        date_to = now_kyiv()
        date_from = date_to - timedelta(days=14)
        payments_resp = get_payments_list(
            client,
            principal,
            account_id=account_id,
            date_from=date_from.strftime("%d.%m.%Y"),
            date_to=date_to.strftime("%d.%m.%Y"),
        )
    except Exception as exc:
        return {"source": "novapay", "ok": False, "error": str(exc), "transactions": []}

    payments_raw = getattr(payments_resp, "payments", None)
    payments = _parse_payments_xml(payments_raw) if payments_raw else []
    importable = [p for p in payments if is_importable_novapay_payment(p)]

    def _sort_key(p: dict) -> tuple:
        ds = str(p.get("datetime_str") or p.get("date") or "")
        return (ds, str(p.get("doc_id") or ""))

    importable.sort(key=_sort_key, reverse=True)

    rows: list[dict] = []
    for p in importable[:limit]:
        amount = float(p.get("amount") or 0)
        rows.append({
            "date": (p.get("datetime_str") or p.get("date") or "—").strip(),
            "amount": round(abs(amount), 2),
            "type": "income" if amount >= 0 else "expense",
            "counterparty": (p.get("counterparty") or "").strip() or "—",
            "description": (p.get("purpose") or p.get("comment") or "").strip() or "—",
        })

    return {
        "source": "novapay",
        "ok": True,
        "error": None,
        "fetched": len(importable),
        "transactions": rows,
        "message": f"Отримано {len(importable)} проведених платежів за 14 днів",
    }


def probe_fop_recent_transactions(fop_name: str, *, limit: int = 5) -> dict:
    """Перевірка останніх транзакцій з усіх підключених джерел ФОП."""
    from mono.config import MONO_TOKENS
    from novapay.config import NOVAPAY_TOKENS

    fop_name = (fop_name or "").strip()
    if not fop_name:
        return {"ok": False, "error": "Не вказано ФОП", "results": []}

    results: list[dict] = []
    if fop_name in MONO_TOKENS:
        results.append(probe_mono_recent_transactions(fop_name, limit=limit))
    if fop_name in NOVAPAY_TOKENS:
        results.append(probe_novapay_recent_transactions(fop_name, limit=limit))

    if not results:
        return {
            "ok": False,
            "fop_name": fop_name,
            "error": "У ФОП немає підключеного Monobank або NovaPay",
            "results": [],
        }

    return {
        "ok": any(r.get("ok") for r in results),
        "fop_name": fop_name,
        "error": None if any(r.get("ok") for r in results) else "Не вдалося отримати транзакції",
        "results": results,
    }
