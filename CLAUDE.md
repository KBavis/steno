# Steno

An organization-wide context engine for AI agents. It ingests repositories into a layered knowledge graph (Organization → Space → Application) and exposes it over MCP.

## Start here

- **`docs/DESIGN_DOC.md` is the source of truth.** Read it before making changes. The detailed design is in `docs/knowledge-graph.md`, `docs/ingestion.md`, `docs/retrieval-and-mcp.md`, `docs/jev.md`, and `docs/use-cases.md`.
- Every design point is marked **Decided**, **Proposed**, or **Open**, and the decision log is DESIGN_DOC §20. Follow Decided items; raise Proposed or Open items with the user before relying on them. Design choices belong to the user.

## Current phase

**Phase 1: ingest one application** in depth, starting with a dry run that writes no LLM text and reports time per stage, graph size, and projected LLM cost (DESIGN_DOC §16).

## Repository layout

- `backend/src/steno/`: `api/` (Admin API), `mcp/` (MCP tools), `ingestion/` (job queue, stage pipeline, worker), `db/` (SQLAlchemy models + Alembic migrations for the Postgres tables in ingestion.md §8), `graph/` (Neo4j schema, stable IDs, projections), `decisions/` (the `Decision` interface; Jev and stand-in backends), `connectors/`, `extractors/`, `llm/`, `poc/` (YAML config loader)
- `frontend/`: Vite + React + TS; `/api` is proxied to the backend on :8000
- `resolver-jvm/`: JavaParser helper (Gradle wrapper)
- `rule-packs/`: one folder per pack: `pack.yaml` + `rules/`

## Commands

Run from the repo root (see `Makefile`): `make up`, `make migrate`, `make graph-init`, `make load-config`, `make api`, `make worker`, `make web`, `make dry-run REPO=<name>`, `make test`, `make lint`. After changing `db/models.py`, create a migration with `cd backend && uv run alembic revision --autogenerate -m "..."` and review it.

## Key decisions to keep in mind

- **One Neo4j graph** with **architecture nodes** (what the software does: applications, interfaces, flows, steps, tables) and **code nodes** (how it's built: repository, module, file, function), joined by `BUILT_FROM`, `ENTRY`, and `RUNS`. Search only covers architecture nodes.
- **Postgres** holds what Steno is told and what it did (connectors, jobs, change history, LLM/Jev/tool logs). Neo4j must stay rebuildable from Postgres + git.
- **Deterministic first:** extractor rules produce facts; decision models handle what rules can't; an LLM only writes text (flow purposes and narratives, cards).
- **No file contents are stored.** Code tools read from the git host at the ingested commit.
- **Every MCP result carries citations.**
- **The graph schema is language-agnostic.** Language specifics live in rule packs and resolvers.
