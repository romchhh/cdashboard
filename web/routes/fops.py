"""Адмін-панель: керування ФОП (Mono / NovaPay / сесії)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from bot.database.engine import AsyncSessionLocal
from web.auth import require_admin
from web.deps import templates
from web.fop_admin import (
    add_mono_fop,
    add_novapay_fop,
    build_fop_rows,
    build_archived_fop_rows,
    delete_fop,
    finish_novapay_session,
    list_mono_accounts,
    probe_fop_recent_transactions,
    refresh_novapay_session,
    restore_fop,
    session_status_for_name,
    start_novapay_session,
    update_novapay_fop,
)
from web.security import verify_csrf_dependency

router = APIRouter(tags=["fops"])


def _flash_url(msg: str, *, kind: str = "ok") -> str:
    from urllib.parse import quote

    return f"/fops?msg={quote(msg)}&kind={quote(kind)}"


@router.get("/fops", response_class=HTMLResponse)
async def fops_page(request: Request, user: str = Depends(require_admin)):
    async with AsyncSessionLocal() as session:
        rows = await build_fop_rows(session)
        archived_rows = await build_archived_fop_rows(session)
    msg = request.query_params.get("msg")
    kind = request.query_params.get("kind") or "ok"
    return templates.TemplateResponse(
        "fops.html",
        {
            "request": request,
            "rows": rows,
            "archived_rows": archived_rows,
            "flash_msg": msg,
            "flash_kind": kind,
            "session_fop": request.query_params.get("session_fop") or "",
            "challenge": request.query_params.get("challenge") or "",
            "session_step": request.query_params.get("step") or "",
        },
    )


@router.post("/fops/mono/accounts")
async def fops_mono_accounts(
    request: Request,
    user: str = Depends(require_admin),
    _csrf: None = Depends(verify_csrf_dependency),
    token: str = Form(...),
):
    result = await list_mono_accounts(token)
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@router.post("/fops/mono/add")
async def fops_mono_add(
    request: Request,
    user: str = Depends(require_admin),
    _csrf: None = Depends(verify_csrf_dependency),
    name: str = Form(...),
    token: str = Form(...),
    iban: str = Form(""),
):
    try:
        async with AsyncSessionLocal() as session:
            await add_mono_fop(session, name=name, token=token, iban=iban or None)
        return RedirectResponse(
            url=_flash_url(f"ФОП «{name.strip()}» додано (Monobank)"),
            status_code=302,
        )
    except Exception as e:
        return RedirectResponse(url=_flash_url(str(e), kind="error"), status_code=302)


@router.post("/fops/novapay/add")
async def fops_novapay_add(
    request: Request,
    user: str = Depends(require_admin),
    _csrf: None = Depends(verify_csrf_dependency),
    name: str = Form(...),
    refresh: str = Form(""),
    cert: str = Form(""),
    login: str = Form(""),
    password: str = Form(""),
):
    name = (name or "").strip()
    try:
        async with AsyncSessionLocal() as session:
            await add_novapay_fop(
                session,
                name=name,
                refresh=refresh or None,
                cert=cert or None,
                login=login or None,
            )
    except Exception as e:
        return RedirectResponse(url=_flash_url(str(e), kind="error"), status_code=302)

    if (refresh or "").strip() and (login or "").strip() and (cert or "").strip():
        started = start_novapay_session(name, login)
        if not started.get("ok"):
            return RedirectResponse(
                url=_flash_url(
                    f"ФОП додано, але JWT-сесія не створена: {started.get('error')}",
                    kind="error",
                ),
                status_code=302,
            )
        return RedirectResponse(
            url=_flash_url(
                f"ФОП «{name}» додано. JWT-сесія: {started.get('message')}"
            ),
            status_code=302,
        )

    return RedirectResponse(
        url=_flash_url(
            f"ФОП «{name}» додано (NovaPay). Вкажіть refresh + login + certificate і створіть JWT-сесію."
        ),
        status_code=302,
    )


@router.post("/fops/novapay/credentials/update")
async def fops_novapay_credentials_update(
    request: Request,
    user: str = Depends(require_admin),
    _csrf: None = Depends(verify_csrf_dependency),
    fop_name: str = Form(...),
    refresh: str = Form(...),
    cert: str = Form(""),
    login: str = Form(""),
    password: str = Form(""),
):
    """Оновити Refresh Token NovaPay і за потреби одразу розпочати створення сесії."""
    wants_json = "application/json" in (request.headers.get("accept") or "")
    name = (fop_name or "").strip()
    try:
        async with AsyncSessionLocal() as session:
            result = await update_novapay_fop(
                session,
                fop_name=name,
                refresh=refresh,
                cert=cert or None,
                login=login or None,
                reset_session=True,
            )
    except Exception as e:
        if wants_json:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        return RedirectResponse(url=_flash_url(str(e), kind="error"), status_code=302)

    try_jwt = bool((login or "").strip()) or bool((cert or "").strip())
    if try_jwt:
        started = start_novapay_session(name, login or None)
        if wants_json:
            if not started.get("ok"):
                return JSONResponse(
                    {
                        "ok": True,
                        "message": result["message"],
                        "session_start_error": started.get("error"),
                    },
                    status_code=200,
                )
            return JSONResponse(
                {
                    "ok": True,
                    "message": f"{result['message']} JWT: {started.get('message')}",
                    "fop_name": name,
                }
            )
        if not started.get("ok"):
            return RedirectResponse(
                url=_flash_url(
                    f"{result['message']} JWT не створено: {started.get('error')}",
                    kind="error",
                ),
                status_code=302,
            )
        return RedirectResponse(
            url=_flash_url(f"{result['message']} JWT-сесія: {started.get('message')}"),
            status_code=302,
        )

    if wants_json:
        return JSONResponse({"ok": True, **result})
    return RedirectResponse(url=_flash_url(result["message"]), status_code=302)


@router.post("/fops/{fop_id:int}/delete")
async def fops_delete(
    fop_id: int,
    request: Request,
    user: str = Depends(require_admin),
    _csrf: None = Depends(verify_csrf_dependency),
):
    try:
        async with AsyncSessionLocal() as session:
            result = await delete_fop(session, fop_id)
        name = result["name"]
        kept = result["transactions_kept"]
        return RedirectResponse(
            url=_flash_url(
                f"ФОП «{name}» перенесено в архів. Транзакції збережено ({kept}) — статистика не змінилась."
            )
            + "&archive=1",
            status_code=302,
        )
    except Exception as e:
        return RedirectResponse(url=_flash_url(str(e), kind="error"), status_code=302)


@router.post("/fops/{fop_id:int}/restore")
async def fops_restore(
    fop_id: int,
    request: Request,
    user: str = Depends(require_admin),
    _csrf: None = Depends(verify_csrf_dependency),
):
    try:
        async with AsyncSessionLocal() as session:
            result = await restore_fop(session, fop_id)
        name = result["name"]
        kept = result["transactions_kept"]
        return RedirectResponse(
            url=_flash_url(
                f"ФОП «{name}» повернуто з архіву ({kept} транз.). "
                "Щоб знову синхронізувати банк — підключіть Mono або NovaPay через «Додати ФОП»."
            ),
            status_code=302,
        )
    except Exception as e:
        return RedirectResponse(url=_flash_url(str(e), kind="error"), status_code=302)


@router.post("/fops/novapay/session/start")
async def fops_session_start(
    request: Request,
    user: str = Depends(require_admin),
    _csrf: None = Depends(verify_csrf_dependency),
    fop_name: str = Form(...),
    login: str = Form(""),
    password: str = Form(""),
):
    wants_json = "application/json" in (request.headers.get("accept") or "")
    started = start_novapay_session(fop_name, login or None, password or None)
    if wants_json:
        return JSONResponse(started, status_code=200 if started.get("ok") else 400)
    if not started.get("ok"):
        return RedirectResponse(
            url=_flash_url(started.get("error") or "Помилка", kind="error"),
            status_code=302,
        )
    return RedirectResponse(
        url=_flash_url(
            f"JWT-сесія NovaPay для «{started.get('fop_name')}»: {started.get('message')}"
        ),
        status_code=302,
    )


@router.post("/fops/novapay/session/otp")
async def fops_session_otp(
    request: Request,
    user: str = Depends(require_admin),
    _csrf: None = Depends(verify_csrf_dependency),
    challenge: str = Form(...),
    otp: str = Form(...),
):
    wants_json = "application/json" in (request.headers.get("accept") or "")
    done = finish_novapay_session(challenge, otp)
    if wants_json:
        return JSONResponse(done, status_code=200 if done.get("ok") else 400)
    if not done.get("ok"):
        return RedirectResponse(
            url=_flash_url(done.get("error") or "Помилка OTP", kind="error"),
            status_code=302,
        )
    return RedirectResponse(
        url=_flash_url(
            f"Сесію NovaPay для «{done.get('fop_name')}» створено ({done.get('message')})"
        ),
        status_code=302,
    )


@router.post("/fops/novapay/session/refresh")
async def fops_session_refresh(
    request: Request,
    user: str = Depends(require_admin),
    _csrf: None = Depends(verify_csrf_dependency),
    fop_name: str = Form(...),
):
    wants_json = "application/json" in (request.headers.get("accept") or "")
    result = refresh_novapay_session(fop_name)
    if wants_json:
        return JSONResponse(result, status_code=200 if result.get("ok") else 400)
    if not result.get("ok"):
        return RedirectResponse(
            url=_flash_url(result.get("error") or "Refresh не вдався", kind="error"),
            status_code=302,
        )
    return RedirectResponse(
        url=_flash_url(f"Сесію продовжено: {result.get('message')}"),
        status_code=302,
    )


@router.get("/fops/transactions/preview")
async def fops_transactions_preview(
    request: Request,
    user: str = Depends(require_admin),
    fop_name: str = "",
    limit: int = 5,
):
    name = (fop_name or request.query_params.get("fop_name") or "").strip()
    if not name:
        return JSONResponse({"ok": False, "error": "Не вказано fop_name"}, status_code=400)
    lim = max(1, min(int(limit or 5), 15))
    return JSONResponse(probe_fop_recent_transactions(name, limit=lim))


@router.get("/fops/novapay/session/status")
async def fops_session_status(
    request: Request,
    user: str = Depends(require_admin),
    fop_name: str = "",
):
    name = (fop_name or request.query_params.get("fop_name") or "").strip()
    if not name:
        return JSONResponse({"ok": False, "error": "Не вказано fop_name"}, status_code=400)
    return JSONResponse(session_status_for_name(name))
