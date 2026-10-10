# Later

Designs that are parked until the POC and v1 prove out. Each is kept so the work done so far isn't lost, and so nothing built now blocks it. Part of [Plan](../README.md).

> **In one minute**
> - **Jev** (a fast, calibrated decision model) for routing, verification, and ingestion decisions: designed in detail, `v2`. Stand-ins until then.
> - **Runtime evidence** (OBSERVED): traces and logs as a witness, mostly to check the static graph. `v2`.
> - **Frontends, data store schemas, ownership, environments:** `v2`, each with a hook kept now. Project-aware planning moved to the separate context-layer project (D98).

| Item | What it adds | Hook kept now | Scope |
|---|---|---|---|
| **[Jev](./jev.md)** | Typed, calibrated decisions at ingestion (DI candidates, transports, entry points, hosts, card regeneration) and query time (routing, verification, ranking, guided navigation) | The `Decision` interface (`decisions/`); every decision can be switched off; stand-ins | `v2` |
| **Runtime evidence (OBSERVED)** | Which calls really happen: OpenTelemetry, service mesh, logs, broker client IDs, scheduler run history. Most valuable as a **checker** of the static graph. | OBSERVED is a witness type in every facet's matrix | `v2` |
| **Frontends** | UI applications whose calls join backend endpoints: "which screens break if I change this endpoint?" | UI routes are an entry-point type; Routing marks public endpoints (D92) | `v2` |
| **Data V2** | Columns, constraints, relationships, purpose; a read-only database catalog; migrations replayed into a temporary database | `Table` stubs, `:Migrations` modules | `v2` |
| **Ownership** | `Team` nodes from CODEOWNERS, manifests, catalogs, HR directories | Manifest team fields can be read as aliases now | `v2` |
| **Environments** | What's deployed where, on which servers, with what resources; matching a production stack trace to the commit that produced it | Deploy names are already PLATFORM name sources | `v2` |
| **Project-aware planning** | Moved out of Steno (D98) to the separate context-layer project. Steno provides what it needs: [integration](../../purpose/integration.md). | Stable and planned IDs (stage A); `fact_change` | Elsewhere |
| **Entity anchors** | Retrieval from a question's nouns and verbs to flows that perform that operation on those entities | `USES_ENTITY`, `MAPS_TO` | `v2` |
| **Rule generation from templates** | Coverage items turned into rules by filling in a form | The coverage report and its triage | `v2` ([coverage report §4](../../how-it-works/ingestion/coverage-report.md#4-turning-a-coverage-item-into-a-rule-deferred)) |

## The UI's long-term vision

MCP is the primary interface; the UI's first job is onboarding and coverage review (D93). Later areas:

| Area | What it does |
|---|---|
| **Low-confidence review queue** | People resolve decisions marked `ambiguous` |
| **Flow tracer** | Follow a flow step by step, stitched across services, with its code |
| **"Bloomberg terminal" view** | Navigate organization → space → application and see each space's ins and outs by transport. It renders the same views `get_node` returns to agents. |
| **Change timeline** | What each nightly run changed, attributed to the PRs merged that day, later to projects |
| **Visualize enhancements** | Semantic zoom (D53) is built; free-form exploration with a WebGL renderer may come later |
