# Trust

What "accurate" means for Steno, and how it's achieved and measured. When agents act on Steno's answers, a confidently wrong answer is the worst outcome: an impact analysis that leaves out a consumer leads straight to a broken release.

> **In one minute**
> - **Deterministic first:** rules produce facts; nothing is guessed silently; an LLM only writes text.
> - **Every claim is cited** to a file, line, and commit, or to the system it came from.
> - **Gaps are never silent:** a call Steno couldn't follow, a send that joins no receiver, a receiver nothing sends to, a disagreement between witnesses all show up, in answers and in the coverage report.
> - **Independent witnesses are compared.** A topic consumer found in code *and* granted in XLD is confirmed; one found in only one place is flagged.
> - **It's measured:** a question set with expected answers, a comparison with an agent working without Steno, and idempotency checks on every run.

---

## 1. Who does what

| Work | Done by | When |
|---|---|---|
| Extracting facts | Deterministic rules in rule packs | Ingestion |
| Decisions rules can't make (which implementation DI injects, whether a host is internal) | A decision model: stand-ins in the POC, [Jev](../plan/later/jev.md) later (`v2`) | Ingestion and query |
| Writing text: flow purposes and narratives, cards | An LLM | Ingestion only |
| Reasoning and the final answer | The client's own LLM (Claude Code, Copilot) | Query |

Steno runs no LLM at query time. A decision a model makes carries its confidence, and every decision can be switched off.

## 2. Citations and confidence

Every MCP result cites where each claim comes from (Decided, D29):

```json
"citations": [{
  "claim": "writes clients (insert)",
  "space": "Onboarding", "app": "users-svc", "repo": "microservices",
  "file": "src/main/java/.../ClientService.java", "lines": "57", "commit": "a1b2c3d",
  "url": "https://<git host>/.../ClientService.java?at=a1b2c3d#lines-57"
}],
"searched": ["space:Onboarding", "space:Identity"]
```

`searched` lists the scopes an answer came from, so the reader knows what wasn't looked at. Claims from other witnesses cite their source: an XLD file and line, or "ADMIN: J. Doe, 2026-10-09: host X is Billing".

**Confidence (Open):** today every fact is 1.0 except inferred flows (0.5, D74). The plan is levels by how a fact was found (a literal match, an inferred type, a heuristic, an inferred trigger at 0.5), and agreement between witnesses. A low-confidence fact must never look as certain as a deterministic one.

## 3. Agreement between witnesses

When two independent witnesses answer the same question (see [Witnesses and facets](./witnesses-and-facets.md)), each fact gets a state (Decided):

| CODE | Other witness | State | Likely causes |
|---|---|---|---|
| ✓ | ✓ | **Confirmed** | Highest confidence |
| ✓ | ✗ | **Code only** | The declaration or platform config is missing or out of date. For a reviewer: "I see this in the code; why isn't it declared?" |
| ✗ | ✓ | **Declared only** | A rule gap (Steno missed it), a stale declaration (an unused grant, a dead dependency), or a name mismatch |

A disagreement means **look here**, not "the rules are wrong". Each one is triaged like a coverage item. The pattern across many says which: many in one direction for one framework means a missing rule; scattered across applications means stale declarations.

**Independence matters.** A source generated from the code (an OpenAPI spec built from the controllers) always agrees with it and confirms nothing. Packs mark generated sources so they don't count as a second witness.

## 4. Every signal that something is incomplete

| Signal | Meaning | Where it shows |
|---|---|---|
| **Unresolved calls** | A flow stopped at a call no resolver could follow (D71) | The flow (`incomplete`), `get_flow`, coverage report |
| **Unjoined sends** | A call or publish with no known receiver | Coverage report; impact answers |
| **Orphan receivers** | An endpoint or consumer nothing sends to: genuinely unused, or a sender was missed. Public endpoints (marked by Routing) are exempt. | Coverage report |
| **Disagreements** | Code-only or declared-only facts | Agreement view; answers |
| **Unmatched names** | A name from another source that matches no application | Identity review list |
| **Unknown triggers** | Work nothing in the code starts (D74) | Coverage report |
| **Stale witnesses** | A source that failed or whose data is old | Answers ("XLD as of …"); setup |

**The join rate** is the share of sends that join a known receiver (or a known external system). It's the main number for how connected an organization's graph is: the value of cross-service answers grows with it. Orphan receivers catch misses from the other side, without knowing in advance what was missed.

### The ledger (D101)

Steno models the **map**: what starts work, what work touches, the route between them, and the joins across applications. It can't promise every map fact is found, but it can promise nothing that could change the map is silently ignored: every I/O-capable library call and every framework hook is explained by a rule, declared harmless by a pack, or shown as a gap: a stop on the flow, an unknown hook on its application, a coverage item. Everything below the map (how a function computes its result) is code detail, read from the code Steno points to.

## 5. What an agent sees

Impact and dependents answers carry a **completeness block** (Proposed; `POC` stage A), so an incomplete answer never looks complete:

```
find_dependents(payment.settled, downstream)

  Reconciliation · Finance   confirmed     code + XLD
  Ledger · Finance           declared only XLD grant; no consumer found in code
  Audit Trail · Risk         code only     not in XLD

  Incomplete: 1 publish in Fraud Screening may target this topic
  (topic name comes from an unresolved config value).
  XLD as of 2026-10-09 02:10. Searched: Finance, Risk, Payments.
```

## 6. How accuracy is measured

| Measure | What it checks | Status |
|---|---|---|
| **Question set** | 10–15 questions per target, drawn from the [lead use cases](../purpose/use-cases.md#lead-use-cases-in-detail), with expected answers written before asking: find a flow, dependents, how to trigger, scheduled work, where code lives. Scored on correctness, tool calls (≤ 3), and honesty about gaps. | `POC` stage A |
| **Comparison without Steno** | The same questions answered by an agent with the repositories checked out and no Steno | `POC` stage A, then each stage |
| **Gold set** | Hand-labeled endpoints, topics, outbound calls, data access, and key flows for one application; precision and recall per fact type | `POC` |
| **Known-graph check** | The stub organization is written by us, so its correct graph is known exactly | `POC` stage B |
| **Idempotency** | Re-ingesting the same commit writes nothing and adds no `fact_change` rows; a fresh ingest equals a nightly run at the same commit | Built |
| **Join rate, agreement counts** | How connected the graph is; how often witnesses agree | `POC` stages B–D |
| **Decision calibration** | Predicted confidence vs. actual accuracy, once a decision model is in use | `v2` |

The exit criteria built on these are in the [roadmap](../plan/roadmap.md#3-poc-exit-criteria).

## 7. What static analysis can't see

Behavior that leaves no trace in code or config (reflection, routing chosen entirely by runtime data, a customer's own Jira host stored in a database row) can be marked unknown but not filled in. PLATFORM witnesses close part of that gap; runtime evidence (OBSERVED, `v2`) closes most of the rest.
