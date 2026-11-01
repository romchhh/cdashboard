# Ініціалізація початкових даних в БД

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from bot.database.models import Category, ClassificationRule
from bot.database.categories_config import CATEGORIES, CATEGORY_GROUPS
import logging

logger = logging.getLogger(__name__)


# Використовуємо категорії з конфігураційного файлу
DEFAULT_CATEGORIES = CATEGORIES


async def init_categories(session: AsyncSession):
    """
    Створити стандартні категорії з конфігу.
    Якщо категорії вже є — додати лише ті з DEFAULT_CATEGORIES, яких немає за назвою.
    """
    result = await session.execute(select(Category))
    existing = result.scalars().all()
    existing_names = {c.name for c in existing}

    if not existing:
        logger.info("📝 Створюю стандартні категорії з конфігу...")
        for cat_data in DEFAULT_CATEGORIES:
            # Визначаємо is_expense з групи, якщо не вказано явно
            group = cat_data.get("group", "other")
            group_info = CATEGORY_GROUPS.get(group, {})
            is_expense = group_info.get("is_expense", True)
            
            category = Category(
                name=cat_data["name"],
                is_expense=is_expense,
            )
            session.add(category)
            await session.flush()
            
            conf = cat_data.get("confidence", 0.9)
            
            # Додаємо правила з keywords
            for keyword in cat_data.get("keywords", []):
                rule = ClassificationRule(
                    category_id=category.id,
                    keyword=keyword.lower(),
                    confidence=conf,
                )
                session.add(rule)
            
            # Додаємо правила з counterparty_patterns
            for pattern in cat_data.get("counterparty_patterns", []):
                rule = ClassificationRule(
                    category_id=category.id,
                    counterparty_pattern=pattern.lower(),
                    confidence=1.0,
                )
                session.add(rule)
        
        await session.commit()
        logger.info(f"✅ Створено {len(DEFAULT_CATEGORIES)} категорій та їх правила класифікації")
        return

    # Є існуючі категорії — додати лише відсутні з DEFAULT_CATEGORIES
    to_add = [c for c in DEFAULT_CATEGORIES if c["name"] not in existing_names]
    if not to_add:
        logger.info(f"✅ Всі категорії зі списку вже є: {len(existing)} шт.")
        # Перевіряємо, чи всі категорії з конфігу присутні
        config_names = {c["name"] for c in DEFAULT_CATEGORIES}
        missing = config_names - existing_names
        if missing:
            logger.warning(f"⚠️ Відсутні категорії з конфігу: {', '.join(missing)}")
        return

    logger.info(f"📝 Додаю {len(to_add)} нових категорій зі списку: {', '.join([c['name'] for c in to_add])}")
    for cat_data in to_add:
        # Визначаємо is_expense з групи, якщо не вказано явно
        group = cat_data.get("group", "other")
        group_info = CATEGORY_GROUPS.get(group, {})
        is_expense = group_info.get("is_expense", True)
        
        category = Category(
            name=cat_data["name"],
            is_expense=is_expense,
        )
        session.add(category)
        await session.flush()
        
        conf = cat_data.get("confidence", 0.9)
        
        for keyword in cat_data.get("keywords", []):
            rule = ClassificationRule(
                category_id=category.id,
                keyword=keyword.lower(),
                confidence=conf,
            )
            session.add(rule)
        
        for pattern in cat_data.get("counterparty_patterns", []):
            rule = ClassificationRule(
                category_id=category.id,
                counterparty_pattern=pattern.lower(),
                confidence=1.0,
            )
            session.add(rule)
    
    await session.commit()
    logger.info(f"✅ Додано {len(to_add)} категорій. Всього категорій: {len(existing) + len(to_add)}")


async def reclassify_transactions(session: AsyncSession):
    """Перекласифікувати існуючі транзакції без категорій"""
    from bot.database.models import Transaction, TransactionStatus, TransactionType
    from bot.services.classifier import TransactionClassifier

    result = await session.execute(
        select(Transaction).where(Transaction.category_id.is_(None))
    )
    unclassified = result.scalars().all()

    if not unclassified:
        logger.info("✅ Всі транзакції вже класифіковані")
        return

    logger.info(f"🔄 Перекласифікація {len(unclassified)} транзакцій...")

    sales_row = await session.execute(select(Category).where(Category.name == "Продаж"))
    sales_category = sales_row.scalar_one_or_none()
    sales_category_id = sales_category.id if sales_category else None

    classifier = TransactionClassifier(session)
    threshold = await classifier.get_confidence_threshold()
    classified_count = 0

    for tx in unclassified:
        if tx.transaction_type == TransactionType.INCOME:
            if sales_category_id:
                tx.category_id = sales_category_id
                tx.status = TransactionStatus.CONFIRMED
                classified_count += 1
            continue
        category_id, confidence = await classifier.classify(
            tx.description or "",
            tx.counterparty or ""
        )
        if category_id:
            tx.category_id = category_id
            tx.status = TransactionStatus.CONFIRMED if confidence >= threshold else TransactionStatus.PENDING
            classified_count += 1

    await session.commit()
    logger.info(f"✅ Класифіковано {classified_count} транзакцій")


async def init_database():
    """Ініціалізація даних: перекласифікувати транзакції (без створення категорій)."""
    from bot.database.engine import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        await reclassify_transactions(session)
