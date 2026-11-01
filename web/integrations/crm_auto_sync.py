"""Автоматична синхронізація CRM для розділу «Аналіз реклами»."""

from __future__ import annotations

import asyncio
import logging

from web.integrations.crm_analytics_cache import cache_invalidate_all, crm_sync_is_stale, mark_crm_synced

logger = logging.getLogger(__name__)

_sync_lock = asyncio.Lock()


async def ensure_crm_fresh_for_advertising(*, ttl_hours: int = 6) -> bool:
    """
    Якщо CRM давно не оновлювався — синхронізує analytics_customer.xml у фоні.
    Повертає True, якщо синхронізацію виконано.
    """
    if not crm_sync_is_stale(ttl_hours=ttl_hours):
        return False

    async with _sync_lock:
        if not crm_sync_is_stale(ttl_hours=ttl_hours):
            return False
        try:
            from bot.database.clients_engine import AsyncSessionLocalClients
            from bot.services.clients_sync import sync_clients_from_feed
            from web.ad_analytics import warm_default_crm_analytics_cache

            stats = await sync_clients_from_feed()
            cache_invalidate_all()
            mark_crm_synced()
            async with AsyncSessionLocalClients() as session:
                await warm_default_crm_analytics_cache(session)
            logger.info(
                "Auto CRM sync for advertising: customers=%s orders=%s",
                stats.get("customers"),
                stats.get("orders"),
            )
            return True
        except Exception:
            logger.exception("Auto CRM sync for advertising failed")
            return False
