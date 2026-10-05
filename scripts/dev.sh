#!/usr/bin/env bash
# Start the dev stack in one terminal: Postgres + Neo4j, then API + MCP, worker, and UI.
# Ctrl+C stops the API, worker, and UI. The databases keep running; `make down` stops them.
set -euo pipefail
cd "$(dirname "$0")/.."

# bootstrap.sh installs Node 20 with nvm; load it so the UI doesn't pick up an older system Node.
if [ -s "$HOME/.nvm/nvm.sh" ]; then
  # shellcheck disable=SC1091
  . "$HOME/.nvm/nvm.sh" && nvm use --silent 20 >/dev/null
fi

MAKE="make --no-print-directory -s"
$MAKE up
$MAKE migrate graph-init  # both are idempotent; no-ops after the first run

pids=()
run() {  # run NAME CMD...: start in the background, prefixing output with [NAME]
  local name=$1
  shift
  ("$@" 2>&1 | sed -u "s/^/[$name] /") &
  pids+=($!)
}

kill_tree() {  # make → uv → uvicorn, make → npm → vite
  local child
  for child in $(pgrep -P "$1"); do kill_tree "$child"; done
  kill -TERM "$1" 2>/dev/null || true
}

stop() {
  trap - INT TERM EXIT
  for pid in "${pids[@]}"; do kill_tree "$pid"; done
  wait 2>/dev/null || true
}

trap stop INT TERM EXIT
run api $MAKE api
run worker $MAKE worker
run web $MAKE web
wait
