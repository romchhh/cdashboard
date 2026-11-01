"""
Ініціалізація JWT-сесії NovaPay (UserAuthenticationJWT).

Потрібні в .env / БД: refresh token, public certificate, логін API для кожного ФОП.

Запуск:
  ./myenv/bin/python init_novapay_session.py
  ./myenv/bin/python init_novapay_session.py --fop Тимченко
  ./myenv/bin/python init_novapay_session.py --fop 7
  ./myenv/bin/python init_novapay_session.py --list
  ./myenv/bin/python init_novapay_session.py --refresh --fop Тимченко

Сесія зберігається у sessions/novapay_session_<suffix>.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

from novapay.auth import (
    credentials_for_fop,
    load_session,
    renew_jwt_for_fop,
    try_refresh_session,
    create_client,
)
from novapay.config import NOVAPAY_TOKENS, SESSION_SUFFIX, get_session_file


def _list_fop_keys() -> list[str]:
    return list(NOVAPAY_TOKENS.keys())


def _session_status(user_key: str) -> str:
    path = get_session_file(user_key)
    if not os.path.exists(path):
        return "немає сесії"
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        expiration = datetime.strptime(data["expiration"], "%Y-%m-%d %H:%M:%S")
        if datetime.now() >= expiration:
            return f"прострочена ({data['expiration']})"
        mode = data.get("auth_mode") or "jwt"
        return f"OK ({mode}) до {data['expiration']}"
    except Exception:
        return "помилка файлу сесії"


def _resolve_fop_key(raw: str, keys: list[str]) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    if s.isdigit():
        idx = int(s)
        if 1 <= idx <= len(keys):
            return keys[idx - 1]
        return None
    if s in keys:
        return s
    low = s.casefold()
    for key in keys:
        if key.casefold() == low:
            return key
    for key in keys:
        if low in key.casefold():
            return key
    suffix_to_key = {v: k for k, v in SESSION_SUFFIX.items()}
    if s in suffix_to_key and suffix_to_key[s] in keys:
        return suffix_to_key[s]
    return None


def print_fop_list(keys: list[str]) -> None:
    print("ФОП NovaPay у .env:")
    for i, key in enumerate(keys, start=1):
        suffix = SESSION_SUFFIX.get(key, "default")
        creds = credentials_for_fop(key)
        cred_hint = "JWT credentials OK" if creds else "немає refresh/login/cert"
        print(f"  {i}. {key}")
        print(f"     статус: {_session_status(key)}")
        print(f"     API:    {cred_hint}")
        print(f"     файл:   {get_session_file(key)}  (suffix={suffix})")


def pick_user_key(cli_fop: str | None, *, action: str = "ініціалізувати") -> str | None:
    keys = _list_fop_keys()
    if not keys:
        print("NOVAPAY_TOKENS порожні. Додайте NOVAPAY_* у .env")
        return None
    if cli_fop:
        resolved = _resolve_fop_key(cli_fop, keys)
        if resolved:
            return resolved
        print(f"ФОП «{cli_fop}» не знайдено. Доступні: {', '.join(keys)}")
        return None

    print()
    print_fop_list(keys)
    print()
    while True:
        choice = input(
            f"Для якого ФОП {action} сесію? "
            f"Введіть номер (1–{len(keys)}) або ім'я: "
        ).strip()
        if not choice:
            print("Потрібно обрати ФОП.")
            continue
        resolved = _resolve_fop_key(choice, keys)
        if resolved:
            print(f"→ Обрано: {resolved}")
            return resolved
        print("Невірний вибір. Введіть номер зі списку або частину імені (напр. Тимченко).")


def init_session(user_key: str) -> int:
    session_file = get_session_file(user_key)
    print(f"\n=== JWT-сесія NovaPay: {user_key} ===")
    print(f"Файл: {session_file}")
    print(f"Поточний статус: {_session_status(user_key)}")
    if not credentials_for_fop(user_key):
        print(
            "\n❌ Немає refresh + certificate + login. "
            "Додайте NOVAPAY_* у .env або налаштуйте ФОП у веб-кабінеті /fops."
        )
        return 1

    done = renew_jwt_for_fop(user_key, session_file=session_file)
    if not done.get("ok"):
        print(f"\n❌ UserAuthenticationJWT не вдався: {done.get('error')}")
        return 1
    print(f"\n✓ Готово. Сесія {user_key}: {_session_status(user_key)}")
    return 0


def refresh_session(user_key: str) -> int:
    session_file = get_session_file(user_key)
    print(f"\n=== Оновлення сесії NovaPay: {user_key} ===")
    print(f"Файл: {session_file}")
    print(f"Поточний статус: {_session_status(user_key)}")

    if credentials_for_fop(user_key):
        return init_session(user_key)

    principal, expiration = load_session(session_file=session_file)
    if not principal:
        print("Немає діючої сесії. Налаштуйте JWT credentials і запустіть без --refresh.")
        return 1

    client = create_client()
    new_principal = try_refresh_session(client, principal, session_file=session_file)
    if not new_principal:
        print("\n❌ Legacy refresh не вдався. Перейдіть на JWT.")
        return 1
    print(f"\n✓ Готово. Сесія {user_key}: {_session_status(user_key)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="JWT / refresh сесії NovaPay")
    parser.add_argument("--fop", help="Ім'я ФОП, номер зі списку або suffix (t, y, cr…)")
    parser.add_argument("--list", action="store_true", help="Показати ФОП і статус сесій")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Оновити JWT (або legacy refresh, якщо credentials відсутні)",
    )
    args = parser.parse_args()

    keys = _list_fop_keys()
    if not keys:
        print("NOVAPAY_TOKENS порожні. Додайте NOVAPAY_* у .env")
        return 1

    if args.list:
        print_fop_list(keys)
        return 0

    action = "оновити" if args.refresh else "створити"
    user_key = pick_user_key(args.fop, action=action)
    if not user_key:
        return 1

    if args.refresh:
        return refresh_session(user_key)
    return init_session(user_key)


if __name__ == "__main__":
    sys.exit(main())
