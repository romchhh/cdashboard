"""
Синхронізація клієнтів та замовлень з XML-фіду Cosmy (analytics_customer.xml).
"""

from __future__ import annotations

import logging
import asyncio
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any

from dateutil import parser as date_parser
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.clients_engine import AsyncSessionLocalClients
from bot.database.clients_models import Client, ClientFeedTag, ClientOrder, ClientOrderLine
from bot.services.clients_feed_channels import augment_customer_feed_tags
from bot.utils.feed_download import stream_url_to_tempfile
from config import COSMY_CUSTOMERS_FEED_URL

logger = logging.getLogger(__name__)

# Частіші commit + явні flush: без цього autoflush перед кожним SELECT зливає сотні
# відкладених UPDATE в один пакет → довгі блокування та SQLITE_BUSY.
_CLIENTS_COMMIT_EVERY = 100
_ORDERS_FLUSH_EVERY = 25
_SQLITE_IN_BATCH = 800
_CLIENTS_SYNC_LOCK = asyncio.Lock()


def _local_tag(tag: str) -> str:
    if not tag:
        return ""
    return tag.split("}")[-1] if "}" in tag else tag


def _text(el: ET.Element | None, path: str) -> str:
    if el is None:
        return ""
    for child in el:
        if _local_tag(child.tag) == path:
            return (child.text or "").strip()
    return ""


def _parse_dt(val: str | None) -> datetime | None:
    if not val or not str(val).strip():
        return None
    try:
        return date_parser.isoparse(str(val).strip())
    except (ValueError, TypeError):
        return None


def _parse_int(val: str | None) -> int | None:
    if val is None or str(val).strip() == "":
        return None
    try:
        return int(float(str(val).strip()))
    except (ValueError, TypeError):
        return None


def _parse_float(val: str | None) -> float | None:
    if val is None or str(val).strip() == "":
        return None
    try:
        return float(str(val).strip())
    except (ValueError, TypeError):
        return None


def _parse_bool01(val: str | None) -> bool:
    return str(val).strip() in ("1", "true", "True", "yes")


def _parse_customer_tags(c_el: ET.Element) -> list[dict[str, Any]]:
    tags: list[dict[str, Any]] = []
    for child in c_el:
        if _local_tag(child.tag) != "tags":
            continue
        for t in child:
            if _local_tag(t.tag) != "tag":
                continue
            code = _text(t, "code").strip().lower()
            if not code:
                continue
            label = (_text(t, "label") or code).strip() or code
            tags.append(
                {
                    "code": code,
                    "label": label,
                    "orders_count": _parse_int(_text(t, "orders_count")),
                    "first_order_date": _parse_dt(_text(t, "first_order_date")),
                    "last_order_date": _parse_dt(_text(t, "last_order_date")),
                }
            )
        break
    return tags


def _parse_characteristics_text(c_el: ET.Element) -> str | None:
    characteristics_el = None
    for child in c_el:
        if _local_tag(child.tag) == "characteristics":
            characteristics_el = child
            break
    if characteristics_el is None:
        return None

    notes: list[str] = []
    for ch in characteristics_el:
        if _local_tag(ch.tag) != "characteristic":
            continue
        txt = _text(ch, "text")
        if txt:
            notes.append(txt)
    if not notes:
        return None
    return " | ".join(notes)


def _parse_customer_el(
    c_el: ET.Element, feed_generated_at: datetime | None
) -> dict[str, Any]:
    return {
        "customer_id": _text(c_el, "customer_id"),
        "opencart_customer_id": _parse_int(_text(c_el, "opencart_customer_id")),
        "firstname": _text(c_el, "firstname") or None,
        "lastname": _text(c_el, "lastname") or None,
        "telephone": _text(c_el, "telephone") or None,
        "email": _text(c_el, "email") or None,
        "is_registered": _parse_bool01(_text(c_el, "is_registered")),
        "geo_city": _text(c_el, "geo_city") or None,
        "ip": _text(c_el, "ip") or None,
        "language": _text(c_el, "language") or None,
        "source_category": _text(c_el, "source_category") or None,
        "source_raw": _text(c_el, "source_raw") or None,
        "source_utm_source": _text(c_el, "source_utm_source") or None,
        "source_utm_medium": _text(c_el, "source_utm_medium") or None,
        "source_utm_campaign": _text(c_el, "source_utm_campaign") or None,
        "source_referrer": _text(c_el, "source_referrer") or None,
        "date_registered": _parse_dt(_text(c_el, "date_registered")),
        "first_order_date": _parse_dt(_text(c_el, "first_order_date")),
        "last_order_date": _parse_dt(_text(c_el, "last_order_date")),
        "orders_count": _parse_int(_text(c_el, "orders_count")),
        "ltv": _parse_float(_text(c_el, "ltv")),
        "average_order_total": _parse_float(_text(c_el, "average_order_total")),
        "feed_characteristics": _parse_characteristics_text(c_el),
        "feed_generated_at": feed_generated_at,
    }


