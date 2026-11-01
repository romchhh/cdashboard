"""Збереження ротованих refresh/certificate NovaPay у БД і runtime-кеш."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from bot.database.engine import get_sync_engine
from bot.database.models import FOP
from config import NOVAPAY_TOKENS, SESSIONS_DIR, ensure_session_suffix

logger = logging.getLogger(__name__)

# Старий шлях: усі кириличні імена згортались у «_» → один спільний refresh на всіх ФОП.
_LEGACY_SHARED_STASH = Path(SESSIONS_DIR) / "_.refresh.json"


def _stash_path(fop_name: str) -> Path:
    """Окремий файл на ФОП (той самий suffix, що й у novapay_session_*.json)."""
    suffix = ensure_session_suffix(fop_name)
    return Path(SESSIONS_DIR) / f"novapay_refresh_{suffix}.json"


def purge_legacy_shared_stash() -> bool:
    """Прибрати спільний _.refresh.json (кириличні імена раніше згортались у один файл)."""
    path = _LEGACY_SHARED_STASH
    if not path.is_file():
        return False
    try:
        path.unlink()
        logger.warning(
            "Видалено sessions/_.refresh.json — він був спільним для всіх кириличних ФОП "
            "і міг підміняти їхні refresh/login."
        )
        return True
    except OSError:
        logger.exception("Не вдалося видалити %s", path)
        return False


def load_rotated_stash(fop_name: str) -> dict | None:
    purge_legacy_shared_stash()
    name = (fop_name or "").strip()
    path = _stash_path(name)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    refresh = (data.get("refresh_token") or "").strip()
    if not refresh or refresh == "session-managed":
        return None
    # stash від іншого ФОП (старі файли / ручне копіювання) — ігноруємо
    stored_name = (data.get("fop_name") or "").strip()
    if stored_name and stored_name != name:
        logger.warning(
            "Ігнорую stash %s: fop_name=%r, очікувалось %r",
            path.name,
            stored_name,
            name,
        )
        return None
    return data


def stash_rotated_tokens(
    fop_name: str,
    refresh_token: str,
    public_certificate: str,
    *,
    login: str | None = None,
) -> None:
    """Записати новий refresh одразу, ще до БД. Інакше одноразовий токен губиться, якщо commit не встиг."""
    purge_legacy_shared_stash()
    name = (fop_name or "").strip()
    refresh = (refresh_token or "").strip()
    cert = (public_certificate or "").strip()
    login_val = (login or "").strip()
    if not name or not refresh or not cert:
        return
    payload = {
        "fop_name": name,
        "refresh_token": refresh,
        "public_certificate": cert,
        "login": login_val,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }
    path = _stash_path(name)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        return
    cfg = NOVAPAY_TOKENS.get(name)
    if cfg is not None:
        cfg["refresh_token"] = refresh
        cfg["certificate"] = cert
        if login_val:
            cfg["login"] = login_val


def import_rotated_stash_into_db() -> int:
    """Записати refresh зі sessions/novapay_refresh_*.json у ФОП.

    Ротація ховає новий одноразовий refresh у файл до commit у БД. Якщо колонок
    ще не було або запис у базу впав, файл лишається єдиною копією — без цього
    синк не бачить ФОП і не може оновити JWT.
    """
    purge_legacy_shared_stash()
    root = Path(SESSIONS_DIR)
    if not root.is_dir():
        return 0
    updated = 0
    with Session(get_sync_engine()) as session:
        for path in sorted(root.glob("novapay_refresh_*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            name = (data.get("fop_name") or "").strip()
            refresh = (data.get("refresh_token") or "").strip()
            cert = (data.get("public_certificate") or "").strip()
            login_val = (data.get("login") or "").strip()
            if not name or not refresh or refresh == "session-managed" or not cert:
                continue
            suffix = path.name.removeprefix("novapay_refresh_").removesuffix(".json")
            fop = session.execute(select(FOP).where(FOP.name == name)).scalar_one_or_none()
            if fop is None:
                fop = FOP(name=name, is_active=True)
                session.add(fop)
            fop.novapay_refresh_token = refresh
            fop.novapay_certificate = cert
            if login_val:
                fop.novapay_login = login_val
            if suffix:
                fop.novapay_session_suffix = suffix
            cfg = NOVAPAY_TOKENS.get(name)
            if cfg is not None:
                cfg["refresh_token"] = refresh
                cfg["certificate"] = cert
                if login_val:
                    cfg["login"] = login_val
            updated += 1
        if updated:
            session.commit()
    if updated:
        logger.info("NovaPay: у БД перенесено refresh для %s ФОП із sessions/", updated)
    return updated


def clear_rotated_stash(fop_name: str) -> None:
    path = _stash_path(fop_name)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return
    # на випадок, якщо ще лишився старий спільний файл
    purge_legacy_shared_stash()


def persist_novapay_tokens_sync(
    fop_name: str,
    refresh_token: str,
    public_certificate: str,
    *,
    login: str | None = None,
) -> None:
    """Оновити токени після UserAuthenticationJWT (sync, без async greenlet)."""
    name = (fop_name or "").strip()
    refresh = (refresh_token or "").strip()
    cert = (public_certificate or "").strip()
    login_val = (login or "").strip()
    if not name or not refresh or not cert:
        return

    with Session(get_sync_engine()) as session:
        fop = session.execute(select(FOP).where(FOP.name == name)).scalar_one_or_none()
        if fop is None:
            return
        fop.novapay_refresh_token = refresh
        fop.novapay_certificate = cert
        if login_val:
            fop.novapay_login = login_val
        session.commit()

    cfg = NOVAPAY_TOKENS.get(name)
    if cfg is not None:
        cfg["refresh_token"] = refresh
        cfg["certificate"] = cert
        if login_val:
            cfg["login"] = login_val
