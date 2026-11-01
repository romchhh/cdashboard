"""Поля авторизації для запитів Client API (JWT)."""

from __future__ import annotations


def api_auth_kwargs(access_token: str) -> dict:
    """JWT — рекомендований спосіб; principal лишаємо порожнім."""
    token = (access_token or "").strip()
    return {"principal": "", "jwt": token}
