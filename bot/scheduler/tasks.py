"""
Cron-задачі для планувальника (APScheduler).

РОЗКЛАД ВСІХ ЗАВДАНЬ:
======================

Фінанси:
  sync_all_transactions        — кожні 15 хвилин   → Monobank + NovaPay (31 день, JWT з БД)
  refresh_novapay_tokens       — кожні 6 год + 10:15/22:15 → JWT NovaPay

Клієнти Cosmy:
  sync_clients_from_feed       — щодня о 05:00 → analytics_customer.xml → таблиці clients, client_orders

Продукти / Procurement Copilot:
  sync_products_from_feed      — щодня о 03:00     → повний цикл (дані для веб Procurement; у Telegram не шлеться):
                                                       1. sync_cosmy_products   — парсинг XML-фіда, оновлення БД
                                                       2. sync_clients_from_feed — CRM-замовлення
                                                       3. apply_success_crm_sales — продажі лише успішних замовлень
                                                       4. collect_daily_sales_snapshot — щоденний зріз продажів
                                                       5. calculate_all_analytics      — розрахунок аналітики
                                                       6. calculate_abc_analysis       — ABC-класифікація

Реклама (кеш API):
  refresh_ad_cache             — щодня о 02:00 → завантажує Google Ads / Meta Ads / GSC у файловий кеш.
                                                  Сторінка «Аналіз реклами» завжди відповідає з кешу
                                                  миттєво; кнопка ↻ API — для примусового оновлення.

Резервні копії БД:
  backup_databases_to_telegram — щодня о 01:30 → zip SQLite (finance / products / clients) → Telegram адмінам
                                                  (якщо архів > ліміту — кількома частинами).

Парсер цін:
  rotate_parser_brands         — будні 01:00–22:00 (і вдень): черга з ~03:05, drain кожну хв + ланцюжок після бренда;
                                  цикл 14 днів (~5–10 брендів на ніч).

Примітка:
  Налаштування порогів (fast_mover_threshold, dead_stock_days, тощо) зберігаються
  у таблиці procurement_settings і завантажуються при кожному запуску розрахунку.
"""

import asyncio
import logging
import traceback
from datetime import datetime

from bot.database.engine import AsyncSessionLocal
from bot.services.balance_sync import format_balance_display, format_amount_signed
from bot.services.sync_transactions import TransactionSyncService
from novapay.config import NOVAPAY_TOKENS, get_session_file
from novapay.auth import (
    credentials_for_fop,
    create_client,
    ensure_jwt_session,
    load_session,
    try_refresh_session,
)

logger = logging.getLogger(__name__)

# Нічні задачі не пишуть у finance.db одночасно: інакше SQLite віддає database is locked.
_FINANCE_JOBS = asyncio.Lock()


async def reload_fops_runtime_from_db() -> None:
    """Актуальні Mono/NovaPay токени з БД (після змін у /fops без рестарту процесу)."""
    from bot.services.fop_config import refresh_fop_runtime

    try:
        async with AsyncSessionLocal() as session:
            await refresh_fop_runtime(session)
    except Exception as exc:
        logger.error("Не вдалося перечитати ФОП з БД (продовжуємо зі старим кешем): %s", exc)


async def ensure_novapay_jwt_sessions() -> None:
    """Продовжити JWT у файлах sessions/ для всіх ФОП з NovaPay (перед синком)."""
    await reload_fops_runtime_from_db()
    for user_key in list(NOVAPAY_TOKENS.keys()):
        try:
            session_file = get_session_file(user_key)
            jwt = await asyncio.to_thread(ensure_jwt_session, user_key, session_file)
            if jwt:
                logger.info("NovaPay JWT готовий для %s", user_key)
            else:
                logger.warning(
                    "NovaPay JWT не отримано для %s (файл %s) — перевірте refresh/login/certificate у /fops",
                    user_key,
                    session_file,
                )
        except Exception as e:
            logger.error("NovaPay JWT для %s: %s", user_key, e)


async def sync_all_transactions(days: int = 31, *, reraise: bool = True):
    """
    Синхронізація Monobank + NovaPay.
    days: lookback (31 за замовчуванням, щоб закривати дірки крона).
    """
    logger.info("🔄 Початок синхронізації транзакцій (останні %s дн.)...", days)
    async with _FINANCE_JOBS:
        try:
            await ensure_novapay_jwt_sessions()
            async with AsyncSessionLocal() as session:
                sync_service = TransactionSyncService(session)
                await sync_service.sync_all(days=days)
            logger.info("✅ Синхронізація транзакцій завершена")
        except Exception as e:
            logger.error("❌ Помилка синхронізації транзакцій: %s\n%s", e, traceback.format_exc())
            if reraise:
                raise  # щоб веб-форма /sync показала помилку користувачу
            logger.error("Нічний/фоновий синк продовжує роботу без падіння процесу")


