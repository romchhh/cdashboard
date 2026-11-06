#!/usr/bin/env bash
# Запуск scrape-worker ParserService з авто-перезапуском при падінні.
# ./start.sh | stop | restart | status
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$ROOT/run.py"
PIDFILE="$ROOT/logs/parser.pid"
WATCHPID="$ROOT/logs/parser.watch.pid"
LOG="$ROOT/logs/parser.log"
# Пауза перед рестартом (сек). Не тримати в .env — дефолт тут.
RESPAWN_DELAY="${PARSER_RESPAWN_DELAY:-3}"
LOG_MAX_BYTES="${PARSER_LOG_MAX_BYTES:-5242880}" # 5 MiB

pick_python() {
  # У монорепо CosmyFinancesBot venv часто в корені, не в ParserService/.
  local parent="$(cd "$ROOT/.." && pwd)"
  local candidates=(
    "$ROOT/.venv/bin/python"
    "$ROOT/venv/bin/python"
    "$ROOT/myenv/bin/python"
    "$ROOT/runtime/.venv/bin/python"
    "$parent/.venv/bin/python"
    "$parent/venv/bin/python"
    "$parent/myenv/bin/python"
    "$(command -v python3 2>/dev/null || true)"
    "$(command -v python 2>/dev/null || true)"
  )
  local py
  for py in "${candidates[@]}"; do
    [[ -n "$py" && -x "$py" ]] || continue
    if "$py" -c "import fastapi, uvicorn" >/dev/null 2>&1; then
      echo "$py"
      return 0
    fi
  done
  return 1
}

parser_port() {
  local port="8091"
  local from_env=""
  if [[ -f "$ROOT/.env" ]]; then
    from_env="$(grep -E '^PARSER_PORT=' "$ROOT/.env" | tail -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '[:space:]')"
  fi
  if [[ -n "$from_env" ]]; then
    port="$from_env"
  fi
  echo "$port"
}

collect_pids() {
  local script="$1"
  local pidfile="$2"
  {
    if [[ -f "$pidfile" ]]; then
      tr -cd '0-9\n' <"$pidfile"
    fi
    if command -v pgrep >/dev/null 2>&1; then
      pgrep -f "$script" || true
      pgrep -f "ParserService/start.sh" || true
      pgrep -f "app.main:app" || true
    fi
  } | awk 'NF && $1 ~ /^[0-9]+$/ && !seen[$1]++ { print $1 }'
}

pid_matches() {
  local pid="$1"
  local needle="$2"
  local cmd
  cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  [[ -n "$cmd" && "$cmd" == *"$needle"* ]]
}

running_pids() {
  local script="$1"
  local pidfile="$2"
  collect_pids "$script" "$pidfile" | while IFS= read -r pid; do
    if pid_matches "$pid" "$script" || pid_matches "$pid" "app.main:app" || pid_matches "$pid" "run.py"; then
      echo "$pid"
    fi
  done
}

kill_pids() {
  local pid child
  for pid in "$@"; do
    [[ -n "$pid" ]] || continue
    kill -TERM -"$pid" 2>/dev/null || true
    if command -v pgrep >/dev/null 2>&1; then
      for child in $(pgrep -P "$pid" 2>/dev/null || true); do
        kill -TERM "$child" 2>/dev/null || true
      done
    fi
    kill -TERM "$pid" 2>/dev/null || true
  done
  sleep 2
  for pid in "$@"; do
    [[ -n "$pid" ]] || continue
    if kill -0 "$pid" 2>/dev/null; then
      kill -KILL -"$pid" 2>/dev/null || true
      kill -KILL "$pid" 2>/dev/null || true
    fi
  done
}