async def _sync_client_tags(
    session: AsyncSession, customer_id: str, tags: list[dict[str, Any]]
) -> None:
    await session.execute(
        delete(ClientFeedTag).where(ClientFeedTag.client_customer_id == customer_id)
    )
    for tag in tags:
        session.add(ClientFeedTag(client_customer_id=customer_id, **tag))


async def _upsert_client(session: AsyncSession, data: dict[str, Any]) -> None:
    cid = data.get("customer_id")
    if not cid:
        return
    with session.no_autoflush:
        r = await session.execute(select(Client).where(Client.customer_id == cid))
        row = r.scalar_one_or_none()
    if row:
        for k, v in data.items():
            setattr(row, k, v)
    else:
        session.add(Client(**data))


async def _ensure_placeholder_client(
    session: AsyncSession, customer_id: str, feed_generated_at: datetime | None
) -> None:
    with session.no_autoflush:
        r = await session.execute(select(Client).where(Client.customer_id == customer_id))
        if r.scalar_one_or_none():
            return
    session.add(
        Client(
            customer_id=customer_id,
            opencart_customer_id=0,
            is_registered=False,
            feed_generated_at=feed_generated_at,
        )
    )


def _parse_order_lines(o_el: ET.Element) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    products_el = None
    for ch in o_el:
        if _local_tag(ch.tag) == "products":
            products_el = ch
            break
    if products_el is None:
        return lines
    for ch in products_el:
        if _local_tag(ch.tag) != "product":
            continue
        lines.append(
            {
                "product_id": _parse_int(_text(ch, "product_id")),
                "name": _text(ch, "name") or None,
                "quantity": _parse_int(_text(ch, "quantity")),
                "price": _parse_float(_text(ch, "price")),
                "brand": _text(ch, "brand") or None,
            }
        )
    return lines


async def _upsert_order(
    session: AsyncSession,
    o_el: ET.Element,
    feed_generated_at: datetime | None,
) -> bool:
    oid = _parse_int(_text(o_el, "order_id"))
    cust_id = _text(o_el, "customer_id")
    if oid is None or not cust_id:
        return False

    await _ensure_placeholder_client(session, cust_id, feed_generated_at)

    lines_data = _parse_order_lines(o_el)
    order_payload = {
        "order_id": oid,
        "client_customer_id": cust_id,
        "opencart_customer_id": _parse_int(_text(o_el, "opencart_customer_id")),
        "order_date": _parse_dt(_text(o_el, "order_date")),
        "order_total": _parse_float(_text(o_el, "order_total")),
        "order_status_id": _parse_int(_text(o_el, "order_status_id")),
        "order_status": (_text(o_el, "order_status") or None),
        "manager_user_id": _parse_int(_text(o_el, "user_id")),
        "manager_username": _text(o_el, "username") or None,
        "referer": _text(o_el, "referer") or None,
        "utm_source": _text(o_el, "utm_source") or None,
        "utm_medium": _text(o_el, "utm_medium") or None,
        "utm_campaign": _text(o_el, "utm_campaign") or None,
        "utm_content": _text(o_el, "utm_content") or None,
        "utm_term": _text(o_el, "utm_term") or None,
    }

    with session.no_autoflush:
        r = await session.execute(select(ClientOrder).where(ClientOrder.order_id == oid))
        existing = r.scalar_one_or_none()
    if existing:
        await session.execute(
            delete(ClientOrderLine).where(ClientOrderLine.order_row_id == existing.id)
        )
        for k, v in order_payload.items():
            setattr(existing, k, v)
        order_row = existing
    else:
        order_row = ClientOrder(**order_payload)
        session.add(order_row)
        await session.flush()

    for ln in lines_data:
        session.add(ClientOrderLine(order_row_id=order_row.id, **ln))
    return True


