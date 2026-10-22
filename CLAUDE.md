# Steno

An organization-wide context engine for AI agents. It ingests repositories into a layered knowledge graph (Organization → Space → Application) and exposes it over MCP. It answers "what is" about an organization's software; the larger context layer and agent-first SDLC it will feed are designed in a separate project (D98).

## Start here

- **`docs/` is the source of truth. Start at `docs/README.md`.** It has four sections, each with a hub page (`README.md`) that summarizes it and links deeper: `docs/purpose/` (what Steno is for, lead use cases, where it fits, integration contract), `docs/concepts/` (the graph, witnesses and facets, identity, trust), `docs/how-it-works/` (architecture, ingestion stage by stage, rules, resolvers, graph schema, MCP, storage, security), `docs/plan/` (roadmap, onboarding, decisions).
- Every design point is marked **Decided**, **Proposed**, or **Open**, and scoped `POC`, `v1`, or `v2`. The decision log is `docs/plan/decisions.md` (D1–D129 and open questions). Follow Decided items; raise Proposed or Open items with the user before relying on them. Design choices belong to the user.
- Reference pages: the node and edge vocabulary is `docs/how-it-works/graph-reference.md` (§3–4); the rule format is `docs/how-it-works/ingestion/rules-and-packs.md`; the Postgres tables are `docs/how-it-works/storage.md`.
- `docs/next-steps.md` is a local, gitignored working list; never link it from tracked files. `docs/my_docs/` is the user's personal notes (gitignored).
- Docs are Markdown only, layered: a short "In one minute" summary and a diagram at the top of each page, detail in linked sub-pages.

## Current stage

**Proof of concept, stage A** (`docs/plan/roadmap.md`). Built: identity, witness types, claims and reconcile, the completeness block. Remaining: application kinds, the work in the roadmap's build order for D115–D127 (answers that say what they checked and list their sources, Java at scale, patterns and rule curation), and a scored question set compared against an agent without Steno. Then B (a stub organization exercising every facet), C (spring-petclinic-microservices), D (our organization). Accuracy of MCP answers is the bar, and a feature belongs in Steno only if a use case in `docs/purpose/use-cases.md` needs it. Jev and runtime evidence are `v2`; decisions use stand-ins.

## Repository layout

- `backend/src/steno/`: `api/` (Admin API), `mcp/` (MCP tools), `ingestion/` (job queue, stage pipeline, worker, `local.py`: parse → extract → assemble on a folder, for the CLI and tests; jobs use the pipeline), `db/` (SQLAlchemy models + Alembic migrations for the Postgres tables in `docs/how-it-works/storage.md`), `graph/` (Neo4j schema, stable IDs, projections; structure, builder, writer), `coverage/` (the coverage report), `decisions/` (the `Decision` interface; stand-in backends), `connectors/`, `llm/`. Ingestion code by stage: `source/` (listing files, reading config and structured records; shared), `rule_packs/` (loading, selecting, testing packs; library versions), `extraction/` (the rule engine: parse + extract), `resolvers/` (symbol resolvers, one per language), `assemblers/` (one per clue type), `identity/` (the identify stage and the identity registry), `claims/` (claim types and the claim store), `reconcile/` (comparing the code with other witnesses)
- `frontend/`: Vite + React + TS; `/api` is proxied to the backend on :8000
- `resolver-jvm/`: JavaParser helper (Gradle wrapper)
- `rule-packs/`: packs grouped by ecosystem (`java/`, `python/`, `http/`, `kafka/`, `catalog/`, `org/`); each has `pack.yaml` (with its `witness_type`), `rules/`, `tests/`. `steno rules list` shows them. Write rules with the `rule-pack-author` skill (`.claude/skills/rule-pack-author/`); the format is `docs/how-it-works/ingestion/rules-and-packs.md`

## Commands

Run from the repo root (see `Makefile`): `make up`, `make migrate`, `make graph-init`, `make dev`, `make api`, `make worker`, `make web`, `make dry-run REPO=<name>`, `make test` (includes every rule pack's test cases), `make lint`, `make resolver`. Rules: `cd backend && uv run steno rules test [pack]` and `uv run steno extract <folder> [--json out.json]`. After changing `db/models.py`, create a migration with `cd backend && uv run alembic revision --autogenerate -m "..."` and review it.

## Skills

- `rule-pack-author`: write rules and rule packs from a plain-language request
- `github-issue`: draft, label, and link GitHub issues (shows a draft for approval before creating anything)

## Key decisions to keep in mind

- **One Neo4j graph** with **architecture nodes** (what the software does: applications, interfaces, flows, steps, tables) and **code nodes** (how it's built: repository, module, file), joined by `BUILT_FROM`. Functions aren't nodes: each flow stores its trace (D60). Search covers the searchable architecture nodes (flows, applications, spaces, organization, interfaces, entities, tables, data stores, external systems, and, once built, interceptors; D104, D116) plus function names in traces; never code nodes.
- **Postgres** holds what Steno is told and what it did (connectors, jobs, change history, logs, the identity registry, the claim store; planned: rule curation's patterns and proposals). Neo4j must stay rebuildable from Postgres + git.
- **Deterministic first:** rules produce facts; decisions rules can't make use stand-ins (Jev in `v2`); an LLM writes text (flow purposes and narratives, cards) and drafts rules, never facts. Drafted rules are proven (tests, a shadow run) and approved by a person before they run (rule curation, D120).
- **Breadth, not depth:** Steno is the map and serves code from the git host; consuming agents reason and clone only what a task needs. Answers list their `sources`; Steno never runs an agent to answer a question (D119).
- **Rules stay YAML in git.** Postgres holds only state about rules (patterns, proposals, rule health). Approved rules are committed by Steno: rules for public libraries to the core packs in `rule-packs/`, rules for internal frameworks to the organization's pack repository (D125, D129).
- **Witnesses and facets:** a **witness** is one concrete source of statements (an application's code, the XLD repository, a manifest, an admin); its **witness type** is CODE, PLATFORM, DECLARED, or ADMIN (OBSERVED in `v2`). Steno answers a fixed set of facets; each organization's witnesses supply them; independent witnesses are compared (D76–D80, D112). "Provider" means an LLM provider only.
- **Application identity:** a permanent Steno ID, a canonical (deployed) name, a display name, and aliases; child IDs are built on the Steno ID (D81–D84).
- **Gaps are visible, never silent.** No file contents are stored; code tools read from the git host at the ingested commit. **Every MCP result carries citations.**
- **The graph schema is language-agnostic.** Language specifics live in rule packs and symbol resolvers. Steno never asks an organization to change its repositories.
