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
ROOT="$(pwd)"

# A previous run that wasn't shut down cleanly (a closed terminal, a crash) can leave the API,
# workers, or UI running: they hold ports 8000 and 5173, and an old worker keeps running old
# code. Stop anything started from this checkout (and only from it) before starting again.
stale() {  # the API and workers (and the worker pool's processes), and the UI's dev server;
  # not other Python processes from the venv, like an editor's language server
  pgrep -f "$ROOT/backend/.venv/bin/steno (api|worker)|$ROOT/backend/.venv/bin/python -c from multiprocessing|$ROOT/frontend/node_modules/.bin/vite" || true
}
leftovers=$(stale)
if [ -n "$leftovers" ]; then
  echo "Stopping processes left from an earlier run: $(echo "$leftovers" | tr '\n' ' ')"
  kill -TERM $leftovers 2>/dev/null || true
  for _ in $(seq 1 20); do  # up to 5s to exit cleanly, then force
    [ -z "$(stale)" ] && break
    sleep 0.25
  done
  remaining=$(stale)
  [ -n "$remaining" ] && kill -KILL $remaining 2>/dev/null || true
fi

$MAKE up
$MAKE migrate graph-init  # both are idempotent; no-ops after the first run
# Jobs a stopped worker was running would stay "running" forever, and keep their repository
# locked (one job per repository); no worker is running now, so mark them interrupted
(cd backend && uv run steno job recover)

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