async def sync_balances():
    """
    Задача: синхронізація балансів рахунків в БД.
    Запускається кожні 30 хвилин.
    """
    logger.info("💰 Початок синхронізації балансів...")
    async with _FINANCE_JOBS:
        try:
            from bot.services.balance_sync import sync_balances_to_db

            await reload_fops_runtime_from_db()
            async with AsyncSessionLocal() as session:
                await sync_balances_to_db(session)
            
            logger.info("✅ Синхронізація балансів завершена")
        except Exception as e:
            logger.error(f"❌ Помилка синхронізації балансів: {e}")


async def refresh_novapay_tokens():
    """
    Задача: оновлення токенів NovaPay для всіх користувачів.
    Запускається кожні 6 годин і перед плановим синком транзакцій.
    """
    logger.info("🔄 Оновлення токенів NovaPay...")
    async with _FINANCE_JOBS:
        await reload_fops_runtime_from_db()

    for user_key in list(NOVAPAY_TOKENS.keys()):
        try:
            session_file = get_session_file(user_key)
            if credentials_for_fop(user_key):
                jwt = await asyncio.to_thread(ensure_jwt_session, user_key, session_file)
                if jwt:
                    logger.info("✅ JWT для %s актуальний", user_key)
                else:
                    logger.error(
                        "❌ JWT для %s не оновлено (%s). Перевірте refresh/login/certificate у /fops.",
                        user_key,
                        session_file,
                    )
                continue

            client = await asyncio.to_thread(create_client)
            principal, expiration = await asyncio.to_thread(load_session, session_file)
            if not principal:
                logger.warning("⚠️ Немає legacy-сесії для %s", user_key)
                continue

            new_principal = None
            for attempt in range(1, 3):
                new_principal = await asyncio.to_thread(
                    try_refresh_session, client, principal, session_file
                )
                if new_principal:
                    logger.info("✅ Legacy-токен оновлено для %s (спроба %s)", user_key, attempt)
                    break
                if attempt == 1:
                    await asyncio.sleep(5)

            if not new_principal:
                logger.error(
                    "❌ Не вдалося оновити legacy-сесію для %s. Потрібен JWT у /fops.",
                    user_key,
                )
        except Exception as e:
            logger.error("❌ Помилка оновлення токена для %s: %s", user_key, e)
    
    logger.info("✅ Оновлення токенів NovaPay завершено")


async def daily_report():
    """
    Задача: щоденний звіт адміну о 9:00.
    Надсилає статистику за вчора.
    """
    logger.info("📊 Генерація щоденного звіту...")
    try:
        from config import administrators, BOT_TOKEN
        from aiogram import Bot
        from bot.services.reports import get_cashflow
        from datetime import timedelta
        
        # Вчорашній день
        today = datetime.now()
        yesterday_start = (today - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday_end = yesterday_start.replace(hour=23, minute=59, second=59)
        
        async with AsyncSessionLocal() as session:
            data = await get_cashflow(session, yesterday_start, yesterday_end)
        
        text = (
            "🌅 <b>Доброго ранку!</b>\n\n"
            f"<b>Вчора ({yesterday_start.strftime('%d.%m.%Y')}):</b>\n"
            f"Доходи: <b>+{format_balance_display(data['income'])} грн</b>\n"
            f"Витрати: <b>-{format_balance_display(data['expense'])} грн</b>\n"
            f"Прибуток: <b>{format_amount_signed(data['net'])} грн</b>\n"
        )
        
        # Надіслати всім адмінам
        bot = Bot(token=BOT_TOKEN)
        for admin_id in administrators:
            try:
                await bot.send_message(admin_id, text, parse_mode="HTML")
            except Exception as e:
                logger.error(f"❌ Не вдалося надіслати звіт адміну {admin_id}: {e}")
        
        await bot.session.close()
        logger.info("✅ Щоденний звіт надіслано")
        
    except Exception as e:
        logger.error(f"❌ Помилка генерації щоденного звіту: {e}")


async def check_pending_transactions():
    """
    Задача: перевірка непідтверджених транзакцій.
    Надсилає сповіщення, якщо є нові.
    Запускається кожну годину.
    """
    logger.info("🔍 Перевірка непідтверджених транзакцій...")
    try:
        from config import administrators, BOT_TOKEN
        from aiogram import Bot
        from bot.database.models import Transaction, TransactionStatus
        from sqlalchemy import select
        
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Transaction)
                .where(Transaction.status == TransactionStatus.PENDING)
                .order_by(Transaction.date.desc())
                .limit(10)
            )
            pending = result.scalars().all()
            
            if not pending:
                logger.info("✅ Немає непідтверджених транзакцій")
                return
            
            text = (
                f"⏳ <b>Непідтверджені транзакції: {len(pending)}</b>\n\n"
                "Використайте /pending для перегляду"
            )
            
            bot = Bot(token=BOT_TOKEN)
            for admin_id in administrators:
                try:
                    await bot.send_message(admin_id, text, parse_mode="HTML")
                except Exception as e:
                    logger.error(f"❌ Не вдалося надіслати сповіщення адміну {admin_id}: {e}")
            
            await bot.session.close()
            logger.info(f"✅ Сповіщення про {len(pending)} непідтверджених транзакцій надіслано")
            
    except Exception as e:
        logger.error(f"❌ Помилка перевірки непідтверджених транзакцій: {e}")


