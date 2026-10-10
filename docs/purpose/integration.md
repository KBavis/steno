# Integration

What Steno provides to other systems that build on it: project history tools like [Contextualized](https://github.com/KBavis/contextualized), standards and constraints stores, and workflows. This page is Steno's side of the contract; how those systems combine is designed elsewhere (D98). Part of [Purpose](./README.md).

> **In one minute**
> - **Stable IDs** that survive renames and moves, so anything linked to an application, flow, or interface stays linked.
> - **Planned IDs**: IDs come from natural keys, so a plan can name something before it exists and the link becomes real when the code lands.
> - **Change history** per nightly run (`fact_change`), with commits mapped to PRs (later), so other systems can attribute changes.
> - **Scopes** (organization, space, application) that other systems attach to.
> - **MCP tools** with citations, as the read interface.
> - **Steno must work without any of them.** Contextualized supplies the *why*; Steno supplies the *what*.

---

## 1. What Steno offers

| Offer | What it means | Status |
|---|---|---|
| **Stable IDs** | Every application has a permanent Steno ID; everything beneath it is keyed on that ID, so renames and moves don't break links (D81–D84) | `POC` stage A |
| **Planned IDs** | Endpoint, flow, and topic IDs come from natural keys (`POST /v1/refunds` on an application), so a plan can reference something not built yet; the link becomes real when it's ingested, the way stubs merge | Follows from natural keys; `POC` stage A |
| **Scopes** | The organization, spaces, and applications, with stable IDs, for other systems to attach rules, constraints, or ownership to | Built (spaces, organization); applications with stage A |
| **Change history** | Every run's added, removed, and changed facts with before and after (`fact_change`) | Built |
| **Commits → PRs** | Each run's commit range mapped to PRs (`job_commit`), so a change can be attributed | `v1` |
| **Attribution hook** | `(:Project)-[:CHANGED {kind, pr, commit}]->(element)` edges on the facts a project changed, supplied by a project system as a provider | Designed; with the integration |
| **Read interface** | MCP tools with citations and completeness | Built (5 tools); more planned |

## 2. Division of work with Contextualized (Decided, D11)

| | Steno | Contextualized |
|---|---|---|
| Knows | The architecture as it is: applications, interfaces, flows, tables, dependencies | What each project changed and why: PRs, diffs, tickets, summaries |
| Built from | Code (and the systems around it), re-derived nightly | Projects an organization registers |
| Answers | "What does this flow do? What depends on it? What does this change touch?" | "Which project introduced this? Why?" |

Steno owns *what* changed (deterministic facts and deltas) and owns re-ingestion. Contextualized owns *why*. It never supplies structural facts.

## 3. Attribution, when it's connected

```mermaid
sequenceDiagram
    participant Git as Git host
    participant St as Steno (nightly)
    participant Ctx as Contextualized
    Git->>St: main moved: commits a1b2..f9e8
    St->>St: re-analyze; fact_change: what was added, removed, changed
    St->>St: job_commit: commits → PRs (#812, #815)
    St->>Ctx: which project is PR #812 part of?
    Ctx-->>St: Project "Refunds v2" + summary
    St->>St: Project -CHANGED-> each fact the PR changed
```

Projects attach **at the granularity of what they changed**, never assumed to explain a whole flow:

- **A flow a project created:** that project's context explains the whole flow.
- **An existing flow a project changed:** the context is scoped to the change ("Refunds v2 added the audit write at step 1.2"). A PR that only refactored internals is found by matching its changed files and lines against flow traces.
- **Flows no project touched** keep their code-derived purpose.

The graph side is described in the [graph reference](../how-it-works/graph-reference.md#business-context-from-contextualized-later).

## 4. What stays out of Steno

Planning across projects (spotting conflicts between in-flight projects, protecting planned work, ordering bodies of work, comparing planned and actual changes) uses Steno's IDs, history, and impact analysis, but lives in the systems built on top. Those ideas moved to the separate context-layer project (D97 → D98).
