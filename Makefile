# Common development commands. Run from the repo root.
COMPOSE := docker compose -f deploy/docker-compose.yml
STENO   := cd backend && uv run steno

# Neo4j's heap follows the machine: 40% of its memory, between 1 and 8 GB (D134). Set
# NEO4J_HEAP_MB to override; where /proc/meminfo is missing the compose default (2 GB) applies.
NEO4J_HEAP_MB ?= $(shell awk '/MemTotal/ {m = int($$2 / 1024 * 0.4); if (m < 1024) m = 1024; if (m > 8192) m = 8192; print m}' /proc/meminfo 2>/dev/null)
export NEO4J_HEAP_MB

.PHONY: dev up down setup migrate graph-init api worker web dry-run test lint resolver

dev:           ## Install missing prerequisites, then start everything in one terminal (Ctrl+C to stop)
	./scripts/bootstrap.sh
	./scripts/dev.sh

up:            ## Start Postgres and Neo4j
	$(COMPOSE) up -d --wait

down:          ## Stop Postgres and Neo4j (data is kept in volumes)
	$(COMPOSE) down

setup:         ## Install prerequisites (uv, Node 20) and backend/frontend dependencies
	./scripts/bootstrap.sh

migrate:       ## Apply Postgres migrations
	$(STENO) db upgrade

graph-init:    ## Create Neo4j constraints and indexes
	$(STENO) graph init

api:           ## Admin API on :8000/api and MCP on :8000/mcp
	$(STENO) api --reload

worker:        ## Ingestion worker
	$(STENO) worker

web:           ## UI on :5173
	cd frontend && npm run dev

dry-run:       ## Queue a dry run: make dry-run REPO=<name>
	$(STENO) job enqueue $(REPO) --mode dry_run

test:
	cd backend && uv run pytest

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .
	cd frontend && npm run lint

resolver:      ## Build the JVM symbol-resolution helper
	cd resolver-jvm && (./gradlew installDist || (./gradlew --stop && ./gradlew installDist))