async def sync_products_from_feed():
    """
    Задача: щоденна синхронізація продуктів Cosmy з XML-фіда.
    Запускається о 03:00 за київським часом.
    """
    logger.info("🛒 Запуск щоденної синхронізації продуктів Cosmy...")
    try:
        from bot.services.products_sync import sync_and_recalculate
        await sync_and_recalculate()
        logger.info("✅ Щоденна синхронізація продуктів Cosmy завершена")
    except Exception as e:
        logger.error("❌ Помилка синхронізації продуктів Cosmy: %s", e)


async def sync_clients_from_feed():
    """Щоденна синхронізація клієнтів та замовлень з analytics_customer.xml (05:00 за Києвом)."""
    logger.info("👥 Запуск синхронізації клієнтів Cosmy...")
    try:
        from bot.services.clients_sync import sync_clients_from_feed as run_clients_sync

        stats = await run_clients_sync()
        logger.info(
            "✅ Синхронізація клієнтів завершена: клієнтів %s, замовлень %s",
            stats.get("customers"),
            stats.get("orders"),
        )
    except Exception as e:
        logger.error("❌ Помилка синхронізації клієнтів Cosmy: %s\n%s", e, traceback.format_exc())


async def refresh_ad_cache(*, period_labels: tuple[str, ...] | None = None, sync_crm: bool = True):
    """
    Оновлення кешу рекламних API.

    period_labels — обмежити періоди (напр. лише «поточний місяць»); None = усі пресети.
    sync_crm — чи синхронізувати CRM-фід перед оновленням реклами.
    """
    logger.info("📊 Запуск оновлення кешу реклами та CRM...")
    try:
        if sync_crm:
            # ── 1. Синхронізація CRM ──
            logger.info("  Синхронізація CRM-фіду клієнтів/замовлень...")
            try:
                from bot.services.clients_sync import sync_clients_from_feed
                stats = await sync_clients_from_feed()
                logger.info(
                    "  ✅ CRM синхронізовано: клієнтів %s, замовлень %s",
                    stats.get("customers"), stats.get("orders"),
                )
            except Exception as e:
                logger.warning("  ⚠️ Синхронізація CRM: %s", e)

        # ── 2. Кеш рекламних API ──
        from mono.api import now_kyiv
        from web.integrations.ad_cache import iter_ad_api_cache_periods, purge_expired_ad_cache
        from web.integrations.crm_analytics_cache import cache_invalidate_all
        from web.integrations.google_ads import fetch_google_ads_stats
        from web.integrations.google_search_console import fetch_gsc_stats
        from web.integrations.google_analytics import fetch_ga4_stats
        from web.integrations.meta_ads import fetch_meta_ads_stats

        purge_expired_ad_cache()

        if sync_crm:
            cache_invalidate_all()

        now = now_kyiv()
        periods = list(iter_ad_api_cache_periods(now))
        if period_labels:
            allowed = set(period_labels)
            periods = [p for p in periods if p[2] in allowed]

        async def _refresh_period_apis(start, end, label: str) -> None:
            async def _run(name: str, fn) -> None:
                logger.info("  Завантаження %s (%s)...", name, label)
                try:
                    await asyncio.to_thread(fn, start, end, use_cache=False)
                    logger.info("  ✅ %s (%s) збережено", name, label)
                except Exception as e:
                    logger.warning("  ⚠️ %s (%s): %s", name, label, e)

            await asyncio.gather(
                _run("Google Ads", fetch_google_ads_stats),
                _run("Meta Ads", fetch_meta_ads_stats),
                _run("GSC", fetch_gsc_stats),
                _run("GA4", fetch_ga4_stats),
            )

        for start, end, label in periods:
            await _refresh_period_apis(start, end, label)

        logger.info("✅ Оновлення кешу реклами завершено")

    except Exception as e:
        logger.error("❌ Помилка оновлення кешу реклами: %s\n%s", e, traceback.format_exc())


