"""Відписка від маркетингових email: токени, БД, фільтрація перед відправкою."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.clients_models import EmailUnsubscribe
from config import EMAIL_UNSUBSCRIBE_URL, WEB_PUBLIC_BASE_URL, WEB_SECRET_KEY

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_TOKEN_SERIALIZER = URLSafeTimedSerializer(WEB_SECRET_KEY, salt="cosmy-email-unsubscribe")

MARKETING_EMAIL_TRIGGERS = frozenset(
    {
        "abandoned_cart",
        "abandoned_payment",
        "price_drop_wishlist",
        "price_drop_cart",
        "promo_signup",
        "manual",
        "manual_resend",
    }
)

SOURCE_LABELS_UA = {
    "link": "Посилання в листі",
    "one_click": "One-click (Gmail)",
    "admin": "Вручну (панель)",
}


def normalize_email(raw: str | None) -> str:
    return str(raw or "").strip().lower()


def is_valid_email(raw: str | None) -> bool:
    email = normalize_email(raw)
    return bool(email and _EMAIL_RE.match(email))


def is_marketing_trigger(trigger_type: str | None) -> bool:
    return str(trigger_type or "").strip().lower() in MARKETING_EMAIL_TRIGGERS


def make_unsubscribe_token(email: str) -> str:
    return _TOKEN_SERIALIZER.dumps({"email": normalize_email(email)})


def parse_unsubscribe_token(token: str | None) -> str | None:
    raw = str(token or "").strip()
    if not raw:
        return None
    try:
        data = _TOKEN_SERIALIZER.loads(raw, max_age=None)
    except BadSignature:
        return None
    email = normalize_email(data.get("email") if isinstance(data, dict) else None)
    return email if is_valid_email(email) else None


def unsubscribe_url_for_email(email: str) -> str:
    token = make_unsubscribe_token(email)
    template = str(EMAIL_UNSUBSCRIBE_URL or "").strip()
    base = str(WEB_PUBLIC_BASE_URL or "").strip().rstrip("/")
    normalized = normalize_email(email)

    if template:
        if "{token}" in template or "{base}" in template or "{email}" in template:
            return (
                template.replace("{base}", base)
                .replace("{token}", token)
                .replace("{email}", normalized)
            )
        return template

    if base:
        return f"{base}/index.php?route=promo/unsubscribe&t={token}"
    return f"/index.php?route=promo/unsubscribe&t={token}"


def is_promo_unsubscribe_route(route: str | None) -> bool:
    return str(route or "").strip().lower() in {"promo/unsubscribe", "promo/unsubscribe/"}


async def is_email_unsubscribed(session: AsyncSession, email: str) -> bool:
    normalized = normalize_email(email)
    if not normalized:
        return False
    row = (
        await session.execute(
            select(EmailUnsubscribe.id).where(EmailUnsubscribe.email == normalized).limit(1)
        )
    ).scalar_one_or_none()
    return row is not None


# SQLite: ліміт змінних у одному запиті (~999 за замовч.). Не класти всю базу email в IN ().
_UNSUB_IN_CHUNK = 400


async def fetch_unsubscribed_set(session: AsyncSession, emails: list[str]) -> set[str]:
    normalized = sorted({normalize_email(e) for e in emails if normalize_email(e)})
    if not normalized:
        return set()

    # Велика вибірка одержувачів: один SELECT усіх відписок + перетин у Python
    # (відписок зазвичай набагато менше, ніж клієнтів у базі).
    if len(normalized) > _UNSUB_IN_CHUNK * 2:
        rows = (await session.execute(select(EmailUnsubscribe.email))).scalars().all()
        blocked = {normalize_email(x) for x in rows if x}
        return blocked.intersection(normalized)

    out: set[str] = set()
    for i in range(0, len(normalized), _UNSUB_IN_CHUNK):
        chunk = normalized[i : i + _UNSUB_IN_CHUNK]
        rows = (
            await session.execute(
                select(EmailUnsubscribe.email).where(EmailUnsubscribe.email.in_(chunk))
            )
        ).scalars().all()
        out.update(normalize_email(x) for x in rows if x)
    return out


async def filter_unsubscribed_emails(
    session: AsyncSession,
    emails: list[str],
) -> tuple[list[str], int]:
    normalized = [normalize_email(e) for e in emails if normalize_email(e)]
    if not normalized:
        return [], 0
    blocked = await fetch_unsubscribed_set(session, normalized)
    kept = [e for e in normalized if e not in blocked]
    return kept, len(normalized) - len(kept)


async def count_unsubscribes(session: AsyncSession) -> int:
    return int((await session.execute(select(func.count()).select_from(EmailUnsubscribe))).scalar() or 0)


async def record_unsubscribe(
    session: AsyncSession,
    email: str,
    *,
    source: str = "link",
    note: str | None = None,
    created_by: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    commit: bool = True,
) -> tuple[EmailUnsubscribe, bool]:
    """Повертає (рядок, created) — created=False якщо вже був у списку."""
    normalized = normalize_email(email)
    if not is_valid_email(normalized):
        raise ValueError("Некоректна email-адреса.")

    existing = (
        await session.execute(
            select(EmailUnsubscribe).where(EmailUnsubscribe.email == normalized).limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    row = EmailUnsubscribe(
        email=normalized,
        source=str(source or "link").strip()[:32] or "link",
        note=(note or "").strip() or None,
        created_by=(created_by or "").strip() or None,
        ip_address=(ip_address or "").strip()[:64] or None,
        user_agent=(user_agent or "").strip()[:500] or None,
        created_at=datetime.utcnow(),
    )
    session.add(row)
    if commit:
        await session.commit()
        await session.refresh(row)
    else:
        await session.flush()
    return row, True


async def remove_unsubscribe(session: AsyncSession, email: str, *, commit: bool = True) -> bool:
    normalized = normalize_email(email)
    if not normalized:
        return False
    row = (
        await session.execute(
            select(EmailUnsubscribe).where(EmailUnsubscribe.email == normalized).limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    await session.delete(row)
    if commit:
        await session.commit()
    return True


async def remove_unsubscribe_by_id(session: AsyncSession, row_id: int, *, commit: bool = True) -> bool:
    row = (
        await session.execute(select(EmailUnsubscribe).where(EmailUnsubscribe.id == int(row_id)).limit(1))
    ).scalar_one_or_none()
    if row is None:
        return False
    await session.delete(row)
    if commit:
        await session.commit()
    return True


async def fetch_unsubscribes_page(
    session: AsyncSession,
    *,
    q: str = "",
    page: int = 1,
    per_page: int = 50,
) -> dict[str, Any]:
    page = max(1, int(page or 1))
    per_page = max(1, min(200, int(per_page or 50)))
    total_all = int((await session.execute(select(func.count()).select_from(EmailUnsubscribe))).scalar() or 0)

    q_norm = normalize_email(q) if "@" in str(q or "") else str(q or "").strip().lower()
    filters = []
    if q_norm:
        filters.append(
            or_(
                EmailUnsubscribe.email.contains(q_norm),
                func.coalesce(EmailUnsubscribe.note, "").contains(q_norm),
            )
        )

    count_stmt = select(func.count()).select_from(EmailUnsubscribe)
    query = select(EmailUnsubscribe)
    if filters:
        count_stmt = count_stmt.where(*filters)
        query = query.where(*filters)

    total_filtered = int((await session.execute(count_stmt)).scalar() or 0)
    offset = (page - 1) * per_page
    rows = (
        await session.execute(
            query.order_by(desc(EmailUnsubscribe.created_at)).offset(offset).limit(per_page)
        )
    ).scalars().all()

    pages = max(1, (total_filtered + per_page - 1) // per_page)
    return {
        "rows": rows,
        "total_all": total_all,
        "total_filtered": total_filtered,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }
