# Steno

Steno is an **organization-wide context engine for AI agents**. It ingests an organization's repositories into a layered, continuously updated **knowledge graph** (Organization → Space → Application) and exposes it over **MCP**, so agents like Claude Code and Copilot can understand how everything fits together instead of seeing only the repository they're working in.

> **Status:** proof of concept, stage A (see the [roadmap](docs/plan/roadmap.md)). Start with the [docs](docs/README.md).

## Why

Today's coding agents are strong inside one repository and blind beyond it. Most of what matters for a change lives outside the repo: who consumes this topic, which flows pass through this service, which space owns this data. Steno makes that queryable, so one engineer working with an agent can make changes across the organization without breaking established patterns.

## How it works

- **Built bottom-up, searched top-down.** Each application is ingested in depth: endpoints, outbound calls, transports, flows, entities, data store access, and code structure. Space and organization views are **rollups** of those facts.
- **Flows are the core unit.** A flow is a unit of work started by a trigger (an endpoint, a consumed topic, a schedule): an ordered tree of steps, linked to other applications' flows across interfaces.
- **Deterministic first.** Declarative rules produce the facts. Decisions rules can't make use stand-ins now ([Jev](docs/plan/later/jev.md) later). An LLM only writes summary cards.
- **Accurate, or says what it couldn't see.** Every answer is cited; independent sources (code, deploy config, manifests) are compared; gaps are never silent.
- **Company-agnostic.** Steno asks a fixed set of questions about every organization; rule packs and providers say where each organization keeps the answers. Nothing is required of an organization's repositories.
- **Idempotent and current.** Nightly, every repository whose main branch moved is re-analyzed and only changes are written.

## Development

| Path | What it is |
|---|---|
| `backend/` | Python 3.12 (uv). Admin API (`/api`) and MCP server (`/mcp`) in one FastAPI process, plus the ingestion worker |
| `frontend/` | Vite + React + TypeScript UI |
| `resolver-jvm/` | JavaParser symbol-resolution helper, called by the worker |
| `rule-packs/` | Rule packs |
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

[Contextualized](https://github.com/KBavis/contextualized) gives agents context scoped to a single Project: what that Project changed, and why. Steno supplies the wider view: how those changes fit into the rest of the organization. Steno works on its own. Contextualized will later attribute Steno's deltas to the Projects that caused them; see [Projects](docs/purpose/integration.md).

## Documentation

Start at **[docs/README.md](docs/README.md)**. Each section has a hub page that summarizes it and links deeper:

| Section | Covers |
|---|---|
| [Purpose](docs/purpose/README.md) | What Steno is for, the lead use cases, where it fits, what it offers other systems |
| [Concepts](docs/concepts/README.md) | The graph, witnesses and facets, identity, trust |
| [How it works](docs/how-it-works/README.md) | Architecture, the ingestion pipeline, rules and resolvers, the graph schema, MCP, storage, security |
| [Plan](docs/plan/README.md) | Roadmap (POC, v1, v2), onboarding, decisions and open questions |

---

## Why "Steno"

Steno is named after [Nicolas Steno](https://en.wikipedia.org/wiki/Nicolas_Steno), the 17th-century founder of stratigraphy. Steno formulated the law of superposition: rock forms in layers over time, the oldest at the bottom and the newest on top, and that history can be read back out of the layers themselves just by observing how they're stacked.

That's close to what this project does. An organization's software architecture is a set of layers (organization, space, application) that accumulate over time. Each change adds a new layer on top of what existed before, and the *reason* for that layer is worth keeping alongside the structure it produced. Steno's job is to hold that stack: an initial snapshot of "what's there," plus every change after it, each with its own provenance and, once Contextualized is connected, its own why.
