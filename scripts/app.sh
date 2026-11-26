#!/usr/bin/env bash
# Start / stop the broker + UI together with safe port + stale-socket handling.
#
# Usage:
#   scripts/app.sh start   # start backend + frontend; wait for health; trap Ctrl-C
#   scripts/app.sh stop    # stop both if running; clean up stale bridge socket
#   scripts/app.sh status  # print what's running
#
# Design notes:
#   - PID files live under ignored var/run/; tracking only our own children.
#   - A process on the backend/frontend port that this script did not start is
#     treated as fatal (will not kill someone else's work).
#   - The UI bridge Unix socket at var/ui/bridge.sock is removed only when no
#     process holds it (verified via lsof); otherwise we refuse.

set -eo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VENV_PY="$ROOT/.venv/bin/python"
RUN_DIR="$ROOT/var/run"
BACKEND_PID_FILE="$RUN_DIR/backend.pid"
FRONTEND_PID_FILE="$RUN_DIR/frontend.pid"
BACKEND_LOG="$RUN_DIR/backend.log"
FRONTEND_LOG="$RUN_DIR/frontend.log"
BRIDGE_SOCKET="$ROOT/var/ui/bridge.sock"
# Default to the canonical ports when the caller passes empty strings too.
: "${BACKEND_PORT:=}"
: "${FRONTEND_PORT:=}"
[ -n "${BACKEND_PORT}" ] || BACKEND_PORT="8000"
[ -n "${FRONTEND_PORT}" ] || FRONTEND_PORT="5173"

mkdir -p "$RUN_DIR"

color()  { printf '\033[1;%sm%s\033[0m\n' "$1" "$2"; }
note()   { color 34 "$1"; }
warn()   { color 33 "$1"; }
fatal()  { color 31 "$1"; exit 1; }

port_in_use() {
  local port="$1"
  lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
}

pid_from_file() {
  local file="$1"
  [ -f "$file" ] || { echo ""; return; }
  local pid
  pid="$(cat "$file" 2>/dev/null || true)"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    echo "$pid"
  else
    rm -f "$file"
    echo ""
  fi
}

stop_pid() {
  local pid="$1" label="$2"
  [ -n "$pid" ] || return 0
  note "Stopping $label (pid $pid)…"
  kill -TERM "$pid" 2>/dev/null || return 0
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 0.3
  done
  warn "$label did not exit on SIGTERM; sending SIGKILL."
  kill -KILL "$pid" 2>/dev/null || true
}

clear_stale_bridge() {
  [ -e "$BRIDGE_SOCKET" ] || return 0
  if lsof -nP "$BRIDGE_SOCKET" >/dev/null 2>&1; then
    fatal "UI bridge socket is held by a live process. Stop it first."
  fi
  note "Removing stale UI bridge socket: $BRIDGE_SOCKET"
  rm -f "$BRIDGE_SOCKET"
}

wait_for_health() {
  local tries=60
  local url="http://127.0.0.1:${BACKEND_PORT}/api/health"
  note "Waiting for backend health at $url…"
  while [ "$tries" -gt 0 ]; do
    if curl -fs --max-time 2 "$url" >/dev/null 2>&1; then
      note "Backend ready."
      return 0
    fi
    sleep 0.5
    tries=$((tries - 1))
  done
  warn "Backend did not answer /api/health in 30s. See $BACKEND_LOG."
  return 1
}

status() {
  local backend_pid frontend_pid
  backend_pid="$(pid_from_file "$BACKEND_PID_FILE")"
  frontend_pid="$(pid_from_file "$FRONTEND_PID_FILE")"
  echo "backend pid:   ${backend_pid:-<not running>}"
  echo "frontend pid:  ${frontend_pid:-<not running>}"
  if port_in_use "$BACKEND_PORT"; then
    echo "port $BACKEND_PORT: in use"
  else
    echo "port $BACKEND_PORT: free"
  fi
  if port_in_use "$FRONTEND_PORT"; then
    echo "port $FRONTEND_PORT: in use"
  else
    echo "port $FRONTEND_PORT: free"
  fi
  if [ -e "$BRIDGE_SOCKET" ]; then
    if lsof -nP "$BRIDGE_SOCKET" >/dev/null 2>&1; then
      echo "ui bridge:     active"
    else
      echo "ui bridge:     stale (safe to remove via 'scripts/app.sh stop')"
    fi
  else
    echo "ui bridge:     absent"
  fi
}

