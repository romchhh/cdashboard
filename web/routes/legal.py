"""Публічні юридичні сторінки для Google OAuth consent screen (без авторизації)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from config import LEGAL_APP_NAME, LEGAL_COMPANY_NAME, LEGAL_CONTACT_EMAIL, WEB_PUBLIC_BASE_URL
from web.deps import templates

router = APIRouter(tags=["legal"])


def _public_base_url(request: Request) -> str:
    if WEB_PUBLIC_BASE_URL:
        return WEB_PUBLIC_BASE_URL
    return str(request.base_url).rstrip("/")


def _legal_context(request: Request) -> dict:
    base = _public_base_url(request)
    return {
        "request": request,
        "app_name": LEGAL_APP_NAME,
        "company_name": LEGAL_COMPANY_NAME,
        "contact_email": LEGAL_CONTACT_EMAIL,
        "public_base_url": base,
        "home_url": f"{base}/legal",
        "privacy_url": f"{base}/privacy",
        "terms_url": f"{base}/terms",
    }


@router.get("/legal", response_class=HTMLResponse)
async def legal_home(request: Request):
    """Application home page для OAuth consent screen."""
    return templates.TemplateResponse(
        "legal/home.html",
        _legal_context(request),
    )


@router.get("/privacy", response_class=HTMLResponse)
async def privacy_policy(request: Request):
    """Політика конфіденційності (публічна)."""
    return templates.TemplateResponse(
        "legal/privacy.html",
        _legal_context(request),
    )


@router.get("/terms", response_class=HTMLResponse)
async def terms_of_service(request: Request):
    """Умови використання (публічні)."""
    return templates.TemplateResponse(
        "legal/terms.html",
        _legal_context(request),
    )