stop_all() {
  local -a pids=()
  local raw cmd port
  while IFS= read -r raw; do
    [[ -n "$raw" ]] || continue
    pids+=("$raw")
  done < <(running_pids "$SCRIPT" "$PIDFILE" || true)

  if [[ -f "$WATCHPID" ]]; then
    pids+=("$(tr -cd '0-9' <"$WATCHPID")")
  fi
  # watchdog loops that mention start.sh supervise
  if command -v pgrep >/dev/null 2>&1; then
    while IFS= read -r raw; do
      [[ -n "$raw" ]] || continue
      cmd="$(ps -p "$raw" -o command= 2>/dev/null || true)"
      if [[ "$cmd" == *"start.sh"* && "$cmd" == *"supervise"* ]]; then
        pids+=("$raw")
      fi
    done < <(pgrep -f "start.sh" || true)
  fi

  if command -v lsof >/dev/null 2>&1; then
    port="$(parser_port)"
    while IFS= read -r raw; do
      [[ -n "$raw" ]] || continue
      cmd="$(ps -p "$raw" -o command= 2>/dev/null || true)"
      if [[ "$cmd" == *"$SCRIPT"* || "$cmd" == *"app.main:app"* || "$cmd" == *"run.py"* ]]; then
        pids+=("$raw")
      fi
    done < <(lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
  fi

  if [[ ${#pids[@]} -eq 0 ]]; then
    rm -f "$PIDFILE" "$WATCHPID"
    echo "Запущених процесів немає: Parser worker."
    return 0
  fi

  echo "Закриваю Parser worker: ${pids[*]}"
  kill_pids "${pids[@]}"
  rm -f "$PIDFILE" "$WATCHPID"
  echo "Parser worker закрито."
}

ensure_runtime() {
  if [[ -f "$ROOT/runtime/cosmyparser/cosmy_parse.py" ]]; then
    return 0
  fi
  if [[ -f "$ROOT/../cosmyparser/cosmy_parse.py" ]]; then
    echo "runtime/ відсутній — збираю через pack.sh…"
    bash "$ROOT/pack.sh"
    return 0
  fi
  echo "Немає двигуна cosmy_parse." >&2
  echo "На монорепо: bash pack.sh" >&2
  echo "На окремому сервері: скопіюй ParserService/ разом із runtime/ після pack.sh" >&2
  return 1
}

run_once() {
  local py="$1"
  mkdir -p "$ROOT/logs"
  {
    echo ""
    echo "===== $(date '+%Y-%m-%d %H:%M:%S') start Parser worker ====="
  } >>"$LOG"
  cd "$ROOT"
  exec "$py" -u "$SCRIPT"
}

rotate_log_if_huge() {
  local size
  [[ -f "$LOG" ]] || return 0
  size="$(wc -c <"$LOG" 2>/dev/null | tr -d ' ' || echo 0)"
  if [[ "${size:-0}" -gt "$LOG_MAX_BYTES" ]]; then
    mv -f "$LOG" "${LOG}.1" 2>/dev/null || true
    : >"$LOG"
    echo "===== $(date '+%Y-%m-%d %H:%M:%S') log rotated (was ${size} bytes) =====" >>"$LOG"
  fi
}

# Внутрішній watchdog: якщо uvicorn впав — підняти знову.
supervise_loop() {
  local py="$1"
  local child=0
  local rc=0
  trap '[[ $child -gt 0 ]] && kill -TERM $child 2>/dev/null || true; exit 0' TERM INT
  while true; do
    rotate_log_if_huge
    {
      echo ""
      echo "===== $(date '+%Y-%m-%d %H:%M:%S') supervise spawn ====="
    } >>"$LOG"
    "$py" -u "$SCRIPT" >>"$LOG" 2>&1 &
    child=$!
    echo "$child" >"$PIDFILE"
    echo "Parser worker pid $child (watchdog)"
    set +e
    wait "$child"
    rc=$?
    set -e
    child=0
    rm -f "$PIDFILE"
    if [[ "$rc" -eq 137 || "$rc" -eq 9 ]]; then
      echo "Parser worker Killed (exit $rc) — ймовірно OOM. Перезапуск через ${RESPAWN_DELAY}s…" >>"$LOG"
    else
      echo "Parser worker впав (exit $rc) — перезапуск через ${RESPAWN_DELAY}s…" >>"$LOG"
    fi
    sleep "$RESPAWN_DELAY"
  done
}

start_all() {
  local py existing
  ensure_runtime || exit 1
  py="$(pick_python)" || {
    echo "Не знайдено Python із fastapi та uvicorn." >&2
    echo "У ParserService: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 127
  }

  existing="$(running_pids "$SCRIPT" "$PIDFILE" | tr '\n' ' ')" || true
  if [[ -n "${existing// /}" ]]; then
    echo "Parser worker уже запущено (pid $existing)."
    return 0
  fi

  mkdir -p "$ROOT/logs"
  if command -v setsid >/dev/null 2>&1; then
    setsid bash "$ROOT/start.sh" supervise "$py" >>"$LOG" 2>&1 &
    echo $! >"$WATCHPID"
  else
    bash "$ROOT/start.sh" supervise "$py" >>"$LOG" 2>&1 &
    echo $! >"$WATCHPID"
  fi
  sleep 1.5
  if [[ -f "$PIDFILE" ]] && kill -0 "$(tr -cd '0-9' <"$PIDFILE")" 2>/dev/null; then
    echo "Parser worker запущено (watchdog pid $(tr -cd '0-9' <"$WATCHPID"))."
    echo "Порт: $(parser_port)"
    echo "Лог: $LOG"
  else
    echo "Не вдалося стартувати. Останні рядки логу:" >&2
    tail -n 40 "$LOG" >&2 || true
    return 1
  fi
}

status_all() {
  local pids
  pids="$(running_pids "$SCRIPT" "$PIDFILE" | tr '\n' ' ')"
  if [[ -n "${pids// /}" ]]; then
    echo "running pids: $pids  port=$(parser_port)"
  else
    echo "stopped"
    return 1
  fi
}

usage() {
  echo "Використання: $0 [start|stop|restart|status]"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  case "${1:-start}" in
    supervise)
      # внутрішнє: $2 = python path
      supervise_loop "${2:?python}"
      ;;
    start) start_all ;;
    stop) stop_all ;;
    restart) stop_all; start_all ;;
    status) status_all ;;
    -h|--help|help) usage ;;
    *)
      echo "Невідома команда: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
fi
