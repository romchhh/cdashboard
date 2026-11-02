# Синхронізація транзакцій з Monobank та NovaPay

import asyncio
import logging
import time
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram import Bot

from config import BOT_TOKEN, administrators
from bot.database.models import Transaction, TransactionType, TransactionSource, TransactionStatus, FOP, Category
from bot.services.classifier import TransactionClassifier, forced_expense_category
from mono.api import (
    get_client_info_retried,
    get_all_statements,
    format_amount,
    utc_timestamp_to_kyiv,
    now_kyiv,
    MonoStatementError,
)
from config import INACTIVE_FOP_NAMES
from mono.config import MONO_TOKENS
from novapay.auth import (
    create_client,
    ensure_jwt_session,
    load_session,
    novapay_session_rejected,
    renew_jwt_for_fop,
)
from novapay.accounts import (
    get_accounts_list,
    get_payments_list,
    get_clients_list,
    novapay_response_error,
    unwrap_zeep_list,
)
from novapay.config import NOVAPAY_TOKENS, get_session_file

logger = logging.getLogger(__name__)


def _load_novapay_payments(user_key: str, days: int) -> dict:
    """Мережа NovaPay. Лише з окремого потоку, інакше сайт не відповідає, поки банк мовчить."""
    from novapay.menu import _parse_payments_xml, is_importable_novapay_payment

    session_file = get_session_file(user_key)
    try:
        client = create_client()
        principal = ensure_jwt_session(user_key, session_file=session_file)
        if not principal:
            principal, _ = load_session(session_file=session_file)
        if not principal:
            return {
                "ok": False,
                "error": "no-session",
                "session_file": session_file,
                "notify": (
                    f"❌ NovaPay: немає активного токена/сесії для ключа <b>{user_key}</b> "
                    f"(файл {session_file}). Увійдіть у NovaPay через бота або оновіть токени."
                ),
            }

        clients_resp = get_clients_list(client, principal)
        auth_err = novapay_response_error(clients_resp)
        if auth_err and novapay_session_rejected(auth_err):
            renewed = renew_jwt_for_fop(user_key, session_file=session_file)
            if renewed.get("ok") and renewed.get("jwt"):
                principal = renewed["jwt"]
                clients_resp = get_clients_list(client, principal)
                auth_err = novapay_response_error(clients_resp)
        if auth_err:
            short = auth_err.split("\n", 1)[0]
            return {
                "ok": False,
                "error": short,
                "session_file": session_file,
                "notify": f"❌ NovaPay <b>{user_key}</b>: {short}",
            }

        clients = unwrap_zeep_list(getattr(clients_resp, "clients", None), ["Clients", "item", "Item"])
        if not clients:
            return {"ok": False, "error": "no-clients", "session_file": session_file}

        first_client = clients[0]
        client_id = getattr(first_client, "id", None) or getattr(first_client, "Id", None)
        acc_resp = get_accounts_list(client, principal, client_id)
        accounts = unwrap_zeep_list(getattr(acc_resp, "accounts", None), ["Account", "Accounts", "item", "Item"])
        if not accounts:
            return {"ok": False, "error": "no-accounts", "session_file": session_file}

        first_account = accounts[0]
        account_id = getattr(first_account, "id", None) or getattr(first_account, "Id", None)
        date_to = now_kyiv()
        date_from = date_to - timedelta(days=days)
        payments_resp = get_payments_list(
            client,
            principal,
            account_id=account_id,
            date_from=date_from.strftime("%d.%m.%Y"),
            date_to=date_to.strftime("%d.%m.%Y"),
        )
        payments_raw = getattr(payments_resp, "payments", None)
        payments = _parse_payments_xml(payments_raw) if payments_raw else []
        importable = [p for p in payments if is_importable_novapay_payment(p)]
        return {
            "ok": True,
            "payments": importable,
            "skipped_status": len(payments) - len(importable),
            "received": len(payments),
            "session_file": session_file,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "session_file": session_file,
            "notify": f"❌ NovaPay: помилка при отриманні платежів для ключа <b>{user_key}</b>: {exc}",
        }


