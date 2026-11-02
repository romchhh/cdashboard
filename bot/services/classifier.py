# Автокласифікація транзакцій

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from bot.database.models import ClassificationRule, Category, Transaction, TransactionStatus
import re


# Дефолтні правила розпізнавання (якщо в БД порожньо)
DEFAULT_RULES = {
    "Закупка товару": ["товар", "закупка", "invoice", "product", "supplier", "постачальник", "сплата за товар", "сплата з товар", "за косметику", "косметичний товар", "оплата за косметичний", "оплата товару"],
    "Постачальники": ["сплата за товар", "сплата з товар", "оплата товару", "оплата за косметичний", "за косметику", "косметичний товар", "замовлення"],
    "Повернення": ["повернення коштів", "повне повернення", "refund"],
    "Логістика": ["доставка", "логістика", "shipping", "delivery", "нова пошта", "meest", "офіс поставок", "перевезення відправлень"],
    "Реклама Google": ["google", "ads", "advertising", "google ireland"],
    "Реклама Meta": ["meta", "facebook", "instagram", "meta platforms"],
    "Зарплати": ["зп", "salary", "зарплата", "винагорода", "заробітна"],
    "Оренда": ["оренда", "rent", "приміщення", "орендодавець"],
    "Оренда та комунальні послуги": [
        "комунальні послуги", "водопостачання", "водовідведення",
        "відшкодування витрат", "жек", "жкг", "квартплата",
    ],
    "Податки": [
        "податок", "податки", "податків", "tax", "єдиний", "єп", "дпс",
        "сплата податків", "сплата військового збору", "військовий збір",
        "гук у ", "101 сплата", "11010500", "11011001",
    ],
    "Сервіси": ["підписка", "subscription", "сервіс", "aws", "chatgpt", "figma", "saas"],
    "Комісії": ["комісія", "fee", "commission", "visa", "mastercard"],
    "Підрядники": ["підрядник", "contractor", "послуги", "services"],
}


# Ці формулювання NovaPay інакше лишаються без категорії і не потрапляють у список фінансів.
FORCED_EXPENSE_CATEGORIES = (
    ("на чорну картку", "Переказ на картку"),
    ("на білу картку", "Переказ на картку"),
    ("купівля в торгівельно-сервісній мережі", "Оплата карткою"),
)


def forced_expense_category(*parts: str | None) -> str | None:
    """Категорія, яку ставимо одразу, без ручного підтвердження."""
    text = " ".join(p or "" for p in parts).lower()
    for needle, category_name in FORCED_EXPENSE_CATEGORIES:
        if needle in text:
            return category_name
    return None


class TransactionClassifier:
    """Класифікатор транзакцій"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def classify(
        self,
        description: str,
        counterparty: str = None,
        comment: str = None,
    ) -> tuple[int | None, float]:
        """
        Класифікує транзакцію за описом, контрагентом та коментарем.
        Повертає: (category_id, confidence)
        confidence: 0.0-1.0, де 1.0 = 100% впевненість
        """
        text = f"{description or ''} {counterparty or ''} {comment or ''}".lower()

        forced_name = forced_expense_category(text)
        if forced_name:
            category_id = await self._ensure_category(forced_name)
            if category_id:
                return category_id, 1.0

        # 1. Шукаємо точне співпадіння контрагента (найвища впевненість)
        if counterparty:
            result = await self.session.execute(
                select(ClassificationRule, Category)
                .join(Category)
                .where(ClassificationRule.counterparty_pattern.isnot(None))
            )
            for rule, category in result:
                if rule.counterparty_pattern and rule.counterparty_pattern.lower() in counterparty.lower():
                    return category.id, 1.0

        # 2. Шукаємо по ключовим словам з БД
        result = await self.session.execute(
            select(ClassificationRule, Category)
            .join(Category)
            .where(ClassificationRule.keyword.isnot(None))
            .order_by(ClassificationRule.confidence.desc())
        )

        for rule, category in result:
            if rule.keyword and rule.keyword.lower() in text:
                return category.id, rule.confidence

        # 3. Fallback: дефолтні правила
        for category_name, keywords in DEFAULT_RULES.items():
            for keyword in keywords:
                if keyword.lower() in text:
                    # Знайти категорію в БД або створити
                    cat_result = await self.session.execute(
                        select(Category).where(Category.name == category_name)
                    )
                    cat = cat_result.scalar_one_or_none()
                    if cat:
                        return cat.id, 0.8  # 80% впевненість для дефолтних правил

        return None, 0.0  # Не вдалося класифікувати

    async def _ensure_category(self, name: str) -> int | None:
        cached = getattr(self, "_category_ids", None)
        if cached is None:
            cached = {}
            self._category_ids = cached
        if name in cached:
            return cached[name]
        result = await self.session.execute(select(Category).where(Category.name == name))
        category = result.scalar_one_or_none()
        if category is None:
            category = Category(name=name, is_expense=True)
            self.session.add(category)
            await self.session.flush()
        cached[name] = category.id
        return category.id

    async def learn_from_confirmation(
        self,
        transaction: Transaction,
        category_id: int,
        user_id: int
    ):
        """
        Навчає систему на основі підтвердження адміном.
        Створює/оновлює правило для майбутніх транзакцій.
        """
        # Перевіряємо, чи є вже правило для цього контрагента
        if transaction.counterparty:
            result = await self.session.execute(
                select(ClassificationRule).where(
                    ClassificationRule.category_id == category_id,
                    ClassificationRule.counterparty_pattern == transaction.counterparty
                )
            )
            existing_rule = result.scalar_one_or_none()

            if existing_rule:
                # Оновлюємо існуюче правило
                existing_rule.times_used += 1
                existing_rule.confidence = min(1.0, existing_rule.confidence + 0.05)  # Збільшуємо впевненість
            else:
                # Створюємо нове правило
                new_rule = ClassificationRule(
                    category_id=category_id,
                    counterparty_pattern=transaction.counterparty,
                    confidence=1.0,
                    times_used=1,
                    created_by_user=user_id
                )
                self.session.add(new_rule)

        # Додатково: витягуємо ключові слова з опису та коментаря
        desc_and_comment = f"{transaction.description or ''} {transaction.comment or ''}".strip()
        if desc_and_comment:
            words = re.findall(r'\b\w{4,}\b', desc_and_comment.lower())
            if words:
                # Беремо перше значуще слово як ключове
                keyword = words[0]
                result = await self.session.execute(
                    select(ClassificationRule).where(
                        ClassificationRule.category_id == category_id,
                        ClassificationRule.keyword == keyword
                    )
                )
                existing_kw_rule = result.scalar_one_or_none()

                if not existing_kw_rule:
                    new_kw_rule = ClassificationRule(
                        category_id=category_id,
                        keyword=keyword,
                        confidence=0.7,
                        times_used=1,
                        created_by_user=user_id
                    )
                    self.session.add(new_kw_rule)

        await self.session.commit()

    async def get_confidence_threshold(self) -> float:
        """Поріг впевненості для автопідтвердження (можна налаштовувати)"""
        return 0.9  # 90%+: автопідтвердження, <90%: потребує підтвердження
