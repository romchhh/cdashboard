# Моделі бази даних для фінансового агента

from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text, Boolean, Enum as SQLEnum
from sqlalchemy.orm import declarative_base, relationship
import enum

Base = declarative_base()


class WebUser(Base):
    """Користувач веб-панелі (адмін або менеджер). Логін, пароль, відображуване ім'я."""
    __tablename__ = "web_users"

    id = Column(Integer, primary_key=True)
    username = Column(String(100), nullable=False, unique=True)
    password_hash = Column(String(255), nullable=False)
    display_name = Column(String(150), nullable=True)  # Ім'я для відображення (напр. "Олена", "Адмін (Ьогдан)")
    role = Column(String(20), nullable=False, default="manager")  # admin | manager
    # Області доступу (для role=manager; у admin ігноруються — повний доступ)
    access_finance = Column(Boolean, default=True)  # каса, гроші, транзакції, звіти…
    access_marketing = Column(Boolean, default=False)  # клієнти Cosmy
    access_warehouse = Column(Boolean, default=False)  # товари, закупки
    access_clients_export = Column(Boolean, default=False)  # експорт CSV/Excel бази клієнтів
    access_advertising = Column(Boolean, default=False)  # аналіз реклами (Google/Meta/SEO)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TransactionType(enum.Enum):
    """Тип транзакції"""
    INCOME = "income"
    EXPENSE = "expense"


class TransactionStatus(enum.Enum):
    """Статус транзакції"""
    CONFIRMED = "confirmed"  # Підтверджено
    PENDING = "pending"  # Потребує підтвердження
    ERROR = "error"  # Помилка
    WAITING = "waiting"  # Очікується


class TransactionSource(enum.Enum):
    """Джерело транзакції"""
    MONOBANK = "monobank"
    NOVAPAY = "novapay"
    ACQUIRING = "acquiring"
    CASH = "cash"
    MANUAL = "manual"


class FOP(Base):
    """ФОП (підприємець)"""
    __tablename__ = "fops"

    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False, unique=True)  # ФОП Криворучко Альона
    edrpou = Column(String(50), nullable=True)  # ЄДРПОУ/РНОКПП
    is_active = Column(Boolean, default=True)
    # Облікові дані банків (раніше MONO_TOKEN_* / NOVAPAY_* у .env)
    mono_token = Column(Text, nullable=True)
    mono_iban = Column(String(50), nullable=True)
    novapay_refresh_token = Column(Text, nullable=True)
    novapay_certificate = Column(Text, nullable=True)
    novapay_login = Column(String(200), nullable=True)
    novapay_session_suffix = Column(String(20), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    transactions = relationship("Transaction", back_populates="fop", passive_deletes=True)


class Category(Base):
    """Категорія витрат/доходів"""
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False, unique=True)  # Закупка товару, Реклама Google
    parent_id = Column(Integer, ForeignKey("categories.id"), nullable=True)  # Для ієрархії
    is_expense = Column(Boolean, default=True)  # True=витрата, False=дохід
    created_at = Column(DateTime, default=datetime.utcnow)

    transactions = relationship("Transaction", back_populates="category")
    rules = relationship("ClassificationRule", back_populates="category")
    parent = relationship("Category", remote_side=[id], backref="subcategories")


class Transaction(Base):
    """Транзакція (дохід або витрата)"""
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True)
    external_id = Column(String(200), nullable=True, unique=True)  # ID з Mono/Nova API
    
    # Основні дані
    amount = Column(Float, nullable=False)  # Сума в грн
    transaction_type = Column(SQLEnum(TransactionType), nullable=False)
    date = Column(DateTime, nullable=False)
    
    # Джерело та ФОП
    source = Column(SQLEnum(TransactionSource), nullable=False)
    fop_id = Column(Integer, ForeignKey("fops.id"), nullable=True)
    
    # Класифікація
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    status = Column(SQLEnum(TransactionStatus), default=TransactionStatus.PENDING)
    
    # Додаткова інформація
    counterparty = Column(String(300), nullable=True)  # Контрагент (counterName / хто платить/отримувач)
    description = Column(Text, nullable=True)  # Опис (призначення з банку: "Від: ФОП ...", "ФОП ...")
    comment = Column(Text, nullable=True)  # Коментар до платежу (Mono comment, призначення детально)
    admin_comment = Column(Text, nullable=True)  # Коментар адміна
    
    # Метадані
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    confirmed_by_user = Column(Integer, nullable=True)  # Telegram user_id хто підтвердив
    confirmed_at = Column(DateTime, nullable=True)
    operator_username = Column(String(150), nullable=True)  # Хто зробив операцію з веб-панелі (логін або "Ім'я (логін)")
    
    fop = relationship("FOP", back_populates="transactions")
    category = relationship("Category", back_populates="transactions")


class ClassificationRule(Base):
    """Правило автокласифікації"""
    __tablename__ = "classification_rules"

    id = Column(Integer, primary_key=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    
    # Правило розпізнавання
    keyword = Column(String(200), nullable=True)  # Ключове слово в описі
    counterparty_pattern = Column(String(300), nullable=True)  # Шаблон контрагента
    
    # Метадані
    confidence = Column(Float, default=1.0)  # Впевненість (0.0-1.0)
    times_used = Column(Integer, default=0)  # Скільки разів застосовано
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by_user = Column(Integer, nullable=True)  # Хто створив правило
    
    category = relationship("Category", back_populates="rules")


class Budget(Base):
    """Бюджет (ліміт) по категорії"""
    __tablename__ = "budgets"

    id = Column(Integer, primary_key=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    fop_id = Column(Integer, ForeignKey("fops.id"), nullable=True)  # Якщо для конкретного ФОПа
    
    amount = Column(Float, nullable=False)  # Ліміт в грн
    period = Column(String(20), default="monthly")  # monthly, weekly, daily
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class NotificationSettings(Base):
    """Налаштування сповіщень для адміна"""
    __tablename__ = "notification_settings"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, unique=True)  # Telegram user_id
    
    # Типи сповіщень
    daily_report = Column(Boolean, default=True)
    large_transactions = Column(Boolean, default=True)
    budget_exceeded = Column(Boolean, default=True)
    pending_transactions = Column(Boolean, default=True)
    
    # Пороги
    large_transaction_threshold = Column(Float, default=50000)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AccountBalance(Base):
    """Історія балансів рахунків (знімки в часі)"""
    __tablename__ = "account_balances"

    id = Column(Integer, primary_key=True)
    fop_id = Column(Integer, ForeignKey("fops.id"), nullable=True)
    
    # Джерело
    source = Column(SQLEnum(TransactionSource), nullable=False)  # monobank, novapay
    
    # Баланс
    balance = Column(Float, nullable=False)
    currency = Column(String(10), default="UAH")
    
    # Ідентифікатори рахунку
    iban = Column(String(50), nullable=True)
    account_name = Column(String(200), nullable=True)
    
    # Часова мітка
    recorded_at = Column(DateTime, default=datetime.utcnow, index=True)
    
    fop = relationship("FOP")
