#!/usr/bin/env bash
# Закриває локальний ParserService, веб-панель і Telegram-бота, потім відкриває їх заново.
# Парсер перед стартом збирає runtime з поточного cosmyparser.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=start.sh
source "$ROOT/start.sh"

stop_all
start_all
