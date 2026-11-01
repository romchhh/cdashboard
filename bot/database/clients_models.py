"""
Моделі окремої БД клієнтів Cosmy (фід analytics_customer.xml).
Таблиці: clients, client_orders, client_order_lines — не в основній finance_bot.db.
"""

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import declarative_base, relationship

ClientsBase = declarative_base()


class Client(ClientsBase):
    """Клієнт з фіду analytics_customer.xml (Cosmy / OpenCart)."""

    __tablename__ = "clients"

    id = Column(Integer, primary_key=True)
    customer_id = Column(String(128), unique=True, nullable=False, index=True)
    opencart_customer_id = Column(Integer, nullable=True, index=True)

    firstname = Column(String(200), nullable=True)
    lastname = Column(String(200), nullable=True)
    telephone = Column(String(120), nullable=True)
    email = Column(String(320), nullable=True)
    is_registered = Column(Boolean, default=True)

    geo_city = Column(String(200), nullable=True)
    ip = Column(String(45), nullable=True)
    language = Column(String(32), nullable=True)

    source_category = Column(String(120), nullable=True)
    source_raw = Column(String(120), nullable=True)
    source_utm_source = Column(String(200), nullable=True)
    source_utm_medium = Column(String(200), nullable=True)
    source_utm_campaign = Column(String(400), nullable=True)
    source_referrer = Column(String(500), nullable=True)

    date_registered = Column(DateTime, nullable=True)
    first_order_date = Column(DateTime, nullable=True)
    last_order_date = Column(DateTime, nullable=True)
    orders_count = Column(Integer, nullable=True)
    ltv = Column(Float, nullable=True)
    average_order_total = Column(Float, nullable=True)
    feed_characteristics = Column(Text, nullable=True)
    last_order_manager_username = Column(String(255), nullable=True, index=True)

    feed_generated_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Менеджерські поля (не з фіду): статус останнього замовлення, типи взаємодій
    crm_last_order_pickup = Column(String(24), nullable=True)
    crm_interaction_keys = Column(Text, nullable=True)
    # Хто останній змінював CRM / коментар (не з фіду; логін не зберігаємо — лише відображення як у нотатках)
    crm_last_touched_by = Column(String(255), nullable=True)
    crm_last_touched_at = Column(DateTime, nullable=True)
    # Відповідальний менеджер (логін web_users.username); окрема БД від фінансів — лише рядок
    crm_assigned_username = Column(String(100), nullable=True, index=True)
    # Статус ручного опрацювання ліда: neutral | in_progress | communicated | closed
    crm_processing_status = Column(String(24), nullable=True, default="neutral", index=True)

    shop_orders = relationship(
        "ClientOrder",
        back_populates="client",
        foreign_keys="ClientOrder.client_customer_id",
        primaryjoin="Client.customer_id==ClientOrder.client_customer_id",
    )


class ClientFeedTag(ClientsBase):
    """Мітка клієнта з фіду analytics_customer.xml (<tags><tag>…</tag></tags>)."""

    __tablename__ = "client_feed_tags"
    __table_args__ = (
        UniqueConstraint("client_customer_id", "code", name="uq_client_feed_tags_customer_code"),
    )

    id = Column(Integer, primary_key=True)
    client_customer_id = Column(String(128), ForeignKey("clients.customer_id"), nullable=False, index=True)
    code = Column(String(64), nullable=False, index=True)
    label = Column(String(200), nullable=True)
    orders_count = Column(Integer, nullable=True)
    first_order_date = Column(DateTime, nullable=True)
    last_order_date = Column(DateTime, nullable=True)


class ClientOrder(ClientsBase):
    """Замовлення з фіду (прив’язка до клієнта за customer_id з фіду)."""

    __tablename__ = "client_orders"

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, unique=True, nullable=False, index=True)
    client_customer_id = Column(String(128), ForeignKey("clients.customer_id"), nullable=False, index=True)
    opencart_customer_id = Column(Integer, nullable=True)

    order_date = Column(DateTime, nullable=True)
    order_total = Column(Float, nullable=True)
    order_status_id = Column(Integer, nullable=True, index=True)
    order_status = Column(String(255), nullable=True, index=True)
    manager_user_id = Column(Integer, nullable=True, index=True)
    manager_username = Column(String(255), nullable=True, index=True)
    referer = Column(String(500), nullable=True)
    utm_source = Column(String(200), nullable=True)
    utm_medium = Column(String(200), nullable=True)
    utm_campaign = Column(String(400), nullable=True)
    utm_content = Column(String(200), nullable=True)
    utm_term = Column(String(200), nullable=True)

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    client = relationship("Client", back_populates="shop_orders", foreign_keys=[client_customer_id])
    lines = relationship(
        "ClientOrderLine",
        back_populates="order",
        cascade="all, delete-orphan",
    )


