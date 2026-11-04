# Integration

What Steno provides to the systems and pillars that build on it: workflows and SDLC tools, and, from `v1`, its own Projects and Constraints pillars ([the context layer](./context-layer.md), D141), which use the same IDs, scopes, and history. Part of [Purpose](./README.md).

> **In one minute**
> - **Stable IDs** that survive renames and moves, so anything linked to an application, flow, or interface stays linked.
> - **Planned IDs**: IDs come from natural keys, so a plan can name something before it exists and the link becomes real when the code lands.
> - **Change history** per nightly run (`fact_change`), with commits mapped to PRs (later), so other systems can attribute changes.
> - **Scopes** (organization, space, application) that other systems attach to.
> - **MCP tools** with citations, as the read interface, and the **sources** behind every answer, so a harness can fetch exactly the code a task needs.
> - **The architecture must work without any of them.** The Projects pillar (`v1`) supplies the *why* and the *who*; the architecture supplies the *what*.

---

## 1. What Steno offers

| Offer | What it means | Status |
|---|---|---|
| **Stable IDs** | Every application has a permanent Steno ID; everything beneath it is keyed on that ID, so renames and moves don't break links (D81–D84) | `POC` stage A |
| **Planned IDs** | Endpoint, flow, and topic IDs come from natural keys (`POST /v1/refunds` on an application), so a plan can reference something not built yet; the link becomes real when it's ingested, the way stubs merge | Follows from natural keys; `POC` stage A |
| **Scopes** | The organization, spaces, and applications, with stable IDs, for other systems to attach rules, constraints, or ownership to | Built (spaces, organization); applications with stage A |
| **Change history** | Every run's added, removed, and changed facts with before and after (`fact_change`) | Built |
| **Commits → PRs** | Each run's commit range mapped to PRs (`job_commit`), so a change can be attributed | `v1` |
| **Attribution hook** | `(:Project)-[:CHANGED {kind, pr, commit}]->(element)` edges on the facts a project changed, written by the Projects pillar | Designed; `v1` |
| **Read interface** | MCP tools with citations and completeness | Built (5 tools); more planned |
| **Sources to fetch** | Every answer that cites code lists its `sources`: repository, clone URL, branch, ingested commit, whether the branch has moved since (`head_moved`), cited folders, and why. A harness that clones for agents (an SDLC tool built on Steno, not part of it) uses it to fetch exactly what a task needs (D119). | Built |

## 2. Division of work between the architecture and Projects (Decided, D11; Projects becomes a Steno pillar by D141)

Both are Steno pillars; how Projects is set up and ingested is in [the context layer §3](./context-layer.md#3-projects-v1).

| | Architecture | Projects |
|---|---|---|
| Knows | The architecture as it is: applications, interfaces, flows, tables, dependencies | What each project changed and why: PRs, diffs, tickets, summaries |
| Built from | Code (and the systems around it), re-derived nightly | Projects an organization registers |
| Answers | "What does this flow do? What depends on it? What does this change touch?" | "Which project introduced this? Why?" |

The architecture owns *what* changed (deterministic facts and deltas) and owns re-ingestion. Projects owns *why* and *who*. It never supplies structural facts.

## 3. Attribution (`v1`)

```mermaid
sequenceDiagram
    participant Git as Git host
    participant St as Steno (nightly)
    participant Pr as Steno Projects pillar
    Git->>St: main moved: commits a1b2..f9e8
    St->>St: re-analyze; fact_change: what was added, removed, changed
    St->>St: job_commit: commits → PRs (#812, #815)
    St->>Pr: which project is PR #812 part of?
    Pr-->>St: Project "Refunds v2" (via its ticket and epic) + summary
    St->>St: Project -CHANGED-> each fact the PR changed
```

Projects attach **at the granularity of what they changed**, never assumed to explain a whole flow:

- **A flow a project created:** that project's context explains the whole flow.
- **An existing flow a project changed:** the context is scoped to the change ("Refunds v2 added the audit write at step 1.2"). A PR that only refactored internals is found by matching its changed files and lines against flow traces.
- **Flows no project touched** keep their code-derived purpose.

The graph side is described in the [graph reference](../how-it-works/graph-reference.md#business-context-from-projects-v1).

## 4. Planning across projects

Spotting conflicts between in-flight projects, protecting planned work, ordering bodies of work, and comparing planned and actual changes use Steno's IDs, history, and impact analysis. Moved out of Steno by D98, these come back with the Projects pillar in `v1` (D97, D141). The workflows that act on them stay on top, in other repositories.
