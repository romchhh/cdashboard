"""Окремий async-движок і сесія для БД клієнтів Cosmy."""

from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from config import CLIENTS_DATABASE_URL
from bot.database.clients_models import ClientsBase
from bot.database.engine import SQLITE_BUSY_MS, SQLITE_TIMEOUT_SEC, RetryAsyncSession

# timeout (сек.) — скільки чекати звільнення файлу при SQLITE_BUSY (разом із PRAGMA busy_timeout)
_clients_sqlite = "sqlite" in (CLIENTS_DATABASE_URL or "")
_clients_kwargs: dict = {"echo": False, "pool_pre_ping": True}
if _clients_sqlite:
    _clients_kwargs["connect_args"] = {"timeout": SQLITE_TIMEOUT_SEC}
    _clients_kwargs["poolclass"] = NullPool
clients_engine = create_async_engine(CLIENTS_DATABASE_URL, **_clients_kwargs)


@event.listens_for(clients_engine.sync_engine, "connect")
def _sqlite_clients_on_connect(dbapi_conn, connection_record):
    from web.client_name_gender import gender_is_match_for_sql
    from web.client_marketing import (
        extract_geo_oblast_label,
        normalize_geo_city_for_sql,
        city_group_id_for_sql,
    )

    # Фільтр статі: повна Python-логіка (усі слова в firstname, unicode lower).
    dbapi_conn.create_function(
        "gender_is_match",
        3,
        gender_is_match_for_sql,
        deterministic=True,
    )
    dbapi_conn.create_function(
        "client_geo_oblast",
        1,
        extract_geo_oblast_label,
        deterministic=True,
    )
    dbapi_conn.create_function(
        "client_geo_city_norm",
        1,
        normalize_geo_city_for_sql,
        deterministic=True,
    )
    dbapi_conn.create_function(
        "client_geo_city_group",
        1,
        city_group_id_for_sql,
        deterministic=True,
    )
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_MS}")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()

AsyncSessionLocalClients = async_sessionmaker(
    clients_engine,
    class_=RetryAsyncSession,
    expire_on_commit=False,
)


