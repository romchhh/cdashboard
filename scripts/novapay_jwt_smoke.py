#!/usr/bin/env python3
"""Перевірка NovaPay JWT: credentials з БД + опційно live UserAuthenticationJWT."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")


async def _load_runtime() -> None:
    from bot.database.engine import AsyncSessionLocal, init_db

    await init_db()
    from bot.services.fop_config import refresh_fop_runtime

    async with AsyncSessionLocal() as session:
        await refresh_fop_runtime(session)


def _report() -> int:
    import config
    from novapay.auth import credentials_for_fop, peek_session_status, renew_jwt_for_fop
    from novapay.config import get_session_file

    names = sorted(config.NOVAPAY_TOKENS.keys(), key=str.casefold)
    if not names:
        print("NOVAPAY_TOKENS порожні (немає активних ФОП з NovaPay у БД).")
        return 1

    print(f"NovaPay ФОП у runtime: {len(names)}")
    for name in names:
        creds = credentials_for_fop(name)
        path = get_session_file(name)
        st = peek_session_status(session_file=path)
        cfg = config.NOVAPAY_TOKENS.get(name) or {}
        has_login = bool((cfg.get("login") or "").strip())
        has_cert = bool((cfg.get("certificate") or "").strip())
        has_refresh = creds is not None
        jwt_ready = has_refresh and has_login and has_cert
        print(f"\n— {name}")
        print(f"  JWT ready (refresh+login+cert): {jwt_ready}")
        print(f"  refresh: {has_refresh}, login: {has_login}, certificate: {has_cert}")
        print(f"  сесія: {st.get('message')} ({path})")
    return 0


def _live(fop: str) -> int:
    from novapay.auth import credentials_for_fop, renew_jwt_for_fop
    from novapay.config import get_session_file

    name = (fop or "").strip()
    if not credentials_for_fop(name):
        print(f"❌ Для «{name}» немає повного набору refresh + login + certificate.")
        return 1
    print(f"→ UserAuthenticationJWT для «{name}» (refresh буде ротовано)…")
    done = renew_jwt_for_fop(name, session_file=get_session_file(name))
    if not done.get("ok"):
        print(f"❌ {done.get('error')}")
        return 1
    print(f"✅ JWT OK, діє до {done.get('expiration')}")
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", metavar="FOP", help="Реальний виклик UserAuthenticationJWT")
    args = parser.parse_args()

    await _load_runtime()
    code = _report()
    if args.live:
        if os.environ.get("NOVAPAY_JWT_LIVE") != "1":
            print(
                "\nLive-виклик пропущено. Для реального JWT додайте NOVAPAY_JWT_LIVE=1 "
                "і знову: scripts/novapay_jwt_smoke.py --live «імʼя ФОП»"
            )
            return code
        return _live(args.live)
    return code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
