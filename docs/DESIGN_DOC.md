# Steno Design Doc

| | |
|---|---|
| **Status** | Draft v0.1 |
| **Author** | Kellen Bavis |
| **Last updated** | 2026-09-27 |
| **Detailed docs** | [Knowledge Graph](./knowledge-graph.md) · [Ingestion](./ingestion.md) · [Retrieval & MCP](./retrieval-and-mcp.md) · [Jev](./jev.md) · [Use Cases](./use-cases.md) |

Status markers used throughout: **Decided** · **Proposed** (suggested, not yet confirmed) · **Open** (not yet decided).

---

## 1. Summary

Steno is an **organization-wide context engine for agents**. It ingests an organization's repositories into a **layered knowledge graph** (Organization → Space → Application) and exposes it over **MCP**. Agents such as Claude Code and Copilot can then answer questions about **how everything fits together**: what exists, how it communicates, which flows run through it, and what a change will affect.

Key design choices:

- **Built bottom-up, searched top-down.** Applications are ingested first, and space and org views are **rollups** of application facts. Queries start at the highest layer the question needs.
- **Deterministic first.** Rules extract facts. **Jev** makes typed, calibrated decisions where rules can't. An **LLM only writes** summaries.
- **Agnostic core, org-specific rules.** Connectors reach sources, and extractors are declarative rules that any org can extend.
- **Fast by design.** Precomputed cards, routing to the right layer, server-side fan-out, and batch tools minimize agent round trips.
- **Idempotent and incremental.** A one-time initial ingestion, then updates driven by commit ranges on every merge to main.

---

## 2. Problem

Agents are strong within one repository and blind beyond it. In a large enterprise, most of what matters for a change lives **outside** the repo: who consumes this topic, which flows cross this service, which space owns this data. Today that knowledge lives in people's heads and stale docs.