async def _notify_admins(text: str) -> None:
    """
    Відправляє службове повідомлення всім адміністраторам через Telegram-бота.
    Використовується, зокрема, для алертів про проблеми з токенами Monobank / NovaPay.
    """
    if not BOT_TOKEN or not administrators:
        logger.warning("Не можу надіслати алерт адміністраторам: BOT_TOKEN або administrators не налаштовані")
        return
    bot = Bot(token=BOT_TOKEN)
    try:
        for admin_id in administrators:
            try:
                await bot.send_message(admin_id, text, parse_mode="HTML")
            except Exception as e:
                logger.error("Не вдалося надіслати повідомлення адміну %s: %s", admin_id, e)
    finally:
        await bot.session.close()


async def _get_or_create_fop(session: AsyncSession, name: str) -> FOP | None:
    """Знайти FOP за назвою або створити, якщо немає."""
    result = await session.execute(select(FOP).where(FOP.name == name))
    fop = result.scalar_one_or_none()
    if not fop:
        fop = FOP(name=name, is_active=True)
        session.add(fop)
        await session.flush()
        logger.info("Створено FOP у БД: %s", name)
    return fop


class TransactionSyncService:
    """Сервіс синхронізації транзакцій"""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.classifier = TransactionClassifier(session)

    async def _confirm_if_forced(self, tx: Transaction, threshold: float, *, within_days: int = 14) -> bool:
        """Підтвердити нещодавнє списання на картку, яке інакше лишається поза списком."""
        if tx.status != TransactionStatus.PENDING or tx.transaction_type != TransactionType.EXPENSE:
            return False
        if not tx.date or tx.date < now_kyiv() - timedelta(days=within_days):
            return False
        if not forced_expense_category(tx.description, tx.counterparty, tx.comment):
            return False
        category_id, confidence = await self.classifier.classify(
            tx.description or "",
            tx.counterparty or "",
            tx.comment or "",
        )
        if not category_id or confidence < threshold:
            return False
        tx.category_id = category_id
        tx.status = TransactionStatus.CONFIRMED
        return True

    async def confirm_recent_card_debits(self, within_days: int = 14) -> int:
        threshold = await self.classifier.get_confidence_threshold()
        cutoff = now_kyiv() - timedelta(days=within_days)
        result = await self.session.execute(
            select(Transaction).where(
                Transaction.status == TransactionStatus.PENDING,
                Transaction.transaction_type == TransactionType.EXPENSE,
                Transaction.date >= cutoff,
            )
        )
        updated = 0
        for tx in result.scalars().all():
            if await self._confirm_if_forced(tx, threshold, within_days=within_days):
                updated += 1
        if updated:
            await self.session.commit()
            logger.info("Підтверджено карткові списання за %s дн.: %s", within_days, updated)
        return updated

    async def sync_monobank(self, fop_name: str, days: int = 30):
        """Синхронізація транзакцій з Monobank"""
        if fop_name in INACTIVE_FOP_NAMES:
            return

        fop = await _get_or_create_fop(self.session, fop_name)
        if not fop or not fop.is_active:
            return

        token = MONO_TOKENS.get(fop_name)
        if not token:
            logger.warning("Monobank: немає токена для FOP %s", fop_name)
            await _notify_admins(
                f"❌ Monobank: немає токена для ФОП <b>{fop_name}</b>. "
                "Перевірте налаштування Monobank для ФОП у веб-адмінці (БД)."
            )
            return

        to_time = int(time.time())
        from_time = to_time - (days * 24 * 60 * 60)

        client_info, error_reason, http_status = await asyncio.to_thread(
            get_client_info_retried, token
        )
        if not client_info:
            logger.warning(
                "Monobank: не вдалося отримати client_info для %s (HTTP %s): %s",
                fop_name,
                http_status,
                error_reason,
            )
            await _notify_admins(
                f"❌ Monobank: не вдалося отримати дані по ФОП <b>{fop_name}</b>.\n"
                f"Причина: {error_reason or 'невідома помилка'}\n"
                f"<i>Якщо це 500/JarsByClient — це збій на стороні Monobank; "
                f"наступний автосинк через ~30 хв або запустіть синк вручну.</i>"
            )
            return

        fop_account = None
        from config import mono_iban_for_fop

        preferred_iban = mono_iban_for_fop(fop_name)
        if preferred_iban:
            for account in client_info["accounts"]:
                if (account.get("iban") or "").strip() == preferred_iban:
                    fop_account = account
                    break
            if not fop_account:
                logger.warning(
                    "Monobank: для %s не знайдено рахунок з IBAN %s — шукаємо type=fop",
                    fop_name,
                    preferred_iban,
                )
        if not fop_account:
            for account in client_info["accounts"]:
                if account["type"] == "fop":
                    fop_account = account
                    break

        if not fop_account:
            logger.warning("Monobank: не знайдено рахунку типу fop для %s", fop_name)
            return

        try:
            transactions = await asyncio.to_thread(
                get_all_statements, token, fop_account["id"], from_time, to_time
            )
        except MonoStatementError as e:
            logger.exception("Monobank: помилка виписки для %s: %s", fop_name, e)
            await _notify_admins(
                f"❌ Monobank: не вдалося завантажити виписку для ФОП <b>{fop_name}</b> "
                f"за {days} дн.: <code>{e}</code>\n"
                "Надходження в Фінадміні можуть бути неповними, поки синк не пройде успішно."
            )
            return
        except Exception as e:
            logger.exception("Monobank: помилка get_all_statements для %s: %s", fop_name, e)
            await _notify_admins(
                f"❌ Monobank: помилка при завантаженні виписок для ФОП <b>{fop_name}</b>: {e}"
            )
            return

        # Вхідні = продаж, без розподілу (фізособи, ФОП, банк, NovaPay тощо)
        sales_row = await self.session.execute(select(Category).where(Category.name == "Продаж"))
        sales_category = sales_row.scalar_one_or_none()
        sales_category_id = sales_category.id if sales_category else None

        # Зберегти транзакції в БД
        new_count = 0
        for tx in transactions:
            external_id = f"mono_{tx['id']}"

            # Перевірити, чи вже є
            existing = await self.session.execute(
                select(Transaction).where(Transaction.external_id == external_id)
            )
            if existing.scalar_one_or_none():
                continue

            new_count += 1
            amount = abs(format_amount(tx["amount"]))
            tx_type = TransactionType.INCOME if tx["amount"] > 0 else TransactionType.EXPENSE

            # Вхідні → Продаж; витрати → класифікатор (постачальник/повернення за коментарем)
            if tx_type == TransactionType.INCOME:
                category_id, confidence = sales_category_id, 1.0
            else:
                category_id, confidence = await self.classifier.classify(
                    tx.get("description", ""),
                    tx.get("counterName", ""),
                    tx.get("comment", ""),
                )

            threshold = await self.classifier.get_confidence_threshold()
            status = TransactionStatus.CONFIRMED if confidence >= threshold else TransactionStatus.PENDING

            new_tx = Transaction(
                external_id=external_id,
                amount=amount,
                transaction_type=tx_type,
                date=utc_timestamp_to_kyiv(tx["time"]),
                source=TransactionSource.MONOBANK,
                fop_id=fop.id,
                category_id=category_id,
                status=status,
                counterparty=tx.get("counterName", ""),
                description=tx.get("description", ""),
                comment=tx.get("comment", ""),
            )
            self.session.add(new_tx)

        await self.session.commit()
        logger.info("Monobank %s: додано нових транзакцій %s (всього отримано %s)", fop_name, new_count, len(transactions))

    async def sync_novapay(self, user_key: str, days: int = 30):
        """Синхронізація транзакцій з NovaPay"""
        fop = await _get_or_create_fop(self.session, user_key)
        if not fop:
            return

        fetched = await asyncio.to_thread(_load_novapay_payments, user_key, days)
        if not fetched.get("ok"):
            error = fetched.get("error") or "невідома помилка"
            logger.warning("NovaPay %s: %s", user_key, error)
            if fetched.get("notify"):
                await _notify_admins(fetched["notify"])
            return

        from novapay.menu import (
            novapay_legacy_external_id,
            novapay_primary_external_id,
            novapay_same_payment_record,
        )
        importable = fetched.get("payments") or []
        skipped_status = int(fetched.get("skipped_status") or 0)
        received_n = int(fetched.get("received") or 0)

        # Вхідні = продаж, без розподілу
        sales_row = await self.session.execute(select(Category).where(Category.name == "Продаж"))
        sales_category = sales_row.scalar_one_or_none()
        sales_category_id = sales_category.id if sales_category else None

        new_count = 0
        skipped_dup = 0
        skipped_no_id = 0
        migrated_ids = 0
        confirmed_hidden = 0
        threshold = await self.classifier.get_confidence_threshold()
        for p in importable:
            external_id = novapay_primary_external_id(p)
            if not external_id:
                skipped_no_id += 1
                continue

            existing = await self.session.execute(
                select(Transaction).where(Transaction.external_id == external_id)
            )
            existing_tx = existing.scalar_one_or_none()
            if existing_tx:
                if await self._confirm_if_forced(existing_tx, threshold):
                    confirmed_hidden += 1
                skipped_dup += 1
                continue

            legacy_id = novapay_legacy_external_id(p)
            if legacy_id and legacy_id != external_id:
                leg_res = await self.session.execute(
                    select(Transaction).where(Transaction.external_id == legacy_id)
                )
                legacy_tx = leg_res.scalar_one_or_none()
                if legacy_tx:
                    if novapay_same_payment_record(legacy_tx, p):
                        legacy_tx.external_id = external_id
                        if await self._confirm_if_forced(legacy_tx, threshold):
                            confirmed_hidden += 1
                        migrated_ids += 1
                        skipped_dup += 1
                        continue
                    # Інакше legacy з code+date належить іншому платежу — імпортуємо з doc ID.

            new_count += 1
            amount = abs(p["amount"])
            tx_type = TransactionType.EXPENSE if p["amount"] < 0 else TransactionType.INCOME

            # Вхідні → Продаж; витрати → класифікатор
            if tx_type == TransactionType.INCOME:
                category_id, confidence = sales_category_id, 1.0
            else:
                category_id, confidence = await self.classifier.classify(
                    p.get("purpose", ""),
                    p.get("counterparty", ""),
                    p.get("comment", ""),
                )

            threshold = await self.classifier.get_confidence_threshold()
            status = TransactionStatus.CONFIRMED if confidence >= threshold else TransactionStatus.PENDING

            # NovaPay: дата+час з datetime_str (Changed/Created), інакше лише дата; fallback — Київ
            novapay_date = now_kyiv()
            if p.get("datetime_str") and " " in p["datetime_str"]:
                try:
                    novapay_date = datetime.strptime(p["datetime_str"], "%d.%m.%Y %H:%M:%S")
                except (ValueError, TypeError):
                    novapay_date = datetime.strptime(p["date"], "%d.%m.%Y") if p.get("date") else now_kyiv()
            elif p.get("date"):
                novapay_date = datetime.strptime(p["date"], "%d.%m.%Y")
            # NovaPay часто повертає лише Purpose; якщо окремого Comment немає — дублюємо purpose у comment
            novapay_comment = (p.get("comment") or "").strip() or (p.get("purpose") or "").strip() or ""
            new_tx = Transaction(
                external_id=external_id,
                amount=amount,
                transaction_type=tx_type,
                date=novapay_date,
                source=TransactionSource.NOVAPAY,
                fop_id=fop.id,
                category_id=category_id,
                status=status,
                counterparty=p.get("counterparty", ""),
                description=p.get("purpose", ""),
                comment=novapay_comment,
            )
            self.session.add(new_tx)

        await self.session.commit()
        logger.info(
            "NovaPay %s: додано %s, підтверджено прихованих %s, пропущено дублікатів %s (міграцій ID %s), "
            "без ID %s, отримано %s, проведених %s, пропущено статусом %s",
            user_key,
            new_count,
            confirmed_hidden,
            skipped_dup,
            migrated_ids,
            skipped_no_id,
            received_n,
            len(importable),
            skipped_status,
        )

    async def sync_all(self, days: int = 30):
        """Синхронізувати всі джерела (Monobank + NovaPay) за останні days днів."""
        mono_keys = list(MONO_TOKENS.keys())
        nova_keys = list(NOVAPAY_TOKENS.keys())
        await self.confirm_recent_card_debits()
        if not mono_keys and not nova_keys:
            logger.warning("Синхронізація: немає жодного джерела (MONO_TOKENS і NOVAPAY_TOKENS порожні)")
            return
        logger.info("Синхронізація: Monobank FOPs=%s, NovaPay keys=%s, days=%s", mono_keys, nova_keys, days)

        for idx, fop_name in enumerate(mono_keys):
            if idx > 0:
                await asyncio.sleep(3)
            await self.sync_monobank(fop_name, days)

        for user_key in nova_keys:
            await self.sync_novapay(user_key, days)
