"""Допоміжна логіка розсилок: парсинг email, шаблони, API-тригери."""

from __future__ import annotations

import asyncio
import csv
import html
import io
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Request, UploadFile
from openpyxl import load_workbook
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import uuid4

from bot.database.clients_models import Client, EmailCampaign
from config import MARKETING_API_KEY, RESEND_FROM_EMAIL, RESEND_REPLY_TO, WEB_PUBLIC_BASE_URL
from web.deps import EMAIL_TEMPLATES_DIR, form_checkbox_on
from web.cosmy_email import ensure_brand_fonts_in_html
from web.email_delivery import (
    html_to_plain_text,
    list_unsubscribe_headers,
    resend_config_error_ua,
    send_trigger_email_resend,
)
from web.mailing_delivery_log import build_manual_delivery_entries, find_successful_api_delivery, log_api_delivery

DEFAULT_PRODUCT_IMAGE = "/static/cosmy25@2x.png"
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_STATIC_QUOTED_RE = re.compile(
    r'((?:src|href)\s*=\s*["\'])(/static/[^"\']+)(["\'])',
    re.IGNORECASE,
)
_STATIC_UNQUOTED_RE = re.compile(
    r'((?:src|href)\s*=\s*)(/static/[^\s<>"]+)',
    re.IGNORECASE,
)
_STATIC_CSS_URL_RE = re.compile(
    r'url\(\s*["\']?(/static/[^"\'\)\s]+)["\']?\s*\)',
    re.IGNORECASE,
)
_IMAGE_EXT_BY_CT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
}
_ALLOWED_IMAGE_EXT = frozenset(_IMAGE_EXT_BY_CT.values())

_MAIL_TEMPLATE_LABELS_UA: dict[str, str] = {
    "welcome_email.html": "Вітальний лист (реєстрація)",
    "trigger_order_placed.html": "Замовлення прийнято",
    "trigger_abandoned_cart.html": "Покинутий кошик",
    "trigger_abandoned_payment.html": "Покинута оплата",
    "trigger_price_drop_wishlist.html": "Зниження ціни (wishlist)",
    "trigger_price_drop_cart.html": "Зниження ціни (кошик)",
    "trigger_promo_signup.html": "Промокод підписки (popup)",
    "sale.html": "Акційна розсилка",
    "promo.html": "Промо новинок",
    "informational.html": "Інформаційний дайджест",
    "post_order.html": "Після замовлення",
    "black_friday.html": "Чорна п'ятниця",
    "birthday_offer.html": "Привітання з днем народження",
    "new_collection.html": "Нова колекція",
}

_templates_cache: list[dict[str, str]] | None = None
_templates_dir_mtime: float = 0.0


def invalidate_mailing_templates_cache() -> None:
    global _templates_cache, _templates_dir_mtime
    _templates_cache = None
    _templates_dir_mtime = 0.0


def _templates_dir_latest_mtime() -> float:
    if not EMAIL_TEMPLATES_DIR.is_dir():
        return 0.0
    latest = 0.0
    for fp in EMAIL_TEMPLATES_DIR.glob("*.html"):
        try:
            latest = max(latest, fp.stat().st_mtime)
        except OSError:
            continue
    return latest


def extract_html_title(raw_html: str) -> str | None:
    match = _TITLE_RE.search(str(raw_html or ""))
    if not match:
        return None
    title = re.sub(r"\s+", " ", match.group(1)).strip()
    return title or None


def template_label_ua(filename: str) -> str:
    key = str(filename or "").strip().lower()
    if key in _MAIL_TEMPLATE_LABELS_UA:
        return _MAIL_TEMPLATE_LABELS_UA[key]
    stem = Path(key).stem.replace("_", " ").strip()
    return stem[:1].upper() + stem[1:] if stem else "Шаблон"