**[Contextualized](https://github.com/KBavis/contextualized)** (my per-project context tool) showed that packaging context behind MCP tools works:
- An organization can add **all of its Projects** to it.
- The context each Project gives an agent is **scoped to the exact changes that Project introduced**: its diffs, tickets, and summary.
- That provides the idea of **incremental change tied to an originating Project**. Anyone can look at what a Project did and why.

**But more context is needed.** Contextualized knows what a Project changed, not how those changes fit into the rest of the landscape: what else exists, who depends on it, and how it all connects. Steno supplies that view. Its scale (a whole organization, not one Project's changes) needs a different storage and retrieval design than Contextualized's relational DB plus vector chunks. The two are complementary: Contextualized says *what a Project changed and why*, and Steno says *what that change touched across the org* (see [§15](#15-contextualized-integration-deferred)).

## 3. Goals and non-goals

**Goals**

1. Let an agent **understand one application in depth**: endpoints, outbound calls, transports, flows, entities, and code structure.
2. **Roll up** to space and organization views: communication within and between spaces, and each space's purpose and glossary.
3. **Answer quickly**, in ≤ 3 tool calls for typical questions (see [latency targets](./retrieval-and-mcp.md#6-latency-targets-proposed)).
4. **Stay current** through incremental updates on merge to main.
5. **Work at any org**, without hard-coding one company's structure or tools.
6. **Show confidence and provenance** for every fact.

**Non-goals (for now)**

- Building the tools that *use* the context (test generators, CI bots). Agents and workflows build those on top. See [Use Cases → Scope boundary](./use-cases.md#scope-boundary).
- Runtime observation (Splunk, OpenTelemetry). Phase 1 is static only.
- Full data store schema introspection (V2).
- Version history and time travel, beyond the delta store.
- Capturing the "why" behind facts. That arrives with the Contextualized integration.

## 4. Scope and target

- **Target scale:** a large enterprise, with thousands of repositories and many spaces.
- **Phase 1 target:** one application in our own org (Spring Boot, Kafka, Bitbucket, XLDeploy), with an agnostic design underneath.
- **Languages: multi-language by design, Java first.** Our org is primarily Java, so Java rules and resolution come first. Nothing in the graph model, pipeline, or MCP tools is Java-specific: each additional language needs extractor rule packs (ast-grep / Semgrep support many languages) and its own symbol resolver.

---

## 5. Core concepts

### 5.1 Layers

| Layer | What it is |
|---|---|
| **Organization** | The whole org, made up of spaces |
| **Space** | A generic name for however an org divides itself. It can nest, or an org can have only one. Owns data stores and topics. |
| **Application** | An independently deployable unit. One repository can contain several (e.g. our microservices repo). |

Under each application, the code structure is `Repository → Module → File → Function`. Project is **not** a layer. Projects attach to facts as attribution, later, through Contextualized.

### 5.2 Built bottom-up, searched top-down

**Build: understand each application fully, then roll up.** Ingestion derives everything important about an application. Each layer above is computed from the layer below it.

```mermaid
flowchart BT
    A["<b>Application</b> (ingested)<br/>endpoints · outbound calls · transports<br/>flows · entities · data store access · structure"]
    S["<b>Space</b> (rolled up)<br/>communication within the space · ins/outs with other spaces<br/>purpose · glossary"]
    O["<b>Organization</b> (rolled up)<br/>space-to-space map · spaces' purposes"]
    A --> S --> O
```

```mermaid
flowchart TD
    subgraph Search["Search: top-down, entering at the right layer"]
      Q[Question] --> R{Jev routing}
      R -- clear --> AP[Application]
      R -- one space --> SP[Space] --> AP
      R -- unclear --> OR[Org] --> SP
    end
```

**Facts are stored only at the lowest level.** Everything above is derived, so there's one source of truth.

### 5.3 Who does what

| Work | Done by | When |
|---|---|---|
| Extracting facts | Deterministic rules (extractors) | Ingestion |
| Decisions: classify, route, verify, gate, score | **Jev**, Steno's navigator | Ingestion and query |
| Writing cards, flow summaries, glossary definitions | LLM: a stronger model for flow, app, and space cards; a small one for the rest | Ingestion only |
| Reasoning and the final answer | The client's LLM | Query |

---

## 6. Architecture

Steno has two independent paths that share the same two databases.

**Write path: ingestion** (runs in the background, heavy)

```mermaid
flowchart LR
    GIT[Git host] -- "merge to main" --> ORC[Orchestrator]
    ORC -- "queues a run" --> WK[Ingestion workers]
    WK -- clone --> GIT
    WK -- "decisions / cards" --> AI[Jev + LLM]
    WK -- "facts, cards" --> NEO[(Neo4j)]
    WK -- "runs, deltas" --> PG[(Postgres)]
```

**Read path: queries** (interactive, fast)

```mermaid
flowchart LR
    AG[Agents<br/>Claude Code · Copilot] -- MCP --> MCP[MCP server]
    MCP -- routing --> JEV[Jev]
    MCP -- "graph + vector search" --> NEO[(Neo4j)]
    MCP -- deltas --> PG[(Postgres)]
```

The UI (later) talks to an Admin API for connectors and runs, and uses the same read path as agents.

| Component | Responsibility |
|---|---|
| **MCP server** | Tools for agents. Stateless, reads Neo4j and Postgres, calls Jev for routing. |
| **Admin / Onboarding API** | Connectors, extractors, space declarations, run status. Backs the UI. |
| **Orchestrator** | Turns webhooks, schedules, and manual requests into ingestion runs. Tracks `last_ingested_sha`. |
| **Ingestion workers** | Execute ingestion runs: clone, extract, resolve, derive flows, write facts, compute deltas, generate cards. They're separate from the MCP server so heavy work never slows queries, and several can run in parallel. In the POC, one process can play both orchestrator and worker. |
| **Neo4j** | The knowledge graph, summary cards, and the vector index |
| **Postgres** | Application state: connectors, extractor registry, runs, deltas, Jev and LLM logs |

---

## 7. Technology choices

| Area | Choice | Why | Status |
|---|---|---|---|
| Knowledge graph | **Neo4j** | Paths of variable length (a flow chain across 10+ services), graph algorithms (community detection for proposing spaces), built-in vector index | Decided (leaning); **Open**: edition/licensing |
| Application state | **Postgres** | Relational data: connectors, runs, deltas, logs | Decided |
| Code-pattern rules | **ast-grep / Semgrep** YAML rules (tree-sitter based) | Declarative, many languages, orgs can write their own | Decided |
| Config rules | YAML / properties path rules | Kafka topics, URLs, and placeholders are in config | Decided |
| Symbol resolution | **Our own**: JavaParser symbol solver + dependency JARs, run as a JVM helper | Resolves calls, including through library types, without a full build | Decided (**SCIP not planned**) |
| DI resolution | Per-framework rule plugins (Spring first) + Jev I1 | The compiler can't know which bean gets injected | Proposed |
| Source access | **git clone** into temporary workspaces | Full source for resolution, rate limits, exact diffs | Proposed |
| File store | Postgres (`repo_file` / `file_blob`), object storage later | The MCP server reads files without calling the git host; code search across the org | Proposed (option A) |
| Decisions | **Jev** (TypeSafe) | Typed, calibrated, fast, cheap decisions | Decided |
| Writing | **Flow / app / space cards:** a stronger model (e.g. Sonnet-class), given everything retrieved for the node. **Other cards:** a small model (e.g. Haiku-class). Batch API. | Flow cards decide whether flows can be found | Proposed; **Open**: confirm by comparing on a sample |
| Embeddings | **Stored in Neo4j's vector index** (no separate vector DB); model TBD | One store: vector search and graph traversal in the same query | Decided (storage); **Open**: model |
| Keyword search | Neo4j full-text index | Exact names: paths, topics, symbols | Proposed |
| Agent interface | **MCP** over Streamable HTTP | Works with Claude Code, Copilot, and custom agents | Decided |
| Schema replay (V2) | Testcontainers | Replay migrations into a temporary DB, then read its catalog | Proposed (V2) |
| Steno's implementation language | **Python** (leaning) | Official MCP SDK, Neo4j driver, tree-sitter / ast-grep bindings, Anthropic SDK, LiteLLM; familiar from Contextualized. JavaParser runs as a small JVM helper. | Leaning |
| UI | **Next.js / React** (leaning; a chance to learn TypeScript). React Flow + ELK.js for the flow tracer; Cytoscape.js or Sigma.js for the org map | | Leaning (later) |

---

## 8. Knowledge graph

Full model: [knowledge-graph.md](./knowledge-graph.md).

- **Three independent aspects per node:**
  - **Labels**: what it is, e.g. `(:Interface:KafkaTopic)`.
  - **One `BELONGS_TO` edge**: where it lives.
  - **Interaction edges**: what it does.
- **No `IS_A` edges.** Neo4j labels handle abstraction.
- **Nodes:** `Organization`, `Space`, `Repository`, `Application`, `Module` (roles: `:Service`, `:Library`, `:Contract`, `:Migrations`, `:Test`, `:Build`), `File`, `Function`, `Interface` (`:HttpEndpoint`, `:GrpcMethod`, `:KafkaTopic`, `:Queue`, plugin-defined), `Schedule`, `Flow`, `DataStore → Schema → Table → Column`, `Entity`, `ExternalSystem`, `KafkaCluster`.
- **Key edges:** `INVOKES {seq}` (code calls code), `CALLS` (code calls over the network), `PRODUCES`, `CONSUMES`, `READS_FROM`, `WRITES_TO`, `STARTS` (what kicks off a flow), `LEADS_TO` (one flow causes another; **derived**), `EXPOSES`, `BUILT_FROM`, `DEPENDS_ON` (build dependency), `MAPS_TO` (entity → table). Every edge is either extracted from code/config or derived from other edges; see [Relationship types](./knowledge-graph.md#4-relationship-types).
- **Flows:**
  - A flow is a **unit of work** started by a trigger (an interface or a schedule). There is no Job type and no Subflow type.
  - Every first-party function reachable from an entry point is stored.
  - `significant` and `utility` are **derived tags**, not filters.
  - Steps are an **ordered tree** (by call-site `seq`).
  - A flow points to another flow across an interface (sync: waits; async: fire and forget) and **never contains it**.
- **Internal vs. external** is derived from the lowest common ancestor in the tree.
- **Stubs** are placeholders for targets that aren't ingested yet, and merge with the real node by stable ID.
- **Data stores:** V1 records entity → table mappings and table-level reads/writes, with stub tables. V2 adds columns, constraints, and purpose.

```mermaid
flowchart LR
    SCH[Schedule] -- STARTS --> FL
    EP[/HttpEndpoint/] -- STARTS --> FL[Flow]
    FL -- ENTRY --> FN1[Function]
    FN1 -- "INVOKES {seq}" --> FN2[Function]
    FN2 -- CALLS --> EP2[/HttpEndpoint<br/>other app/]
    FN2 -- PRODUCES --> T{{KafkaTopic}}
    FN2 -- READS_FROM --> TB[(Table)]
    T -- STARTS --> FL2[Flow<br/>consumer]
    EP2 -- STARTS --> FL3[Flow<br/>other app]
    APP[Application] -- EXPOSES --> EP
    TB -- BELONGS_TO --> DS[(DataStore)] -- BELONGS_TO --> SP[Space]
    T -- BELONGS_TO --> SP
```

---

## 9. Ingestion

Full design: [ingestion.md](./ingestion.md).

```mermaid
flowchart LR
    C[Connector] --> W[Temporary clone] --> X[Extractors<br/>rules] --> CG[Call graph<br/>+ flows]
    W --> SR[Symbol resolution<br/>JavaParser + JARs] --> CG
    W --> FS[File store]
    CG --> R[Resolver] --> GW[Graph writer<br/>MERGE by stable ID] --> D[Delta] --> CARD[Cards<br/>Jev-gated LLM]
```

- **Connectors** reach sources. Credentials are references into a secret manager.
- **Extractors** are rules ("when you see X, emit Y") in ast-grep / Semgrep YAML, config path rules, or code plugins. They match **file types and patterns, not connectors**. Orgs add their own.
  - Published as versioned **rule packs** (~40–60 rules for a Spring/Kafka stack), auto-enabled from build dependencies.
  - A **coverage report** after every ingestion ranks what no rule explained, which points straight at the internal frameworks worth a rule. Optionally, an LLM drafts the rule from the report's examples, and Steno validates it.
- **Cloning** into a temporary workspace gives resolution the full source, avoids rate limits, and makes `git diff` exact. Text files go into the **file store**.
- **Config files** become facts (topics, clusters, base URLs, datasources, schedules), plus a property map for placeholders.
- **Resolution:**
  - Symbols: Steno's own (JavaParser symbol solver + dependency JARs). SCIP is not planned.
  - DI: framework rules, then Jev I1.
  - Config placeholders are resolved. Secret values are never ingested.
- **Passes:**
  1. Per file.
  2. Whole app (call graph → all flows at once).
  3. Resolve.
  4. Write.
- **Idempotency:** stable IDs from natural keys. Re-ingesting a scope replaces its facts. Shared nodes are removed only when nothing references them.
- **Incremental updates:**
  - Triggered by a merge to main.
  - Diff by **commit range** from `last_ingested_sha`.
  - Re-extract the changed files, then re-derive only the flows that reach changed functions.
  - Map commits to PRs for attribution.
- **Steno owns re-ingestion.** Contextualized will add attribution and the "why."

### Relational database

Postgres tables: `connector`, `repository` (holding `last_ingested_sha`), `extractor`, `connector_extractor`, `space_assignment`, `ingestion_run`, `run_delta` (**before/after** of changed facts), `run_pr`, `jev_decision`, `llm_generation`, `repo_file` / `file_blob` (file store), `config_property`, `glossary_term`. See the [ER diagram](./ingestion.md#8-relational-database-postgres).

---

## 10. Retrieval and MCP

Full design: [retrieval-and-mcp.md](./retrieval-and-mcp.md).

- **Round trips dominate latency**, so the design minimizes calls:
  - precomputed **cards** for every node
  - **Jev routing** to the right layer
  - **parallel fan-out on the server** across spaces
  - progressive disclosure
  - batch tools
  - token budgets
- **Search, with Jev as the navigator:**
  1. Jev Q0 (what kind of question?), Q1 (which spaces?), Q2 (which app?).
  2. **Candidates from four signals**, in parallel per scope:
     - vector search over **flow cards**
     - **keyword** (full-text index)
     - **entity anchors**: the question's nouns → `Entity` / `Table` nodes, and its verb → an operation (create → insert / POST / `*.created`)
     - **glossary** expansion
  3. **Jev Q4 verifies each candidate** ("does this flow do what was asked?"), and Q3 ranks and trims.
  4. The winners are expanded along `LEADS_TO`.

  The client's LLM synthesizes the answer. Steno runs no LLM at query time. Recall over precision: 5–10 verified candidates with confidence.
- **Code access:** `view_file`, `list_directory`, `view_flow_code`, and `search_code` read from Steno's **file store** (Proposed, option A), so the MCP server never calls the git host.
- **Proposed tools:** `search`, `get_node` (its Space view is the "Bloomberg" ins/outs), `glossary`, `get_flow`, `view_flow_code`, `view_file`, `list_directory`, `search_code`, `find_symbols`, `find_dependents`, `find_paths`, `analyze_change` (takes PRs, collects related PRs, and runs a dry-run ingestion into a temporary overlay), `get_interface`, `get_changes`.
- **Latency targets (Proposed):** graph tools p95 < 300 ms; `search` p95 < 1.5 s; ≤ 3 calls per typical question.

```mermaid
sequenceDiagram
    participant Ag as Agent (Claude Code)
    participant M as Steno MCP
    participant J as Jev
    participant N as Neo4j
    Ag->>M: search("how does a client get created?")
    M->>J: Q0 intent + Q1 one Noul per space (one call)
    J-->>M: find-a-flow · Onboarding 0.95, Identity 0.62, Billing 0.08
    par fan out
      M->>N: cards (vector) + keyword + entity anchors (Onboarding)
    and
      M->>N: cards (vector) + keyword + entity anchors (Identity)
    end
    M->>J: Q4 verify each candidate + Q3 rank (one call)
    J-->>M: POST /v1/clients 0.94, ImportClientsJob 0.71, ...
    M->>N: expand winners along LEADS_TO
    M-->>Ag: ranked cards + consumers + flows + anchors (one response)
    Ag->>M: view_flow_code(flow_id)
    M-->>Ag: code for every step (one response)
```

---

## 11. Jev

Full design: [jev.md](./jev.md). **Decided:** Jev makes every decision that deterministic rules can't. **Proposed:** the specific decisions below.

- **Ingestion:**
  - I1: DI candidate
  - I2: transport of an unrecognized call
  - I3: unrecognized entry point
  - I4: org host vs. vendor
  - I5: does the card need regenerating (controls LLM cost)
  - I6: space proposal, always confirmed by a human
  - I7: glossary term → node, confirmed by a human
  - I8: is an unexplained call worth the coverage report
- **Query:**
  - Q0: what kind of question
  - Q1: which spaces (one Noul per space, in one call)
  - Q2 / Q2b: which app / which entity
  - **Q4: does each candidate flow do what was asked** (verification)
  - Q3: relevance score
- **What Jev sees:** bounded **digests** built from the graph (a flow digest is its trigger, card, and interactions, ~300–600 tokens however big the flow is), never raw code. That keeps every call within Jev's ~64k input budget.
- **Why it matters:** each of these would otherwise be a slow LLM call. With Jev, one `search` costs three rounds of Jev calls, ~0.2–1.5 s.
- **Confidence bands:** > 0.9 act · 0.5–0.9 act and mark low-confidence · < 0.5 mark ambiguous or ask a human.
- **Safeguards:** every decision is logged so calibration can be measured, and a `Decision` interface keeps Jev replaceable.

---

## 12. LLM usage and cost

- The LLM **writes** cards, flow summaries, and glossary definitions. It also drafts rules on request (optional, rare). Nothing else.
- **Flow, app, and space cards** get a stronger model and **everything retrieved for the node** (trigger, entities, ordered significant steps, data access, downstream flows' cards, bounded code of significant steps), because flow cards decide whether flows can be found. Other cards use a small model and the node's facts.
- Only some nodes get cards: roughly **700–1,000 per large app** (flows, interfaces, entities, and a few key functions), not millions.
- Output is cached by the hash of its inputs, and regenerated only when Jev I5 says the meaning changed. Generated with the Batch API (50% cheaper).
- Rough one-time cost: **~$3–7 per app**, so **~$7k–14k for 2,000 apps** (see [Retrieval](./retrieval-and-mcp.md#summary-cards)).
- Every generation is logged (`llm_generation`), so the cost per app is measured, not guessed.

---

## 13. Onboarding an organization

**Passive by default. Nothing is required of applications.**

| Level | What the org does | What Steno gets |
|---|---|---|
| **Passive** (default) | Grants read access | Repos, config, deployment definitions (XLDeploy) |
| **Annotate** (optional) | Adds a small `steno.yaml` where extractors get something wrong | Overrides and names |
| **Instrument** (optional, later) | Adopts OpenTelemetry, or exposes Splunk | Runtime-observed edges |

**Defining spaces:**
1. **Declare** (Phase 1–2): a space is a list of repositories.
2. **Propose** (Phase 3+): Jev I6, community detection over the communication graph, ownership, and XLDeploy groupings suggest spaces.
3. **Confirm:** a human accepts or corrects the proposal, and it's saved as `space_assignment`.

**Extending to a new org:** add connectors for its tools, and add extractor rules or plugins for its frameworks. The core model doesn't change.

---

## 14. UI (long-term vision)

MCP is the primary interface. A UI comes later, for the tasks where people need it:

| Area | What it does |
|---|---|
| **Onboarding and connectors** | Add connectors, pick extractors, declare spaces, watch ingestion runs |
| **Space confirmation** | Review proposed spaces (Jev I6 + clustering) and accept or correct them |
| **Low-confidence review queue** | Humans resolve decisions Jev marked `ambiguous` |
| **Flow tracer** | Visually follow a flow step by step, stitched across services, with its code |
| **"Bloomberg terminal" view** | Navigate the org → space → app, and see each space's ins and outs by transport, live. It renders the same layer views `get_node` returns to agents, so the data is built once. |
| **Change timeline** | What each merge changed (from `run_delta`), later tied to Projects |

---

## 15. Contextualized integration (Deferred)

**Steno must work without Contextualized.** The integration is the end goal, not a dependency.

```mermaid
sequenceDiagram
    participant Ctx as Contextualized
    participant St as Steno
    Note over St: Merge to main → Steno re-extracts and computes the delta (already in place)
    St->>St: run_pr: commits → PR #123
    St->>Ctx: which Project is PR #123 part of? what's its summary?
    Ctx-->>St: Project X + the why
    St->>St: fill introduced_by / modified_by on the changed facts
```

- **Division of work:**
  - Steno owns **what** changed: deterministic facts and deltas.
  - Contextualized owns **why**: the Project, the tickets, the intent.
- Contextualized's project deltas then enable "introduced in Project A, modified in Project B," and delta-driven testing with full context.
- **Long-term vision:** Steno (org architecture) and Contextualized (projects) could combine into one **org-wide agentic context layer** for developers, managers, and solution leads.

---

## 16. Roadmap

| Phase | Scope | Exit criteria (Proposed) |
|---|---|---|
| **1: Application** | Ingest one application fully: endpoints, outbound calls, transports, flows, entities, structure. Static only. MCP tools over it. | Against a hand-labeled gold set for that app: ≥ 90% of endpoints/topics/outbound calls found, flows match the gold set for the top entry points, and typical questions answered in ≤ 3 calls |
| **2: Space** | Onboard one space (declared). Rollups within the space, ins/outs, purpose, glossary. | Communication within the space matches what the team says it is |
| **3: Organization** | Several spaces, cross-space edges, space proposals, the org view | Cross-space edges confirmed by the space owners |
| **Incremental** | Merge-to-main updates. Can start during Phase 1. | Graph after incremental updates = graph after a full re-ingest |
| **Later** | Contextualized attribution · V2 schemas · runtime signals · UI · more languages | |

---

## 17. Evaluation (Proposed)

Correctness comes first, so it has to be measured:

- **Gold set:** hand-label one application's endpoints, topics, outbound calls, data store access, and 10–20 key flows.
- **Metrics:** precision and recall per fact type. Flow step accuracy. Jev calibration (predicted confidence vs. actual accuracy). Tool calls per answered question. Latency.
- **Consistency check:** the graph after N incremental updates must equal the graph from a full re-ingest at the same SHA. This proves idempotency.
- **Agnostic check:** a public multi-language system (e.g. the OpenTelemetry Demo) as a second target, so the design doesn't overfit to our org.

---

## 18. Security and privacy

- **Stored source code is sensitive.** The file store (option A) holds a full copy of every ingested repository's text files, so Steno needs the same access controls as the repositories themselves.
- **Secret values are never ingested.** Credentials live in a secret manager.
- **Data leaving the org:** Jev receives digests (names, summaries, interactions; rarely code). The LLM receives flow context, including code snippets, when writing cards. **Confirm the org's policy before Phase 1**, and send only the minimum.
- **MCP access control (Open):** authentication (OAuth over Streamable HTTP) and scoping results by space or role (a contractor sees less than an employee).
- **Auditing:** tool calls, Jev decisions, and LLM generations are logged.

---

## 19. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Static call graphs miss DI, reflection, and dynamic dispatch | Missing or wrong flow steps | Symbol solver with dependency JARs + DI rules + Jev I1. Mark `ambiguous`. Coverage report. Runtime signals later. |
| Flows can't be found from business-language questions | Search misses the right flow | Business-language flow cards from a stronger model, four retrieval signals, Jev Q4 verification, measured against a question → flow set |
| Extractors overfit to our org | Not agnostic | Rules and plugins, a public second target, the core never depends on org rules |
| Enterprise scale (millions of functions) | Slow ingestion and queries | Incremental updates, derived tags, materialized rollups, measure early |
| Jev is new | Wrong decisions | Calibration on a gold set, logging, a replaceable `Decision` interface |
| Data egress policy | Blocks Jev / LLM | Confirm early. Minimal state. |
| Round trips still too many | Slow for agents | Measure calls per question. Add batch tools where the logs show chatty patterns. |

---

## 20. Decision log

| # | Decision | Status |
|---|---|---|
| D1 | Three layers: Organization → Space → Application. Data stores and topics belong to Spaces. | Decided |
| D2 | Build bottom-up, search top-down. Higher layers are rollups. | Decided |
| D3 | Interfaces are nodes. Edges between apps come from shared interface nodes. | Decided |
| D4 | Phase order: Application → Space → Organization | Decided |
| D5 | Phase 1 is static only | Decided |
| D6 | Neo4j for the graph, Postgres for app state | Decided (Neo4j leaning) |
| D7 | Flows: triggers instead of Jobs, no Subflows, ordered steps, flows linked across interfaces and never nested | Decided |
| D8 | Store all first-party reachable functions. Significance is a tag. | Decided |
| D9 | One repository → many Applications, detected via Service/Library modules | Decided |
| D10 | Incremental updates by commit range on merge to main. PRs are used for attribution. | Decided |
| D11 | Steno owns re-ingestion. Contextualized adds attribution and the why. | Decided |
| D12 | Jev for all decisions rules can't make, at ingestion and query time | Decided |
| D13 | Steno's own symbol resolution (JavaParser + dependency JARs), with cloning into temporary workspaces. SCIP not planned. | Decided |
| D14 | Routing: classify with Jev, then search one scope or fan out in parallel | Decided |
| D15 | Data store schemas in V2. V1 records entity → table mappings and table-level access. | Decided |
| D16 | Labels for abstraction, no `IS_A` edges | Proposed |
| D17 | File store (option A): all text files at the ingested commit; the MCP server never calls the git host | Proposed |
| D18 | `run_delta` keeps the before/after of changed facts | Proposed |
| D19 | MCP tool set ([list](./retrieval-and-mcp.md#5-mcp-tools-proposed-pending-review)) | Proposed |
| D20 | Edge names: `INVOKES` (code → code), `CALLS` (network), `STARTS` (what kicks off a flow), `LEADS_TO` (flow → flow, derived) | Decided |
| D21 | Jev is the navigator at query time: Q0 intent, Q1/Q2 routing, **Q4 candidate verification**, Q3 ranking, using bounded digests | Decided |
| D22 | Find flows with four signals: flow cards, keyword, entity anchors, glossary | Proposed |
| D23 | Embeddings live in Neo4j's vector index; no separate vector DB | Decided |
| D24 | Flow / app / space cards use a stronger model with the node's full retrieved context | Decided (model to be confirmed) |
| D25 | Rule packs, auto-enabled from dependencies, plus a coverage report after every ingestion | Proposed |

## 21. Open questions

**Carried over**
- [ ] File store (option A) vs. mixed vs. git host for `view_file`
- [ ] How the glossary is populated (code, docs, declared `glossary.yaml`, usage), and who confirms mappings
- [ ] LLM-drafted rules: include, or rely on hand-written rules only?
- [ ] Which model writes flow cards: compare Haiku-class vs. Sonnet-class on a sample
- [ ] The signals that mark a Service module in our microservices repo, and how `plugins/` modules are detected as Libraries
- [ ] The fan-in threshold for `utility`, and when shared logic gets its own card
- [ ] Neo4j edition and licensing (Community vs. Enterprise, Graph Data Science library)
- [ ] Jev: calibration on our data, and our org's data policy
- [ ] Final MCP tool names and scope

**Not yet discussed**
- [ ] Deployment: where Steno runs, and whether it's single-tenant per org (recommended) or multi-tenant
- [ ] MCP authentication and access scoping
- [ ] Embeddings model, and the exact card format and size
- [ ] How confidence is shown to agents and people, so a Jev-resolved edge never looks deterministic
- [ ] Cost budget for the initial ingestion of a large org
- [ ] How the gold set for Phase 1 evaluation gets built, and by whom

---

## 22. Glossary

| Term | Meaning |
|---|---|
| **Space** | Any segment an org uses to divide itself. Can nest. |
| **Application** | An independently deployable unit |
| **Interface** | Something that can be called or subscribed to: an HTTP endpoint, gRPC method, topic, or queue |
| **Transport** | *How* an interface communicates: REST, gRPC, Kafka, an internal framework |
| **Flow** | A unit of work: a trigger → entry function → everything it reaches, as ordered steps |
| **Trigger** | What starts a flow: an Interface or a Schedule |
| **Card** | An LLM-written, fixed-size summary of a node, built from its facts |
| **Stub** | A placeholder node for a target that isn't ingested yet. It merges with the real node by stable ID. |
| **Delta** | The facts added, removed, or changed by one ingestion run |
| **Connector** | Access to a source (where it is, and the credentials reference) |
| **Extractor** | A rule or plugin: "when you see X, emit fact Y" |
| **Rollup** | A higher-layer fact derived from lower-layer facts |
| **Digest** | A small, bounded summary of a node built from the graph (trigger, card, interactions), used as Jev's input |
| **Rule pack** | A versioned bundle of extractor rules for one framework or org |
| **Coverage report** | A per-ingestion list of what no rule explained, ranked by frequency |
| **Entity anchor** | Retrieval that maps a question's nouns to Entity/Table nodes and its verb to an operation, then finds flows performing it |
