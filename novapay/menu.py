# Уніфіковане меню для роботи з рахунком NovaPay

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

from zeep.exceptions import Fault

from novapay.accounts import (
    unwrap_zeep_list,
    get_account_rest,
    get_payments_list,
    get_account_turns,
)

# Імпортуємо лише проведені платежі; решта — чернетки/відхилені спроби того ж платежу.
NOVAPAY_IMPORTABLE_STATUSES = frozenset({"Conducted"})


def _local_tag(elem):
    """Повертає локальне ім'я тега без namespace."""
    return elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag


def is_importable_novapay_payment(payment: dict) -> bool:
    """Чи варто зберігати платіж у БД (лише фактично проведені)."""
    status = str(payment.get("status") or "").strip()
    if status:
        return status in NOVAPAY_IMPORTABLE_STATUSES
    # Проведені документи NovaPay мають числовий ID; чернетки — ні.
    return bool(str(payment.get("doc_id") or "").strip())


def novapay_legacy_external_id(payment: dict) -> str | None:
    """Старий формат external_id (code + date). Може збігатися для різних платежів."""
    code = str(payment.get("code") or "").strip()
    date = str(payment.get("date") or "").strip()
    if code and date:
        return f"novapay_{code}_{date}"
    return None


def novapay_primary_external_id(payment: dict) -> str | None:
    """Канонічний external_id: спочатку doc ID з NovaPay, інакше legacy."""
    doc_id = str(payment.get("doc_id") or "").strip()
    if doc_id:
        return f"novapay_{doc_id}"
    return novapay_legacy_external_id(payment)


def novapay_payment_external_ids(payment: dict) -> list[str]:
    """Усі можливі external_id (для зворотної сумісності)."""
    ids: list[str] = []
    primary = novapay_primary_external_id(payment)
    if primary:
        ids.append(primary)
    legacy = novapay_legacy_external_id(payment)
    if legacy and legacy not in ids:
        ids.append(legacy)
    return ids


def _parse_payment_date(payment: dict):
    ds = str(payment.get("date") or "").strip()[:10]
    if not ds:
        return None
    try:
        return datetime.strptime(ds, "%d.%m.%Y").date()
    except ValueError:
        return None


def novapay_same_payment_record(existing_tx, payment: dict) -> bool:
    """Чи legacy-запис у БД — це той самий платіж (сума + дата)."""
    try:
        pay_amount = abs(float(payment.get("amount") or 0))
    except (TypeError, ValueError):
        return False
    if abs(float(existing_tx.amount or 0) - pay_amount) >= 0.01:
        return False
    ex_date = existing_tx.date.date() if getattr(existing_tx, "date", None) else None
    pay_date = _parse_payment_date(payment)
    return bool(ex_date and pay_date and ex_date == pay_date)


def _parse_payments_xml(payments_str):
    """
    Парсить XML відповідь GetPaymentsList (п. 3.7).
    Повертає список словників: date, amount, currency, code, doc_id, status, counterparty, purpose.
    """
    if not payments_str or not isinstance(payments_str, str):
        return []
    try:
        root = ET.fromstring(payments_str)
    except ET.ParseError:
        return []

    result = []
    for elem in root.iter():
        if _local_tag(elem) != "Docs":
            continue
        el = elem

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
        # Debit = списання з рахунку (мінус), Credit = надходження (плюс)
        payment_type = text("PaymentType")
        if payment_type and payment_type.lower() == "debit":
            amount = -abs(amount)
        else:
            amount = abs(amount)

        date_val = text("DayDate") or text("PayDate") or text("OrgDate")
        # Час з Changed або Created (формат "dd.mm.yyyy HH:MM:SS"), інакше лише дата
        datetime_str = text("Changed") or text("Created") or (f"{date_val} 00:00:00" if date_val else "")
        if payment_type and payment_type.lower() == "debit":
            counterparty = text("CreditName")  # хто отримав (ми платники)
        else:
            counterparty = text("DebitName")  # хто відправив (ми отримувачі)

        result.append({
            "date": date_val,
            "datetime_str": datetime_str,
            "amount": amount,
            "currency": attr("CurrencyTag", "UAH"),
            "code": text("Code"),
            "doc_id": text("ID"),
            "status": text("StatusDocument"),
            "status_id": text("StatusDocumentId"),
            "counterparty": counterparty or "—",
            "purpose": text("Purpose") or "—",
            "comment": text("Comment") or "",
            "payment_type": payment_type,
        })
    return result


def show_balance(client, principal, account_id, account_info):
    """1. Баланс — доступний, підтверджений, прогнозований."""
    print("\n" + "=" * 60)
    print("💰 БАЛАНС")
    print("=" * 60)
    try:
        rest_resp = get_account_rest(client, principal, account_id)
        if getattr(rest_resp, "result", "").lower() == "ok":
            conf = getattr(rest_resp, "confirmed_balance", None)
            avail = getattr(rest_resp, "available_balance", None)
            proj = getattr(rest_resp, "projected_balance", None)
            currency = account_info.get("currency", "UAH")
            print(f"Підтверджений: {conf} {currency}")
            print(f"Доступний:     {avail} {currency}")
            print(f"Прогнозований: {proj} {currency}")
        else:
            print("❌ Помилка:", getattr(rest_resp, "error", rest_resp.result))
    except Exception as e:
        print(f"❌ {e}")


