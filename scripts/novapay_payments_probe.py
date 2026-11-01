"""
Тестовий скрипт для перевірки структури платежів NovaPay API.
Виводить сирий XML та усі поля кожного платежу (щоб побачити, чи є окремий коментар).

Запуск: python scripts/novapay_payments_probe.py
       python scripts/novapay_payments_probe.py --fop 2 --days 90
       python scripts/novapay_payments_probe.py --fop Криворучко

Потрібна діюча сесія NovaPay у sessions/ (увійти через бота або refresh токени).
"""

import argparse
import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

from novapay.auth import create_client, load_session
from novapay.accounts import get_clients_list, get_accounts_list, get_payments_list, unwrap_zeep_list
from novapay.config import NOVAPAY_TOKENS, SESSION_SUFFIX, get_session_file
from novapay.menu import _parse_payments_xml


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
        return f"OK до {data['expiration']}"
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


def pick_user_key(cli_fop: str | None) -> str | None:
    keys = _list_fop_keys()
    if not keys:
        return None
    if cli_fop:
        resolved = _resolve_fop_key(cli_fop, keys)
        if resolved:
            return resolved
        print(f"ФОП «{cli_fop}» не знайдено. Доступні: {', '.join(keys)}")
        return None
    if len(keys) == 1:
        return keys[0]

    print("ФОП NovaPay у .env:")
    for i, key in enumerate(keys, start=1):
        session_file = get_session_file(key)
        print(f"  {i}. {key}")
        print(f"     сесія: {_session_status(key)}")
        print(f"     файл:  {session_file}")

    while True:
        choice = input(f"\nОберіть номер ФОП (1–{len(keys)}, Enter = 1): ").strip()
        if not choice:
            return keys[0]
        resolved = _resolve_fop_key(choice, keys)
        if resolved:
            return resolved
        print("Невірний вибір. Введіть номер або частину імені ФОП.")


def _parse_ua_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip()[:10], "%d.%m.%Y")
    except ValueError:
        return None


def _payment_dates(rows: list[dict]) -> list[str]:
    parsed = [(d, _parse_ua_date(d)) for d in {r.get("date") for r in rows if r.get("date")}]
    parsed = [(d, dt) for d, dt in parsed if dt is not None]
    parsed.sort(key=lambda x: x[1])
    return [d for d, _ in parsed]


def _local_tag(elem):
    return elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag


def _account_label(account) -> str:
    iban = getattr(account, "iban", None) or getattr(account, "IBAN", None) or "—"
    name = getattr(account, "name", None) or getattr(account, "Name", None) or ""
    currency = getattr(account, "currency", None) or getattr(account, "Currency", None) or "UAH"
    return f"{iban} ({currency}) {name}".strip()


def _print_payment_details(xml_str: str) -> int:
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError as e:
        print("Помилка парсингу XML:", e)
        return 0

    doc_count = 0
    for elem in root.iter():
        if _local_tag(elem) != "Docs":
            continue
        doc_count += 1
        el = elem
        print(f"\n--- ПЛАТІЖ #{doc_count} ---")
        print("Усі теги у елементі Docs:")
        for c in el:
            tag = _local_tag(c)
            text = (c.text or "").strip() if c.text else ""
            if len(text) > 60:
                text = text[:60] + "..."
            print(f"  {tag!r}: {text!r}")
        if el.attrib:
            print("  Атрибути:", dict(el.attrib))

        def text(tag):
            for c in el:
                if _local_tag(c) == tag and c.text:
                    return c.text.strip()
            return ""

        def attr(name, default=""):
            return el.get(name) or default

        amount_str = attr("Amount", "0")
        try:
            amount = float(amount_str.replace(",", "."))
        except (ValueError, TypeError):
            amount = 0
        payment_type = text("PaymentType")
        if payment_type and payment_type.lower() == "debit":
            amount = -abs(amount)
            counterparty = text("CreditName")
        else:
            amount = abs(amount)
            counterparty = text("DebitName")
        purpose = text("Purpose") or "—"
        comment = text("Comment") or ""
        effective_comment = comment.strip() or purpose if purpose != "—" else ""

        print("\nНаш парсер (counterparty, purpose, comment):")
        print(f"  counterparty: {counterparty or '—'}")
        print(f"  purpose:       {purpose[:70]}{'...' if len(purpose) > 70 else ''}")
        print(f"  comment (XML): {comment[:70] if comment else '(немає тега Comment)'}{'...' if len(comment) > 70 else ''}")
        print(f"  comment (БД):  {effective_comment[:70]}{'...' if len(effective_comment) > 70 else ''}")
        print(f"  amount:        {amount:+.2f}")
        print("=" * 80)
    return doc_count


