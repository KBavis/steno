# Common development commands. Run from the repo root.
COMPOSE := docker compose -f deploy/docker-compose.yml
STENO   := cd backend && uv run steno

.PHONY: up down setup migrate graph-init load-config api worker web dry-run test lint resolver

up:            ## Start Postgres and Neo4j
	$(COMPOSE) up -d --wait

down:          ## Stop Postgres and Neo4j (data is kept in volumes)
	$(COMPOSE) down

setup:         ## Install backend and frontend dependencies
	cd backend && uv sync
	cd frontend && npm install

migrate:       ## Apply Postgres migrations
	$(STENO) db upgrade

graph-init:    ## Create Neo4j constraints and indexes
	$(STENO) graph init

load-config:   ## Load config/steno.yaml into Postgres and project spaces into Neo4j
	$(STENO) load-config

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
	cd resolver-jvm && ./gradlew installDist
