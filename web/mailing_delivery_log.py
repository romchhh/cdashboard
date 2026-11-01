"""Журнал відправок email (API-тригери та ручні розсилки)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import case, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.clients_engine import AsyncSessionLocalClients
from bot.database.clients_models import EmailDeliveryLog

EMAIL_TRIGGER_LABELS_UA: dict[str, str] = {
    "welcome": "Реєстрація (welcome)",
    "order": "Замовлення прийнято",
    "abandoned_cart": "Покинутий кошик",
    "abandoned_payment": "Покинута оплата",
    "price_drop_wishlist": "Зниження ціни (wishlist)",
    "price_drop_cart": "Зниження ціни (кошик)",
    "promo_signup": "Промокод підписки (popup)",
    "manual": "Ручна розсилка",
    "manual_resend": "Повторна ручна розсилка",
}

CHANNEL_LABELS_UA = {"api": "API (сайт)", "manual": "Вручну (панель)"}

MAILING_STATS_PER_PAGE = 50


def trigger_label_ua(trigger_type: str) -> str:
    key = str(trigger_type or "").strip()
    return EMAIL_TRIGGER_LABELS_UA.get(key, key or "—")


def channel_label_ua(channel: str) -> str:
    return CHANNEL_LABELS_UA.get(str(channel or "").strip(), str(channel or "—"))


def product_ids_to_str(product_ids: list[int] | None) -> str | None:
    if not product_ids:
        return None
    return ",".join(str(int(x)) for x in product_ids)


def _failure_map(failures: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in failures or []:
        line = str(raw or "").strip()
        if not line:
            continue
        email_part = line.split(":", 1)[0].strip().lower()
        if email_part:
            out.setdefault(email_part, line.split(":", 1)[-1].strip()[:500])
    return out


def build_manual_delivery_entries(
    *,
    recipients: list[str],
    subject: str,
    failures: list[str],
    sent_at: datetime,
    trigger_type: str,
    campaign_id: int | None,
    created_by: str | None,
) -> list[EmailDeliveryLog]:
    fail_map = _failure_map(failures)
    rows: list[EmailDeliveryLog] = []
    for addr in recipients:
        email = str(addr or "").strip().lower()
        if not email:
            continue
        err = fail_map.get(email)
        rows.append(
            EmailDeliveryLog(
                sent_at=sent_at,
                channel="manual",
                trigger_type=trigger_type,
                recipient_email=email,
                subject=(subject or "")[:500],
                success=err is None,
                error_message=err,
                campaign_id=campaign_id,
                product_ids=None,
                created_by=created_by,
            )
        )
    return rows


async def log_delivery_rows(rows: list[EmailDeliveryLog]) -> None:
    if not rows:
        return
    async with AsyncSessionLocalClients() as session:
        session.add_all(rows)
        await session.commit()


async def log_api_delivery(
    *,
    recipient_email: str,
    subject: str,
    trigger_type: str,
    success: bool,
    error_message: str | None = None,
    product_ids: list[int] | None = None,
    sent_at: datetime | None = None,
    external_request_id: str | None = None,
    resend_message_id: str | None = None,
) -> None:
    from mono.api import now_kyiv

    await log_delivery_rows(
        [
            EmailDeliveryLog(
                sent_at=sent_at or now_kyiv(),
                channel="api",
                trigger_type=trigger_type,
                recipient_email=recipient_email.strip().lower(),
                subject=(subject or "")[:500],
                success=bool(success),
                error_message=(error_message or "")[:2000] or None,
                campaign_id=None,
                product_ids=product_ids_to_str(product_ids),
                created_by=None,
                external_request_id=(external_request_id or "")[:128] or None,
                resend_message_id=(resend_message_id or "")[:128] or None,
            )
        ]
    )


async def find_successful_api_delivery(
    *,
    trigger_type: str,
    external_request_id: str,
) -> EmailDeliveryLog | None:
    req_id = str(external_request_id or "").strip()
    if not req_id:
        return None
    async with AsyncSessionLocalClients() as session:
        return (
            await session.execute(
                select(EmailDeliveryLog)
                .where(
                    EmailDeliveryLog.trigger_type == trigger_type,
                    EmailDeliveryLog.external_request_id == req_id,
                    EmailDeliveryLog.success.is_(True),
                )
                .order_by(desc(EmailDeliveryLog.id))
                .limit(1)
            )
        ).scalar_one_or_none()


def parse_mailing_stats_period(
    date_from_str: str | None,
    date_to_str: str | None,
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Повертає (початок дня from, кінець дня to) у локальному часі now_kyiv."""
    from mono.api import now_kyiv

    current = now or now_kyiv()
    end_day = current.replace(hour=23, minute=59, second=59, microsecond=999999)
    start_default = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    def _parse_one(raw: str | None, default: datetime) -> datetime:
        s = (raw or "").strip()
        if not s:
            return default
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d")
        except ValueError:
            return default

    start = _parse_one(date_from_str, start_default).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end = _parse_one(date_to_str, end_day).replace(
        hour=23, minute=59, second=59, microsecond=999999
    )
    if start > end:
        start, end = end.replace(hour=0, minute=0, second=0, microsecond=0), end
    return start, end