async def sync_fop_ytd_and_reconcile():
    """Календарний рік: дозавантаження виписок + звірка Моно vs БД."""
    from bot.services.period_range import days_since_year_start
    from bot.services.fop_income_limit import reconcile_mono_ytd

    days = days_since_year_start()
    logger.info("📊 YTD синк ФОП (%s дн.) + звірка Моно...", days)
    try:
        await sync_all_transactions(days=days)
        async with AsyncSessionLocal() as session:
            payload = await reconcile_mono_ytd(session)
        gaps = [
            f"{name}: виписка−БД={row.get('mono_gap')}"
            for name, row in (payload.get("fops") or {}).items()
            if abs(float(row.get("mono_gap") or 0)) >= 1
        ]
        if gaps:
            logger.warning("⚠️ Розрив Моно YTD: %s", "; ".join(gaps))
            try:
                from bot.services.sync_transactions import _notify_admins

                await _notify_admins(
                    "⚠️ <b>Розрив виписки Моно і Фінадміна (YTD)</b>\n"
                    + "\n".join(gaps)
                    + "\nНе орієнтуйте ліміт ФОП на цифру з БД, поки дірку не закрито."
                )
            except Exception:
                pass
        logger.info("✅ YTD синк і звірка завершені")
    except Exception as e:
        logger.error("❌ YTD синк ФОП: %s\n%s", e, traceback.format_exc())


async def refresh_ad_cache_recent():
    """Періодичне оновлення актуальних періодів без повної CRM-синхронізації."""
    await refresh_ad_cache(
        period_labels=(
            "поточний місяць",
            "30 днів",
            "порівняння попереднього періоду",
            "порівняння 30 днів",
        ),
        sync_crm=False,
    )


async def rotate_parser_brands():
    """Один бренд із денної черги. Поза 01:00–22:00 і на вихідних no-op. Цикл 14 днів."""
    try:
        from web import parser_service as ps

        result = await asyncio.to_thread(ps.advance_parser_rotation)
        action = (result or {}).get("action")
        if action == "started":
            logger.info(
                "Парсер: автозапуск «%s», у черзі ще %s",
                result.get("brand"),
                result.get("left"),
            )
        elif action == "busy":
            logger.info("Парсер: черга чекає, зараз іде «%s»", result.get("brand") or "прогін")
        elif action == "skipped":
            logger.warning("Парсер: бренди пропущено після помилок старту")
        elif action == "empty":
            logger.debug("Парсер: черга порожня")
        elif action == "daytime":
            logger.debug("Парсер: поза вікном 01:00–22:00 — автооновлення пропущено")
        elif action == "weekend":
            logger.debug("Парсер: вихідні — автооновлення брендів пропущено")
        elif action == "error":
            logger.error("Парсер: помилка планувальника: %s", (result or {}).get("error"))
    except Exception as e:
        logger.error("Парсер: ротація брендів не вдалась: %s\n%s", e, traceback.format_exc())


_TELEGRAM_BACKUP_TIMEOUT = 180.0


async def backup_databases_to_telegram():
    """Копія SQLite під локом фінансів; Telegram — уже без лока і з таймаутом."""
    logger.info("💾 Початок резервного копіювання БД...")
    from bot.services.db_backup import notify_backup_telegram, run_database_backup

    try:
        async with _FINANCE_JOBS:
            result = await run_database_backup(notify_telegram=False)
    except Exception as e:
        logger.error("❌ Резервна копія БД: %s\n%s", e, traceback.format_exc())
        return

    if not result.ok:
        logger.error("❌ Резервна копія БД не вдалася: %s", "; ".join(result.errors))
        return

    try:
        await asyncio.wait_for(
            notify_backup_telegram(result),
            timeout=_TELEGRAM_BACKUP_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.error(
            "Telegram бекап: немає відповіді за %.0f с. Архів %s лишився на диску, фінанси не блокуємо.",
            _TELEGRAM_BACKUP_TIMEOUT,
            result.archive_path,
        )
        return
    except Exception as e:
        logger.error("❌ Резервна копія БД (Telegram): %s\n%s", e, traceback.format_exc())
        return

    if result.telegram_sent:
        logger.info("✅ Резервна копія БД створена та надіслана (%s)", result.archive_path)
    else:
        logger.warning("✅ Архів створено, але Telegram: %s", "; ".join(result.errors) or "не надіслано")
