#!/usr/bin/env bash
# Відновлення SQLite з databases/backups/cosmy_db_backup_*.zip
# Спершу зупиніть веб і бота: bash -c 'source ./start.sh && stop_all'
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH=.

pick_python() {
  local candidates=(
    "$ROOT/.venv/bin/python"
    "$ROOT/venv/bin/python"
    "$ROOT/myenv/bin/python"
    "$(command -v python3 2>/dev/null || true)"
    "$(command -v python 2>/dev/null || true)"
  )
  local py
  for py in "${candidates[@]}"; do
    [[ -n "$py" && -x "$py" ]] || continue
    if "$py" -c "import dotenv" >/dev/null 2>&1; then
      echo "$py"
      return 0
    fi
  done
  return 1
}

PY="$(pick_python)" || {
  echo "Не знайдено Python з залежностями проєкту." >&2
  echo "  cd $ROOT && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 127
}

exec "$PY" "$ROOT/scripts/restore_db_backup.py" "$@"
