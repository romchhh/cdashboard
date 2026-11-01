# Авторизація та робота з сесією NovaPay

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

from zeep import Client
from zeep.transports import Transport
from zeep.exceptions import Fault

from novapay.config import (
    WSDL_URL,
    SESSION_FILE,
    SESSIONS_DIR,
    REFRESH_BEFORE_HOURS,
    SESSION_VALIDITY_HOURS,
)
from novapay.refresh_token import refresh_authentication
from novapay.token_store import persist_novapay_tokens_sync


def _novapay_error_from_response(resp) -> str:
    if not hasattr(resp, "error") or resp.error is None:
        return ""
    err = resp.error
    title = (getattr(err, "title", None) or "").strip()
    code = (getattr(err, "code", None) or getattr(err, "error_code", None) or "").strip()
    if title and code and code.lower() not in title.lower():
        return f"{title} (код: {code})"
    if title:
        return title
    if code:
        return f"Помилка NovaPay (код: {code})"
    return str(err).strip()


def format_novapay_user_message(raw: str) -> str:
    """Текст помилки API + коротка підказка українською для типових відповідей."""
    raw = (raw or "").strip()
    if not raw:
        return "Помилка NovaPay"
    low = raw.casefold()
    hints = (
        (
            "principal authorization is forbidden",
            (
                "NovaPay більше не приймає старий вхід (логін + пароль + OTP).\n"
                "Використовуйте UserAuthenticationJWT: refresh token + логін API + public certificate "
                "з кабінету business.novapay.ua → Система → Налаштування → API."
            ),
        ),
        (
            "invalid login or password",
            "Перевірте логін і пароль у кабінеті NovaPay Business і спробуйте ще раз.",
        ),
        (
            "user is blocked",
            "Обліковий запис заблоковано в NovaPay — зверніться в підтримку Business.",
        ),
        (
            "refresh token does not apply to login",
            (
                "Refresh token не підходить до цього логіна API (або вже використаний/замінений).\n"
                "У кабінеті NovaPay Business згенеруйте новий Refresh Token для користувача з логіном "
                "TymchenkoNV, вставте його в «Токен / сесія» разом із актуальним certificate — "
                "не вставляйте старий refresh повторно."
            ),
        ),
    )
    for pattern, hint in hints:
        if pattern in low:
            return f"{raw}\n\n{hint}"
    return raw


def novapay_session_rejected(message: str | None) -> bool:
    """API відхилив JWT/principal — сесію треба випустити заново."""
    low = (message or "").casefold()
    return (
        "not logged in" in low
        or "authorization is forbidden" in low
        or "invalid jwt" in low
        or "token expired" in low
        or "refresh token" in low
    )


def create_client():
    """Створює zeep Client для NovaPay API."""
    transport = Transport(timeout=45, operation_timeout=45)
    return Client(WSDL_URL, transport=transport)


def _parse_api_expiration(expiration_raw) -> datetime | None:
    """Локальний naive datetime. NovaPay віддає «21.10.2026 16:25» без секунд."""
    if expiration_raw is None or expiration_raw == "":
        return None
    if isinstance(expiration_raw, datetime):
        if expiration_raw.tzinfo is not None:
            return expiration_raw.astimezone().replace(tzinfo=None)
        return expiration_raw
    text = str(expiration_raw).strip()
    for fmt, size in (
        ("%Y-%m-%d %H:%M:%S", 19),
        ("%Y-%m-%dT%H:%M:%S", 19),
        ("%d.%m.%Y %H:%M:%S", 19),
        ("%d.%m.%Y %H:%M", 16),
    ):
        try:
            return datetime.strptime(text[:size], fmt)
        except ValueError:
            continue
    return None


def jwt_expiration_local(token: str | None) -> datetime | None:
    """exp з JWT у локальному часі. Це реальний термін, навіть якщо файл сесії записав +24 год."""
    raw = (token or "").strip()
    parts = raw.split(".")
    if len(parts) < 2:
        return None
    try:
        import base64
        import json as _json

        pad = "=" * (-len(parts[1]) % 4)
        payload = _json.loads(base64.urlsafe_b64decode(parts[1] + pad))
    except (ValueError, OSError, TypeError):
        return None
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)):
        return None
    utc = datetime.fromtimestamp(exp, timezone.utc)
    return utc.astimezone().replace(tzinfo=None)


