#!/usr/bin/env bash
# Закриває веб-панель і Telegram-бота, потім відкриває обидва заново.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=start.sh
source "$ROOT/start.sh"

stop_all
start_all
