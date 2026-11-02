# Отримання балансів з Monobank та NovaPay в реальному часі

import asyncio

from config import INACTIVE_FOP_NAMES, mono_iban_for_fop
from mono.config import MONO_TOKENS
from mono.api import get_client_info, format_amount
from novapay.config import NOVAPAY_TOKENS, get_session_file
from novapay.auth import create_client, ensure_jwt_session, load_session
from novapay.accounts import get_clients_list, get_accounts_list, get_account_rest, unwrap_zeep_list


async def get_all_balances() -> dict:
    """
    Отримати баланси всіх рахунків з Mono та NovaPay.
    Мережа йде в окремому потоці, щоб не зупиняти відповіді сайту.
    Повертає: {
        "monobank": [{"fop": "...", "balance": 123.45, "currency": "UAH"}, ...],
        "novapay": [{"fop": "...", "balance": 123.45, "currency": "UAH"}, ...]
    }
    """
    return await asyncio.to_thread(_get_all_balances_sync)


def _get_all_balances_sync() -> dict:
    mono_balances = []
    nova_balances = []

    # Monobank
    for fop_name, token in MONO_TOKENS.items():
        if fop_name in INACTIVE_FOP_NAMES:
            continue
        try:
            client_info, _err, _status = get_client_info(token)
            if client_info:
                preferred_iban = mono_iban_for_fop(fop_name)
                for account in client_info.get("accounts", []):
                    if preferred_iban:
                        if (account.get("iban") or "").strip() != preferred_iban:
                            continue
                    elif account.get("type") != "fop":
                        continue
                    balance = format_amount(account["balance"])
                    currency_code = account.get("currencyCode", 980)
                    currency = "UAH" if currency_code == 980 else "USD" if currency_code == 840 else str(currency_code)
                    mono_balances.append({
                        "fop": fop_name,
                        "balance": balance,
                        "currency": currency,
                        "iban": account.get("iban", "—"),
                    })
                    if preferred_iban:
                        break
        except Exception as e:
            print(f"❌ Помилка Monobank {fop_name}: {e}")

    # NovaPay
    for user_key in NOVAPAY_TOKENS.keys():
        try:
            client = create_client()
            session_file = get_session_file(user_key)
            principal = ensure_jwt_session(user_key, session_file=session_file)
            if not principal:
                principal, _ = load_session(session_file=session_file)

            if not principal:
                continue

            # Отримати клієнта та рахунки
            clients_resp = get_clients_list(client, principal)
            raw_clients = getattr(clients_resp, "clients", None)
            clients = unwrap_zeep_list(raw_clients, ["Clients", "item", "Item"])

            if not clients:
                continue

            first_client = clients[0]
            client_id = getattr(first_client, "id", None) or getattr(first_client, "Id", None)

            acc_resp = get_accounts_list(client, principal, client_id)
            raw_accounts = getattr(acc_resp, "accounts", None)
            accounts = unwrap_zeep_list(raw_accounts, ["Account", "Accounts", "item", "Item"])

            if not accounts:
                continue

            # Отримати баланс першого рахунку
            first_account = accounts[0]
            account_id = getattr(first_account, "id", None) or getattr(first_account, "Id", None)
            iban = getattr(first_account, "IBAN", None) or getattr(first_account, "BAN", None) or "—"
            currency = getattr(first_account, "currency", None) or getattr(first_account, "Currency", None) or "UAH"

            rest_resp = get_account_rest(client, principal, account_id)
            if getattr(rest_resp, "result", "").lower() == "ok":
                avail = getattr(rest_resp, "available_balance", 0)
                nova_balances.append({
                    "fop": user_key,
                    "balance": float(avail) if avail else 0,
                    "currency": currency,
                    "iban": iban,
                })

        except Exception as e:
            print(f"❌ Помилка NovaPay {user_key}: {e}")

    return {
        "monobank": mono_balances,
        "novapay": nova_balances,
    }
