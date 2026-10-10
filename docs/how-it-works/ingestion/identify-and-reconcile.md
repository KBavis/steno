# Identify and reconcile

Two planned additions to the pipeline. **Identify** gives every application a permanent Steno ID and its real name. **Reconcile** combines claims from different witnesses into facts with sources and agreement states. Part of [Ingestion](./README.md); the ideas are in [Identity](../../concepts/identity.md) and [Witnesses and facets](../../concepts/witnesses-and-facets.md).

> **In one minute**
> - **Identify** (Decided; `POC` stage A) runs after structure detection. It looks each deployable up in a Postgres **identity registry** by canonical name or by repository + module path, reuses or assigns a Steno ID, and marks unseen applications removed. Everything under an application is then keyed by its Steno ID.
> - **Reconcile** (direction Decided, design **Open**; before stage B) handles the fact that witnesses for one subject come from **different repositories ingested on different nights**: the XLD repo describes a topic; a consumer's code lives elsewhere. Each run stores its claims; reconcile recomputes only the subjects a run touched, from every stored claim about them.
>
> Neither is built yet.

---

## 1. Where they sit

```mermaid
flowchart LR
    X["extract<br/>code rules + file rules<br/>(claims tagged by witness)"] --> A["assemble"]
    A --> ST["structure"]
    ST --> ID["<b>identify</b><br/>registry lookup"]
    ID --> FL["flows"]
    FL --> W["write"]
    W --> RC["<b>reconcile</b><br/>touched subjects only"]
    RC --> CV["coverage<br/>+ agreement view"]
    REG[("Identity registry<br/>Postgres")] <--> ID
    CS[("Claim store<br/>Postgres")] <--> RC
    W -. "non-CODE claims" .-> CS
```

## 2. Identify (Decided)

**Input:** the deployables structure detection found in this repository, each with its anchors: location (git host repository ID + module path), and every name claim from the name ladder (deploy name, manifest ID, build artifact name, runtime self-name).

**Steps:**

1. **Pick each deployable's canonical name** from its name claims, by ladder priority (ADMIN → PLATFORM → DECLARED → CODE build name → CODE runtime name → path, provisional). Apply declared naming patterns. Every other name becomes an alias with its source.
2. **Look it up** in the registry by canonical name or by location.
   - Found → reuse the Steno ID; record a changed name or location; the old name becomes an alias.
   - Found by name, but active in another repository → **conflict**, flagged; no merge.
   - A pending admin rename ("`users-svc` becomes `org-svc-identity`") matches → reuse that Steno ID.
   - Not found → assign a new Steno ID.
3. **Mark removed** every registered application of this repository not seen in this run (a tombstone). A removed application whose name or location appears again is restored with its old ID.
4. **Report** to the run's change report: new, renamed, moved, removed, restored, conflicts, and "removed X, added Y in the same run" (a possible rename-and-move for an admin to link).

**Output:** each deployable's Steno ID, canonical name, display name, aliases, kind labels. The flows stage builds every child ID (endpoint, flow, entity, schedule) on the Steno ID.

**Registry (Postgres, proposed shape):**

| Table | Columns |
|---|---|
| `application_identity` | `steno_id`, `canonical_name`, `display_name`, `repository_id`, `module_path`, `state` (active / removed / deprecated), `first_seen_job`, `last_seen_job`, `removed_job` |
| `application_alias` | `steno_id`, `name`, `source` (witness + provider + file/line), `slot` (ladder slot), `is_past_name` |
| `application_link` | `from_steno_id`, `to_steno_id` or `pending_name`, `kind` (rename / same), `by`, `at`, `reason` |
| `unmatched_name` | `name`, `source`, `first_seen`, `status` (open / mapped / ignored), `mapped_to` |

**Migration from today:** application IDs are `app:{repo}[-{module}]` and every child ID embeds that name. Stage A assigns Steno IDs to existing applications by their current location, rewrites child IDs once, and from then on IDs never depend on names.

**Proving it** (stage B): scripted rename, move, rename-and-move, split, removal, restoration, and two repositories claiming one name; after each, re-ingest and check no duplicates, no dead flows, expected IDs.

## 3. Reconcile (Open: technical design before stage B)

What's decided:

- **Every provider produces claims** in its facet's shape, tagged with witness type, provider, and source location (D76, D87).
- **CODE facts stay in the graph** as today. **Non-CODE claims** (PLATFORM, DECLARED, ADMIN) are stored in a **claim store in Postgres** with an as-of time, because they don't all come from git and Neo4j must stay rebuildable (D36).
- **Reconcile recomputes only touched subjects.** A subject is the thing a claim is about: a topic, an application, an endpoint, a table. When the XLD repository changes tonight, only the topics it mentions are recomputed; when an application's code changes, only its subjects are.
- **Evidence facets** get an agreement state per fact (`confirmed` / `code only` / `declared only`); **authority facets** resolve by precedence (D80).
- **The same system read two ways is one witness** (D78): merged before comparison; differences are drift.
- **Admin overrides** are claims like any other, with who, when, and why, and are still compared with code.

Open questions for the design:

- [ ] Claim shape per facet and the emit syntax in packs
- [ ] How "declared only" facts appear in the graph (a node or edge marked `declared_only`, or only in the claim store and the agreement view)
- [ ] Where agreement states live: properties on graph facts, or a side table
- [ ] Subject keys for each facet, so claims from different repositories line up (largely the same natural keys as the graph)
- [ ] How removals propagate: a claim that disappears (a grant removed from XLD) recomputes its subject
- [ ] Ordering across repositories in one nightly run (reconcile after all jobs of the night, or per job)

```mermaid
sequenceDiagram
    participant XR as Job: XLD repository
    participant CR as Job: recon-svc code
    participant CS as Claim store (Postgres)
    participant G as Graph (Neo4j)
    XR->>CS: PLATFORM claims: payment.settled consumers = [recon-svc, ledger-svc]
    XR->>XR: reconcile subjects touched: topic payment.settled
    CS-->>XR: all claims for payment.settled
    G-->>XR: CODE facts for payment.settled (recon-svc consumes)
    XR->>G: recon-svc: confirmed · ledger-svc: declared only
    CR->>G: write code facts (recon-svc listener unchanged)
    CR->>CR: reconcile subjects touched: nothing changed for payment.settled
```