stop() {
  stop_pid "$(pid_from_file "$BACKEND_PID_FILE")" "backend"
  stop_pid "$(pid_from_file "$FRONTEND_PID_FILE")" "frontend"
  rm -f "$BACKEND_PID_FILE" "$FRONTEND_PID_FILE"
  clear_stale_bridge
  note "Stopped."
}

reset() {
  # Leaves the machine ready for a clean 'start'. Only ever stops what this
  # script started: anything else holding a port is reported, never killed,
  # because it may be unrelated work.
  stop
  rm -f "$BACKEND_PID_FILE" "$FRONTEND_PID_FILE"
  local blocked=0
  for entry in "backend:$BACKEND_PORT" "frontend:$FRONTEND_PORT"; do
    local label="${entry%%:*}" port="${entry##*:}"
    if port_in_use "$port"; then
      blocked=1
      warn "Port $port ($label) is still held by a process this script did not start:"
      lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | tail -n +2 | sed 's/^/    /'
    fi
  done
  if [ -e "$BRIDGE_SOCKET" ]; then
    blocked=1
    warn "UI bridge socket still present: $BRIDGE_SOCKET"
  fi
  if curl -fs --max-time 2 "http://127.0.0.1:11434/api/version" >/dev/null 2>&1; then
    note "Ollama is running and was left alone; real mode needs it."
  else
    warn "Ollama is not running. Start it before 'app-start' with MOCK_MODE=0."
  fi
  if [ "$blocked" -eq 0 ]; then
    note "Clean. Ready to start."
  else
    warn "Resolve the above, then run this again."
    return 1
  fi
}

start() {
  # Refuse to start if our previous PID files are stale AND the ports are held
  # by someone else. Clean up stale PID files automatically.
  local existing_backend existing_frontend
  existing_backend="$(pid_from_file "$BACKEND_PID_FILE")"
  existing_frontend="$(pid_from_file "$FRONTEND_PID_FILE")"
  if [ -n "$existing_backend" ] || [ -n "$existing_frontend" ]; then
    warn "Already running. Use 'scripts/app.sh status' or 'scripts/app.sh stop' first."
    status
    exit 1
  fi
  if port_in_use "$BACKEND_PORT"; then
    fatal "Port $BACKEND_PORT is in use by another process. Stop it first."
  fi
  if port_in_use "$FRONTEND_PORT"; then
    fatal "Port $FRONTEND_PORT is in use by another process. Stop it first."
  fi
  clear_stale_bridge

  : >"$BACKEND_LOG"
  : >"$FRONTEND_LOG"

  note "Starting backend on port $BACKEND_PORT…"
  (
    cd "$ROOT"
    PYTHONPATH="$ROOT" exec "$VENV_PY" -m backend.api.server --ui \
      --port "$BACKEND_PORT" >"$BACKEND_LOG" 2>&1
  ) &
  echo "$!" >"$BACKEND_PID_FILE"
  if ! wait_for_health; then
    stop
    fatal "Backend failed to start. See $BACKEND_LOG."
  fi

  note "Starting frontend on port $FRONTEND_PORT…"
  (
    cd "$ROOT/frontend"
    exec npm run dev -- --port "$FRONTEND_PORT" >"$FRONTEND_LOG" 2>&1
  ) &
  echo "$!" >"$FRONTEND_PID_FILE"

  note "App is up:"
  echo "  Backend : http://127.0.0.1:${BACKEND_PORT}/api/health"
  echo "  Frontend: http://127.0.0.1:${FRONTEND_PORT}"
  echo "  Logs    : $BACKEND_LOG  $FRONTEND_LOG"
  echo
  note "Press Ctrl-C to stop both."

  trap 'echo; stop; exit 0' INT TERM
  # Keep the script alive while either child is running.
  while :; do
    bpid="$(pid_from_file "$BACKEND_PID_FILE" || true)"
    fpid="$(pid_from_file "$FRONTEND_PID_FILE" || true)"
    if [ -z "$bpid" ] && [ -z "$fpid" ]; then
      note "Both processes exited."
      stop
      break
    fi
    sleep 1
  done
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  reset) reset ;;
  *)
    cat >&2 <<'USAGE'
usage: scripts/app.sh <start|stop|status|reset>
  start   start backend + frontend, wait for health, keep foreground; Ctrl-C stops both
  stop    stop any running backend/frontend started by this script; clear stale UI bridge
  status  show running pids, port state, and bridge-socket state
  reset   stop, clear stale pid files and socket, report anything still holding a port
USAGE
    exit 2
    ;;
esac
