# Steno

An organization-wide context engine for AI agents. It ingests repositories into a layered knowledge graph (Organization → Space → Application) and exposes it over MCP.

## Start here

- **`docs/DESIGN_DOC.md` is the source of truth.** Read it before making changes. The detailed design is in `docs/knowledge-graph.md`, `docs/ingestion.md`, `docs/rule-packs.md`, `docs/retrieval-and-mcp.md`, `docs/jev.md`, and `docs/use-cases.md`.
- Every design point is marked **Decided**, **Proposed**, or **Open**, and the decision log is DESIGN_DOC §20. Follow Decided items; raise Proposed or Open items with the user before relying on them. Design choices belong to the user.

## Current phase

**Phase 1: ingest one application** in depth, starting with a dry run that writes no LLM text and reports time per stage, graph size, and projected LLM cost (DESIGN_DOC §16).

## Repository layout

- `backend/src/steno/`: `api/` (Admin API), `mcp/` (MCP tools), `ingestion/` (job queue, stage pipeline, worker, `local.py`: parse → extract → assemble on a folder, for the CLI and tests; jobs use the pipeline), `db/` (SQLAlchemy models + Alembic migrations for the Postgres tables in ingestion.md §8), `graph/` (Neo4j schema, stable IDs, projections; structure, builder, writer), `decisions/` (the `Decision` interface; Jev and stand-in backends), `connectors/`, `llm/`. Ingestion code by stage: `source/` (listing files, reading config; shared), `rule_packs/` (loading, selecting, testing packs), `extraction/` (the rule engine: parse + extract), `resolvers/` (symbol resolvers, one per language), `assemblers/` (one per clue type)
- `frontend/`: Vite + React + TS; `/api` is proxied to the backend on :8000
- `resolver-jvm/`: JavaParser helper (Gradle wrapper)
- `rule-packs/`: packs grouped by ecosystem (`java/`, `python/`, `org/`); each has `pack.yaml`, `rules/`, `tests/`. Write rules with the `rule-pack-author` skill (`.claude/skills/rule-pack-author/`); the format is `docs/rule-packs.md`

## Commands

Run from the repo root (see `Makefile`): `make up`, `make migrate`, `make graph-init`, `make dev`, `make api`, `make worker`, `make web`, `make dry-run REPO=<name>`, `make test` (includes every rule pack's test cases), `make lint`. Rules: `cd backend && uv run steno rules test [pack]` and `uv run steno extract <folder> [--json out.json]`. After changing `db/models.py`, create a migration with `cd backend && uv run alembic revision --autogenerate -m "..."` and review it.

## Skills

- `rule-pack-author`: write rules and rule packs from a plain-language request
- `github-issue`: draft, label, and link GitHub issues (shows a draft for approval before creating anything)

## Key decisions to keep in mind

- **One Neo4j graph** with **architecture nodes** (what the software does: applications, interfaces, flows, steps, tables) and **code nodes** (how it's built: repository, module, file, function), joined by `BUILT_FROM`, `ENTRY`, and `RUNS`. Search only covers architecture nodes.
- **Postgres** holds what Steno is told and what it did (connectors, jobs, change history, LLM/Jev/tool logs). Neo4j must stay rebuildable from Postgres + git.
- **Deterministic first:** rules produce facts; decision models handle what rules can't; an LLM only writes text (flow purposes and narratives, cards).
- **No file contents are stored.** Code tools read from the git host at the ingested commit.
- **Every MCP result carries citations.**
- **The graph schema is language-agnostic.** Language specifics live in rule packs and symbol resolvers.
