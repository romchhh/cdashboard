#!/usr/bin/env bash
# Запуск веб-панелі та Telegram-бота з кореня проєкту.
# Якщо обидва вже працюють, другі процеси не створюються: ./restart.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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
    if "$py" -c "import fastapi, uvicorn, aiogram" >/dev/null 2>&1; then
      echo "$py"
      return 0
    fi
  done
  return 1
}

web_port() {
  local port="8000"
  local from_env=""
  if [[ -f "$ROOT/.env" ]]; then
    from_env="$(grep -E '^WEB_PORT=' "$ROOT/.env" | tail -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '[:space:]')"
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
    if pid_matches "$pid" "$script"; then
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

stop_service() {
  local title="$1"
  local script="$2"
  local pidfile="$3"
  local -a pids=()
  local raw cmd port
  while IFS= read -r raw; do
    [[ -n "$raw" ]] || continue
    pids+=("$raw")
  done < <(running_pids "$script" "$pidfile" || true)

  if [[ "$script" == *main_web.py ]] && command -v lsof >/dev/null 2>&1; then
    port="$(web_port)"
    while IFS= read -r raw; do
      [[ -n "$raw" ]] || continue
      cmd="$(ps -p "$raw" -o command= 2>/dev/null || true)"
      if [[ "$cmd" == *"$script"* || "$cmd" == *"web.main:app"* ]]; then
        pids+=("$raw")
      fi
    done < <(lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
  fi

  if [[ ${#pids[@]} -eq 0 ]]; then
    rm -f "$pidfile"
    echo "Запущених процесів немає: $title."
    return 0
  fi

  echo "Закриваю $title: ${pids[*]}"
  kill_pids "${pids[@]}"
  rm -f "$pidfile"
  echo "$title закрито."
}

launch_service() {
  local title="$1"
  local script="$2"
  local pidfile="$3"
  local log="$4"
  local py="$5"
  local existing pid

  existing="$(running_pids "$script" "$pidfile" | tr '\n' ' ')" || true
  if [[ -n "${existing// /}" ]]; then
    echo "$title уже запущено (pid $existing)."
    return 0
  fi

  mkdir -p "$ROOT/logs"
  {
    echo ""
    echo "===== $(date '+%Y-%m-%d %H:%M:%S') start $title ====="
  } >>"$log"

  if command -v setsid >/dev/null 2>&1; then
    setsid "$py" -u "$script" >>"$log" 2>&1 &
    echo $! >"$pidfile"
  else
    "$py" -c '
import os, sys
os.chdir(sys.argv[1])
log = open(sys.argv[2], "a", buffering=1)
os.setsid()
os.dup2(log.fileno(), 1)
os.dup2(log.fileno(), 2)
os.execv(sys.argv[3], [sys.argv[3], "-u", sys.argv[4]])
' "$ROOT" "$log" "$py" "$script" &
    echo $! >"$pidfile"
  fi

  sleep 1
  pid="$(tr -cd '0-9' <"$pidfile")"
  if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
    echo "$title одразу завершився. Останні рядки логу:" >&2
    tail -n 40 "$log" >&2 || true
    return 1
  fi
  echo "$title запущено. pid $pid"
  echo "Лог: $log"
}

stop_all() {
  stop_service "Веб-панель" "$ROOT/main_web.py" "$ROOT/logs/web.pid"
  stop_service "Telegram-бот" "$ROOT/main_bot.py" "$ROOT/logs/bot.pid"
}

start_all() {
  local py
  py="$(pick_python)" || {
    echo "Не знайдено Python із fastapi, uvicorn та aiogram." >&2
    echo "У корені проєкту: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 127
  }
  launch_service "Веб-панель" "$ROOT/main_web.py" "$ROOT/logs/web.pid" "$ROOT/logs/web.log" "$py"
  echo "Порт веб-панелі: $(web_port)"
  launch_service "Telegram-бот" "$ROOT/main_bot.py" "$ROOT/logs/bot.pid" "$ROOT/logs/bot.log" "$py"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  start_all
fi
