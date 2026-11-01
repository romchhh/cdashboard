#!/usr/bin/env python3
"""Google Ads OAuth: одноразово або daemon для кнопки в налаштуваннях (порт 8765)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from web.integrations.google_ads_oauth_local import (  # noqa: E402
    run_local_oauth_daemon,
    run_local_oauth_flow,
)


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
    except PermissionError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1

    print("\n" + "=" * 50)
    print("✅ REFRESH TOKEN ЗБЕРЕЖЕНО У settings/marketing.json")
    print("=" * 50)
    print(f"\nGOOGLE_ADS_REFRESH_TOKEN={refresh[:20]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