class ClientOrderLine(ClientsBase):
    """Рядок замовлення (товар у замовленні)."""

    __tablename__ = "client_order_lines"

    id = Column(Integer, primary_key=True)
    order_row_id = Column(Integer, ForeignKey("client_orders.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(Integer, nullable=True)
    name = Column(Text, nullable=True)
    quantity = Column(Integer, nullable=True)
    price = Column(Float, nullable=True)
    brand = Column(String(255), nullable=True)

    order = relationship("ClientOrder", back_populates="lines")


class ClientCrmHistory(ClientsBase):
    """Історія CRM-дій по клієнту (закриття ліда, зміна статусу, каналів тощо)."""

    __tablename__ = "client_crm_history"

    id = Column(Integer, primary_key=True)
    client_customer_id = Column(String(128), ForeignKey("clients.customer_id"), nullable=False, index=True)
    event_type = Column(String(32), nullable=False, index=True)
    processing_status = Column(String(24), nullable=True)
    closure_result = Column(String(32), nullable=True)
    interaction_keys = Column(Text, nullable=True)
    assigned_username = Column(String(100), nullable=True)
    author_display = Column(String(255), nullable=False)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class ClientManagerNote(ClientsBase):
    """Внутрішній коментар менеджера до клієнта (лог, без перезапису)."""

    __tablename__ = "client_manager_notes"

    id = Column(Integer, primary_key=True)
    client_customer_id = Column(String(128), ForeignKey("clients.customer_id"), nullable=False, index=True)
    author_display = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class EmailCampaign(ClientsBase):
    """Збережені email-розсилки і шаблони маркетингу."""

    __tablename__ = "email_campaigns"

    id = Column(Integer, primary_key=True)
    subject = Column(String(500), nullable=False)
    body = Column(Text, nullable=False)
    is_html = Column(Boolean, default=False)

    # all = усі email з бази, custom = вказані адреси
    target_mode = Column(String(20), nullable=False, default="all")
    custom_recipients_raw = Column(Text, nullable=True)

    # Шаблони (для повторного використання перед відправкою)
    is_template = Column(Boolean, default=False)
    template_name = Column(String(200), nullable=True)

    # Метадані розсилки
    sent_total = Column(Integer, default=0)  # сумарно успішно відправлено
    run_count = Column(Integer, default=0)  # скільки разів надсилали
    created_by = Column(String(120), nullable=True)
    updated_by = Column(String(120), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_sent_at = Column(DateTime, nullable=True)


class EmailDeliveryLog(ClientsBase):
    """Журнал кожної спроби відправки листа (API або вручну з панелі)."""

    __tablename__ = "email_delivery_logs"

    id = Column(Integer, primary_key=True)
    sent_at = Column(DateTime, nullable=False, index=True)
    channel = Column(String(20), nullable=False, index=True)  # api | manual
    trigger_type = Column(String(64), nullable=False, index=True)
    recipient_email = Column(String(320), nullable=False, index=True)
    subject = Column(String(500), nullable=False)
    success = Column(Boolean, nullable=False, default=True)
    error_message = Column(Text, nullable=True)
    campaign_id = Column(Integer, nullable=True, index=True)
    product_ids = Column(String(500), nullable=True)
    created_by = Column(String(120), nullable=True)
    external_request_id = Column(String(128), nullable=True, index=True)
    resend_message_id = Column(String(128), nullable=True)


class EmailUnsubscribe(ClientsBase):
    """Адреси, які відписались від маркетингових email-розсилок."""

    __tablename__ = "email_unsubscribes"

    id = Column(Integer, primary_key=True)
    email = Column(String(320), nullable=False, unique=True, index=True)
    source = Column(String(32), nullable=False, default="link", index=True)  # link | one_click | admin
    note = Column(Text, nullable=True)
    ip_address = Column(String(64), nullable=True)
    user_agent = Column(String(500), nullable=True)
    created_by = Column(String(120), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class ClientExportLog(ClientsBase):
    """Журнал завантажень бази клієнтів (CSV/Excel)."""

    __tablename__ = "client_export_logs"

    id = Column(Integer, primary_key=True)
    exported_at = Column(DateTime, nullable=False, index=True)
    username = Column(String(100), nullable=False, index=True)
    display_name = Column(String(150), nullable=True)
    role = Column(String(20), nullable=False)
    export_kind = Column(String(64), nullable=False, index=True)
    file_format = Column(String(10), nullable=False)
    filename = Column(String(255), nullable=False)
    row_count = Column(Integer, nullable=True)
    include_email = Column(Boolean, nullable=False, default=False)
    filters_summary = Column(Text, nullable=True)
    client_ip = Column(String(64), nullable=True)
    client_id = Column(Integer, nullable=True, index=True)


class SeoExpense(ClientsBase):
    """Ручні витрати на SEO для маркетингового дашборду."""

    __tablename__ = "seo_expenses"

    id = Column(String(36), primary_key=True)
    expense_date = Column(DateTime, nullable=False, index=True)
    amount_uah = Column(Float, nullable=False)
    description = Column(String(500), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