def mailing_file_templates(
    *, force_reload: bool = False, include_html: bool = True
) -> list[dict[str, str]]:
    global _templates_cache, _templates_dir_mtime
    mtime = _templates_dir_latest_mtime()
    if (
        not force_reload
        and _templates_cache is not None
        and mtime == _templates_dir_mtime
    ):
        cached = _templates_cache
    else:
        out: list[dict[str, str]] = []
        if EMAIL_TEMPLATES_DIR.is_dir():
            for fp in sorted(EMAIL_TEMPLATES_DIR.glob("*.html")):
                try:
                    raw = fp.read_text(encoding="utf-8")
                except OSError:
                    continue
                label = template_label_ua(fp.name)
                out.append(
                    {
                        "name": fp.name,
                        "label": label,
                        "subject": extract_html_title(raw) or label,
                        "html": raw,
                    }
                )
        _templates_cache = out
        _templates_dir_mtime = mtime
        cached = out

    if include_html:
        return cached
    return [
        {
            "name": str(item.get("name") or ""),
            "label": str(item.get("label") or ""),
            "subject": str(item.get("subject") or ""),
        }
        for item in cached
    ]


def mailing_template_by_name(name: str) -> dict[str, str] | None:
    """Load a single template by filename (avoids reading every .html when cache is cold)."""
    target = str(name or "").strip()
    if not target:
        return None
    # Reject path traversal; only basename .html under templates dir.
    safe = Path(target).name
    if safe != target or not safe.lower().endswith(".html"):
        return None
    if _templates_cache is not None:
        for item in _templates_cache:
            if str(item.get("name") or "").strip().lower() == safe.lower():
                return item
    fp = EMAIL_TEMPLATES_DIR / safe
    if not fp.is_file() or not fp.resolve().is_relative_to(EMAIL_TEMPLATES_DIR.resolve()):
        return None
    try:
        raw = fp.read_text(encoding="utf-8")
    except OSError:
        return None
    label = template_label_ua(fp.name)
    return {
        "name": fp.name,
        "label": label,
        "subject": extract_html_title(raw) or label,
        "html": raw,
    }


def absolutize_static_urls_for_email(raw_html: str) -> str:
    content = str(raw_html or "")
    if not content:
        return content
    base_url = str(WEB_PUBLIC_BASE_URL or "").strip().rstrip("/")
    if not base_url:
        return content

    def _prefix(match: re.Match) -> str:
        return f"{match.group(1)}{base_url}{match.group(2)}{match.group(3)}"

    def _prefix_unquoted(match: re.Match) -> str:
        return f"{match.group(1)}{base_url}{match.group(2)}"

    def _prefix_css(match: re.Match) -> str:
        return f'url("{base_url}{match.group(1)}")'

    content = _STATIC_QUOTED_RE.sub(_prefix, content)
    content = _STATIC_UNQUOTED_RE.sub(_prefix_unquoted, content)
    return _STATIC_CSS_URL_RE.sub(_prefix_css, content)


def safe_image_ext(filename: str, content_type: str | None) -> str | None:
    ext = (Path(str(filename or "")).suffix or "").lower().strip()
    if ext in _ALLOWED_IMAGE_EXT:
        return ext
    return _IMAGE_EXT_BY_CT.get(str(content_type or "").lower().strip())


def emails_from_text(raw: str | None) -> list[str]:
    if not raw:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for email in _EMAIL_RE.findall(str(raw)):
        normalized = email.strip().lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            out.append(normalized)
    return out


def emails_from_upload_bytes(filename: str, content: bytes) -> tuple[list[str], str | None]:
    name = str(filename or "").strip().lower()
    if not content:
        return [], None

    if name.endswith(".csv") or name.endswith(".txt"):
        text = content.decode("utf-8-sig", errors="ignore")
        try:
            sample = text[:4096]
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
            except Exception:
                dialect = csv.excel
                # експорт клієнтів Cosmy — завжди «;»
                if sample.count(";") >= sample.count(","):
                    dialect.delimiter = ";"
            reader = csv.reader(io.StringIO(text), dialect)
            flat = "\n".join("\n".join(cell for cell in row if cell) for row in reader)
        except Exception:
            flat = text
        return emails_from_text(flat), None

    if name.endswith(".xlsx"):
        try:
            wb = load_workbook(filename=io.BytesIO(content), read_only=True, data_only=True)
            chunks: list[str] = []
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    for cell in row:
                        if cell is not None:
                            chunks.append(str(cell))
            return emails_from_text("\n".join(chunks)), None
        except Exception:
            return [], "Не вдалося прочитати XLSX-файл. Перевірте формат."

    return [], "Підтримуються лише файли .csv або .xlsx."