def session_needs_refresh(expiration: datetime, *, now: datetime | None = None) -> bool:
    """Чи пора брати новий JWT.

    NovaPay JWT живе близько двох годин. Старий поріг REFRESH_BEFORE_HOURS (2 год)
    спрацьовував одразу після логіну і на кожному синку витрачав одноразовий refresh.
    Оновлюємо, коли лишилось не більше 30 хвилин — синк кожні 15 хв встигає.
    """
    current = now or datetime.now()
    return expiration - current <= timedelta(minutes=30)


def jwt_audience(token: str | None) -> str | None:
    """Audience (логін API) з JWT payload — без перевірки підпису."""
    raw = (token or "").strip()
    parts = raw.split(".")
    if len(parts) < 2:
        return None
    try:
        import base64
        import json as _json

        pad = "=" * (-len(parts[1]) % 4)
        payload = _json.loads(base64.urlsafe_b64decode(parts[1] + pad))
    except (ValueError, OSError, TypeError):
        return None
    aud = payload.get("aud")
    if isinstance(aud, list):
        aud = aud[0] if aud else None
    text = str(aud or "").strip()
    return text or None


def jwt_matches_login(token: str | None, login: str | None) -> bool:
    """Чи JWT виданий для цього логіна API. Без логіна — не перевіряємо."""
    expected = (login or "").strip()
    if not expected:
        return True
    aud = jwt_audience(token)
    if not aud:
        return False
    return aud.casefold() == expected.casefold()


def credentials_for_fop(fop_name: str) -> dict | None:
    from config import NOVAPAY_TOKENS

    name = (fop_name or "").strip()
    cfg = NOVAPAY_TOKENS.get(name)
    if not cfg:
        return None
    refresh = (cfg.get("refresh_token") or "").strip()
    cert = (cfg.get("certificate") or "").strip()
    login = (cfg.get("login") or "").strip()
    from novapay.token_store import load_rotated_stash

    stashed = load_rotated_stash(name) or {}
    stash_refresh = (stashed.get("refresh_token") or "").strip()
    if stash_refresh:
        stash_login = (stashed.get("login") or "").strip()
        # Чужий логін у stash не має перекривати токени цього ФОП.
        if login and stash_login and stash_login.casefold() != login.casefold():
            pass
        else:
            refresh = stash_refresh
            cert = (stashed.get("public_certificate") or cert).strip()
            if stash_login:
                login = stash_login
    if not refresh or refresh == "session-managed" or not cert:
        return None
    return {
        "fop_name": name,
        "refresh_token": refresh,
        "login": login,
        "public_certificate": cert,
    }