async def init_clients_db() -> None:
    """Створити таблиці clients / client_orders / client_order_lines у окремому файлі БД."""
    async with clients_engine.begin() as conn:
        await conn.run_sync(ClientsBase.metadata.create_all)
        # Легка міграція для існуючих файлів SQLite: додаємо нові поля статусу замовлення.
        cols_res = await conn.exec_driver_sql("PRAGMA table_info(client_orders)")
        existing_cols = {str(row[1]) for row in cols_res.fetchall()}
        if "order_status_id" not in existing_cols:
            await conn.exec_driver_sql("ALTER TABLE client_orders ADD COLUMN order_status_id INTEGER")
        if "order_status" not in existing_cols:
            await conn.exec_driver_sql("ALTER TABLE client_orders ADD COLUMN order_status VARCHAR(255)")
        if "manager_user_id" not in existing_cols:
            await conn.exec_driver_sql("ALTER TABLE client_orders ADD COLUMN manager_user_id INTEGER")
        if "manager_username" not in existing_cols:
            await conn.exec_driver_sql("ALTER TABLE client_orders ADD COLUMN manager_username VARCHAR(255)")
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_client_orders_order_status_id ON client_orders (order_status_id)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_client_orders_order_status ON client_orders (order_status)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_client_orders_manager_user_id ON client_orders (manager_user_id)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_client_orders_manager_username ON client_orders (manager_username)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_client_orders_order_date ON client_orders (order_date)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_client_orders_date_customer "
            "ON client_orders (order_date, client_customer_id)"
        )
        # CRM: останній контакт і призначений менеджер
        cli_cols = await conn.exec_driver_sql("PRAGMA table_info(clients)")
        cli_existing = {str(row[1]) for row in cli_cols.fetchall()}
        if "crm_last_touched_by" not in cli_existing:
            await conn.exec_driver_sql(
                "ALTER TABLE clients ADD COLUMN crm_last_touched_by VARCHAR(255)"
            )
        if "crm_last_touched_at" not in cli_existing:
            await conn.exec_driver_sql("ALTER TABLE clients ADD COLUMN crm_last_touched_at DATETIME")
        if "crm_assigned_username" not in cli_existing:
            await conn.exec_driver_sql(
                "ALTER TABLE clients ADD COLUMN crm_assigned_username VARCHAR(100)"
            )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_clients_crm_assigned_username ON clients (crm_assigned_username)"
        )
        # Менеджерські поля клієнта + таблиця коментарів
        clients_cols = await conn.exec_driver_sql("PRAGMA table_info(clients)")
        clients_existing = {str(row[1]) for row in clients_cols.fetchall()}
        if "crm_last_order_pickup" not in clients_existing:
            await conn.exec_driver_sql(
                "ALTER TABLE clients ADD COLUMN crm_last_order_pickup VARCHAR(24)"
            )
        if "crm_interaction_keys" not in clients_existing:
            await conn.exec_driver_sql("ALTER TABLE clients ADD COLUMN crm_interaction_keys TEXT")
        if "crm_processing_status" not in clients_existing:
            await conn.exec_driver_sql(
                "ALTER TABLE clients ADD COLUMN crm_processing_status VARCHAR(24) DEFAULT 'neutral'"
            )
        await conn.exec_driver_sql(
            "UPDATE clients SET crm_processing_status = 'neutral' "
            "WHERE crm_processing_status IS NULL OR trim(coalesce(crm_processing_status, '')) = ''"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_clients_crm_processing_status ON clients (crm_processing_status)"
        )
        if "feed_characteristics" not in clients_existing:
            await conn.exec_driver_sql("ALTER TABLE clients ADD COLUMN feed_characteristics TEXT")
        if "last_order_manager_username" not in clients_existing:
            await conn.exec_driver_sql("ALTER TABLE clients ADD COLUMN last_order_manager_username VARCHAR(255)")
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_clients_last_order_manager_username "
            "ON clients (last_order_manager_username)"
        )
        await conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS client_manager_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_customer_id VARCHAR(128) NOT NULL REFERENCES clients(customer_id),
                author_display VARCHAR(255) NOT NULL,
                body TEXT NOT NULL,
                created_at DATETIME NOT NULL
            )
            """
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_client_manager_notes_customer "
            "ON client_manager_notes (client_customer_id)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_client_manager_notes_created "
            "ON client_manager_notes (created_at)"
        )
        await conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS client_crm_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_customer_id VARCHAR(128) NOT NULL REFERENCES clients(customer_id),
                event_type VARCHAR(32) NOT NULL,
                processing_status VARCHAR(24),
                closure_result VARCHAR(32),
                interaction_keys TEXT,
                assigned_username VARCHAR(100),
                author_display VARCHAR(255) NOT NULL,
                note TEXT,
                created_at DATETIME NOT NULL
            )
            """
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_client_crm_history_customer "
            "ON client_crm_history (client_customer_id)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_client_crm_history_event ON client_crm_history (event_type)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_client_crm_history_created ON client_crm_history (created_at)"
        )
        await conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS email_campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject VARCHAR(500) NOT NULL,
                body TEXT NOT NULL,
                is_html BOOLEAN DEFAULT 0,
                target_mode VARCHAR(20) NOT NULL DEFAULT 'all',
                custom_recipients_raw TEXT,
                is_template BOOLEAN DEFAULT 0,
                template_name VARCHAR(200),
                sent_total INTEGER DEFAULT 0,
                run_count INTEGER DEFAULT 0,
                created_by VARCHAR(120),
                updated_by VARCHAR(120),
                created_at DATETIME NOT NULL,
                updated_at DATETIME,
                last_sent_at DATETIME
            )
            """
        )
        ec_cols = await conn.exec_driver_sql("PRAGMA table_info(email_campaigns)")
        ec_existing = {str(row[1]) for row in ec_cols.fetchall()}
        if "is_template" not in ec_existing:
            await conn.exec_driver_sql("ALTER TABLE email_campaigns ADD COLUMN is_template BOOLEAN DEFAULT 0")
        if "template_name" not in ec_existing:
            await conn.exec_driver_sql("ALTER TABLE email_campaigns ADD COLUMN template_name VARCHAR(200)")
        if "updated_by" not in ec_existing:
            await conn.exec_driver_sql("ALTER TABLE email_campaigns ADD COLUMN updated_by VARCHAR(120)")
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_email_campaigns_created_at ON email_campaigns (created_at)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_email_campaigns_last_sent_at ON email_campaigns (last_sent_at)"
        )
        await conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS email_delivery_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sent_at DATETIME NOT NULL,
                channel VARCHAR(20) NOT NULL,
                trigger_type VARCHAR(64) NOT NULL,
                recipient_email VARCHAR(320) NOT NULL,
                subject VARCHAR(500) NOT NULL,
                success BOOLEAN NOT NULL DEFAULT 1,
                error_message TEXT,
                campaign_id INTEGER,
                product_ids VARCHAR(500),
                created_by VARCHAR(120)
            )
            """
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_email_delivery_logs_sent_at ON email_delivery_logs (sent_at)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_email_delivery_logs_channel ON email_delivery_logs (channel)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_email_delivery_logs_recipient ON email_delivery_logs (recipient_email)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_email_delivery_logs_trigger ON email_delivery_logs (trigger_type)"
        )
        edl_cols = await conn.exec_driver_sql("PRAGMA table_info(email_delivery_logs)")
        edl_existing = {str(row[1]) for row in edl_cols.fetchall()}
        if "external_request_id" not in edl_existing:
            await conn.exec_driver_sql(
                "ALTER TABLE email_delivery_logs ADD COLUMN external_request_id VARCHAR(128)"
            )
        if "resend_message_id" not in edl_existing:
            await conn.exec_driver_sql(
                "ALTER TABLE email_delivery_logs ADD COLUMN resend_message_id VARCHAR(128)"
            )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_email_delivery_logs_ext_req "
            "ON email_delivery_logs (external_request_id)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_email_delivery_logs_subject_success_sent "
            "ON email_delivery_logs (subject, success, sent_at)"
        )
        await conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS client_export_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exported_at DATETIME NOT NULL,
                username VARCHAR(100) NOT NULL,
                display_name VARCHAR(150),
                role VARCHAR(20) NOT NULL,
                export_kind VARCHAR(64) NOT NULL,
                file_format VARCHAR(10) NOT NULL,
                filename VARCHAR(255) NOT NULL,
                row_count INTEGER,
                include_email BOOLEAN NOT NULL DEFAULT 0,
                filters_summary TEXT,
                client_ip VARCHAR(64),
                client_id INTEGER
            )
            """
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_client_export_logs_exported_at ON client_export_logs (exported_at)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_client_export_logs_username ON client_export_logs (username)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_client_export_logs_export_kind ON client_export_logs (export_kind)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_client_export_logs_client_id ON client_export_logs (client_id)"
        )
        await conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS client_feed_tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_customer_id VARCHAR(128) NOT NULL REFERENCES clients(customer_id),
                code VARCHAR(64) NOT NULL,
                label VARCHAR(200),
                orders_count INTEGER,
                first_order_date DATETIME,
                last_order_date DATETIME,
                UNIQUE (client_customer_id, code)
            )
            """
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_client_feed_tags_customer "
            "ON client_feed_tags (client_customer_id)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_client_feed_tags_code ON client_feed_tags (code)"
        )
        await conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS email_unsubscribes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email VARCHAR(320) NOT NULL UNIQUE,
                source VARCHAR(32) NOT NULL DEFAULT 'link',
                note TEXT,
                ip_address VARCHAR(64),
                user_agent VARCHAR(500),
                created_by VARCHAR(120),
                created_at DATETIME NOT NULL
            )
            """
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_email_unsubscribes_email ON email_unsubscribes (email)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_email_unsubscribes_source ON email_unsubscribes (source)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_email_unsubscribes_created_at ON email_unsubscribes (created_at)"
        )
        await conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS seo_expenses (
                id VARCHAR(36) PRIMARY KEY,
                expense_date DATETIME NOT NULL,
                amount_uah FLOAT NOT NULL,
                description VARCHAR(500) NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_seo_expenses_expense_date ON seo_expenses (expense_date)"
        )