async def emails_from_uploaded_file(upload: UploadFile | None) -> tuple[list[str], str | None]:
    if not upload or not upload.filename:
        return [], None
    content = await upload.read()
    return emails_from_upload_bytes(str(upload.filename), content)


def valid_single_email(email: str | None) -> str | None:
    normalized, _, _ = validate_recipient_email(email or "")
    return normalized


def validate_recipient_email(raw: str | None) -> tuple[str | None, str | None, str | None]:
    """
    Перевірка email перед відправкою.
    Повертає (normalized_email, error_message, error_code).
    """
    text = str(raw or "").strip()
    if not text:
        return None, "Вкажіть коректний email.", "missing_email"

    try:
        from email_validator import EmailNotValidError, validate_email as ev_validate_email

        try:
            parts = ev_validate_email(text, check_deliverability=False)
            return parts.normalized.lower(), None, None
        except EmailNotValidError as exc:
            msg = str(exc).strip() or "Невалідний email."
            return None, msg, "invalid_email"
    except ImportError:
        pass

    parsed = emails_from_text(text)
    if len(parsed) == 1 and parsed[0] == text.strip().lower():
        return parsed[0], None, None
    return None, "Вкажіть коректний email.", "invalid_email"


def api_key_ok(request: Request) -> bool:
    import hmac
    import os

    expected = str(MARKETING_API_KEY or "").strip()
    if not expected:
        if os.getenv("ENV", "development").strip().lower() == "production":
            return False
        return True
    provided = (
        str(request.headers.get("x-api-key") or "").strip()
        or str(request.query_params.get("api_key") or "").strip()
    )
    return bool(provided and hmac.compare_digest(provided, expected))