def save_session(access_token, expiration=None, session_file=None, *, auth_mode: str = "jwt"):
    """Зберігає JWT (або legacy principal) у файл sessions/."""
    path = session_file if session_file is not None else SESSION_FILE
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    saved_at_dt = datetime.now()
    token = (access_token or "").strip()
    expiration_dt = _parse_api_expiration(expiration)
    jwt_exp = jwt_expiration_local(token)
    if expiration_dt is None:
        expiration_dt = jwt_exp
    elif jwt_exp is not None and jwt_exp < expiration_dt:
        expiration_dt = jwt_exp
    if expiration_dt is None:
        expiration_dt = saved_at_dt + timedelta(hours=SESSION_VALIDITY_HOURS)
    session_data = {
        "jwt": token,
        "principal": token,
        "auth_mode": auth_mode or "jwt",
        "expiration": expiration_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "saved_at": saved_at_dt.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(session_data, f, ensure_ascii=False, indent=2)
    print(f"✓ Сесію збережено: {path}")
    print(f"  Термін дії до: {session_data['expiration']}")


def load_session(session_file=None):
    """
    Завантажує JWT/principal та expiration з файлу.
    Повертає (token, expiration) якщо сесія дійсна, інакше (None, None).
    """
    path = session_file if session_file is not None else SESSION_FILE
    if not os.path.exists(path):
        print(f"⚠ Файл сесії не знайдено: {path}")
        return None, None
    try:
        with open(path, "r", encoding="utf-8") as f:
            session_data = json.load(f)
        expiration = datetime.strptime(session_data["expiration"], "%Y-%m-%d %H:%M:%S")
        token = (session_data.get("jwt") or session_data.get("principal") or "").strip()
        if not token:
            return None, None
        jwt_exp = jwt_expiration_local(token)
        if jwt_exp is not None and jwt_exp < expiration:
            expiration = jwt_exp
        if datetime.now() >= expiration:
            print(f"⚠ Сесія прострочена (термін дії: {expiration:%Y-%m-%d %H:%M:%S})")
            return None, None
        print(f"✓ Завантажено сесію (діє до {expiration:%Y-%m-%d %H:%M:%S})")
        return token, expiration
    except Exception as e:
        print(f"⚠ Помилка завантаження сесії: {e}")
        return None, None


def user_authentication_jwt(client, refresh_token, login, public_certificate):
    """UserAuthenticationJWT — отримання JWT (замість PreUserAuthentication + OTP)."""
    request_type = client.get_type("{http://tempuri.org/}GetAuthJwtRequest")
    req = request_type(
        request_ref=str(uuid.uuid4()),
        refresh_token=(refresh_token or "").strip(),
        login=(login or "").strip(),
        public_certificate=(public_certificate or "").strip(),
    )
    return client.service.UserAuthenticationJWT(req)


def jwt_authenticate_credentials(
    refresh_token: str,
    login: str,
    public_certificate: str,
    session_file=None,
    *,
    fop_name: str | None = None,
    persist_tokens: bool = True,
) -> dict:
    """UserAuthenticationJWT без input()."""
    refresh = (refresh_token or "").strip()
    login_val = (login or "").strip()
    cert = (public_certificate or "").strip()
    if not refresh:
        return {"ok": False, "error": "Вкажіть Refresh Token NovaPay"}
    if not login_val:
        return {"ok": False, "error": "Вкажіть логін API NovaPay (кабінет → Налаштування → API)"}
    if not cert:
        return {"ok": False, "error": "Вкажіть Public certificate (PEM) з кабінету NovaPay"}

    client = create_client()
    try:
        resp = user_authentication_jwt(client, refresh, login_val, cert)
        if str(getattr(resp, "result", "")).lower() != "ok":
            title = _novapay_error_from_response(resp)
            return {
                "ok": False,
                "error": format_novapay_user_message(title or "Помилка UserAuthenticationJWT"),
            }
        jwt = (getattr(resp, "jwt", None) or "").strip()
        if not jwt:
            return {"ok": False, "error": "NovaPay не повернула JWT"}
        new_refresh = (getattr(resp, "refresh_token", None) or refresh).strip()
        new_cert = (getattr(resp, "public_certificate", None) or cert).strip()
        expiration = getattr(resp, "expiration", None)
        # Новий refresh одноразовий: ховаємо його до будь-якого запису сесії чи БД.
        if fop_name:
            from novapay.token_store import stash_rotated_tokens

            stash_rotated_tokens(fop_name, new_refresh, new_cert, login=login_val)
        save_session(jwt, expiration, session_file=session_file, auth_mode="jwt")
        warning = None
        if persist_tokens and fop_name:
            try:
                persist_novapay_tokens_sync(
                    fop_name,
                    new_refresh,
                    new_cert,
                    login=login_val,
                )
            except Exception as exc:
                warning = (
                    "JWT створено, але новий refresh не записався в базу. "
                    f"Він збережений локально і буде використаний наступного разу. ({exc})"
                )
        status = peek_session_status(session_file=session_file)
        message = status.get("message")
        if warning:
            message = f"{message}. {warning}" if message else warning
        return {
            "ok": True,
            "error": None,
            "jwt": jwt,
            "refresh_token": new_refresh,
            "public_certificate": new_cert,
            "expiration": status.get("expiration"),
            "message": message,
            "warning": warning,
        }
    except Fault as e:
        return {"ok": False, "error": f"SOAP Fault: {e}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _renew_jwt_for_fop_impl(
    fop_name: str,
    login: str | None,
    session_file,
) -> dict:
    from config import get_session_file

    name = (fop_name or "").strip()
    creds = credentials_for_fop(name)
    if not creds:
        return {
            "ok": False,
            "error": f"Немає Refresh Token / certificate для «{name}». Оновіть у /fops.",
        }
    login_val = (login or creds.get("login") or "").strip()
    path = session_file if session_file is not None else get_session_file(name)
    return jwt_authenticate_credentials(
        creds["refresh_token"],
        login_val,
        creds["public_certificate"],
        session_file=path,
        fop_name=name,
        persist_tokens=True,
    )


def renew_jwt_for_fop(fop_name: str, login: str | None = None, session_file=None) -> dict:
    """Отримати JWT за даними ФОП і зберегти ротовані refresh/certificate."""
    from novapay.session_lock import novapay_jwt_lock

    name = (fop_name or "").strip()
    with novapay_jwt_lock(name):
        return _renew_jwt_for_fop_impl(name, login, session_file)


def ensure_jwt_session(fop_name: str, session_file=None) -> str | None:
    """Повертає дійсний JWT: з файлу або новий через UserAuthenticationJWT."""
    from config import get_session_file
    from novapay.session_lock import novapay_jwt_lock
    from novapay.token_store import purge_legacy_shared_stash

    purge_legacy_shared_stash()
    name = (fop_name or "").strip()
    path = session_file if session_file is not None else get_session_file(name)
    creds = credentials_for_fop(name)
    if not creds:
        jwt, _ = load_session(session_file=path)
        return jwt

    with novapay_jwt_lock(name):
        jwt, expiration = load_session(session_file=path)
        login = (creds.get("login") or "").strip()
        # Сесія в sessions/ — основне джерело. Відкидаємо JWT лише якщо логін у ФОП
        # збережений і aud явно інший (інакше можна стерти валідні сесії помилково).
        if jwt and login and not jwt_matches_login(jwt, login):
            print(
                f"⚠ NovaPay «{name}»: JWT у сесії для логіна "
                f"{jwt_audience(jwt)!r}, очікується {login!r} — оновлюю."
            )
            jwt, expiration = None, None
        if jwt and expiration and not session_needs_refresh(expiration):
            return jwt
        if not login:
            # Немає логіна для UserAuthenticationJWT — лишаємо те, що є в файлі сесії.
            return jwt
        renewed = _renew_jwt_for_fop_impl(fop_name, None, path)
        if renewed.get("ok"):
            return renewed.get("jwt")
        status = peek_session_status(session_file=path)
        if status.get("ok") and status.get("principal_present"):
            jwt2, _ = load_session(session_file=path)
            if jwt2 and login and not jwt_matches_login(jwt2, login):
                return None
            return jwt2
        return jwt


def delete_session(session_file=None):
    """Видаляє файл сесії. session_file — шлях; якщо None — SESSION_FILE з config."""
    path = session_file if session_file is not None else SESSION_FILE
    if os.path.exists(path):
        os.remove(path)
        print("✓ Файл сесії видалено")


def ensure_session_fresh(client, access_token, expiration, session_file=None, *, fop_name: str | None = None):
    """
    Повертає актуальний JWT. Для JWT — UserAuthenticationJWT; для legacy — RefreshUserAuthentication.
    """
    if fop_name and credentials_for_fop(fop_name):
        jwt = ensure_jwt_session(fop_name, session_file=session_file)
        return jwt or access_token
    if not access_token or not expiration:
        return access_token
    remaining = expiration - datetime.now()
    if remaining < timedelta(hours=REFRESH_BEFORE_HOURS):
        print(f"\n⏱ До закінчення сесії менше {REFRESH_BEFORE_HOURS} год — оновлюємо (legacy)...")
        new_principal = try_refresh_session(client, access_token, session_file=session_file)
        return new_principal if new_principal else access_token
    return access_token


def pre_authenticate(client, login, password):
    """П. 3.1 — перший фактор аутентифікації. Повертає temp_principal, code_operation_otp."""
    request_type = client.get_type("{http://tempuri.org/}PreUserAuthenticationRequest")
    req = request_type(
        request_ref=str(uuid.uuid4()),
        login=login,
        password=password,
    )
    return client.service.PreUserAuthentication(req)


def authenticate(client, temp_principal, code_operation_otp, otp_password):
    """П. 3.2 — другий фактор (OTP). Повертає principal, expiration."""
    request_type = client.get_type("{http://tempuri.org/}UserAuthRequest")
    req = request_type(
        request_ref=str(uuid.uuid4()),
        temp_principal=temp_principal,
        code_operation_otp=code_operation_otp,
        otp_password=otp_password,
    )
    return client.service.UserAuthentication(req)


def peek_session_status(session_file=None):
    """
    Статус сесії без print.
    Повертає dict: ok, status ('ok'|'expired'|'missing'|'error'), expiration, path, message.
    """
    path = session_file if session_file is not None else SESSION_FILE
    out = {
        "ok": False,
        "status": "missing",
        "expiration": None,
        "path": path,
        "message": "немає сесії",
    }
    if not os.path.exists(path):
        return out
    try:
        with open(path, "r", encoding="utf-8") as f:
            session_data = json.load(f)
        expiration_raw = session_data.get("expiration")
        expiration = datetime.strptime(expiration_raw, "%Y-%m-%d %H:%M:%S")
        token = (session_data.get("jwt") or session_data.get("principal") or "").strip()
        jwt_exp = jwt_expiration_local(token)
        if jwt_exp is not None and jwt_exp < expiration:
            expiration = jwt_exp
            expiration_raw = expiration.strftime("%Y-%m-%d %H:%M:%S")
        out["expiration"] = expiration_raw
        if datetime.now() >= expiration:
            out["status"] = "expired"
            out["message"] = f"прострочена ({expiration_raw})"
            return out
        out["ok"] = True
        out["status"] = "ok"
        out["message"] = f"OK до {expiration_raw}"
        out["principal_present"] = bool(
            session_data.get("jwt") or session_data.get("principal")
        )
        out["auth_mode"] = session_data.get("auth_mode") or "legacy"
        return out
    except Exception as e:
        out["status"] = "error"
        out["message"] = f"помилка файлу сесії: {e}"
        return out


def pre_authenticate_credentials(login, password):
    """
    Перший фактор без input().
    Повертає dict: ok, error, temp_principal, code_operation_otp.
    """
    client = create_client()
    login = (login or "").strip()
    password = password or ""
    if not login or not password:
        return {"ok": False, "error": "Вкажіть логін і пароль NovaPay"}
    try:
        pre_resp = pre_authenticate(client, login, password)
        if str(getattr(pre_resp, "result", "")).lower() != "ok":
            title = _novapay_error_from_response(pre_resp)
            return {
                "ok": False,
                "error": format_novapay_user_message(title or "Помилка першого фактора (логін/пароль)"),
            }
        return {
            "ok": True,
            "temp_principal": pre_resp.temp_principal,
            "code_operation_otp": pre_resp.code_operation_otp,
            "error": None,
        }
    except Fault as e:
        return {"ok": False, "error": f"SOAP Fault: {e}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def complete_authentication(temp_principal, code_operation_otp, otp_password, session_file=None):
    """
    Другий фактор (OTP) без input(). Зберігає сесію при успіху.
    Повертає dict: ok, error, expiration.
    """
    client = create_client()
    otp = (otp_password or "").strip()
    if not temp_principal or not code_operation_otp:
        return {"ok": False, "error": "Немає даних попередньої авторизації. Почніть спочатку."}
    if not otp:
        return {"ok": False, "error": "Введіть OTP-код"}
    try:
        auth_resp = authenticate(client, temp_principal, code_operation_otp, otp)
        if str(getattr(auth_resp, "result", "")).lower() != "ok":
            title = _novapay_error_from_response(auth_resp)
            return {
                "ok": False,
                "error": format_novapay_user_message(title or "Помилка OTP / авторизації"),
            }
        principal = auth_resp.principal
        expiration = auth_resp.expiration
        save_session(principal, expiration, session_file=session_file)
        status = peek_session_status(session_file=session_file)
        return {
            "ok": True,
            "error": None,
            "expiration": status.get("expiration"),
            "message": status.get("message"),
        }
    except Fault as e:
        return {"ok": False, "error": f"SOAP Fault: {e}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def do_full_authentication(session_file=None):
    """Повна аутентифікація: логін, пароль, OTP (п. 3.1 + 3.2). Повертає principal або None."""
    login = input("Логін NovaPay: ").strip()
    password = input("Пароль: ").strip()
    pre = pre_authenticate_credentials(login, password)
    if not pre.get("ok"):
        print(f"❌ {pre.get('error') or 'Помилка першого фактора'}")
        return None
    print("✓ temp_principal отримано")
    print(f"  OTP операція: {pre['code_operation_otp']}")
    otp = input("\n[2/2] Введіть OTP-код: ").strip()
    print("UserAuthentication...")
    done = complete_authentication(
        pre["temp_principal"],
        pre["code_operation_otp"],
        otp,
        session_file=session_file,
    )
    if not done.get("ok"):
        print(f"❌ {done.get('error') or 'Помилка авторизації'}")
        return None
    print(f"\n✓ Авторизація успішна! Термін дії до: {done.get('expiration')}")
    principal, _ = load_session(session_file=session_file)
    return principal


def try_refresh_session(client, principal, session_file=None):
    """
    П. 3.3 — оновлення сесії (продовження терміну дії на 24 год).
    Повертає new_principal або None.
    """
    try:
        print("\n[Refresh] Оновлюємо сесію...")
        refresh_resp = refresh_authentication(client, principal)
        result = str(getattr(refresh_resp, "result", "") or "")
        if result.lower() != "ok":
            print("❌ Не вдалося оновити сесію")
            return None
        new_principal = getattr(refresh_resp, "new_principal", None)
        expiration = refresh_resp.expiration
        print(f"✓ Сесію оновлено! Термін дії до: {expiration}")
        save_session(new_principal, expiration, session_file=session_file)
        return new_principal
    except Exception as e:
        print(f"❌ Помилка оновлення сесії: {e}")
        return None


def refresh_session_quiet(
    session_file=None,
    *,
    fop_name: str | None = None,
    force: bool = False,
):
    """Оновлення сесії без CLI (JWT або legacy refresh)."""
    path = session_file
    status = peek_session_status(session_file=path)

    if fop_name and credentials_for_fop(fop_name):
        jwt, expiration = load_session(session_file=path)
        if (
            not force
            and status.get("ok")
            and status.get("auth_mode") == "jwt"
            and jwt
            and expiration
        ):
            remaining = expiration - datetime.now()
            if remaining >= timedelta(hours=REFRESH_BEFORE_HOURS):
                return {
                    "ok": True,
                    "error": None,
                    "message": (
                        f"Сесія вже дійсна ({status.get('message')}). "
                        "Новий refresh з БД не витрачали."
                    ),
                    "expiration": status.get("expiration"),
                }

        done = renew_jwt_for_fop(fop_name, session_file=path)
        if done.get("ok"):
            return {
                "ok": True,
                "error": None,
                "message": done.get("message"),
                "expiration": done.get("expiration"),
            }
        if status.get("ok") and status.get("auth_mode") == "jwt":
            return {
                "ok": True,
                "error": None,
                "message": (
                    f"Файл сесії ще дійсний ({status.get('message')}). "
                    f"Оновити JWT через кабінет не вдалося: {done.get('error')}. "
                    "Згенеруйте новий Refresh Token у NovaPay і збережіть у «Токен / сесія»."
                ),
                "expiration": status.get("expiration"),
                "renew_skipped": True,
            }
        return {"ok": False, "error": done.get("error") or "JWT не вдався"}
    jwt, _ = load_session(session_file=session_file)
    if not jwt:
        return {
            "ok": False,
            "error": "Немає сесії. Збережіть refresh token + login + certificate і створіть JWT.",
        }
    client = create_client()
    new_token = try_refresh_session(client, jwt, session_file=session_file)
    if not new_token:
        return {"ok": False, "error": "Refresh не вдався. Створіть JWT-сесію заново."}
    status = peek_session_status(session_file=session_file)
    return {"ok": True, "error": None, "message": status.get("message"), "expiration": status.get("expiration")}