async def fetch_mailing_delivery_stats(
    session: AsyncSession,
    *,
    date_from: datetime,
    date_to: datetime,
    channel: str = "all",
    email_q: str = "",
    page: int = 1,
    per_page: int = MAILING_STATS_PER_PAGE,
) -> dict[str, Any]:
    base = [
        EmailDeliveryLog.sent_at >= date_from,
        EmailDeliveryLog.sent_at <= date_to,
    ]
    if channel in ("api", "manual"):
        base.append(EmailDeliveryLog.channel == channel)
    eq = (email_q or "").strip().lower()
    if eq:
        base.append(func.lower(EmailDeliveryLog.recipient_email).like(f"%{eq}%"))

    ok_expr = func.sum(case((EmailDeliveryLog.success.is_(True), 1), else_=0))
    fail_expr = func.sum(case((EmailDeliveryLog.success.is_(False), 1), else_=0))

    total_row = (
        await session.execute(
            select(
                func.count(EmailDeliveryLog.id),
                ok_expr,
                fail_expr,
            ).where(*base)
        )
    ).one()
    total_sent = int(total_row[0] or 0)
    total_ok = int(total_row[1] or 0)
    total_fail = int(total_row[2] or 0)

    by_channel_rows = (
        await session.execute(
            select(
                EmailDeliveryLog.channel,
                func.count(EmailDeliveryLog.id),
                ok_expr,
                fail_expr,
            )
            .where(*base)
            .group_by(EmailDeliveryLog.channel)
        )
    ).all()
    channel_stats: dict[str, dict[str, int]] = {}
    for ch, cnt, ok, fail in by_channel_rows:
        key = str(ch or "")
        channel_stats[key] = {
            "sent": int(cnt or 0),
            "ok": int(ok or 0),
            "fail": int(fail or 0),
        }

    by_trigger_rows = (
        await session.execute(
            select(
                EmailDeliveryLog.trigger_type,
                func.count(EmailDeliveryLog.id),
                ok_expr,
                fail_expr,
            )
            .where(*base)
            .group_by(EmailDeliveryLog.trigger_type)
            .order_by(desc(func.count(EmailDeliveryLog.id)))
            .limit(30)
        )
    ).all()
    by_trigger = [
        {
            "trigger_type": str(tt or ""),
            "label": trigger_label_ua(str(tt or "")),
            "sent": int(cnt or 0),
            "ok": int(ok or 0),
            "fail": int(fail or 0),
        }
        for tt, cnt, ok, fail in by_trigger_rows
    ]

    pages = max(1, (total_sent + per_page - 1) // per_page) if total_sent else 1
    page = max(1, min(page, pages))
    offset = (page - 1) * per_page

    log_rows = (
        await session.execute(
            select(EmailDeliveryLog)
            .where(*base)
            .order_by(desc(EmailDeliveryLog.sent_at), desc(EmailDeliveryLog.id))
            .offset(offset)
            .limit(per_page)
        )
    ).scalars().all()

    entries = []
    for row in log_rows:
        entries.append(
            {
                "id": row.id,
                "sent_at": row.sent_at,
                "channel": row.channel,
                "channel_label": channel_label_ua(row.channel),
                "trigger_type": row.trigger_type,
                "trigger_label": trigger_label_ua(row.trigger_type),
                "recipient_email": row.recipient_email,
                "subject": row.subject,
                "success": bool(row.success),
                "error_message": row.error_message,
                "product_ids": row.product_ids,
                "campaign_id": row.campaign_id,
                "created_by": row.created_by,
            }
        )

    return {
        "total_sent": total_sent,
        "total_ok": total_ok,
        "total_fail": total_fail,
        "channel_stats": channel_stats,
        "by_trigger": by_trigger,
        "entries": entries,
        "page": page,
        "pages": pages,
        "per_page": per_page,
    }
