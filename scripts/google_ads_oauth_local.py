#!/usr/bin/env python3
"""Отримати GOOGLE_ADS_REFRESH_TOKEN через локальний OAuth (redirect http://localhost)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from web.integrations.google_ads_oauth_local import run_local_oauth_daemon, run_local_oauth_flow  # noqa: E402


def main() -> int:
    if "--daemon" in sys.argv:
        try:
            run_local_oauth_daemon()
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            print(f"❌ {exc}", file=sys.stderr)
            return 1
        return 0

    try:
        refresh = run_local_oauth_flow()
    except PermissionError:
        print(
            "❌ Немає доступу до порту. Спробуйте:\n"
            "  GOOGLE_ADS_OAUTH_LOCAL_PORT=8765 GOOGLE_ADS_OAUTH_LOCAL_REDIRECT_URI=http://localhost:8765 "
            "python scripts/google_ads_oauth_local.py\n"
            "і додайте http://localhost:8765 у Google Cloud → Authorized redirect URIs.",
            file=sys.stderr,
        )
        return 1
    except OSError as exc:
        print(f"❌ Помилка локального сервера: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1

    print("\n" + "=" * 50)
    print("✅ REFRESH TOKEN ЗБЕРЕЖЕНО У settings/marketing.json")
    print("=" * 50)
    print(f"\nGOOGLE_ADS_REFRESH_TOKEN={refresh[:20]}…")
    print("\nПерезапустіть веб-апку на сервері, якщо токен оновлювали локально.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
