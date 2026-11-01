"""Контроль і журнал експорту бази клієнтів (CSV/Excel)."""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import func, select

from bot.database.clients_engine import AsyncSessionLocalClients
from bot.database.clients_models import Client, ClientExportLog
from web.auth import ROLE_ADMIN, get_current_user

logger = logging.getLogger(__name__)

EXPORT_KIND_LABELS: dict[str, str] = {
    "clients_list": "Список клієнтів",
    "clients_orders": "Замовлення (за фільтром)",
    "client_orders": "Замовлення клієнта",
}


def user_can_export_clients(request: Request) -> bool:
    if getattr(request.state, "role", None) == ROLE_ADMIN:
        return True
    return bool(getattr(request.state, "access_clients_export", False))


async def require_clients_export(request: Request, user: str = Depends(get_current_user)) -> str:
    if not user_can_export_clients(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Експорт бази клієнтів заборонено для вашого облікового запису. Зверніться до адміністратора.",
        )
    return user


async def count_clients_for_export(session, clauses: list) -> int:
    from web.clients_query import _clients_apply_visibility_filters

    stmt = select(func.count(Client.id))
    stmt = _clients_apply_visibility_filters(stmt, clauses)
    return int((await session.execute(stmt)).scalar() or 0)


def summarize_export_query(query_string: str) -> str:
    s = (query_string or "").strip()
    if not s:
        return "без фільтрів (усі клієнти)"
    return s[:2000]


async def record_client_export_log(
    *,
    username: str,
    display_name: str | None,
    role: str,
    export_kind: str,
    file_format: str,
    filename: str,
    row_count: int | None = None,
    include_email: bool = False,
    filters_summary: str | None = None,
    client_ip: str | None = None,
    client_id: int | None = None,
) -> None:
    try:
        async with AsyncSessionLocalClients() as session:
            session.add(
                ClientExportLog(
                    exported_at=datetime.utcnow(),
                    username=username,
                    display_name=(display_name or "").strip() or None,
                    role=role,
                    export_kind=export_kind,
                    file_format=file_format,
                    filename=filename,
                    row_count=row_count,
                    include_email=bool(include_email),
                    filters_summary=filters_summary,
                    client_ip=client_ip,
                    client_id=client_id,
                )
            )
            await session.commit()
    except Exception:
        logger.exception(
            "Не вдалося записати журнал експорту (%s, %s, user=%s)",
            export_kind,
            file_format,
            username,
        )


async def fetch_client_export_logs(*, limit: int = 200) -> list[ClientExportLog]:
    async with AsyncSessionLocalClients() as session:
        res = await session.execute(
            select(ClientExportLog)
            .order_by(ClientExportLog.exported_at.desc(), ClientExportLog.id.desc())
            .limit(max(1, min(limit, 500)))
        )
        return list(res.scalars().all())
