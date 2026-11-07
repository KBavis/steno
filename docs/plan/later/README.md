# Later

Designs parked until the POC and v1 prove out, each with a hook kept now.

> **In one minute**
> - **Jev** at query time is built and optional. The rest of Jev is `v2`.
> - **Runtime evidence, frontends, schemas, ownership, environments:** `v2`.

```mermaid
flowchart LR
    P["POC"] --> V1["v1: Projects, Constraints"] --> V2["v2: items below"]
```

| Item | Adds | Hook kept now | Scope |
|---|---|---|---|
| **[Jev](./jev.md)** | Ingestion decisions, more navigation | The `Decision` interface | `v2` |
| **Runtime evidence** | Which calls really happen; a checker for the static graph | OBSERVED witness type | `v2` |
| **Frontends** | Which screens break with an endpoint | Routing marks public endpoints (D92) | `v2` |
| **Data V2** | Columns, relationships, catalogs, replayed migrations | `Table` stubs, `:Migrations` | `v2` |
| **Ownership** | `Team` nodes | Manifest team fields | `v2` |
| **Environments** | What's deployed where | Deploy names | `v2` |
| **Project-aware planning** | Back as the [Projects pillar](../../purpose/context-layer.md) | Stable IDs, `fact_change` | `v1` |
| **Entity anchors** | Retrieval from a question's nouns and verbs | `USES_ENTITY`, `MAPS_TO` | `v2` |

**UI, long term:** a low-confidence review queue, a flow tracer, a space-level view of ins and outs, and a change timeline. MCP stays the primary interface (D93).