def show_transactions(client, principal, account_id, account_info):
    """2. Транзакції — перелік платежів за період."""
    print("\n" + "=" * 60)
    print("📋 ТРАНЗАКЦІЇ (ПЛАТЕЖІ)")
    print("=" * 60)
    days = input("Кількість днів (Enter=7): ").strip() or "7"
    try:
        days_int = int(days)
    except ValueError:
        days_int = 7
    
    date_to = datetime.now()
    date_from = date_to - timedelta(days=days_int)
    date_from_str = date_from.strftime("%d.%m.%Y")
    date_to_str = date_to.strftime("%d.%m.%Y")
    
    try:
        resp = get_payments_list(
            client, principal, account_id=account_id, date_from=date_from_str, date_to=date_to_str
        )
        if getattr(resp, "result", "").lower() == "ok":
            payments_raw = getattr(resp, "payments", None)
            rows = _parse_payments_xml(payments_raw) if payments_raw else []
            if rows:
                print(f"\n✓ Платежі за {days_int} днів ({date_from_str} – {date_to_str}): {len(rows)} шт.")
                print(f"{'Дата':<12} {'Сума':>14} {'Статус':<12} {'Контрагент':<28} {'Призначення':<20}")
                print("-" * 92)
                for r in rows:
                    date_s = (r["date"] or "—")[:10]
                    amount = r["amount"]
                    sign = "+" if amount >= 0 else ""
                    curr = r.get("currency", "UAH")
                    status = (r.get("status") or "—")[:11]
                    cp = (r["counterparty"] or "—")[:27] + ("…" if len(r["counterparty"] or "") > 27 else "")
                    purpose = (r["purpose"] or "—")[:19] + ("…" if len(r["purpose"] or "") > 19 else "")
                    print(f"{date_s:<12} {sign}{amount:>12,.2f} {curr} {status:<12} {cp:<28} {purpose:<20}")
            else:
                print("Платежів не знайдено.")
        else:
            print("❌ Помилка:", getattr(resp, "error", resp.result))
    except Exception as e:
        print(f"❌ {e}")


def show_statistics(client, principal, account_id, account_info):
    """3. Статистика — обороти за рахунком."""
    print("\n" + "=" * 60)
    print("📊 СТАТИСТИКА (ОБОРОТИ)")
    print("=" * 60)
    days = input("Кількість днів (Enter=30): ").strip() or "30"
    try:
        days_int = int(days)
    except ValueError:
        days_int = 30
    
    date_to = datetime.now()
    date_from = date_to - timedelta(days=days_int)
    date_from_str = date_from.strftime("%d.%m.%Y")
    date_to_str = date_to.strftime("%d.%m.%Y")
    
    try:
        resp = get_account_turns(
            client, principal, account_id=account_id, date_from=date_from_str, date_to=date_to_str
        )
        if getattr(resp, "result", "").lower() == "ok":
            turns = getattr(resp, "turns", None)
            turns_list = unwrap_zeep_list(turns, ["Turns", "item", "Item"])
            if turns_list:
                print(f"\nОбороти за {days_int} днів ({date_from_str} – {date_to_str}):")
                print(f"{'Дата':<12} {'IBAN':<30} {'Залишок':>15}")
                print("-" * 60)
                for t in turns_list:
                    d = getattr(t, "date", "—")
                    iban = getattr(t, "IBAN", "—")
                    cr = getattr(t, "CrncyRest", "—")
                    print(f"{str(d):<12} {str(iban):<30} {str(cr):>15}")
            else:
                print("Оборотів не знайдено.")
        else:
            print("❌ Помилка:", getattr(resp, "error", resp.result))
    except Exception as e:
        print(f"❌ {e}")


def run_account_menu(client, principal, account_id, account_info):
    """Меню з 3 діями для рахунку NovaPay."""
    iban = account_info.get("iban", "—")
    currency = account_info.get("currency", "UAH")
    name = account_info.get("name", "—")

    while True:
        print("\n" + "=" * 60)
        print(f"Рахунок: {iban} ({currency})")
        print(f"{name}")
        print("=" * 60)
        print("1. 💰 Баланс")
        print("2. 📋 Транзакції (платежі)")
        print("3. 📊 Статистика (обороти)")
        print("0. ⬅️  Назад")

        choice = input("\nВаш вибір (0-3): ").strip()
        if choice == "0":
            return
        elif choice == "1":
            show_balance(client, principal, account_id, account_info)
        elif choice == "2":
            show_transactions(client, principal, account_id, account_info)
        elif choice == "3":
            show_statistics(client, principal, account_id, account_info)
        else:
            print("❌ Введіть 0-3")