def _fetch_payments(client, principal, account_id, days: int):
    date_to = datetime.now()
    date_from = date_to - timedelta(days=days)
    date_from_str = date_from.strftime("%d.%m.%Y")
    date_to_str = date_to.strftime("%d.%m.%Y")
    resp = get_payments_list(
        client,
        principal,
        account_id=account_id,
        date_from=date_from_str,
        date_to=date_to_str,
    )
    raw = getattr(resp, "payments", None)
    xml_str = raw if isinstance(raw, str) else str(raw or "")
    rows = _parse_payments_xml(xml_str)
    return resp, date_from_str, date_to_str, xml_str, rows


def main():
    parser = argparse.ArgumentParser(description="Перевірка NovaPay GetPaymentsList")
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Скільки днів назад запитувати (за замовчуванням 30, як у синхронізації бота)",
    )
    parser.add_argument(
        "--fop",
        type=str,
        default=None,
        help="ФОП: номер зі списку (1–4), ім'я (Криворучко) або суфікс сесії (cr, ch, s, m)",
    )
    args = parser.parse_args()

    if not NOVAPAY_TOKENS:
        print("NOVAPAY_TOKENS порожні. Налаштуйте NovaPay у .env та майте збережену сесію у sessions/")
        return

    user_key = pick_user_key(args.fop)
    if not user_key:
        return

    session_file = get_session_file(user_key)
    print(f"\nNovaPay ФОП: {user_key}")
    print(f"Файл сесії: {session_file}\n")

    client = create_client()
    principal, _ = load_session(session_file=session_file)
    if not principal:
        print("Не вдалося завантажити сесію. Увійдіть у NovaPay через бота або оновіть токени.")
        return

    clients_resp = get_clients_list(client, principal)
    clients = unwrap_zeep_list(getattr(clients_resp, "clients", None), ["Clients", "item", "Item"])
    if not clients:
        print("Клієнти не отримані")
        return

    first_client = clients[0]
    client_id = getattr(first_client, "id", None) or getattr(first_client, "Id", None)
    client_name = getattr(first_client, "name", None) or getattr(first_client, "Name", None) or "—"
    print(f"Підприємство: {client_name} (id={client_id})")

    acc_resp = get_accounts_list(client, principal, client_id)
    accounts = unwrap_zeep_list(getattr(acc_resp, "accounts", None), ["Account", "Accounts", "item", "Item"])
    if not accounts:
        print("Рахунки не отримані")
        return

    first_account = accounts[0]
    account_id = getattr(first_account, "id", None) or getattr(first_account, "Id", None)
    print(f"Рахунок: {_account_label(first_account)} (id={account_id})\n")

    days = max(1, args.days)
    print(f"Запит GetPaymentsList за {days} дн...")
    try:
        resp, date_from_str, date_to_str, xml_str, rows = _fetch_payments(
            client, principal, account_id, days
        )
    except Exception as e:
        print(f"Помилка GetPaymentsList: {e}")
        return

    result = str(getattr(resp, "result", "") or "").lower()
    print(f"result: {getattr(resp, 'result', '—')}")
    print(f"Період: {date_from_str} — {date_to_str}")
    print(f"Розмір XML: {len(xml_str)} символів, платежів у парсері: {len(rows)}")

    if result != "ok":
        err = getattr(resp, "error", None)
        print("Помилка API:", err if err is not None else resp)
        return

    if not rows:
        print("\n" + "=" * 80)
        print("За обраний період операцій немає (<Payments /> — це нормальна відповідь API).")
        print("=" * 80)
        if days < 90:
            print(f"\nПеревіряю ширший період (90 дн.)...")
            try:
                _, _, wide_to, _, wide_rows = _fetch_payments(client, principal, account_id, 90)
            except Exception as e:
                print(f"Не вдалося: {e}")
                wide_rows = []
            if wide_rows:
                dates = _payment_dates(wide_rows)
                print(f"За 90 дн. знайдено {len(wide_rows)} платежів.")
                if dates:
                    print(f"Остання операція у вибірці: {dates[-1]}")
                    print(
                        f"Підказка: для короткого вікна ({days} дн.) результат порожній, "
                        f"бо проведених платежів після {dates[-1]} у цьому періоді не було."
                    )
                print("Спробуйте: python scripts/novapay_payments_probe.py --days 90")
            else:
                print("Навіть за 90 дн. платежів немає — перевірте рахунок або права доступу.")
        print("\nСирий XML:")
        print(xml_str[:500] or "(порожньо)")
        return

    print("\n" + "=" * 80)
    print("СИРИЙ XML (перші 2500 символів):")
    print("=" * 80)
    print(xml_str[:2500])
    if len(xml_str) > 2500:
        print("\n... (обрізано, всього символів:", len(xml_str), ")")
    print("\n" + "=" * 80)

    doc_count = _print_payment_details(xml_str)
    print(f"\nВсього платежів у вибірці: {doc_count}")
    print("\nNovaPay у XML не віддає <Comment> — призначення лише в <Purpose>.")
    print("При синхронізації purpose копіюється в comment для класифікації та пошуку.")


if __name__ == "__main__":
    main()
