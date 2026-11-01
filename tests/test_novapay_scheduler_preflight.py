"""Перед синком крон перечитує ФОП з БД."""

from unittest.mock import AsyncMock, patch

import asyncio


def test_sync_all_transactions_reloads_fops_before_sync():
    from bot.scheduler.tasks import sync_all_transactions

    with patch("bot.scheduler.tasks.reload_fops_runtime_from_db", new_callable=AsyncMock) as reload:
        with patch("bot.scheduler.tasks.ensure_novapay_jwt_sessions", new_callable=AsyncMock) as ensure:
            with patch("bot.scheduler.tasks.AsyncSessionLocal") as session_cls:
                session = AsyncMock()
                session_cls.return_value.__aenter__.return_value = session
                with patch("bot.scheduler.tasks.TransactionSyncService") as svc_cls:
                    svc = AsyncMock()
                    svc.sync_all = AsyncMock()
                    svc_cls.return_value = svc
                    asyncio.run(sync_all_transactions(days=7))
    ensure.assert_awaited_once()
    reload.assert_not_called()
    svc.sync_all.assert_awaited_once_with(days=7)


def test_ensure_novapay_jwt_sessions_reloads_fops():
    from bot.scheduler.tasks import ensure_novapay_jwt_sessions

    with patch("bot.scheduler.tasks.reload_fops_runtime_from_db", new_callable=AsyncMock) as reload:
        with patch("bot.scheduler.tasks.NOVAPAY_TOKENS", {"A": {}}):
            with patch("bot.scheduler.tasks.get_session_file", return_value="/tmp/s.json"):
                with patch("bot.scheduler.tasks.ensure_jwt_session", return_value="jwt"):
                    asyncio.run(ensure_novapay_jwt_sessions())
    reload.assert_awaited_once()
