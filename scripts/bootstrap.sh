#!/usr/bin/env bash
# Install what Steno's dev stack needs on Linux or WSL, then sync the project.
# Safe to run repeatedly: anything already present is skipped. `make dev` and `make setup` call this.
# User-level installs only (uv, nvm, Node 20). Docker and Java need a system install; this script tells you what to do.
set -euo pipefail
cd "$(dirname "$0")/.."

NODE_MAJOR=20
ok()   { printf '  \033[32m✔\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m✘\033[0m %s\n' "$*"; exit 1; }
step() { printf '\n%s\n' "$*"; }

# Installers put tools in ~/.local/bin (uv) and ~/.nvm (Node); make them visible to this script.
export PATH="$HOME/.local/bin:$PATH"

step "Checking prerequisites"

# uv: Python tooling for backend/
if command -v uv >/dev/null 2>&1; then
  ok "uv $(uv --version | awk '{print $2}')"
else
  warn "uv not found; installing to ~/.local/bin"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  command -v uv >/dev/null 2>&1 || fail "uv install finished but uv is not on PATH. Open a new terminal and rerun."
  ok "uv $(uv --version | awk '{print $2}') installed"
fi

# Node: frontend/ (needs 20+)
node_major() { node -v 2>/dev/null | sed 's/^v//' | cut -d. -f1; }
if [ -s "$HOME/.nvm/nvm.sh" ]; then
  # shellcheck disable=SC1091
  . "$HOME/.nvm/nvm.sh"
fi
if [ "$(node_major || echo 0)" -ge "$NODE_MAJOR" ] 2>/dev/null; then
  ok "node $(node -v)"
else
  if ! command -v nvm >/dev/null 2>&1; then
    warn "Node $NODE_MAJOR+ not found; installing nvm to ~/.nvm"
    curl -fsSo- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
    # shellcheck disable=SC1091
    . "$HOME/.nvm/nvm.sh"
  fi
  warn "installing Node $NODE_MAJOR with nvm"
  nvm install "$NODE_MAJOR" >/dev/null
  nvm alias default "$NODE_MAJOR" >/dev/null
  [ "$(node_major)" -ge "$NODE_MAJOR" ] || fail "Node $NODE_MAJOR install failed. Install it manually and rerun."
  ok "node $(node -v) installed"
fi

# Docker: Postgres and Neo4j run in containers
if ! command -v docker >/dev/null 2>&1; then
  fail "Docker not found. On WSL, install Docker Desktop on Windows and enable WSL integration for this distro (Settings → Resources → WSL integration)."
elif ! docker info >/dev/null 2>&1; then
  fail "Docker is installed but not running. Start Docker Desktop, then rerun."
else
  ok "docker running"
fi

# Java 21: resolver-jvm (JavaParser helper, called by the worker)
if command -v java >/dev/null 2>&1 && java -version 2>&1 | grep -q 'version "21'; then
  ok "java 21"
else
  warn "Java 21 not found. The worker's JVM resolver needs it. Install with: sudo apt install openjdk-21-jdk"
fi

step "Installing project dependencies"

(cd backend && uv sync --quiet)
ok "backend/.venv synced"

if [ ! -d frontend/node_modules ] || [ frontend/package-lock.json -nt frontend/node_modules/.package-lock.json ]; then
  (cd frontend && npm install --silent)
  ok "frontend/node_modules installed"
else
  ok "frontend/node_modules up to date"
fi

if [ ! -f backend/.env ]; then
  cp .env.example backend/.env
  ok "created backend/.env from .env.example"
else
  ok "backend/.env present"
fi

printf '\nReady. Run: make dev\n'
