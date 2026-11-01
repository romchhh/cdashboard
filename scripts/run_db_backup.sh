#!/usr/bin/env bash
# Резервна копія БД на сервері (шукає Python з встановленими залежностями).
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
  for py in "${candidates[@]}"; do
    [[ -n "$py" && -x "$py" ]] || continue
    if "$py" -c "import aiogram" 2>/dev/null; then
      echo "$py"
      return 0
    fi
  done
  return 1
}

PY="$(pick_python)" || {
  echo "Не знайдено Python з залежностями проєкту (aiogram)." >&2
  echo "Створіть venv у корені репозиторію:" >&2
  echo "  cd $ROOT" >&2
  echo "  python3 -m venv venv" >&2
  echo "  source venv/bin/activate" >&2
  echo "  pip install -r requirements.txt" >&2
  exit 127
}

exec "$PY" "$ROOT/scripts/run_db_backup.py" "$@"
