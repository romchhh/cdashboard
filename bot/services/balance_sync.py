# Синхронізація балансів рахунків в БД

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import AccountBalance, FOP, TransactionSource
from bot.services.balance import get_all_balances

logger = logging.getLogger(__name__)


def format_balance_display(amount: float) -> str:
    """Без копійок, роздільник тисяч — крапка (напр. 27.809, 513)."""
    n = int(round(amount))
    return f"{n:,}".replace(",", ".")


def format_amount_signed(amount: float) -> str:
    """Форматування суми зі знаком: +27.809 або -1.945 (без копійок, крапка — тисячі)."""
    return ("+" if amount >= 0 else "-") + format_balance_display(abs(amount))


async def sync_balances_to_db(session: AsyncSession):
    """
    Отримує поточні баланси з API та зберігає в БД.
    Викликається періодично для збереження історії балансів.
    """
    logger.info("💰 Синхронізація балансів...")
    
    try:
        # Отримати баланси з API
        balances = await get_all_balances()
        from bot.keyboards.inline import get_fop_list

        allowed = {f.name for f in await get_fop_list(session)}
        balances = {
            "monobank": [a for a in balances.get("monobank", []) if a.get("fop") in allowed],
            "novapay": [a for a in balances.get("novapay", []) if a.get("fop") in allowed],
        }
        
        records_created = 0
        
        # Monobank баланси
        for acc in balances.get("monobank", []):
            # Знайти або створити ФОП
            result = await session.execute(
                select(FOP).where(FOP.name == acc["fop"])
            )
            fop = result.scalar_one_or_none()
            
            if not fop:
                fop = FOP(name=acc["fop"], is_active=True)
                session.add(fop)
                await session.flush()
            elif not fop.is_active:
                continue
            
            # Створити запис балансу
            balance_record = AccountBalance(
                fop_id=fop.id,
                source=TransactionSource.MONOBANK,
                balance=acc["balance"],
                currency=acc["currency"],
                iban=acc["iban"],
                account_name=f"{acc['fop']} (Monobank)",
                recorded_at=datetime.utcnow()
            )
            session.add(balance_record)
            records_created += 1
        
        # NovaPay баланси
        for acc in balances.get("novapay", []):
            # Знайти або створити ФОП
            result = await session.execute(
                select(FOP).where(FOP.name == acc["fop"])
            )
            fop = result.scalar_one_or_none()
            
            if not fop:
                fop = FOP(name=acc["fop"], is_active=True)
                session.add(fop)
                await session.flush()
            elif not fop.is_active:
                continue
            
            # Створити запис балансу
            balance_record = AccountBalance(
                fop_id=fop.id,
                source=TransactionSource.NOVAPAY,
                balance=acc["balance"],
                currency=acc["currency"],
                iban=acc["iban"],
                account_name=f"{acc['fop']} (NovaPay)",
                recorded_at=datetime.utcnow()
            )
            session.add(balance_record)
            records_created += 1
        
        await session.commit()
        logger.info(f"✅ Збережено {records_created} записів балансів")
        
    except Exception as e:
        logger.error(f"❌ Помилка синхронізації балансів: {e}")
        await session.rollback()


async def get_latest_balances(session: AsyncSession) -> list[dict]:
    """
    Останні баланси з БД — лише для ФОП з get_fop_list (активні + в .env).
    """
    from sqlalchemy import func

    from bot.keyboards.inline import get_fop_list

    active_fops = await get_fop_list(session)
    active_ids = {int(f.id) for f in active_fops}
    if not active_ids:
        return []

    subquery = (
        select(
            AccountBalance.fop_id,
            AccountBalance.source,
            func.max(AccountBalance.recorded_at).label("max_date"),
        )
        .where(AccountBalance.fop_id.in_(active_ids))
        .group_by(AccountBalance.fop_id, AccountBalance.source)
        .subquery()
    )

    result = await session.execute(
        select(AccountBalance, FOP)
        .join(FOP, AccountBalance.fop_id == FOP.id)
        .where(FOP.id.in_(active_ids))
        .join(
            subquery,
            (AccountBalance.fop_id == subquery.c.fop_id)
            & (AccountBalance.source == subquery.c.source)
            & (AccountBalance.recorded_at == subquery.c.max_date),
        )
        .order_by(FOP.name, AccountBalance.source)
    )

    balances = []
    for balance, fop in result:
        balances.append(
            {
                "fop_name": fop.name,
                "source": balance.source.value,
                "balance": balance.balance,
                "currency": balance.currency,
                "iban": balance.iban,
                "recorded_at": balance.recorded_at,
            }
        )

    return balances


async def filter_api_balances(session: AsyncSession, balances: dict) -> dict:
    """Залишити в відповіді API лише рахунки активних ФОП з .env."""
    from bot.keyboards.inline import get_fop_list

    allowed = {f.name for f in await get_fop_list(session)}
    return {
        "monobank": [a for a in balances.get("monobank", []) if a.get("fop") in allowed],
        "novapay": [a for a in balances.get("novapay", []) if a.get("fop") in allowed],
    }


async def get_balance_history(
    session: AsyncSession,
    fop_id: int = None,
    source: TransactionSource = None,
    days: int = 30
) -> list[dict]:
    """
    Отримати історію балансів за період.
    Корисно для графіків та аналізу динаміки.
    """
    from datetime import timedelta
    
    date_from = datetime.utcnow() - timedelta(days=days)
    
    query = select(AccountBalance, FOP).join(FOP, AccountBalance.fop_id == FOP.id)
    query = query.where(AccountBalance.recorded_at >= date_from)
    
    if fop_id:
        query = query.where(AccountBalance.fop_id == fop_id)
    if source:
        query = query.where(AccountBalance.source == source)
    
    query = query.order_by(AccountBalance.recorded_at.desc())
    
    result = await session.execute(query)
    
    history = []
    for balance, fop in result:
        history.append({
            "fop_name": fop.name,
            "source": balance.source.value,
            "balance": balance.balance,
            "currency": balance.currency,
            "recorded_at": balance.recorded_at,
        })
    
    return history