async def sync_clients_from_feed() -> dict[str, int]:
    """Один синк клієнтів за раз. Другий виклик чекає, а не пише в clients.db паралельно."""
    if _CLIENTS_SYNC_LOCK.locked():
        logger.info("Синк клієнтів уже йде — чекаємо завершення, без паралельного запису")
    async with _CLIENTS_SYNC_LOCK:
        return await _sync_clients_from_feed_body()


async def _sync_clients_from_feed_body() -> dict[str, int]:
    """
    Завантажити фід, оновити таблиці clients, client_orders, client_order_lines.
    Потокове завантаження на диск + iterparse (без повного XML-дерева в RAM).
    Повертає лічильники: customers, orders.
    """
    url = COSMY_CUSTOMERS_FEED_URL
    logger.info("👥 Завантаження фіду клієнтів: %s", url)
    path: Path | None = None
    customers_n = 0
    orders_n = 0
    pending_writes = 0
    feed_generated_at: datetime | None = None

    try:
        path = await stream_url_to_tempfile(url, suffix=".xml", timeout_total=900.0)

        async with AsyncSessionLocalClients() as session:
            session.autoflush = False
            orders_since_flush = 0

            for event, elem in ET.iterparse(path, events=("start", "end")):
                if event == "start":
                    if _local_tag(elem.tag) == "customer_analytics":
                        gen_at = elem.attrib.get("generated_at")
                        feed_generated_at = _parse_dt(gen_at) if gen_at else None
                    continue

                tag = _local_tag(elem.tag)
                if tag == "customer":
                    data = _parse_customer_el(elem, feed_generated_at)
                    if data.get("customer_id"):
                        tags = augment_customer_feed_tags(
                            data.get("orders_count"),
                            _parse_customer_tags(elem),
                            first_order_date=data.get("first_order_date"),
                            last_order_date=data.get("last_order_date"),
                        )
                        await _upsert_client(session, data)
                        await _sync_client_tags(session, data["customer_id"], tags)
                        await session.flush()
                        customers_n += 1
                        pending_writes += 1
                    elem.clear()
                elif tag == "order":
                    if await _upsert_order(session, elem, feed_generated_at):
                        orders_n += 1
                        pending_writes += 1
                    orders_since_flush += 1
                    if orders_since_flush >= _ORDERS_FLUSH_EVERY:
                        await session.flush()
                        orders_since_flush = 0
                    elem.clear()
                elif tag in ("customers", "orders"):
                    elem.clear()
                elif tag == "customer_analytics":
                    elem.clear()

                if pending_writes >= _CLIENTS_COMMIT_EVERY:
                    await session.flush()
                    await session.commit()
                    pending_writes = 0

            await session.flush()
            # Прив'язуємо до клієнта менеджера, який опрацював його останнє замовлення.
            latest_mgr_rows = (
                await session.execute(
                    select(
                        ClientOrder.client_customer_id,
                        ClientOrder.manager_username,
                    ).order_by(
                        ClientOrder.client_customer_id.asc(),
                        ClientOrder.order_date.desc().nullslast(),
                        ClientOrder.order_id.desc().nullslast(),
                        ClientOrder.id.desc(),
                    )
                )
            ).all()
            latest_mgr_by_client: dict[str, str | None] = {}
            for ccid, mgr_name in latest_mgr_rows:
                key = str(ccid or "").strip()
                if not key or key in latest_mgr_by_client:
                    continue
                latest_mgr_by_client[key] = (str(mgr_name).strip() or None) if mgr_name else None
            if latest_mgr_by_client:
                all_ids = list(latest_mgr_by_client.keys())
                for i in range(0, len(all_ids), _SQLITE_IN_BATCH):
                    chunk = all_ids[i : i + _SQLITE_IN_BATCH]
                    cli_rows = (
                        await session.execute(
                            select(Client).where(Client.customer_id.in_(chunk))
                        )
                    ).scalars().all()
                    for cli in cli_rows:
                        cli.last_order_manager_username = latest_mgr_by_client.get(cli.customer_id)
            await session.commit()
    finally:
        if path is not None:
            path.unlink(missing_ok=True)

    logger.info(
        "👥 Синхронізація клієнтів завершена: клієнтів %s, замовлень %s",
        customers_n,
        orders_n,
    )
    try:
        from web.integrations.crm_analytics_cache import mark_crm_synced

        mark_crm_synced()
    except Exception:
        logger.debug("mark_crm_synced skipped", exc_info=True)
    return {"customers": customers_n, "orders": orders_n}
