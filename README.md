# Steno

Steno is an **organization-wide context engine for AI agents**. It ingests an organization's repositories into a layered, continuously updated **knowledge graph** (Organization → Space → Application) and exposes it over **MCP**, so agents like Claude Code and Copilot can understand how everything fits together instead of seeing only the repository they're working in.

> **Status:** Phase 1 build (ingesting one application). Start with the [Design Doc](docs/DESIGN_DOC.md).

## Why

Today's coding agents are strong inside one repository and blind beyond it. Most of what matters for a change lives outside the repo: who consumes this topic, which flows pass through this service, which space owns this data. Steno makes that queryable, so one engineer working with an agent can make changes across the organization without breaking established patterns.

## How it works

- **Built bottom-up, searched top-down.** Each application is ingested in depth: endpoints, outbound calls, transports, flows, entities, data store access, and code structure. Space and organization views are **rollups** of those facts. Queries enter at the right layer and drill down.
- **Flows are the core unit.** A flow is a unit of work started by a trigger (an endpoint, a consumed topic, a schedule). It's an ordered tree of steps, linked to other applications' flows across interfaces.
- **Deterministic first.** Declarative extractor rules produce the facts. [Jev](docs/jev.md) makes typed, calibrated decisions where rules can't. An LLM only writes summary cards.
- **Company-agnostic.** Connectors reach sources (Bitbucket, GitHub, GitLab, …). Extractor rule packs cover frameworks, and organizations add their own. Applications don't have to opt in to anything.
- **Idempotent and incremental.** A one-time initial ingestion, then updates on every merge to main, driven by the commit range since the last ingested commit.
- **Fast for agents.** Precomputed summary cards, Jev routing, parallel search across spaces, and batch tools keep agent round trips to a minimum.

## Development

| Path | What it is |
|---|---|
| `backend/` | Python 3.12 (uv). Admin API (`/api`) and MCP server (`/mcp`) in one FastAPI process, plus the ingestion worker |
| `frontend/` | Vite + React + TypeScript UI |
| `resolver-jvm/` | JavaParser symbol-resolution helper, called by the worker |
| `rule-packs/` | Extractor rule packs |
| `deploy/` | docker-compose for Postgres and Neo4j |

Prerequisites: Docker (on WSL, enable Docker Desktop's WSL integration), [uv](https://docs.astral.sh/uv/), Node 20+, Java 21.

```sh
make up                                         # Postgres + Neo4j
make setup                                      # backend and frontend dependencies
cp .env.example backend/.env
make migrate graph-init

make dev       # databases + API/MCP (:8000) + worker + UI (:5173); Ctrl+C to stop
               # or separately: make api / make worker / make web

# Open the UI: onboarding walks you through organization → spaces → connectors → repositories.
```

Connect an agent: `claude mcp add --transport http steno http://localhost:8000/mcp`.

## Relationship to Contextualized

[Contextualized](https://github.com/KBavis/contextualized) gives agents context scoped to a single Project: what that Project changed, and why. Steno supplies the wider view: how those changes fit into the rest of the organization. Steno works on its own. Contextualized will later attribute Steno's deltas to the Projects that caused them.

## Documentation

| Doc | Covers |
|---|---|
| [Design Doc](docs/DESIGN_DOC.md) | The overview: architecture, technology choices, roadmap, decision log, open questions |
| [Knowledge Graph](docs/knowledge-graph.md) | Neo4j labels, relationships, ownership, flows, data stores |
| [Ingestion](docs/ingestion.md) | Connectors, extractor rules, symbol resolution, idempotency, incremental updates, Postgres schema |
| [Retrieval & MCP](docs/retrieval-and-mcp.md) | Routing, summary cards, MCP tools, latency |
| [Jev](docs/jev.md) | Every decision Jev makes, and its confidence thresholds |
| [Use Cases](docs/use-cases.md) | What Steno enables: agentic development, planning, testing, operations |

---

## Why "Steno"

Steno is named after [Nicolas Steno](https://en.wikipedia.org/wiki/Nicolas_Steno), the 17th-century founder of stratigraphy. Steno formulated the law of superposition: rock forms in layers over time, the oldest at the bottom and the newest on top, and that history can be read back out of the layers themselves just by observing how they're stacked.

That's close to what this project does. An organization's software architecture is a set of layers (organization, space, application) that accumulate over time. Each change adds a new layer on top of what existed before, and the *reason* for that layer is worth keeping alongside the structure it produced. Steno's job is to hold that stack: an initial snapshot of "what's there," plus every change after it, each with its own provenance and, once Contextualized is connected, its own why.
