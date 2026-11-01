"""
Тестовий скрипт для перевірки платежів Monobank API.
Виводить усю інформацію про кожен платіж, щоб порівняти коментар і опис (description).
Запуск: python test_mono_payments.py [--fop ФОП|номер] [--limit N] [--days N]
"""

import argparse
import json
import time
from datetime import datetime

from mono.api import get_client_info, get_statement, format_amount, utc_timestamp_to_kyiv
from mono.config import MONO_TOKENS


def main():
    parser = argparse.ArgumentParser(description="Перевірка платежів Monobank API")
    parser.add_argument(
        "--fop",
        default=None,
        help="ФОП: назва або номер зі списку (наприклад 1, 2). Якщо не вказано — вибір інтерактивно або перший.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Скільки останніх платежів показати (за замовч. 5)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=31,
        help="За скільки днів назад завантажувати виписку (за замовч. 31)",
    )
    args = parser.parse_args()

    if not MONO_TOKENS:
        print("MONO_TOKENS порожні. Задайте MONO_TOKEN_1=ФОП|токен у .env")
        return

    fop_names = list(MONO_TOKENS.keys())

    # Вибір ФОП
    if args.fop is not None:
        if args.fop.isdigit():
            idx = int(args.fop)
            if 1 <= idx <= len(fop_names):
                fop_name = fop_names[idx - 1]
            else:
                print(f"Номер ФОП має бути від 1 до {len(fop_names)}")
                return
        elif args.fop in MONO_TOKENS:
            fop_name = args.fop
        else:
            print(f"ФОП не знайдено: {args.fop!r}. Доступні: {fop_names}")
            return
    else:
        print("Доступні ФОПи:")
        for i, name in enumerate(fop_names, 1):
            print(f"  {i}. {name}")
        try:
            choice = input(f"Оберіть номер (1–{len(fop_names)}) або Enter для першого: ").strip()
            if not choice:
                fop_name = fop_names[0]
            else:
                idx = int(choice)
                fop_name = fop_names[idx - 1]
        except (ValueError, IndexError):
            print("Невірний вибір, використовується перший ФОП.")
            fop_name = fop_names[0]

    token = MONO_TOKENS[fop_name]
    print(f"\nОбрано ФОП: {fop_name}\n")

    client_info, error_reason, _status = get_client_info(token)
    if not client_info:
        print(f"Не вдалося отримати client-info: {error_reason}")
        return

    fop_account = None
    for acc in client_info.get("accounts", []):
        if acc.get("type") == "fop":
            fop_account = acc
            break
    if not fop_account:
        print("ФОП-рахунок не знайдено")
        return

    account_id = fop_account["id"]
    to_ts = int(time.time())
    from_ts = to_ts - (args.days * 24 * 60 * 60)

    print(f"Завантаження виписки (останні {args.days} дн., показати останні {args.limit} платежів)...\n")
    transactions = get_statement(token, account_id, from_ts, to_ts)
    if not transactions:
        print("Транзакцій не знайдено")
        return

    # Беремо тільки обрану кількість останніх (API повертає від нових до старих)
    transactions = transactions[: args.limit]
    print(f"Показано транзакцій: {len(transactions)}\n")
    print("=" * 80)

    for i, tx in enumerate(transactions, 1):
        print(f"\n--- ПЛАТІЖ #{i} ---")
        print(f"Дата (Київ): {utc_timestamp_to_kyiv(tx['time']).strftime('%d.%m.%Y %H:%M:%S')}")
        print(f"Сума: {format_amount(tx['amount']):+.2f} грн")
        print()

        # Усі ключі, що повертає API
        print("Усі поля відповіді API:")
        for key in sorted(tx.keys()):
            value = tx[key]
            if isinstance(value, str) and len(value) > 80:
                value = value[:80] + "..."
            print(f"  {key!r}: {value!r}")
        print()

        # Явно: опис і контрагент (у Mono: description та counterName)
        description = tx.get("description") or ""
        counter_name = tx.get("counterName") or ""
        print("Порівняння (опис vs коментар/контрагент):")
        print(f"  description (опис/призначення): {description!r}")
        print(f"  counterName (контрагент):       {counter_name!r}")
        print(f"  Однакові? {description.strip() == counter_name.strip()}")
        if description != counter_name:
            print("  -> у БД зберігаємо: counterparty=counterName, description=description; у таблиці коментар показуємо тільки якщо description != counterName")
        print()

        # Повний JSON одного платежу (для копіювання)
        print("Повний JSON платежу:")
        print(json.dumps(tx, ensure_ascii=False, indent=2))
        print("=" * 80)

    print("\nВисновок: у Monobank API є три окремі поля:")
    print("  - description (опис/рядок від банку, напр. «Від: ФОП …»)")
    print("  - counterName (контрагент)")
    print("  - comment (коментар до платежу, напр. «Сплата за товар», «Рахунок 0211»)")
    print("У БД зберігаються всі три; класифікація категорій використовує опис + контрагент + коментар.")


if __name__ == "__main__":
    main()
