"""Email / mailings routes."""

import asyncio
import json
import logging
import time
from datetime import timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import desc, func, select
from sqlalchemy.orm import load_only

from bot.database.clients_models import EmailCampaign, EmailUnsubscribe
from bot.database.clients_engine import AsyncSessionLocalClients
from web.auth import get_current_user
from web.clients_query import (
    _clients_apply_visibility_filters,
    _clients_filter_clauses_from_request,
    _clients_hidden_where,
)
from web.cosmy_email import (
    CART_URL_DEFAULT,
    CHECKOUT_URL_DEFAULT,
    normalize_promo_signup_lang,
    render_abandoned_cart_email,
    render_abandoned_payment_email,
    render_order_placed_email,
    render_price_drop_cart_email,
    render_price_drop_wishlist_email,
    render_promo_signup_email,
    render_welcome_email,
)
from web.deps import BASE_DIR, now_kyiv, templates, verify_csrf_dependency
from web.security import verify_csrf_header_dependency
from web.email_delivery import resend_config_error_ua
from web.mailing_api_payload import parse_order_context, recipient_name, resolve_api_products, tracking_url
from web.mailing_delivery_log import (
    build_manual_delivery_entries,
    fetch_mailing_delivery_stats,
    parse_mailing_stats_period,
)
from web.mailing_helpers import (
    MailingDraft,
    build_new_campaign,
    campaign_body_to_send,
    checkbox_on,
    client_emails_stmt,
    execute_manual_mailing_send,
    fetch_client_emails,
    format_mailing_result_message,
    gather_campaign_recipients,
    gather_send_recipients,
    load_products_for_email,
    mailing_file_templates,
    mailing_image_urls,
    new_mailing_image_filename,
    parse_api_mailing_request,
    prepare_mailing_draft,
    record_manual_delivery,
    safe_image_ext,
    save_template_html_file,
    send_promo_signup_email,
    send_trigger_email,
)
from web.mailing_unsubscribe import (
    SOURCE_LABELS_UA,
    count_unsubscribes,
    fetch_unsubscribes_page,
    filter_unsubscribed_emails,
    is_promo_unsubscribe_route,
    is_valid_email,
    normalize_email,
    parse_unsubscribe_token,
    record_unsubscribe,
    remove_unsubscribe_by_id,
    unsubscribe_url_for_email,
)
from web.mailing_body_staging import delete_staged_body, load_staged_body, save_staged_body
from web.mailing_recipients_upload import (
    PREFILL_TEXTAREA_MAX,
    delete_recipients_text_list,
    delete_saved_recipients_upload,
    load_recipients_text_list,
    open_saved_recipients_upload,
    save_recipients_text_list,
    save_recipients_upload_from_file,
)
from web.mailing_jobs import (
    bump_mailing_job_progress,
    create_mailing_job,
    finalize_mailing_job_cancelled,
    find_active_mailing_job,
    get_mailing_job,
    is_mailing_job_cancel_requested,
    list_mailing_jobs,
    refresh_mailing_job_from_checkpoint,
    request_mailing_job_cancel,
    update_mailing_job,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/api/mailings/countdown.gif")
async def api_mailings_countdown_gif(
    ends: int = Query(..., description="Unix timestamp (секунди) кінця дії"),
):
    """Публічний live countdown для листів (без API-ключа)."""
    from web.countdown_timer import build_countdown_gif

    try:
        ends_ts = int(ends)
    except (TypeError, ValueError):
        return JSONResponse({"success": False, "error": "ends має бути unix timestamp."}, status_code=400)
    if ends_ts < 0 or ends_ts > 4102444800:
        return JSONResponse({"success": False, "error": "ends поза допустимим діапазоном."}, status_code=400)

    try:
        gif = build_countdown_gif(ends_ts)
    except Exception:
        logger.exception("countdown.gif render failed ends=%s", ends_ts)
        return JSONResponse({"success": False, "error": "Не вдалося згенерувати таймер."}, status_code=500)

    if len(gif) < 8_000:
        logger.error("countdown.gif too small (%s bytes) ends=%s — перевір шрифти на сервері", len(gif), ends_ts)
        return JSONResponse(
            {"success": False, "error": "Таймер згенеровано некоректно (немає шрифтів на сервері)."},
            status_code=500,
        )

    return Response(
        content=gif,
        media_type="image/gif",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


def _require_marketing_access(request: Request) -> None:
    if not getattr(request.state, "access_marketing", False):
        raise HTTPException(
            status_code=403,
            detail="Розсилки доступні лише користувачам з доступом до маркетингу.",
        )


def _mailings_redirect(notice: str, msg: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"/clients/mailings?{urlencode({'notice': notice, 'msg': msg})}",
        status_code=302,
    )


def _wants_json(request: Request) -> bool:
    accept = (request.headers.get("accept") or "").lower()
    if "application/json" in accept:
        return True
    requested = (request.headers.get("x-requested-with") or "").lower()
    return requested in {"xmlhttprequest", "fetch"}


async def _send_mailing_flow(
    *,
    recipients: list[str],
    draft,
    user: str,
    mode: str,
    trigger_type: str,
    campaign: EmailCampaign | None = None,
    campaign_id: int | None = None,
    is_template_flag: bool = False,
    template_name: str = "",
    on_progress=None,
    checkpoint_path: str | None = None,
    skipped_already: int = 0,
    on_status=None,
    should_stop=None,
    session=None,
) -> tuple[object, str | None, int | None]:
    cid_hint: int | None = int(campaign.id) if campaign is not None else campaign_id

    if not recipients:
        if skipped_already > 0:
            return None, (
                f"Усі {skipped_already} адрес уже отримували цей лист — нових отримувачів немає."
            ), cid_hint
        return None, "Немає отримувачів для розсилки.", None

    try:
        result = await execute_manual_mailing_send(
            recipients,
            draft,
            on_progress=on_progress,
            checkpoint_path=checkpoint_path,
            on_status=on_status,
            should_stop=should_stop,
        )
    except RuntimeError:
        return None, resend_config_error_ua(), cid_hint
    except Exception as exc:
        label = "повторної відправки" if (campaign is not None or campaign_id) else "відправки"
        return None, f"Помилка {label}: {str(exc)[:180]}", cid_hint

    if result is None:
        return None, "Немає результату відправки.", cid_hint

    async def _persist_db(sess) -> tuple[object, str | None, int | None]:
        nonlocal campaign
        now_dt = now_kyiv()
        camp = campaign
        if camp is None and campaign_id is not None:
            camp = (
                await sess.execute(
                    select(EmailCampaign).where(EmailCampaign.id == int(campaign_id))
                )
            ).scalar_one_or_none()
        if camp is None:
            camp = build_new_campaign(
                draft=draft,
                mode=mode,
                recipients=recipients,
                result=result,
                user=user,
                now_dt=now_dt,
                is_template_flag=is_template_flag,
                template_name=template_name,
            )
            sess.add(camp)
            await sess.flush()
        else:
            camp.run_count = int(camp.run_count or 0) + 1
            camp.sent_total = int(camp.sent_total or 0) + int(getattr(result, "sent", 0) or 0)
            camp.last_sent_at = now_dt
            camp.updated_by = user

        await record_manual_delivery(
            sess,
            recipients=recipients,
            subject=draft.subject,
            failures=result.failures,
            sent_at=now_dt,
            trigger_type=trigger_type,
            campaign_id=int(camp.id),
            created_by=user,
        )
        await sess.commit()
        return result, None, int(camp.id)

    if session is not None:
        return await _persist_db(session)
    async with AsyncSessionLocalClients() as sess:
        return await _persist_db(sess)


async def _prepare_recipients_skip_already(
    session,
    *,
    recipients: list[str],
    subject: str,
) -> tuple[list[str], int, dict]:
    """Пропустити адреси, які вже успішно отримували цей subject."""
    from web.mailing_resume import (
        fetch_resend_sent_emails_for_subject,
        fetch_successful_emails_from_db,
        filter_unsent_recipients,
        load_successful_emails_from_recent_checkpoints,
        merge_already_sent,
    )

    db_sent = await fetch_successful_emails_from_db(session, subject, days=14)
    cp_sent = load_successful_emails_from_recent_checkpoints(subject, max_age_hours=72)
    resend_sent = await asyncio.to_thread(
        fetch_resend_sent_emails_for_subject,
        subject,
        max_pages=40,
        page_size=100,
    )
    already = merge_already_sent(db_sent, cp_sent, resend_sent)
    kept, skipped = filter_unsent_recipients(recipients, already)
    meta = {
        "from_db": len(db_sent),
        "from_checkpoint": len(cp_sent),
        "from_resend": len(resend_sent),
        "already_unique": len(already),
        "skipped": skipped,
        "remaining": len(kept),
    }
    return kept, skipped, meta


async def _import_checkpoint_on_failure(
    *,
    checkpoint_path: str | None,
    subject: str,
    trigger_type: str,
    campaign_id: int | None,
    created_by: str,
) -> int:
    """Після збою — зберегти в журнал те, що встигли надіслати (з checkpoint)."""
    if not checkpoint_path:
        return 0
    from web.mailing_resume import (
        fetch_successful_emails_from_db,
        load_successful_emails_from_checkpoint,
    )

    emails = load_successful_emails_from_checkpoint(checkpoint_path)
    if not emails:
        return 0
    try:
        async with AsyncSessionLocalClients() as session:
            already = await fetch_successful_emails_from_db(session, subject, days=30)
            to_add = sorted(emails - already)
            if not to_add:
                return 0
            now_dt = now_kyiv()
            entries = build_manual_delivery_entries(
                recipients=to_add,
                subject=subject,
                failures=[],
                sent_at=now_dt,
                trigger_type=trigger_type,
                campaign_id=campaign_id,
                created_by=created_by,
            )
            for offset in range(0, len(entries), 300):
                session.add_all(entries[offset : offset + 300])
                await session.flush()
            if campaign_id is not None:
                campaign = (
                    await session.execute(
                        select(EmailCampaign).where(EmailCampaign.id == int(campaign_id))
                    )
                ).scalar_one_or_none()
                if campaign is not None:
                    campaign.sent_total = int(campaign.sent_total or 0) + len(to_add)
                    campaign.last_sent_at = now_dt
            await session.commit()
            return len(to_add)
    except Exception:
        logger.exception("Failed to import mailing checkpoint into delivery log")
        return 0


async def _run_mailing_job(
    job_id: str,
    *,
    recipients: list[str],
    draft: MailingDraft,
    user: str,
    mode: str,
    trigger_type: str,
    campaign_id: int | None = None,
    is_template_flag: bool = False,
    template_name: str = "",
    defer_recipient_gather: bool = False,
    custom_recipients: str = "",
    recipients_upload_path: str | None = None,
) -> None:
    from web.mailing_resume import (
        filter_unsent_recipients,
        load_successful_emails_from_checkpoint,
        new_checkpoint_path,
    )

    update_mailing_job(job_id, status="running")
    checkpoint_path = new_checkpoint_path(subject=draft.subject, job_id=job_id)
    update_mailing_job(
        job_id,
        checkpoint_path=checkpoint_path,
        message="Розсилка запущена на сервері. Можна закрити вкладку — відправка не зупиниться.",
    )

    if defer_recipient_gather:
        update_mailing_job(job_id, message="Збираємо список отримувачів з бази…")
        hidden_clause = _clients_hidden_where()
        upload_handle = open_saved_recipients_upload(recipients_upload_path)
        try:
            async with AsyncSessionLocalClients() as session:
                recipients, recipient_error, skipped_unsub = await gather_send_recipients(
                    session,
                    mode=mode,
                    custom_recipients=custom_recipients,
                    recipients_file=upload_handle,
                    hidden_clause=hidden_clause,
                )
        finally:
            delete_saved_recipients_upload(recipients_upload_path)
        if recipient_error:
            update_mailing_job(job_id, status="error", message=recipient_error)
            return
        if not recipients:
            err = "Немає отримувачів для розсилки."
            if skipped_unsub:
                err = (
                    f"Усі отримувачі відписані від розсилок ({skipped_unsub}). "
                    "Перевірте список у «Відписані»."
                )
            update_mailing_job(job_id, status="error", message=err)
            return
        update_mailing_job(
            job_id,
            total=len(recipients),
            message=f"Отримувачів: {len(recipients)}. Починаємо відправку…",
        )

    _last_status_write = 0.0

    def _on_progress(sent: int, failed: int, _total: int) -> None:
        bump_mailing_job_progress(job_id, sent=sent, failed=failed)

    def _on_status(msg: str) -> None:
        nonlocal _last_status_write
        now = time.time()
        # Retries can spam status; keep job JSON writes rare during send.
        if now - _last_status_write < 2.0:
            return
        _last_status_write = now
        update_mailing_job(job_id, message=str(msg or "")[:500])

    def _should_stop() -> bool:
        return is_mailing_job_cancel_requested(job_id)

    saved_campaign_id = campaign_id
    # Ручні розсилки з панелі — завжди надсилаємо всім отримувачам (повтор, нова спроба).
    # Глобальна дедуплікація за subject лишається лише для автоматичних сценаріїв.
    skip_global_dedup = trigger_type in {"manual", "manual_resend"}
    kept: list[str] = []
    skipped = 0
    try:
        async with AsyncSessionLocalClients() as session:
            campaign = None
            if campaign_id is not None:
                campaign = (
                    await session.execute(
                        select(EmailCampaign).where(EmailCampaign.id == int(campaign_id))
                    )
                ).scalar_one_or_none()
                if campaign is None:
                    update_mailing_job(
                        job_id,
                        status="error",
                        message="Розсилку не знайдено.",
                    )
                    return

            if campaign is None:
                now_dt = now_kyiv()
                campaign = build_new_campaign(
                    draft=draft,
                    mode=mode,
                    recipients=recipients,
                    result=type("R", (), {"sent": 0})(),
                    user=user,
                    now_dt=now_dt,
                    is_template_flag=is_template_flag,
                    template_name=template_name,
                )
                campaign.sent_total = 0
                campaign.run_count = 0
                session.add(campaign)
                await session.commit()
                await session.refresh(campaign)
                saved_campaign_id = int(campaign.id)
                update_mailing_job(job_id, campaign_id=saved_campaign_id)

            if skip_global_dedup:
                kept = [str(x).strip().lower() for x in recipients if str(x).strip()]
                update_mailing_job(job_id, total=len(kept))
            else:
                kept, skipped, meta = await _prepare_recipients_skip_already(
                    session,
                    recipients=recipients,
                    subject=draft.subject,
                )
                if skipped:
                    update_mailing_job(
                        job_id,
                        message=(
                            f"Пропущено вже надісланих: {skipped} "
                            f"(БД {meta['from_db']}, checkpoint {meta['from_checkpoint']}, "
                            f"Resend {meta['from_resend']}). Залишилось: {len(kept)}."
                        ),
                        total=len(kept),
                    )
                else:
                    update_mailing_job(job_id, total=len(kept))

        if not kept:
            if skipped:
                msg = (
                    f"Нових отримувачів немає — усі {skipped} адрес уже отримували цей лист."
                )
                update_mailing_job(
                    job_id,
                    status="done",
                    sent=0,
                    failed=0,
                    message=msg,
                    campaign_id=saved_campaign_id,
                )
            else:
                update_mailing_job(
                    job_id,
                    status="error",
                    message="Немає отримувачів для розсилки.",
                )
            return

        from web.mailing_resume import load_checkpoint_progress

        pending = list(kept)
        last_error = None
        final_result = None
        for attempt in range(1, 4):
            if not pending:
                break
            if _should_stop():
                cur = get_mailing_job(job_id, purge=False)
                finalize_mailing_job_cancelled(
                    job_id,
                    sent=int(cur.sent if cur else 0),
                    failed=int(cur.failed if cur else 0),
                )
                return
            if attempt > 1:
                update_mailing_job(
                    job_id,
                    message=f"Автопродовження після збою (спроба {attempt}/3), залишилось {len(pending)}…",
                    total=len(pending),
                )
                await asyncio.sleep(2.0 * attempt)

            result, error, saved_campaign_id = await _send_mailing_flow(
                recipients=pending,
                draft=draft,
                user=user,
                mode=mode,
                trigger_type=trigger_type,
                campaign_id=saved_campaign_id,
                is_template_flag=is_template_flag,
                template_name=template_name,
                on_progress=_on_progress,
                checkpoint_path=checkpoint_path,
                skipped_already=skipped if attempt == 1 else 0,
                on_status=_on_status,
                should_stop=_should_stop,
            )
            if result is not None and getattr(result, "cancelled", False):
                finalize_mailing_job_cancelled(
                    job_id,
                    sent=int(getattr(result, "sent", 0) or 0),
                    failed=int(getattr(result, "failed", 0) or 0),
                )
                return
            if not error and result is not None:
                final_result = result
                last_error = None
                break

            last_error = error
            await _import_checkpoint_on_failure(
                checkpoint_path=checkpoint_path,
                subject=draft.subject,
                trigger_type=trigger_type,
                campaign_id=saved_campaign_id,
                created_by=user,
            )
            succeeded = load_successful_emails_from_checkpoint(checkpoint_path)
            pending, _ = filter_unsent_recipients(pending, succeeded)
            if not pending:
                last_error = None
                cp_sent, cp_failed = load_checkpoint_progress(checkpoint_path)
                final_result = type(
                    "R",
                    (),
                    {
                        "sent": cp_sent,
                        "failed": cp_failed,
                        "failures": [],
                        "retried": 0,
                    },
                )()
                break

        if last_error and final_result is None:
            imported = await _import_checkpoint_on_failure(
                checkpoint_path=checkpoint_path,
                subject=draft.subject,
                trigger_type=trigger_type,
                campaign_id=saved_campaign_id,
                created_by=user,
            )
            cp_sent, cp_failed = load_checkpoint_progress(checkpoint_path)
            if cp_sent or cp_failed:
                msg = (
                    f"Розсилку завершено з перериваннями. "
                    f"Надіслано: {cp_sent}, помилок: {cp_failed}. "
                    f"Остання помилка: {last_error}"
                )
                if imported:
                    msg += f" Записано в журнал: {imported}."
                update_mailing_job(
                    job_id,
                    status="done",
                    sent=cp_sent,
                    failed=cp_failed,
                    message=msg,
                    campaign_id=saved_campaign_id,
                )
                return
            extra = f" Збережено в журнал з checkpoint: {imported}." if imported else ""
            update_mailing_job(job_id, status="error", message=(last_error or "error") + extra)
            return

        if final_result is None:
            cp_sent, cp_failed = load_checkpoint_progress(checkpoint_path)
            update_mailing_job(
                job_id,
                status="done" if cp_sent or skipped else "error",
                sent=cp_sent,
                failed=cp_failed,
                message=(
                    f"Надіслано: {cp_sent}. Помилок: {cp_failed}."
                    + (f" Пропущено вже надісланих раніше: {skipped}." if skipped else "")
                    if cp_sent or skipped
                    else "Немає результату відправки."
                ),
                campaign_id=saved_campaign_id,
            )
            return

        msg = format_mailing_result_message(final_result, resend=campaign_id is not None)
        if skipped:
            msg += f" Пропущено вже надісланих раніше: {skipped}."
        if getattr(final_result, "retried", 0):
            msg += f" Автоповторів API: {int(final_result.retried)}."
        update_mailing_job(
            job_id,
            status="done",
            sent=int(final_result.sent),
            failed=int(final_result.failed),
            failures=list(final_result.failures or [])[:20],
            message=msg,
            campaign_id=saved_campaign_id,
        )
    except Exception as exc:
        logger.exception("Mailing job %s failed", job_id)
        imported = await _import_checkpoint_on_failure(
            checkpoint_path=checkpoint_path,
            subject=draft.subject,
            trigger_type=trigger_type,
            campaign_id=saved_campaign_id,
            created_by=user,
        )
        from web.mailing_resume import load_successful_emails_from_checkpoint

        from web.mailing_resume import load_checkpoint_progress

        cp_sent, cp_failed = load_checkpoint_progress(checkpoint_path)
        if cp_sent or cp_failed:
            msg = (
                f"Розсилку частково завершено після збою процесу. "
                f"Надіслано: {cp_sent}, помилок: {cp_failed}. Помилка: {str(exc)[:160]}."
            )
            if imported:
                msg += f" У журнал: {imported}."
            update_mailing_job(
                job_id,
                status="done",
                sent=cp_sent,
                failed=cp_failed,
                message=msg,
                campaign_id=saved_campaign_id,
            )
            return
        extra = f" Збережено в журнал з checkpoint: {imported}." if imported else ""
        update_mailing_job(
            job_id,
            status="error",
            message=f"Помилка відправки: {str(exc)[:180]}.{extra}",
        )


def _start_mailing_job_response(
    *,
    recipients: list[str],
    draft: MailingDraft,
    user: str,
    mode: str,
    trigger_type: str,
    campaign_id: int | None = None,
    is_template_flag: bool = False,
    template_name: str = "",
    defer_recipient_gather: bool = False,
    estimated_total: int = 0,
    custom_recipients: str = "",
    recipients_upload_path: str | None = None,
) -> JSONResponse:
    active = find_active_mailing_job()
    if active:
        return JSONResponse(
            {
                "ok": False,
                "error": (
                    "Зараз уже виконується розсилка. Дочекайтесь завершення "
                    "або відкрийте прогрес існуючого завдання."
                ),
                "job_id": active.id,
                "status": active.status,
                "subject": active.subject,
            },
            status_code=409,
        )
    job_total = max(0, int(estimated_total)) if defer_recipient_gather else len(recipients)
    job = create_mailing_job(
        total=job_total,
        subject=draft.subject,
        created_by=user,
    )
    asyncio.create_task(
        _run_mailing_job(
            job.id,
            recipients=recipients,
            draft=draft,
            user=user,
            mode=mode,
            trigger_type=trigger_type,
            campaign_id=campaign_id,
            is_template_flag=is_template_flag,
            template_name=template_name,
            defer_recipient_gather=defer_recipient_gather,
            custom_recipients=custom_recipients,
            recipients_upload_path=recipients_upload_path,
        )
    )
    return JSONResponse(
        {
            "ok": True,
            "job_id": job.id,
            "total": job.total,
            "subject": job.subject,
            "status": job.status,
        }
    )

@router.get("/clients/mailings", response_class=HTMLResponse)
async def clients_mailings_page(
    request: Request,
    user: str = Depends(get_current_user),
    _marketing_access: None = Depends(_require_marketing_access),
):
    from_clients_filters = str(request.query_params.get("from_clients") or "").strip() in {"1", "true", "yes"}
    hidden_clause = _clients_hidden_where()
    clauses: list = []

    if from_clients_filters:
        try:
            clauses, _ = _clients_filter_clauses_from_request(request)
        except Exception:
            clauses = []

    now = now_kyiv()
    week_start = (now - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)

    async with AsyncSessionLocalClients() as session:
        if from_clients_filters and clauses:
            stmt = _clients_apply_visibility_filters(client_emails_stmt(), clauses)
        else:
            stmt = client_emails_stmt(hidden_clause=hidden_clause)
        recipients = await fetch_client_emails(session, stmt)
        eligible_recipients_list, skipped_unsub_in_base = await filter_unsubscribed_emails(session, recipients)

        # Skip body/custom_recipients_raw — multi-MB HTML freezes iPad page parse.
        campaigns = list(
            (
                await session.execute(
                    select(EmailCampaign)
                    .options(
                        load_only(
                            EmailCampaign.id,
                            EmailCampaign.subject,
                            EmailCampaign.is_html,
                            EmailCampaign.target_mode,
                            EmailCampaign.is_template,
                            EmailCampaign.sent_total,
                            EmailCampaign.run_count,
                            EmailCampaign.last_sent_at,
                            EmailCampaign.created_at,
                        )
                    )
                    .order_by(desc(func.coalesce(EmailCampaign.last_sent_at, EmailCampaign.created_at)))
                    .limit(50)
                )
            ).scalars().all()
        )

        week_stats = await fetch_mailing_delivery_stats(
            session,
            date_from=week_start,
            date_to=now,
            page=1,
            per_page=1,
        )
        unsubscribed_total = await count_unsubscribes(session)
        recipients_total = len(recipients)
        eligible_recipients = len(eligible_recipients_list)

    # Metadata only — full HTML is lazy-loaded (keeps iPad Safari usable).
    file_templates = mailing_file_templates(include_html=False)
    campaigns_payload = [
        {
            "id": int(c.id),
            "subject": c.subject,
            "is_html": bool(c.is_html),
            "target_mode": c.target_mode,
        }
        for c in campaigns
    ]

    staged_recipients_key = ""
    staged_recipients_count = 0
    prefill_custom_recipients = ""
    if from_clients_filters and recipients:
        if len(recipients) > PREFILL_TEXTAREA_MAX:
            try:
                staged_recipients_key = save_recipients_text_list(recipients, user=user)
                staged_recipients_count = len(recipients)
            except ValueError:
                # Fallback: still prefill (heavier page) rather than lose the list.
                prefill_custom_recipients = "\n".join(recipients)
        else:
            prefill_custom_recipients = "\n".join(recipients)

    return templates.TemplateResponse(
        "clients/mailings.html",
        {
            "request": request,
            "clients_nav": "mailings",
            "recipients_total": recipients_total,
            "eligible_recipients": eligible_recipients,
            "skipped_unsub_in_base": skipped_unsub_in_base,
            "unsubscribed_total": unsubscribed_total,
            "campaigns": campaigns,
            "campaigns_payload": campaigns_payload,
            "mail_file_templates": file_templates,
            "prefill_target_mode": "custom" if from_clients_filters else "all",
            "prefill_custom_recipients": prefill_custom_recipients,
            "staged_recipients_key": staged_recipients_key,
            "staged_recipients_count": staged_recipients_count,
            "mailings_notice": request.query_params.get("notice"),
            "mailings_msg": request.query_params.get("msg"),
            "week_stats": week_stats,
        },
    )


@router.get("/clients/mailings/log", response_class=HTMLResponse)
async def clients_mailings_log_page(
    request: Request,
    user: str = Depends(get_current_user),
    _marketing_access: None = Depends(_require_marketing_access),
):
    stats_from_str = (request.query_params.get("stats_from") or "").strip()
    stats_to_str = (request.query_params.get("stats_to") or "").strip()
    stats_channel = (request.query_params.get("stats_channel") or "all").strip().lower()
    if stats_channel not in ("all", "api", "manual"):
        stats_channel = "all"
    stats_email_q = (request.query_params.get("stats_email") or "").strip()
    try:
        stats_page = max(1, int(request.query_params.get("stats_page") or "1"))
    except ValueError:
        stats_page = 1

    date_from, date_to = parse_mailing_stats_period(stats_from_str or None, stats_to_str or None)

    async with AsyncSessionLocalClients() as session:
        delivery_stats = await fetch_mailing_delivery_stats(
            session,
            date_from=date_from,
            date_to=date_to,
            channel=stats_channel,
            email_q=stats_email_q,
            page=stats_page,
        )

    total = int(delivery_stats.get("total_sent") or 0)
    ok = int(delivery_stats.get("total_ok") or 0)
    success_rate = round((ok / total) * 100, 1) if total else None

    return templates.TemplateResponse(
        "clients/mailings_log.html",
        {
            "request": request,
            "clients_nav": "mailings_log",
            "delivery_stats": delivery_stats,
            "stats_from_str": stats_from_str or date_from.strftime("%Y-%m-%d"),
            "stats_to_str": stats_to_str or date_to.strftime("%Y-%m-%d"),
            "stats_channel": stats_channel,
            "stats_email_q": stats_email_q,
            "success_rate": success_rate,
        },
    )


def _unsubscribe_query_params(request: Request, t: str = "", email: str = "") -> tuple[str, str]:
    token = str(t or request.query_params.get("t") or request.query_params.get("token") or "").strip()
    email_raw = str(email or request.query_params.get("email") or "").strip()
    return token, email_raw


def _resolve_unsubscribe_email(token: str, email_raw: str) -> str | None:
    email_from_query = normalize_email(email_raw)
    return parse_unsubscribe_token(token) or (email_from_query if is_valid_email(email_from_query) else None)


def _unsubscribe_client_meta(request: Request) -> tuple[str | None, str | None]:
    from web.security import _get_client_ip

    return (
        _get_client_ip(request),
        (request.headers.get("user-agent") or "")[:500] or None,
    )


@router.get("/index.php", response_class=HTMLResponse)
@router.post("/index.php")
async def public_index_php_compat(request: Request, route: str = Query(""), t: str = Query(""), email: str = Query("")):
    if not is_promo_unsubscribe_route(route):
        return Response(status_code=404, content="Not Found")
    if request.method.upper() == "POST":
        return await public_unsubscribe_one_click(request, t=t, email=email)
    return await public_unsubscribe_page(request, t=t, email=email)


@router.get("/unsubscribe", response_class=HTMLResponse)
async def public_unsubscribe_page(request: Request, t: str = Query(""), email: str = Query("")):
    token, email_raw = _unsubscribe_query_params(request, t, email)
    resolved_email = _resolve_unsubscribe_email(token, email_raw)

    if not resolved_email:
        return templates.TemplateResponse(
            "public/unsubscribe.html",
            {
                "request": request,
                "status": "invalid",
                "email": "",
            },
            status_code=400,
        )

    ip_address, user_agent = _unsubscribe_client_meta(request)
    async with AsyncSessionLocalClients() as session:
        row, created = await record_unsubscribe(
            session,
            resolved_email,
            source="link",
            ip_address=ip_address,
            user_agent=user_agent,
        )

    return templates.TemplateResponse(
        "public/unsubscribe.html",
        {
            "request": request,
            "status": "ok",
            "email": row.email,
            "created": created,
        },
    )


@router.post("/unsubscribe")
async def public_unsubscribe_one_click(request: Request, t: str = Query(""), email: str = Query("")):
    token, email_raw = _unsubscribe_query_params(request, t, email)
    resolved_email = _resolve_unsubscribe_email(token, email_raw)
    if not resolved_email:
        return Response(status_code=400, content="Invalid unsubscribe token")

    ip_address, user_agent = _unsubscribe_client_meta(request)
    async with AsyncSessionLocalClients() as session:
        await record_unsubscribe(
            session,
            resolved_email,
            source="one_click",
            ip_address=ip_address,
            user_agent=user_agent,
        )
    return Response(status_code=200, content="Unsubscribed")


@router.get("/clients/mailings/unsubscribes", response_class=HTMLResponse)
async def clients_mailings_unsubscribes_page(
    request: Request,
    user: str = Depends(get_current_user),
    _marketing_access: None = Depends(_require_marketing_access),
):
    q = (request.query_params.get("q") or "").strip()
    try:
        page = max(1, int(request.query_params.get("page") or "1"))
    except ValueError:
        page = 1

    async with AsyncSessionLocalClients() as session:
        data = await fetch_unsubscribes_page(session, q=q, page=page, per_page=50)

    return templates.TemplateResponse(
        "clients/mailings_unsubscribes.html",
        {
            "request": request,
            "clients_nav": "mailings_unsubscribes",
            "rows": data["rows"],
            "total_all": data["total_all"],
            "total_filtered": data["total_filtered"],
            "page": data["page"],
            "pages": data["pages"],
            "q": q,
            "source_labels": SOURCE_LABELS_UA,
            "notice": request.query_params.get("notice"),
            "msg": request.query_params.get("msg"),
        },
    )


@router.post("/clients/mailings/unsubscribes/add")
async def clients_mailings_unsubscribes_add(
    request: Request,
    user: str = Depends(get_current_user),
    _marketing_access: None = Depends(_require_marketing_access),
    email: str = Form(""),
    note: str = Form(""),
    _csrf: None = Depends(verify_csrf_dependency),
):
    from web.auth import get_operator_display

    normalized = normalize_email(email)
    if not is_valid_email(normalized):
        return _mailings_unsubscribes_redirect("error", "Вкажіть коректну email-адресу.")

    async with AsyncSessionLocalClients() as session:
        row, created = await record_unsubscribe(
            session,
            normalized,
            source="admin",
            note=(note or "").strip() or None,
            created_by=get_operator_display(request),
        )

    if created:
        msg = f"Адресу {row.email} додано до списку відписаних."
    else:
        msg = f"Адреса {row.email} уже в списку відписаних."
    return _mailings_unsubscribes_redirect("ok", msg)


@router.post("/clients/mailings/unsubscribes/{row_id:int}/remove")
async def clients_mailings_unsubscribes_remove(
    row_id: int,
    user: str = Depends(get_current_user),
    _marketing_access: None = Depends(_require_marketing_access),
    _csrf: None = Depends(verify_csrf_dependency),
):
    async with AsyncSessionLocalClients() as session:
        row = (
            await session.execute(select(EmailUnsubscribe).where(EmailUnsubscribe.id == int(row_id)).limit(1))
        ).scalar_one_or_none()
        if row is None:
            return _mailings_unsubscribes_redirect("error", "Запис не знайдено.")
        email = row.email
        await remove_unsubscribe_by_id(session, row_id)

    return _mailings_unsubscribes_redirect("ok", f"Адресу {email} видалено зі списку — розсилки знову дозволені.")


def _mailings_unsubscribes_redirect(notice: str, msg: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"/clients/mailings/unsubscribes?{urlencode({'notice': notice, 'msg': msg})}",
        status_code=302,
    )


@router.get("/clients/mailings/jobs")
async def clients_mailings_jobs_list(
    request: Request,
    _user: str = Depends(get_current_user),
    _marketing_access: None = Depends(_require_marketing_access),
):
    active = str(request.query_params.get("active") or "").strip() in {"1", "true", "yes"}
    # Do not re-scan checkpoint jsonl here — sender already bumps job JSON.
    return JSONResponse({"ok": True, "jobs": list_mailing_jobs(only_active=active)})


@router.get("/clients/mailings/jobs/{job_id}")
async def clients_mailings_job_status(
    job_id: str,
    request: Request,
    _user: str = Depends(get_current_user),
    _marketing_access: None = Depends(_require_marketing_access),
):
    # Default: return job JSON only (fast). Optional sync=1 re-reads checkpoint
    # off the event loop when progress looks stuck across workers.
    sync = str(request.query_params.get("sync") or "").strip() in {"1", "true", "yes"}
    if sync:
        await asyncio.to_thread(refresh_mailing_job_from_checkpoint, job_id, force=True)
    job = get_mailing_job(job_id, purge=False)
    if not job:
        raise HTTPException(status_code=404, detail="Завдання розсилки не знайдено.")
    return JSONResponse(job.to_dict())


@router.post("/clients/mailings/jobs/{job_id}/cancel")
async def clients_mailings_job_cancel(
    job_id: str,
    _user: str = Depends(get_current_user),
    _marketing_access: None = Depends(_require_marketing_access),
    _csrf: None = Depends(verify_csrf_header_dependency),
):
    """Stop mailing: flag cancel for the sender loop and finalize job for the UI."""
    job = request_mailing_job_cancel(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Завдання розсилки не знайдено.")
    if job.status in ("queued", "running"):
        # Finalize immediately so the panel closes; live sender also sees
        # status=cancelled via is_mailing_job_cancel_requested() and exits.
        job = finalize_mailing_job_cancelled(job_id, sent=job.sent, failed=job.failed) or job
    return JSONResponse({"ok": True, **job.to_dict()})


@router.get("/clients/mailings/campaigns/{campaign_id}")
async def clients_mailings_campaign_detail(
    campaign_id: int,
    _user: str = Depends(get_current_user),
    _marketing_access: None = Depends(_require_marketing_access),
):
    async with AsyncSessionLocalClients() as session:
        campaign = await session.get(EmailCampaign, int(campaign_id))
    if campaign is None:
        raise HTTPException(status_code=404, detail="Кампанію не знайдено.")
    return JSONResponse(
        {
            "ok": True,
            "id": int(campaign.id),
            "subject": campaign.subject or "",
            "body": campaign.body or "",
            "is_html": bool(campaign.is_html),
            "target_mode": campaign.target_mode or "all",
        }
    )


@router.get("/clients/mailings/templates/{template_name:path}")
async def clients_mailings_template_detail(
    template_name: str,
    _user: str = Depends(get_current_user),
    _marketing_access: None = Depends(_require_marketing_access),
):
    from web.mailing_helpers import mailing_template_by_name

    tpl = mailing_template_by_name(template_name)
    if tpl is None:
        raise HTTPException(status_code=404, detail="Шаблон не знайдено.")
    return JSONResponse(
        {
            "ok": True,
            "name": tpl.get("name") or "",
            "label": tpl.get("label") or "",
            "subject": tpl.get("subject") or "",
            "html": tpl.get("html") or "",
        }
    )


class MailingSendJsonPayload(BaseModel):
    subject: str = ""
    body: str = ""
    body_key: str = ""
    is_html: bool | None = None
    target_mode: str = "all"
    custom_recipients: str = ""
    recipients_key: str = ""
    estimated_recipients: int = 0
    save_as_template: bool = False
    template_name: str = ""
    file_template_name: str = ""


def _mailing_mode_from_target(target_mode: str) -> str:
    return "custom" if (target_mode or "").strip().lower() == "custom" else "all"


def _mailing_custom_recipients_for_mode(mode: str, raw: str) -> str:
    if mode != "custom":
        return ""
    return (raw or "").strip()


def _merge_custom_recipients_with_key(
    *,
    mode: str,
    custom_recipients: str,
    recipients_key: str,
    user: str,
) -> tuple[str, str | None]:
    """Combine textarea + staged filter list. Returns (text, error)."""
    if mode != "custom":
        return "", None
    parts: list[str] = []
    text = (custom_recipients or "").strip()
    if text:
        parts.append(text)
    key = (recipients_key or "").strip()
    if key:
        staged = load_recipients_text_list(key, user=user)
        if staged is None:
            return "", "Список отримувачів з фільтрів прострочений. Поверніться з клієнтів ще раз."
        staged = staged.strip()
        if staged:
            parts.append(staged)
    merged = "\n".join(parts).strip()
    return merged, None


def _resolved_mailing_body_text(
    *,
    body: str,
    body_key: str,
    user: str,
) -> tuple[str | None, str | None]:
    key = (body_key or "").strip()
    if key:
        text = load_staged_body(key, user=user)
        if not text or not str(text).strip():
            return None, "Тіло листа не знайдено. Оновіть сторінку (Ctrl+F5) і надішліть ще раз."
        return str(text), None
    text = (body or "").strip()
    if not text:
        return None, "Вкажіть тему та текст листа."
    return text, None


async def _clients_mailings_send_impl(
    request: Request,
    user: str,
    *,
    subject: str,
    body: str,
    is_html_raw: str | bool | None,
    target_mode: str,
    custom_recipients: str,
    recipients_file: UploadFile | None,
    save_as_template: str | bool | None,
    template_name: str,
    file_template_name: str,
    json_api: bool,
    estimated_recipients: int = 0,
):
    if isinstance(is_html_raw, bool):
        is_html_form = "on" if is_html_raw else None
    else:
        is_html_form = is_html_raw

    draft = prepare_mailing_draft(
        subject,
        body,
        file_template_name=file_template_name,
        is_html_raw=is_html_form,
    )
    if not draft:
        if json_api:
            return JSONResponse({"ok": False, "error": "Вкажіть тему та текст листа."}, status_code=400)
        return _mailings_redirect("error", "Вкажіть тему та текст листа.")

    mode = _mailing_mode_from_target(target_mode)
    custom_for_send = _mailing_custom_recipients_for_mode(mode, custom_recipients)
    hidden_clause = _clients_hidden_where()
    save_tpl = checkbox_on(save_as_template) if not isinstance(save_as_template, bool) else save_as_template

    if json_api:
        recipients_upload_path: str | None = None
        try:
            if mode == "custom" and recipients_file is not None:
                recipients_upload_path = await save_recipients_upload_from_file(recipients_file, user=user)
        except ValueError:
            return JSONResponse(
                {"ok": False, "error": "Файл отримувачів занадто великий або пошкоджений."},
                status_code=400,
            )
        if mode == "custom" and not custom_for_send and not recipients_upload_path:
            return JSONResponse(
                {"ok": False, "error": "Додайте email у поле або завантажте файл отримувачів."},
                status_code=400,
            )
        return _start_mailing_job_response(
            recipients=[],
            draft=draft,
            user=user,
            mode=mode,
            trigger_type="manual",
            is_template_flag=save_tpl,
            template_name=template_name,
            defer_recipient_gather=True,
            estimated_total=max(0, int(estimated_recipients)),
            custom_recipients=custom_for_send,
            recipients_upload_path=recipients_upload_path,
        )

    async with AsyncSessionLocalClients() as session:
        recipients, recipient_error, skipped_unsub = await gather_send_recipients(
            session,
            mode=mode,
            custom_recipients=custom_for_send,
            recipients_file=recipients_file,
            hidden_clause=hidden_clause,
        )
        if recipient_error:
            return _mailings_redirect("error", recipient_error)

        if not recipients:
            err = "Немає отримувачів для розсилки."
            if skipped_unsub:
                err = (
                    f"Усі отримувачі відписані від розсилок ({skipped_unsub}). "
                    "Перевірте список у «Відписані»."
                )
            return _mailings_redirect("error", err)

    result, error, _campaign_id = await _send_mailing_flow(
        recipients=recipients,
        draft=draft,
        user=user,
        mode=mode,
        trigger_type="manual",
        is_template_flag=save_tpl,
        template_name=template_name,
    )
    if error:
        return _mailings_redirect("error", error)

    return _mailings_redirect("ok", format_mailing_result_message(result))


@router.post("/clients/mailings/send")
async def clients_mailings_send(
    request: Request,
    user: str = Depends(get_current_user),
    _marketing_access: None = Depends(_require_marketing_access),
    subject: str = Form(""),
    body: str = Form(""),
    is_html: str | None = Form(None),
    target_mode: str = Form("all"),
    custom_recipients: str = Form(""),
    recipients_file: UploadFile | None = File(None),
    save_as_template: str | None = Form(None),
    template_name: str = Form(""),
    file_template_name: str = Form(""),
    _csrf: None = Depends(verify_csrf_dependency),
):
    json_api = _wants_json(request)
    return await _clients_mailings_send_impl(
        request,
        user,
        subject=subject,
        body=body,
        is_html_raw=is_html,
        target_mode=target_mode,
        custom_recipients=custom_recipients,
        recipients_file=recipients_file,
        save_as_template=save_as_template,
        template_name=template_name,
        file_template_name=file_template_name,
        json_api=json_api,
    )


@router.post("/clients/mailings/stage-body")
async def clients_mailings_stage_body(
    request: Request,
    user: str = Depends(get_current_user),
    _marketing_access: None = Depends(_require_marketing_access),
    _csrf: None = Depends(verify_csrf_header_dependency),
):
    """Зберегти HTML листа окремим запитом (без multipart разом із файлом отримувачів)."""
    raw = await request.body()
    if not raw:
        return JSONResponse({"ok": False, "error": "Порожнє тіло листа."}, status_code=400)
    try:
        key = save_staged_body(raw, user=user)
    except ValueError:
        return JSONResponse({"ok": False, "error": "Лист занадто великий для сервера."}, status_code=413)
    return JSONResponse({"ok": True, "body_key": key, "bytes": len(raw)})


@router.post("/clients/mailings/send-json")
async def clients_mailings_send_json(
    request: Request,
    payload: MailingSendJsonPayload,
    user: str = Depends(get_current_user),
    _marketing_access: None = Depends(_require_marketing_access),
    _csrf: None = Depends(verify_csrf_header_dependency),
):
    """Запуск розсилки з JSON (тіло листа — у полі body або через body_key після /stage-body)."""
    body_text, body_err = _resolved_mailing_body_text(
        body=payload.body,
        body_key=payload.body_key,
        user=user,
    )
    if body_err:
        return JSONResponse({"ok": False, "error": body_err}, status_code=400)
    mode = _mailing_mode_from_target(payload.target_mode)
    custom_merged, custom_err = _merge_custom_recipients_with_key(
        mode=mode,
        custom_recipients=payload.custom_recipients,
        recipients_key=payload.recipients_key,
        user=user,
    )
    if custom_err:
        return JSONResponse({"ok": False, "error": custom_err}, status_code=400)
    resp = await _clients_mailings_send_impl(
        request,
        user,
        subject=payload.subject,
        body=body_text or "",
        is_html_raw=payload.is_html,
        target_mode=mode,
        custom_recipients=custom_merged,
        recipients_file=None,
        save_as_template=payload.save_as_template,
        template_name=payload.template_name,
        file_template_name=payload.file_template_name,
        json_api=True,
        estimated_recipients=payload.estimated_recipients,
    )
    if isinstance(resp, JSONResponse) and resp.status_code < 400:
        if payload.body_key:
            delete_staged_body(payload.body_key, user=user)
        if payload.recipients_key:
            delete_recipients_text_list(payload.recipients_key, user=user)
    return resp


@router.post("/clients/mailings/send-json-with-file")
async def clients_mailings_send_json_with_file(
    request: Request,
    user: str = Depends(get_current_user),
    _marketing_access: None = Depends(_require_marketing_access),
    payload: str = Form(...),
    recipients_file: UploadFile = File(...),
    _csrf: None = Depends(verify_csrf_dependency),
):
    """Розсилка з файлом отримувачів: у multipart лише CSV/XLSX + короткий JSON (HTML уже на сервері)."""
    try:
        data = MailingSendJsonPayload.model_validate(json.loads(payload or "{}"))
    except Exception:
        return JSONResponse({"ok": False, "error": "Некоректні параметри запиту."}, status_code=400)
    body_text, body_err = _resolved_mailing_body_text(
        body=data.body,
        body_key=data.body_key,
        user=user,
    )
    if body_err:
        return JSONResponse({"ok": False, "error": body_err}, status_code=400)
    mode = _mailing_mode_from_target(data.target_mode)
    custom_merged, custom_err = _merge_custom_recipients_with_key(
        mode=mode,
        custom_recipients=data.custom_recipients,
        recipients_key=data.recipients_key,
        user=user,
    )
    if custom_err:
        return JSONResponse({"ok": False, "error": custom_err}, status_code=400)
    resp = await _clients_mailings_send_impl(
        request,
        user,
        subject=data.subject,
        body=body_text or "",
        is_html_raw=data.is_html,
        target_mode=mode,
        custom_recipients=custom_merged,
        recipients_file=recipients_file,
        save_as_template=data.save_as_template,
        template_name=data.template_name,
        file_template_name=data.file_template_name,
        json_api=True,
        estimated_recipients=data.estimated_recipients,
    )
    if isinstance(resp, JSONResponse) and resp.status_code < 400 and data.recipients_key:
        delete_recipients_text_list(data.recipients_key, user=user)
    if data.body_key and isinstance(resp, JSONResponse) and resp.status_code < 400:
        delete_staged_body(data.body_key, user=user)
    return resp


@router.post("/clients/mailings/{campaign_id:int}/resend")
async def clients_mailings_resend(
    request: Request,
    campaign_id: int,
    user: str = Depends(get_current_user),
    _marketing_access: None = Depends(_require_marketing_access),
    _csrf: None = Depends(verify_csrf_dependency),
):
    hidden_clause = _clients_hidden_where()

    async with AsyncSessionLocalClients() as session:
        campaign = (
            await session.execute(select(EmailCampaign).where(EmailCampaign.id == campaign_id))
        ).scalar_one_or_none()
        if not campaign:
            if _wants_json(request):
                return JSONResponse({"ok": False, "error": "Розсилку не знайдено."}, status_code=404)
            return _mailings_redirect("error", "Розсилку не знайдено.")

        recipients, skipped_unsub = await gather_campaign_recipients(session, campaign, hidden_clause)
        if not recipients:
            err = "Немає отримувачів для розсилки."
            if skipped_unsub:
                err = (
                    f"Усі отримувачі відписані від розсилок ({skipped_unsub}). "
                    "Перевірте список у «Відписані»."
                )
            if _wants_json(request):
                return JSONResponse({"ok": False, "error": err}, status_code=400)
            return _mailings_redirect("error", err)
        draft = MailingDraft(
            subject=campaign.subject,
            body=campaign.body,
            body_to_send=campaign_body_to_send(campaign),
            is_html=bool(campaign.is_html),
        )

        resend_campaign_id = int(campaign.id)
        resend_mode = campaign.target_mode

        if _wants_json(request):
            if not recipients:
                return JSONResponse(
                    {"ok": False, "error": "Немає отримувачів для розсилки."},
                    status_code=400,
                )
            return _start_mailing_job_response(
                recipients=recipients,
                draft=draft,
                user=user,
                mode=resend_mode,
                trigger_type="manual_resend",
                campaign_id=resend_campaign_id,
            )

    result, error, _saved_id = await _send_mailing_flow(
        recipients=recipients,
        draft=draft,
        user=user,
        mode=resend_mode,
        trigger_type="manual_resend",
        campaign_id=resend_campaign_id,
    )
    if error:
        return _mailings_redirect("error", error)

    return _mailings_redirect("ok", format_mailing_result_message(result, resend=True))


@router.post("/clients/mailings/count-file-emails")
async def clients_mailings_count_file_emails(
    request: Request,
    user: str = Depends(get_current_user),
    _marketing_access: None = Depends(_require_marketing_access),
    _csrf: None = Depends(verify_csrf_dependency),
    recipients_file: UploadFile | None = File(None),
    custom_recipients: str = Form(""),
):
    """Підрахунок унікальних email у файлі (+ опційно з текстового поля) для UI."""
    from web.mailing_helpers import emails_from_text, emails_from_uploaded_file

    file_list, file_error = await emails_from_uploaded_file(recipients_file)
    if file_error:
        return JSONResponse(
            {"ok": False, "error": file_error, "count": 0, "from_file": 0, "from_text": 0},
            status_code=400,
        )
    text_list = emails_from_text(custom_recipients)
    merged = sorted({*file_list, *text_list})
    return JSONResponse(
        {
            "ok": True,
            "count": len(merged),
            "from_file": len(file_list),
            "from_text": len(text_list),
            "filename": (recipients_file.filename if recipients_file else None),
        }
    )


@router.post("/clients/mailings/save-template")
async def clients_mailings_save_template(
    request: Request,
    user: str = Depends(get_current_user),
    _marketing_access: None = Depends(_require_marketing_access),
    _csrf: None = Depends(verify_csrf_dependency),
):
    try:
        data = await request.json()
        template_name = (data.get("name") or "").strip()
        template_subject = (data.get("subject") or "Новий шаблон").strip()
        template_html = (data.get("html") or "").strip()

        if not template_name or not template_html:
            return JSONResponse(
                {"success": False, "error": "Назва та вміст шаблону обов'язкові."},
                status_code=400,
            )

        path = save_template_html_file(template_name, template_subject, template_html)
        return JSONResponse({"success": True, "name": path.name})
    except FileExistsError as exc:
        return JSONResponse(
            {"success": False, "error": f"Шаблон '{exc.args[0]}' вже існує."},
            status_code=400,
        )
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@router.post("/clients/mailings/upload-image")
async def clients_mailings_upload_image(
    request: Request,
    user: str = Depends(get_current_user),
    _marketing_access: None = Depends(_require_marketing_access),
    image_file: UploadFile | None = File(None),
    _csrf: None = Depends(verify_csrf_dependency),
):
    if not image_file or not image_file.filename:
        return JSONResponse({"success": False, "error": "Не обрано файл зображення."}, status_code=400)

    ext = safe_image_ext(image_file.filename, image_file.content_type)
    if not ext:
        return JSONResponse(
            {"success": False, "error": "Підтримуються лише зображення: JPG, PNG, WEBP, GIF, SVG."},
            status_code=400,
        )

    data = await image_file.read()
    if not data:
        return JSONResponse({"success": False, "error": "Файл порожній."}, status_code=400)
    if len(data) > 8 * 1024 * 1024:
        return JSONResponse({"success": False, "error": "Максимальний розмір файлу — 8MB."}, status_code=400)

    static_dir = BASE_DIR / "static" / "mailings"
    static_dir.mkdir(parents=True, exist_ok=True)
    filename = new_mailing_image_filename(ext)
    (static_dir / filename).write_bytes(data)

    rel_url, abs_url = mailing_image_urls(filename)
    return JSONResponse({"success": True, "url": rel_url, "absolute_url": abs_url, "name": filename})


@router.post("/api/mailings/welcome")
async def api_mailings_welcome(request: Request):
    payload, email, err = await parse_api_mailing_request(request)
    if err:
        return JSONResponse(err[1], status_code=err[0])

    name = str(payload.get("name") or payload.get("first_name") or "").strip()
    subject, html_body = render_welcome_email(name or None)
    status, body_obj = await send_trigger_email(
        email,
        subject,
        html_body,
        str(payload.get("subject") or ""),
        trigger_type="welcome",
    )
    if status == 200:
        body_obj["name"] = name or None
    return JSONResponse(body_obj, status_code=status)


@router.post("/api/mailings/order")
async def api_mailings_order(request: Request):
    payload, email, err = await parse_api_mailing_request(request)
    if err:
        return JSONResponse(err[1], status_code=err[0])

    products, prod_err, product_ids = await resolve_api_products(
        payload, load_by_ids=load_products_for_email, price_drop=False
    )
    if prod_err:
        return JSONResponse({"success": False, "error": prod_err}, status_code=400)

    order_ctx = parse_order_context(payload)
    subject, html_body = render_order_placed_email(recipient_name(payload) or None, products, order_ctx)
    status, body_obj = await send_trigger_email(
        email,
        subject,
        html_body,
        str(payload.get("subject") or ""),
        trigger_type="order",
        product_ids=product_ids,
    )
    if status == 200:
        body_obj.update(
            {
                "order_number": order_ctx.get("order_number"),
                "payment_method": order_ctx.get("payment_method"),
                "order_total": order_ctx.get("order_total"),
                "discount": order_ctx.get("discount"),
                "products_count": len(products),
            }
        )
    return JSONResponse(body_obj, status_code=status)


@router.post("/api/mailings/abandoned-cart")
async def api_mailings_abandoned_cart(request: Request):
    payload, email, err = await parse_api_mailing_request(request)
    if err:
        return JSONResponse(err[1], status_code=err[0])

    products, prod_err, product_ids = await resolve_api_products(
        payload, load_by_ids=load_products_for_email, price_drop=False
    )
    if prod_err:
        return JSONResponse({"success": False, "error": prod_err}, status_code=400)

    cart_url = tracking_url(payload, "cart_url", "checkout_url") or CART_URL_DEFAULT
    subject, html_body = render_abandoned_cart_email(recipient_name(payload) or None, products, cart_url)
    status, body_obj = await send_trigger_email(
        email,
        subject,
        html_body,
        str(payload.get("subject") or ""),
        trigger_type="abandoned_cart",
        product_ids=product_ids,
    )
    if status == 200:
        body_obj["products_count"] = len(products)
    return JSONResponse(body_obj, status_code=status)


async def _api_mailings_price_drop(request: Request, *, scenario: str) -> JSONResponse:
    payload, email, err = await parse_api_mailing_request(request)
    if err:
        return JSONResponse(err[1], status_code=err[0])

    products, prod_err, product_ids = await resolve_api_products(
        payload, load_by_ids=load_products_for_email, price_drop=True
    )
    if prod_err:
        return JSONResponse({"success": False, "error": prod_err}, status_code=400)

    name = recipient_name(payload) or None
    if scenario == "wishlist":
        link = tracking_url(payload, "wishlist_url", "list_url")
        subject, html_body = render_price_drop_wishlist_email(name, products, link)
    else:
        link = tracking_url(payload, "cart_url", "checkout_url") or CART_URL_DEFAULT
        subject, html_body = render_price_drop_cart_email(name, products, link)

    status, body_obj = await send_trigger_email(
        email,
        subject,
        html_body,
        str(payload.get("subject") or ""),
        trigger_type=f"price_drop_{scenario}",
        product_ids=product_ids,
    )
    if status == 200:
        body_obj["scenario"] = scenario
        body_obj["products_count"] = len(products)
    return JSONResponse(body_obj, status_code=status)


@router.post("/api/mailings/price-drop-wishlist")
async def api_mailings_price_drop_wishlist(request: Request):
    return await _api_mailings_price_drop(request, scenario="wishlist")


@router.post("/api/mailings/price-drop-cart")
async def api_mailings_price_drop_cart(request: Request):
    return await _api_mailings_price_drop(request, scenario="cart")


@router.post("/api/mailings/abandoned-payment")
async def api_mailings_abandoned_payment(request: Request):
    payload, email, err = await parse_api_mailing_request(request)
    if err:
        return JSONResponse(err[1], status_code=err[0])

    products: list[dict] = []
    product_ids: list[int] = []
    raw_items = payload.get("items") or payload.get("products")
    if isinstance(raw_items, list) and raw_items:
        resolved, prod_err, product_ids = await resolve_api_products(
            payload, load_by_ids=load_products_for_email, price_drop=False
        )
        if prod_err:
            return JSONResponse({"success": False, "error": prod_err}, status_code=400)
        products = resolved or []

    checkout_url = tracking_url(payload, "checkout_url", "cart_url", "payment_url") or CHECKOUT_URL_DEFAULT
    subject, html_body = render_abandoned_payment_email(
        recipient_name(payload) or None,
        checkout_url,
        products,
    )
    status, body_obj = await send_trigger_email(
        email,
        subject,
        html_body,
        str(payload.get("subject") or ""),
        trigger_type="abandoned_payment",
        product_ids=product_ids or None,
    )
    if status == 200:
        body_obj["name"] = recipient_name(payload) or None
        body_obj["products_count"] = len(products)
    return JSONResponse(body_obj, status_code=status)


@router.post("/api/mailings/promo-signup")
async def api_mailings_promo_signup(request: Request):
    payload, email, err = await parse_api_mailing_request(request)
    if err:
        return JSONResponse(err[1], status_code=err[0])

    lang = normalize_promo_signup_lang(
        str(payload.get("lang") or ""),
        str(payload.get("language") or ""),
    )

    code = str(payload.get("code") or "").strip()
    if not code:
        return JSONResponse({"success": False, "error": "Вкажіть promo code (code)."}, status_code=400)

    store_url = str(payload.get("store_url") or "").strip()
    if not store_url:
        return JSONResponse({"success": False, "error": "Вкажіть store_url."}, status_code=400)

    unsubscribe_url = unsubscribe_url_for_email(email)

    try:
        discount_percent = int(payload.get("discount_percent") or 10)
    except (TypeError, ValueError):
        return JSONResponse(
            {"success": False, "error": "discount_percent має бути цілим числом."},
            status_code=400,
        )

    try:
        valid_hours = int(payload.get("valid_hours") or 24)
    except (TypeError, ValueError):
        return JSONResponse(
            {"success": False, "error": "valid_hours має бути цілим числом."},
            status_code=400,
        )

    try:
        min_total = float(payload.get("min_total") or 0)
    except (TypeError, ValueError):
        return JSONResponse(
            {"success": False, "error": "min_total має бути числом."},
            status_code=400,
        )

    expires_at = str(payload.get("expires_at") or "").strip()
    expires_at_tz = str(payload.get("expires_at_tz") or "Europe/Kyiv").strip() or "Europe/Kyiv"
    external_request_id = str(payload.get("external_request_id") or "").strip()

    subject, html_body = render_promo_signup_email(
        code=code,
        discount_percent=discount_percent,
        valid_hours=valid_hours,
        expires_at=expires_at,
        expires_at_tz=expires_at_tz,
        min_total=min_total,
        store_url=store_url,
        unsubscribe_url=unsubscribe_url,
    )
    status, body_obj = await send_promo_signup_email(
        email,
        subject,
        html_body,
        str(payload.get("subject") or ""),
        external_request_id=external_request_id or None,
        unsubscribe_url=unsubscribe_url,
    )
    if status == 200:
        body_obj["code"] = code
        body_obj["lang"] = lang
    return JSONResponse(body_obj, status_code=status)
