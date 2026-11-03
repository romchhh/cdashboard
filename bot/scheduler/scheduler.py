# Налаштування APScheduler

import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from bot.scheduler.tasks import (
    sync_all_transactions,
    sync_balances,
    refresh_novapay_tokens,
    daily_report,
    check_pending_transactions,
    sync_products_from_feed,
    sync_clients_from_feed,
    refresh_ad_cache,
    refresh_ad_cache_recent,
    sync_fop_ytd_and_reconcile,
    backup_databases_to_telegram,
    rotate_parser_brands,
)

logger = logging.getLogger(__name__)


def _guard(fn):
    """Помилка нічної задачі лишається в логу і не валить процес."""

    async def _run(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except Exception:
            logger.exception(
                "Планувальник: задача %s завершилась з помилкою, процес лишається живим",
                getattr(fn, "__name__", fn),
            )

    _run.__name__ = getattr(fn, "__name__", "scheduled_job")
    return _run


def setup_scheduler() -> AsyncIOScheduler:
    """
    Налаштування та запуск планувальника задач.
    
    Розклад:
    - Синхронізація транзакцій: кожні 15 хвилин (NovaPay + Monobank)
    - Синхронізація балансів: кожні 30 хвилин
    - Оновлення сесій NovaPay: щодня о 10:00 та 22:00
    - Щоденний звіт: щодня о 09:00
    - Перевірка непідтверджених: кожну годину
    """
    scheduler = AsyncIOScheduler(timezone="Europe/Kiev")
    
    # 1. Синхронізація транзакцій (кожні 15 хв), lookback 31 день — закриває дірки API
    scheduler.add_job(
        _guard(sync_all_transactions),
        trigger=IntervalTrigger(minutes=15),
        id="sync_transactions",
        name="Синхронізація транзакцій",
        replace_existing=True,
        kwargs={"days": 31, "reraise": False},
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
    logger.info("✅ Задача: Синхронізація транзакцій (31 день, кожні 15 хв)")
    
    # 2. Синхронізація балансів (кожні 30 хвилин)
    scheduler.add_job(
        _guard(sync_balances),
        trigger=IntervalTrigger(minutes=30),
        id="sync_balances",
        name="Синхронізація балансів",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
    logger.info("✅ Задача: Синхронізація балансів (кожні 30 хв)")
    
    # 3. NovaPay JWT: кожні 6 год + додатково о 10:15 та 22:15
    scheduler.add_job(
        _guard(refresh_novapay_tokens),
        trigger=IntervalTrigger(hours=6),
        id="refresh_novapay_tokens_interval",
        name="Оновлення JWT NovaPay (кожні 6 год)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
    scheduler.add_job(
        _guard(refresh_novapay_tokens),
        trigger=CronTrigger(hour="10,22", minute=15),
        id="refresh_novapay_tokens",
        name="Оновлення JWT NovaPay (10:15, 22:15)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
    logger.info("✅ Задача: Оновлення JWT NovaPay (кожні 6 год + 10:15/22:15)")
    
    # 4. Щоденний звіт (щодня о 09:00)
    scheduler.add_job(
        _guard(daily_report),
        trigger=CronTrigger(hour=9, minute=0),
        id="daily_report",
        name="Щоденний звіт",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
    logger.info("✅ Задача: Щоденний звіт (09:00 щодня)")
    
    # 5. Перевірка непідтверджених транзакцій (кожну годину)
    scheduler.add_job(
        _guard(check_pending_transactions),
        trigger=IntervalTrigger(hours=1),
        id="check_pending",
        name="Перевірка непідтверджених транзакцій",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
    logger.info("✅ Задача: Перевірка непідтверджених (кожну годину)")
    
    # 6. Синхронізація продуктів Cosmy (щодня о 03:00)
    scheduler.add_job(
        _guard(sync_products_from_feed),
        trigger=CronTrigger(hour=3, minute=0),
        id="sync_products_cosmy",
        name="Синхронізація продуктів Cosmy",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
    logger.info("✅ Задача: Синхронізація продуктів Cosmy (03:00 щодня)")
    # Алерти закупок у Telegram вимкнено — у боті лише фінанси; склад залишається для веб-інтерфейсу.

    # 7. Синхронізація клієнтів Cosmy (щодня о 05:00 за Києвом)
    scheduler.add_job(
        _guard(sync_clients_from_feed),
        trigger=CronTrigger(hour=5, minute=0),
        id="sync_clients_cosmy",
        name="Синхронізація клієнтів Cosmy (05:00)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
    logger.info("✅ Задача: Синхронізація клієнтів Cosmy (05:00 щодня)")

    # 8. Нічне оновлення кешу рекламних API (02:00 за Києвом).
    # CRM-фід тут не качаємо: його пише синк клієнтів о 05:00 (і закупки о 03:00).
    scheduler.add_job(
        _guard(refresh_ad_cache),
        trigger=CronTrigger(hour=2, minute=0),
        id="refresh_ad_cache",
        name="Оновлення кешу реклами (Google Ads / Meta / GSC)",
        kwargs={"sync_crm": False},
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
    logger.info("✅ Задача: Оновлення кешу реклами (02:00 щодня)")

    # 9. Періодичне оновлення актуальних періодів (кожні 6 год)
    scheduler.add_job(
        _guard(refresh_ad_cache_recent),
        trigger=IntervalTrigger(hours=6),
        id="refresh_ad_cache_recent",
        name="Оновлення кешу реклами (поточний місяць / 30 днів)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
    logger.info("✅ Задача: Оновлення кешу реклами (кожні 6 год)")

    scheduler.add_job(
        _guard(sync_fop_ytd_and_reconcile),
        trigger=CronTrigger(day_of_week="sun", hour=4, minute=15),
        id="sync_fop_ytd_reconcile",
        name="YTD синк ФОП + звірка Моно",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
    logger.info("✅ Задача: YTD синк ФОП і звірка Моно (неділя 04:15)")

    scheduler.add_job(
        _guard(backup_databases_to_telegram),
        trigger=CronTrigger(hour=1, minute=30),
        id="backup_databases_telegram",
        name="Резервна копія БД → Telegram",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
    logger.info("✅ Задача: Резервна копія БД → Telegram (01:30 щодня)")

    # Парсер: автооновлення у будні 01:00–22:00, включно з днем. Черга після синку ~03:05, drain кожну хв.
    scheduler.add_job(
        _guard(rotate_parser_brands),
        trigger=CronTrigger(hour=3, minute=5),
        id="parser_brand_rotation",
        name="Парсер: черга брендів (цикл 14 днів)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=6 * 3600,
    )
    scheduler.add_job(
        _guard(rotate_parser_brands),
        trigger=CronTrigger(hour=1, minute=5),
        id="parser_brand_rotation_early",
        name="Парсер: доробка черги (01:05)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        _guard(rotate_parser_brands),
        trigger=IntervalTrigger(minutes=1),
        id="parser_brand_rotation_drain",
        name="Парсер: наступний бренд із черги",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
    logger.info(
        "✅ Задача: Парсер брендів (будні 01:00–22:00, і вдень; черга з 03:05, drain 1 хв + ланцюжок)"
    )

    # Запустити планувальник
    scheduler.start()
    logger.info("🚀 Планувальник запущено")
    
    return scheduler
