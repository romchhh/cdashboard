"""Resend email delivery helpers for marketing mailings (resilient)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import html as html_module
import json
import random
import re
import time
from typing import Iterable
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import quote

from config import (
    EMAIL_UNSUBSCRIBE_URL,
    RESEND_API_BASE,
    RESEND_API_KEY,
    RESEND_FROM_EMAIL,
    RESEND_FROM_NAME,
    RESEND_FROM_ORDERS,
    RESEND_REPLY_TO,
    RESEND_USER_AGENT,
)


ProgressCallback = Callable[[int, int, int], None]
StatusCallback = Callable[[str], None]

# Тимчасові збої API / мережі — ретраїмо, розсилку не зупиняємо
_RETRYABLE_HTTP = frozenset({408, 425, 429, 500, 502, 503, 504})
_MAX_ATTEMPTS_PER_EMAIL = 6
_PACE_SEC = 0.04  # легка затримка між листами, щоб менше ловити 429
_REQUEST_TIMEOUT = 45


def _resend_ready() -> bool:
    return bool(RESEND_API_KEY and RESEND_FROM_EMAIL)


def resend_config_error_ua() -> str:
    return (
        "Resend не налаштовано. Заповніть RESEND_API_KEY і RESEND_FROM_EMAIL у .env."
    )


def format_from_header(email: str, name: str | None = None) -> str:
    addr = str(email or "").strip()
    if not addr:
        return ""
    display = str(name or RESEND_FROM_NAME or "").strip()
    if display:
        return f"{display} <{addr}>"
    return addr


def html_to_plain_text(raw_html: str) -> str:
    """Спрощена HTML → plain text для multipart-листів."""
    content = str(raw_html or "")
    if not content.strip():
        return ""

    content = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", content)
    content = re.sub(r"(?is)<br\s*/?>", "\n", content)
    content = re.sub(r"(?is)</(?:p|div|tr|li|h[1-6])>", "\n", content)
    content = re.sub(
        r'(?is)<a\b[^>]*\bhref=(["\'])(.*?)\1[^>]*>(.*?)</a>',
        lambda m: f"{html_module.unescape(m.group(3).strip())} ({m.group(2).strip()})"
        if m.group(2).strip()
        else html_module.unescape(m.group(3).strip()),
        content,
    )
    content = re.sub(r"(?is)<[^>]+>", " ", content)
    content = html_module.unescape(content)
    content = content.replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in content.splitlines()]
    out: list[str] = []
    prev_blank = False
    for line in lines:
        if not line:
            if not prev_blank:
                out.append("")
            prev_blank = True
            continue
        out.append(line)
        prev_blank = False
    return "\n".join(out).strip()


def list_unsubscribe_headers(unsubscribe_url: str) -> dict[str, str]:
    url = str(unsubscribe_url or "").strip()
    if not url:
        return {}
    return {
        "List-Unsubscribe": f"<{url}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }


def bulk_unsubscribe_url(recipient_email: str) -> str:
    from web.mailing_unsubscribe import unsubscribe_url_for_email

    email = str(recipient_email or "").strip()
    if not email:
        return ""
    return unsubscribe_url_for_email(email)


_UNSUBSCRIBE_MARKERS = ("unsubscribe", "відписат", "отписат")


def personalize_bulk_mailing_html(html_body: str, recipient_email: str) -> str:
    """Додає footer «Відписатися» для ручних розсилок, якщо його ще немає."""
    content = str(html_body or "")
    if not content.strip():
        return content
    lower = content.lower()
    if any(marker in lower for marker in _UNSUBSCRIBE_MARKERS):
        return content

    unsub_url = bulk_unsubscribe_url(recipient_email)
    if not unsub_url:
        return content

    safe_url = html_module.escape(unsub_url, quote=True)
    footer = (
        f'<p style="margin:24px 0 0;font-family:Arial,Helvetica,sans-serif;font-size:12px;'
        f'line-height:1.5;color:#6B6565;text-align:center;">'
        f'<a href="{safe_url}" target="_blank" '
        f'style="color:#6B6565;text-decoration:underline;">Відписатися від розсилки</a></p>'
    )
    if re.search(r"</body>", content, re.IGNORECASE):
        return re.sub(r"</body>", footer + "</body>", content, count=1, flags=re.IGNORECASE)
    return content + footer


def bulk_mailing_plain_text(html_body: str, recipient_email: str, *, include_unsubscribe: bool) -> str:
    plain = html_to_plain_text(html_body)
    if not include_unsubscribe:
        return plain
    unsub_url = bulk_unsubscribe_url(recipient_email)
    if not unsub_url:
        return plain
    line = f"Відписатися від розсилки: {unsub_url}"
    return f"{plain}\n\n{line}".strip() if plain else line


@dataclass(slots=True)
class DeliveryResult:
    sent: int
    failed: int
    failures: list[str]
    retried: int = 0
    last_error: "ResendSendError | None" = None
    cancelled: bool = False


@dataclass(slots=True)
class ResendSendError:
    """Структурована помилка від Resend API."""

    http_status: int
    code: str
    message: str

    def as_failure_line(self, email: str) -> str:
        return f"{email}: [{self.code}] {self.message}"

    def to_api_dict(self) -> dict[str, object]:
        return {
            "success": False,
            "error": self.message,
            "error_code": self.code,
            "provider": "resend",
        }

    def api_status(self) -> int:
        """HTTP-статус для нашого API (4xx від Resend — як є, 5xx → 502)."""
        if self.http_status in (401, 403):
            return 502
        if 400 <= self.http_status < 500:
            return self.http_status
        return 502


def _build_payload(
    subject: str,
    body: str,
    to_email: str,
    *,
    is_html: bool,
    from_email: str | None = None,
    from_name: str | None = None,
    text_body: str | None = None,
    reply_to: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> dict:
    sender = format_from_header(from_email or RESEND_FROM_EMAIL, from_name)
    payload: dict[str, object] = {
        "from": sender,
        "to": [to_email],
        "subject": subject,
    }
    if is_html:
        payload["html"] = body
        plain = str(text_body or "").strip() or html_to_plain_text(body)
        if plain:
            payload["text"] = plain
    else:
        payload["text"] = body

    reply = str(reply_to if reply_to is not None else RESEND_REPLY_TO or "").strip()
    if reply:
        payload["reply_to"] = reply

    if extra_headers:
        payload["headers"] = {str(k): str(v) for k, v in extra_headers.items() if k and v}
    return payload


def _parse_resend_message_id(raw_body: str) -> str | None:
    try:
        data = json.loads(raw_body or "{}")
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    for key in ("id", "message_id", "messageId"):
        val = data.get(key)
        if val:
            return str(val).strip() or None
    nested = data.get("data")
    if isinstance(nested, dict):
        for key in ("id", "message_id", "messageId"):
            val = nested.get(key)
            if val:
                return str(val).strip() or None
    result = data.get("result")
    if isinstance(result, dict):
        val = result.get("id")
        if val:
            return str(val).strip() or None
    return None


def _send_one_resend(payload: dict) -> str | None:
    req = urlrequest.Request(
        f"{RESEND_API_BASE.rstrip('/')}/emails",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": RESEND_USER_AGENT,
        },
        method="POST",
    )
    with urlrequest.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
        body = resp.read().decode("utf-8", errors="ignore")
        return _parse_resend_message_id(body)


def _retry_after_seconds(exc: urlerror.HTTPError, attempt: int) -> float:
    """Пауза перед наступною спробою (Retry-After або exponential backoff)."""
    if exc.code == 429:
        raw = ""
        try:
            raw = (exc.headers.get("Retry-After") or "").strip()
        except Exception:
            raw = ""
        if raw.isdigit():
            return min(120.0, float(raw) + random.uniform(0.1, 0.8))
    base = min(60.0, (2 ** max(0, attempt - 1)) * 0.8)
    return base + random.uniform(0.05, 0.4)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, urlerror.HTTPError):
        return int(exc.code) in _RETRYABLE_HTTP
    if isinstance(exc, urlerror.URLError):
        return True
    if isinstance(exc, TimeoutError):
        return True
    # ssl / connection resets тощо
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    needles = ("timeout", "timed out", "temporarily", "connection", "reset", "broken pipe", "ssl")
    return any(n in name or n in msg for n in needles)


def _parse_resend_error_body(raw_body: str, http_status: int) -> ResendSendError:
    code = f"http_{http_status}"
    message = f"HTTP {http_status}"
    try:
        data = json.loads(raw_body or "{}")
    except Exception:
        data = None
    if isinstance(data, dict):
        message = str(data.get("message") or data.get("error") or message).strip() or message
        for key in ("name", "type", "code", "error_code"):
            val = data.get(key)
            if val:
                code = str(val).strip()
                break
        status_from_body = data.get("statusCode")
        if status_from_body:
            try:
                http_status = int(status_from_body)
            except (TypeError, ValueError):
                pass
    elif raw_body:
        message = raw_body.strip()[:500] or message
    return ResendSendError(http_status=http_status, code=code, message=message)


def _format_http_error(exc: urlerror.HTTPError) -> ResendSendError:
    details = ""
    try:
        details = exc.read().decode("utf-8", errors="ignore")
    except Exception:
        details = ""
    return _parse_resend_error_body(details, int(exc.code))


def send_one_with_retries(
    payload: dict,
    *,
    on_status: StatusCallback | None = None,
) -> tuple[bool, ResendSendError | None, int, str | None]:
    """
    Надіслати один лист з ретраями.
    Повертає (ok, resend_error, retry_count, message_id).
    Ніколи не кидає — лише повертає результат.
    """
    retries = 0
    last_err = ResendSendError(http_status=500, code="unknown", message="unknown")
    for attempt in range(1, _MAX_ATTEMPTS_PER_EMAIL + 1):
        try:
            message_id = _send_one_resend(payload)
            return True, None, retries, message_id
        except urlerror.HTTPError as exc:
            last_err = _format_http_error(exc)
            retryable = _is_retryable(exc)
            wait_s = _retry_after_seconds(exc, attempt) if retryable else 0.0
        except Exception as exc:
            last_err = ResendSendError(
                http_status=500,
                code=type(exc).__name__.lower(),
                message=(str(exc)[:400] or type(exc).__name__),
            )
            retryable = _is_retryable(exc)
            wait_s = (
                min(45.0, (2 ** (attempt - 1)) * 0.7 + random.uniform(0.1, 0.5))
                if retryable
                else 0.0
            )

        if not retryable or attempt >= _MAX_ATTEMPTS_PER_EMAIL:
            return False, last_err, retries, None

        retries += 1
        if on_status:
            on_status(
                f"Тимчасовий збій ({last_err.message[:80]}). "
                f"Повтор {attempt}/{_MAX_ATTEMPTS_PER_EMAIL} через {wait_s:.1f}с…"
            )
        time.sleep(max(0.2, wait_s))

    return False, last_err, retries, None


def send_trigger_email_resend(
    to_email: str,
    *,
    subject: str,
    body: str,
    is_html: bool = True,
    text_body: str | None = None,
    from_email: str | None = None,
    from_name: str | None = None,
    reply_to: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> tuple[bool, ResendSendError | None, str | None]:
    """Один тригерний лист через Resend. Повертає (ok, resend_error, message_id)."""
    if not _resend_ready():
        raise RuntimeError(resend_config_error_ua())
    sender = from_email or RESEND_FROM_ORDERS or RESEND_FROM_EMAIL
    payload = _build_payload(
        subject,
        body,
        to_email,
        is_html=is_html,
        from_email=sender,
        from_name=from_name,
        text_body=text_body,
        reply_to=reply_to,
        extra_headers=extra_headers,
    )
    ok, err, _, message_id = send_one_with_retries(payload)
    return ok, err, message_id


def send_marketing_emails(
    recipients: Iterable[str],
    *,
    subject: str,
    body: str,
    is_html: bool,
    text_body: str | None = None,
    from_email: str | None = None,
    reply_to: str | None = None,
    include_unsubscribe: bool = False,
    on_progress: ProgressCallback | None = None,
    checkpoint_path: str | None = None,
    on_status: StatusCallback | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> DeliveryResult:
    """
    Масова відправка. Окремі збої / 429 / таймаути НЕ зупиняють розсилку:
    ретраї по кожному email, далі — наступний отримувач.
    should_stop() — м'яка зупинка між листами (кнопка «Зупинити»).
    """
    if not _resend_ready():
        raise RuntimeError(resend_config_error_ua())

    recipients_list = [str(x).strip() for x in recipients if str(x).strip()]
    if not recipients_list:
        return DeliveryResult(sent=0, failed=0, failures=[])

    from web.mailing_resume import append_checkpoint

    failures: list[str] = []
    last_error: ResendSendError | None = None
    sent = 0
    retried_total = 0
    total = len(recipients_list)
    cancelled = False
    if on_progress:
        on_progress(0, 0, total)

    for idx, to_email in enumerate(recipients_list, start=1):
        if should_stop:
            try:
                if should_stop():
                    cancelled = True
                    if on_status:
                        on_status("Зупинено користувачем. Завершуємо після поточного кроку…")
                    break
            except Exception:
                pass
        try:
            headers: dict[str, str] | None = None
            email_body = body
            email_text: str | None = None

            if is_html:
                if include_unsubscribe:
                    unsub_url = bulk_unsubscribe_url(to_email)
                    headers = list_unsubscribe_headers(unsub_url) or None
                    email_body = personalize_bulk_mailing_html(body, to_email)
                    email_text = bulk_mailing_plain_text(email_body, to_email, include_unsubscribe=True)
                else:
                    email_text = str(text_body or "").strip() or html_to_plain_text(body)
            elif text_body:
                email_text = str(text_body).strip()

            payload = _build_payload(
                subject,
                email_body,
                to_email,
                is_html=is_html,
                from_email=from_email,
                text_body=email_text if is_html else None,
                reply_to=reply_to,
                extra_headers=headers,
            )
            ok, err_obj, retries, _message_id = send_one_with_retries(payload, on_status=on_status)
            retried_total += retries
            if ok:
                sent += 1
                append_checkpoint(
                    checkpoint_path,
                    email=to_email,
                    success=True,
                    subject=subject,
                )
            else:
                if err_obj:
                    last_error = err_obj
                    failures.append(err_obj.as_failure_line(to_email))
                else:
                    failures.append(f"{to_email}: unknown error")
                append_checkpoint(
                    checkpoint_path,
                    email=to_email,
                    success=False,
                    error=(err_obj.message if err_obj else "error")[:400],
                    subject=subject,
                )
        except Exception as exc:  # захист: жоден неочікуваний збій не валить цикл
            err_msg = str(exc)[:400] or type(exc).__name__
            failures.append(f"{to_email}: {err_msg}")
            try:
                append_checkpoint(
                    checkpoint_path,
                    email=to_email,
                    success=False,
                    error=err_msg,
                    subject=subject,
                )
            except Exception:
                pass

        if on_progress:
            try:
                on_progress(sent, len(failures), total)
            except Exception:
                pass

        # невелика пауза між листами (крім останнього)
        if idx < total and _PACE_SEC > 0 and not cancelled:
            time.sleep(_PACE_SEC)

    return DeliveryResult(
        sent=sent,
        failed=len(failures),
        failures=failures,
        retried=retried_total,
        last_error=last_error,
        cancelled=cancelled,
    )


def send_marketing_emails_resilient(
    recipients: list[str],
    *,
    subject: str,
    body: str,
    is_html: bool,
    text_body: str | None = None,
    from_email: str | None = None,
    reply_to: str | None = None,
    include_unsubscribe: bool = False,
    on_progress: ProgressCallback | None = None,
    checkpoint_path: str | None = None,
    on_status: StatusCallback | None = None,
    should_stop: Callable[[], bool] | None = None,
    max_wave_retries: int = 3,
) -> DeliveryResult:
    """
    Кілька «хвиль»: після основного проходу ще раз пробує адреси,
    які впали з тимчасових причин (за checkpoint / failures).
    Розсилка не зупиняється між хвилями (окрім should_stop).
    """
    from web.mailing_resume import load_successful_emails_from_checkpoint

    pending = [str(x).strip().lower() for x in recipients if str(x).strip()]
    total_sent = 0
    all_failures: list[str] = []
    retried_total = 0
    original_total = len(pending)
    cancelled = False

    def _wave_progress(sent_wave: int, failed_wave: int, wave_total: int) -> None:
        if not on_progress:
            return
        # уже успішно в попередніх хвилях + у цій
        ok = total_sent + sent_wave
        # failed_wave — поточні невдачі хвилі (ще можуть піти в наступну)
        fail_est = max(0, original_total - ok - failed_wave)
        on_progress(ok, max(failed_wave, fail_est), original_total)

    for wave in range(max_wave_retries + 1):
        if not pending:
            break
        if should_stop:
            try:
                if should_stop():
                    cancelled = True
                    break
            except Exception:
                pass
        if wave > 0 and on_status:
            on_status(f"Повторна хвиля {wave}/{max_wave_retries} для {len(pending)} адрес…")
            time.sleep(1.5 + random.uniform(0, 1.0))

        result = send_marketing_emails(
            pending,
            subject=subject,
            body=body,
            is_html=is_html,
            text_body=text_body,
            from_email=from_email,
            reply_to=reply_to,
            include_unsubscribe=include_unsubscribe,
            on_progress=_wave_progress,
            checkpoint_path=checkpoint_path,
            on_status=on_status,
            should_stop=should_stop,
        )
        total_sent += int(result.sent)
        retried_total += int(result.retried or 0)
        if getattr(result, "cancelled", False):
            cancelled = True
            all_failures = list(result.failures or [])
            break

        succeeded = load_successful_emails_from_checkpoint(checkpoint_path)
        still_failed = [
            line.split(":", 1)[0].strip().lower()
            for line in (result.failures or [])
            if ":" in line
        ]
        pending = [e for e in still_failed if e and e not in succeeded]
        if not pending:
            all_failures = []
            break
        all_failures = list(result.failures or [])

    # фінальні failures = останній pending (якщо не скасовано)
    if cancelled:
        # Keep partial failure list; remaining unsent are not counted as failed.
        pass
    elif pending:
        # зберегти останні тексти помилок
        fail_map = {}
        for line in all_failures:
            if ":" in line:
                email_part, rest = line.split(":", 1)
                fail_map[email_part.strip().lower()] = rest.strip()
        all_failures = [
            f"{e}: {fail_map.get(e, 'не вдалося після повторів')}" for e in pending
        ]
    else:
        all_failures = []

    if on_progress:
        on_progress(total_sent, len(all_failures), original_total)

    return DeliveryResult(
        sent=total_sent,
        failed=len(all_failures),
        failures=all_failures,
        retried=retried_total,
        cancelled=cancelled,
    )