async def parse_json_payload(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


async def load_products_for_email(
    product_ids: list[int],
    quantities: dict[int, int] | None = None,
) -> tuple[list[dict], list[int]]:
    from bot.database.products_engine import AsyncProductsSessionLocal
    from bot.database.products_models import Product

    if not product_ids:
        return [], []

    unique_ids = list(dict.fromkeys(product_ids))
    async with AsyncProductsSessionLocal() as psession:
        rows = (
            await psession.execute(select(Product).where(Product.product_id.in_(unique_ids)))
        ).scalars().all()
    by_pid = {int(p.product_id): p for p in rows}

    products: list[dict] = []
    missing: list[int] = []
    qty_map = quantities or {}
    for pid in unique_ids:
        product = by_pid.get(pid)
        if not product:
            missing.append(pid)
            continue
        price = (
            product.selling_price_catalog
            if product.selling_price_catalog is not None
            else product.selling_price_order
        )
        products.append(
            {
                "product_id": pid,
                "name": str(product.name or "").strip() or f"Товар #{pid}",
                "image_url": str(product.image_url or "").strip() or DEFAULT_PRODUCT_IMAGE,
                "price": price,
                "quantity": qty_map.get(pid),
            }
        )
    return products, missing


async def _log_trigger_delivery(
    *,
    email: str,
    subject: str,
    trigger_type: str,
    success: bool,
    error_message: str | None = None,
    product_ids: list[int] | None = None,
) -> None:
    await log_api_delivery(
        recipient_email=email,
        subject=subject,
        trigger_type=trigger_type,
        success=success,
        error_message=error_message,
        product_ids=product_ids,
    )


async def send_trigger_email(
    email: str,
    subject: str,
    html_body: str,
    subject_override: str | None = None,
    *,
    trigger_type: str = "api_unknown",
    product_ids: list[int] | None = None,
) -> tuple[int, dict]:
    from bot.database.clients_engine import AsyncSessionLocalClients
    from web.mailing_unsubscribe import (
        is_email_unsubscribed,
        is_marketing_trigger,
        normalize_email,
        unsubscribe_url_for_email,
    )

    subj = (subject_override or "").strip() or subject
    if is_marketing_trigger(trigger_type):
        async with AsyncSessionLocalClients() as session:
            if await is_email_unsubscribed(session, email):
                await _log_trigger_delivery(
                    email=email,
                    subject=subj,
                    trigger_type=trigger_type,
                    success=True,
                    error_message="skipped:unsubscribed",
                    product_ids=product_ids,
                )
                return 200, {
                    "success": True,
                    "sent": 0,
                    "skipped": True,
                    "reason": "unsubscribed",
                    "email": normalize_email(email),
                }
    body = absolutize_static_urls_for_email(ensure_brand_fonts_in_html(html_body))
    text_body = html_to_plain_text(body)
    extra_headers = None
    if is_marketing_trigger(trigger_type):
        extra_headers = _list_unsubscribe_headers(unsubscribe_url_for_email(email)) or None
    try:
        ok, err_obj, _message_id = await asyncio.to_thread(
            send_trigger_email_resend,
            email,
            subject=subj,
            body=body,
            is_html=True,
            text_body=text_body,
            reply_to=RESEND_REPLY_TO or None,
            extra_headers=extra_headers,
        )
    except RuntimeError:
        err = resend_config_error_ua()
        await _log_trigger_delivery(
            email=email,
            subject=subj,
            trigger_type=trigger_type,
            success=False,
            error_message=err,
            product_ids=product_ids,
        )
        return 500, {"success": False, "error": err}
    except Exception as exc:
        err = str(exc)
        await _log_trigger_delivery(
            email=email,
            subject=subj,
            trigger_type=trigger_type,
            success=False,
            error_message=err,
            product_ids=product_ids,
        )
        return 500, {"success": False, "error": err}

    if not ok:
        err_text = err_obj.message if err_obj else "Resend delivery failed"
        await _log_trigger_delivery(
            email=email,
            subject=subj,
            trigger_type=trigger_type,
            success=False,
            error_message=err_text,
            product_ids=product_ids,
        )
        if err_obj:
            body = err_obj.to_api_dict()
            return err_obj.api_status(), body
        return 502, {"success": False, "error": err_text, "error_code": "delivery_failed"}

    await _log_trigger_delivery(
        email=email,
        subject=subj,
        trigger_type=trigger_type,
        success=True,
        product_ids=product_ids,
    )
    return 200, {"success": True, "sent": 1, "email": email, "subject": subj}


def _list_unsubscribe_headers(unsubscribe_url: str) -> dict[str, str]:
    return list_unsubscribe_headers(unsubscribe_url)


async def send_promo_signup_email(
    email: str,
    subject: str,
    html_body: str,
    subject_override: str | None = None,
    *,
    external_request_id: str | None = None,
    unsubscribe_url: str | None = None,
) -> tuple[int, dict]:
    from bot.database.clients_engine import AsyncSessionLocalClients
    from web.mailing_unsubscribe import (
        is_email_unsubscribed,
        normalize_email,
        unsubscribe_url_for_email,
    )

    trigger_type = "promo_signup"
    subj = (subject_override or "").strip() or subject
    req_id = str(external_request_id or "").strip()
    unsub_url = str(unsubscribe_url or "").strip() or unsubscribe_url_for_email(email)

    async with AsyncSessionLocalClients() as session:
        if await is_email_unsubscribed(session, email):
            await log_api_delivery(
                recipient_email=email,
                subject=subj,
                trigger_type=trigger_type,
                success=True,
                error_message="skipped:unsubscribed",
                external_request_id=req_id or None,
            )
            return 200, {
                "success": True,
                "sent": 0,
                "skipped": True,
                "reason": "unsubscribed",
                "email": normalize_email(email),
            }

    if req_id:
        prior = await find_successful_api_delivery(
            trigger_type=trigger_type,
            external_request_id=req_id,
        )
        if prior:
            body: dict[str, Any] = {
                "success": True,
                "sent": 1,
                "email": email,
                "subject": prior.subject or subj,
                "deduplicated": True,
            }
            if prior.resend_message_id:
                body["message_id"] = prior.resend_message_id
            return 200, body

    body_html = absolutize_static_urls_for_email(html_body)
    text_body = html_to_plain_text(body_html)
    headers = _list_unsubscribe_headers(unsub_url)
    try:
        ok, err_obj, message_id = await asyncio.to_thread(
            send_trigger_email_resend,
            email,
            subject=subj,
            body=body_html,
            is_html=True,
            text_body=text_body,
            from_email=RESEND_FROM_EMAIL,
            reply_to=RESEND_REPLY_TO or None,
            extra_headers=headers or None,
        )
    except RuntimeError:
        err = resend_config_error_ua()
        await log_api_delivery(
            recipient_email=email,
            subject=subj,
            trigger_type=trigger_type,
            success=False,
            error_message=err,
            external_request_id=req_id or None,
        )
        return 500, {"success": False, "error": err, "error_code": "resend_not_configured"}
    except Exception as exc:
        err = str(exc)
        await log_api_delivery(
            recipient_email=email,
            subject=subj,
            trigger_type=trigger_type,
            success=False,
            error_message=err,
            external_request_id=req_id or None,
        )
        return 500, {"success": False, "error": err, "error_code": "delivery_failed"}

    if not ok:
        err_text = err_obj.message if err_obj else "Resend delivery failed"
        await log_api_delivery(
            recipient_email=email,
            subject=subj,
            trigger_type=trigger_type,
            success=False,
            error_message=err_text,
            external_request_id=req_id or None,
        )
        if err_obj:
            return err_obj.api_status(), err_obj.to_api_dict()
        return 502, {"success": False, "error": err_text, "error_code": "delivery_failed"}

    await log_api_delivery(
        recipient_email=email,
        subject=subj,
        trigger_type=trigger_type,
        success=True,
        external_request_id=req_id or None,
        resend_message_id=message_id,
    )
    body_obj: dict[str, Any] = {
        "success": True,
        "sent": 1,
        "email": email,
        "subject": subj,
    }
    if message_id:
        body_obj["message_id"] = message_id
    return 200, body_obj


def client_emails_stmt(*, hidden_clause=None, extra_clauses: list | None = None):
    stmt = select(Client.email).where(
        Client.email.isnot(None),
        func.trim(func.coalesce(Client.email, "")) != "",
    )
    if hidden_clause is not None:
        stmt = stmt.where(hidden_clause)
    if extra_clauses:
        stmt = stmt.where(*extra_clauses)
    return stmt


async def fetch_client_emails(session, stmt) -> list[str]:
    raw = (await session.execute(stmt)).scalars().all()
    return sorted({str(x).strip().lower() for x in raw if str(x).strip()})


async def fetch_all_client_emails(session, hidden_clause=None) -> list[str]:
    return await fetch_client_emails(session, client_emails_stmt(hidden_clause=hidden_clause))


async def send_marketing_emails_async(
    recipients: list[str],
    *,
    subject: str,
    body: str,
    is_html: bool,
    on_progress=None,
    checkpoint_path: str | None = None,
    on_status=None,
    should_stop=None,
):
    from web.email_delivery import send_marketing_emails_resilient

    text_body = html_to_plain_text(body) if is_html else None
    return await asyncio.to_thread(
        send_marketing_emails_resilient,
        recipients,
        subject=subject,
        body=body,
        is_html=is_html,
        text_body=text_body,
        from_email=RESEND_FROM_EMAIL,
        reply_to=RESEND_REPLY_TO or None,
        include_unsubscribe=True,
        on_progress=on_progress,
        checkpoint_path=checkpoint_path,
        on_status=on_status,
        should_stop=should_stop,
        max_wave_retries=3,
    )


_PREVIEW_IFRAME_RE = re.compile(
    r'^\s*<iframe\b[^>]*\bmail-preview-frame\b[^>]*\bsrcdoc=(["\'])(.*)\1[^>]*>\s*</iframe>\s*$',
    re.IGNORECASE | re.DOTALL,
)
_PREVIEW_IFRAME_SRCDOC_RE = re.compile(
    r'\bsrcdoc=(["\'])(.*)\1',
    re.IGNORECASE | re.DOTALL,
)
_HTML_TAG_RE = re.compile(r"<[a-z][\s\S]*?>", re.IGNORECASE)


def looks_like_html_content(raw: str) -> bool:
    return bool(_HTML_TAG_RE.search(str(raw or "")))


def recover_mailing_html_body(raw_body: str) -> str:
    """Якщо з форми прийшов iframe превʼю замість HTML листа — відновити srcdoc."""
    content = str(raw_body or "").strip()
    if not content:
        return content

    match = _PREVIEW_IFRAME_RE.match(content)
    if match:
        return html.unescape(match.group(2))

    if re.match(r"^\s*<iframe\b", content, re.IGNORECASE) and "srcdoc=" in content.lower():
        srcdoc = _PREVIEW_IFRAME_SRCDOC_RE.search(content)
        if srcdoc:
            return html.unescape(srcdoc.group(2))

    return content


def checkbox_on(val: str | None) -> bool:
    return form_checkbox_on(val)


def new_mailing_image_filename(ext: str) -> str:
    return f"mailing_{uuid4().hex}{ext}"


def mailing_image_urls(filename: str) -> tuple[str, str]:
    rel_url = f"/static/mailings/{filename}"
    base_url = str(WEB_PUBLIC_BASE_URL or "").strip().rstrip("/")
    abs_url = f"{base_url}{rel_url}" if base_url else rel_url
    return rel_url, abs_url


@dataclass(frozen=True)
class MailingDraft:
    subject: str
    body: str
    body_to_send: str
    is_html: bool


def prepare_mailing_draft(
    subject: str,
    body: str,
    *,
    file_template_name: str = "",
    is_html_raw: str | None = None,
) -> MailingDraft | None:
    subj = (subject or "").strip()
    content = recover_mailing_html_body(body)
    file_tpl = mailing_template_by_name(file_template_name)

    if file_tpl and not content:
        content = file_tpl["html"]
    if file_tpl and not subj:
        subj = file_tpl["subject"]
    if not subj or not content:
        return None

    is_html_flag = checkbox_on(is_html_raw) or bool(file_tpl) or looks_like_html_content(content)
    if is_html_flag:
        content = ensure_brand_fonts_in_html(content)
    body_to_send = absolutize_static_urls_for_email(content) if is_html_flag else content
    return MailingDraft(
        subject=subj,
        body=content,
        body_to_send=body_to_send,
        is_html=is_html_flag,
    )


async def gather_send_recipients(
    session: AsyncSession,
    *,
    mode: str,
    custom_recipients: str,
    recipients_file: UploadFile | None,
    hidden_clause,
) -> tuple[list[str], str | None, int]:
    from web.mailing_unsubscribe import filter_unsubscribed_emails

    if mode != "custom":
        recipients = await fetch_all_client_emails(session, hidden_clause)
    else:
        file_recipients, file_error = await emails_from_uploaded_file(recipients_file)
        if file_error:
            return [], file_error, 0
        recipients = sorted({*emails_from_text(custom_recipients), *file_recipients})

    filtered, skipped = await filter_unsubscribed_emails(session, recipients)
    return filtered, None, skipped


async def gather_campaign_recipients(
    session: AsyncSession,
    campaign: EmailCampaign,
    hidden_clause,
) -> tuple[list[str], int]:
    from web.mailing_unsubscribe import filter_unsubscribed_emails

    if campaign.target_mode == "all":
        recipients = await fetch_all_client_emails(session, hidden_clause)
    else:
        recipients = emails_from_text(campaign.custom_recipients_raw)
    filtered, skipped = await filter_unsubscribed_emails(session, recipients)
    return filtered, skipped


def campaign_body_to_send(campaign: EmailCampaign) -> str:
    if bool(campaign.is_html):
        return absolutize_static_urls_for_email(ensure_brand_fonts_in_html(campaign.body))
    return campaign.body


def format_mailing_result_message(result, *, resend: bool = False) -> str:
    if result is None:
        return "Немає результату відправки."
    prefix = "Повторно надіслано" if resend else "Надіслано"
    msg = f"{prefix}: {getattr(result, 'sent', 0)}. Помилок: {getattr(result, 'failed', 0)}."
    failures = getattr(result, "failures", None) or []
    if failures:
        msg += f" Перша помилка: {failures[0][:220]}"
    return msg


async def execute_manual_mailing_send(
    recipients: list[str],
    draft: MailingDraft,
    *,
    on_progress=None,
    checkpoint_path: str | None = None,
    on_status=None,
    should_stop=None,
):
    return await send_marketing_emails_async(
        recipients,
        subject=draft.subject,
        body=draft.body_to_send,
        is_html=draft.is_html,
        on_progress=on_progress,
        checkpoint_path=checkpoint_path,
        on_status=on_status,
        should_stop=should_stop,
    )


def build_new_campaign(
    *,
    draft: MailingDraft,
    mode: str,
    recipients: list[str],
    result,
    user: str,
    now_dt: datetime,
    is_template_flag: bool,
    template_name: str,
) -> EmailCampaign:
    stored_body = draft.body_to_send if draft.is_html else draft.body
    return EmailCampaign(
        subject=draft.subject,
        body=stored_body,
        is_html=draft.is_html,
        target_mode=mode,
        custom_recipients_raw="\n".join(recipients) if mode == "custom" else None,
        is_template=is_template_flag,
        template_name=(template_name or "").strip() if is_template_flag else None,
        sent_total=int(getattr(result, "sent", 0) or 0),
        run_count=1,
        created_by=user,
        updated_by=user,
        created_at=now_dt,
        last_sent_at=now_dt,
    )


async def record_manual_delivery(
    session: AsyncSession,
    *,
    recipients: list[str],
    subject: str,
    failures: list[str],
    sent_at: datetime,
    trigger_type: str,
    campaign_id: int,
    created_by: str,
    batch_size: int = 300,
) -> None:
    entries = build_manual_delivery_entries(
        recipients=recipients,
        subject=subject,
        failures=failures,
        sent_at=sent_at,
        trigger_type=trigger_type,
        campaign_id=campaign_id,
        created_by=created_by,
    )
    if not entries:
        return
    step = max(50, int(batch_size))
    for offset in range(0, len(entries), step):
        session.add_all(entries[offset : offset + step])
        await session.flush()


def save_template_html_file(template_name: str, template_subject: str, template_html: str) -> Path:
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", template_name.strip())
    if not safe_name.endswith(".html"):
        safe_name += ".html"
    template_path = EMAIL_TEMPLATES_DIR / safe_name
    if template_path.exists():
        raise FileExistsError(safe_name)
    safe_title = html.escape(template_subject, quote=True)
    template_path.write_text(
        f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>{safe_title}</title>
</head>
<body>
{template_html}
</body>
</html>""",
        encoding="utf-8",
    )
    invalidate_mailing_templates_cache()
    return template_path


async def parse_api_mailing_request(
    request: Request,
) -> tuple[dict[str, Any], str, tuple[int, dict] | None]:
    if not api_key_ok(request):
        return {}, "", (401, {"success": False, "error": "Invalid API key."})
    payload = await parse_json_payload(request)
    email, val_err, val_code = validate_recipient_email(str(payload.get("email") or ""))
    if not email:
        return payload, "", (
            400,
            {
                "success": False,
                "error": val_err or "Вкажіть коректний email.",
                "error_code": val_code or "invalid_email",
            },
        )
    return payload, email, None
