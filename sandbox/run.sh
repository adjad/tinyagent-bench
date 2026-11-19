#!/usr/bin/env bash
# One-command sandbox launcher: a second, isolated Harbor backend (own
# ~/.harbor-sandbox/moe) plus the sandbox server (fake Swift app + iPhone
# mockup + Harbor Dev), talking to each other on non-default ports.
#
# Deliberately NOT port 8765 / the real ~/.moe: HarborClient.swift hardcodes
# 8765 and PortGuard.swift SIGTERMs anything else bound to it, and if the
# real Harbor.app were left running while this proxied to 8765, both would be
# subscribers on the same /assistant/events hub — the real app could
# receive and execute a send_email/send_message the sandbox agent decided
# to send, from the user's real accounts. The guard below exists so that
# can never happen by accident.
set -euo pipefail
cd "$(dirname "$0")/.."

# Refuse the real Harbor's ports BEFORE anything is created or started. A sandbox
# backend on 8765 would be refused by the real app (it only trusts the process it spawned,
# and the port would still be taken); a sandbox server aimed at the real
# backend would act as a second app on its event stream. (Same rule as
# sandbox/guard.py, repeated here so it holds even without a Python environment.)
for _port in "${HARBOR_BACKEND_PORT:-8775}" "${SANDBOX_PORT:-8766}"; do
  case "$_port" in
    8765|8000)
      echo "refusing to run the sandbox: port $_port belongs to the real Harbor (use 8775 / 8766)" >&2
      exit 1 ;;
  esac
done

export HARBOR_SANDBOX_HOME="${HARBOR_SANDBOX_HOME:-$HOME/.harbor-sandbox}"
export HARBOR_HOME="${HARBOR_HOME:-$HARBOR_SANDBOX_HOME/moe}"
BACKEND_PORT="${HARBOR_BACKEND_PORT:-8775}"
export HARBOR_BACKEND_URL="http://127.0.0.1:${BACKEND_PORT}"
SANDBOX_PORT="${SANDBOX_PORT:-8766}"

resolved_harbor_home="$(python3 -c "import os; print(os.path.realpath(os.path.expanduser(os.environ['HARBOR_HOME'])))")"
resolved_real_home="$(python3 -c "import os; print(os.path.realpath(os.path.expanduser('~/.moe')))")"
if [ "$resolved_harbor_home" = "$resolved_real_home" ]; then
  echo "refusing to run the sandbox against your real ~/.moe (HARBOR_HOME resolved to it)" >&2
  exit 1
fi

mkdir -p "$HARBOR_HOME" "$HARBOR_SANDBOX_HOME"

if lsof -nP -iTCP:"$BACKEND_PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
  echo "port $BACKEND_PORT is already in use — set HARBOR_BACKEND_PORT to pick another" >&2
  exit 1
fi
if lsof -nP -iTCP:"$SANDBOX_PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
  echo "port $SANDBOX_PORT is already in use — set SANDBOX_PORT to pick another" >&2
  exit 1
fi

if ! curl -sf http://127.0.0.1:8000/v1/models >/dev/null 2>&1; then
  echo "⚠ oMLX doesn't answer on :8000 — /agent turns will fail until it's running" >&2
fi

echo "sandbox home:   $HARBOR_SANDBOX_HOME"
echo "harbor home:      $HARBOR_HOME"
echo "backend:        http://127.0.0.1:$BACKEND_PORT"
echo "sandbox server: http://127.0.0.1:$SANDBOX_PORT"

.venv/bin/uvicorn service.main:app --port "$BACKEND_PORT" --no-proxy-headers >"$HARBOR_SANDBOX_HOME/backend.log" 2>&1 &
BACKEND_PID=$!
# Initialised BEFORE the trap exists: under `set -u` an interrupt or a failed health
# check before the sandbox server starts made the cleanup die on an unset variable,
# leaving the backend running (audit M-8).
SANDBOX_PID=""

cleanup() {
  for _pid in "${BACKEND_PID:-}" "${SANDBOX_PID:-}"; do
    [ -n "$_pid" ] && kill "$_pid" 2>/dev/null || true
  done
}
trap cleanup INT TERM EXIT

echo -n "waiting for the sandbox backend to come up"
for _ in $(seq 1 100); do
  if curl -sf "$HARBOR_BACKEND_URL/mode" >/dev/null 2>&1; then
    echo " ready"
    break
  fi
  echo -n "."
  sleep 0.3
done
if ! curl -sf "$HARBOR_BACKEND_URL/mode" >/dev/null 2>&1; then
  echo
  echo "backend never came up — see $HARBOR_SANDBOX_HOME/backend.log" >&2
  exit 1
fi

.venv/bin/uvicorn sandbox.server:app --port "$SANDBOX_PORT" --reload \
  >"$HARBOR_SANDBOX_HOME/sandbox.log" 2>&1 &
SANDBOX_PID=$!

sleep 1
open "http://127.0.0.1:$SANDBOX_PORT/" 2>/dev/null || true

wait
