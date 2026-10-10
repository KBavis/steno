# Use cases

The questions Steno has to answer well, starting with the five that set the bar. Part of [Purpose](./README.md); how it works is in [How it works](../how-it-works/README.md).

> **In one minute**
> - Five **lead use cases** (★) need only the static graph, have no strong existing tool, and are easy to demonstrate: PR review with context, breaking-change checks, "grill me" planning, delta-driven testing, and stack trace to flow.
> - Each lead use case below lists the **questions** it asks, the **MCP tools** that answer them (built or planned), and **what a correct answer contains**. The POC's question sets are drawn from these.
> - **Scope test:** a feature belongs in Steno only if a use case needs it (D98). The tools that act on the answers (review bots, test generators, planning workflows) are built on top, not inside Steno.

**MCP tools today:** `search`, `get_node`, `get_flow`, `view_flow_code`, `find_dependents`. **Planned:** `analyze_change`, `get_interface`, `find_symbols`, `get_changes`, `find_paths`, `search_code`. See [Retrieval and MCP](../how-it-works/retrieval-and-mcp.md#5-mcp-tools-decided).

---

## Lead use cases in detail

### 1 ★ PR review with context

A reviewer (person or agent) sees what a change touches beyond its diff.

| | |
|---|---|
| **Questions** | Which flows run through the functions this PR changes? Which interfaces or payloads change? Who consumes them, in which spaces? Is any of that incomplete? |
| **Tools** | Today: `search` (function names find their flows), `get_flow`, `find_dependents`. Planned: `analyze_change(pr)` does it in one call, including related PRs. |
| **A correct answer contains** | Every affected flow (with the changed steps cited), every changed interface, every downstream consumer grouped by application and space, and what Steno couldn't see (unresolved calls, unjoined sends). |
| **Needs** | Graph; Δ for "before and after" |
| **Testable from** | POC stage B (consumers in other applications) |

### 2 ★ Breaking-change check in CI

"This PR changes the payload published on `order.created`, which 3 services in 2 spaces consume." Warn, or fail the build.

| | |
|---|---|
| **Questions** | Does this change alter a contract (endpoint, topic payload, table)? Who depends on it? In what order should producer and consumers deploy? |
| **Tools** | Today: `find_dependents` on the interface. Planned: `analyze_change` (contract diff, consumers, deploy order). |
| **A correct answer contains** | The contract change, every consumer (confirmed or code-only), consumers covered by related PRs and those that aren't, a deploy order, and the completeness note. |
| **Needs** | Graph; `USES_ENTITY` (payload contracts, not built yet) |
| **Testable from** | POC stage B |

### 7 ★ "Grill me" planning

A dev manager or lead describes a body of work; an agent finds what it will likely touch and asks the questions the plan hasn't answered: "You're changing `user.created`. Four services in two other spaces consume it. Who's coordinating with them?"

| | |
|---|---|
| **Questions** | Which spaces, applications, and flows does this work touch? What's downstream of each? Does something like it already exist? How many teams and spaces are involved (a rough size)? What has no owner or no clear answer? |
| **Tools** | Today: `search`, `get_node` (an application's or space's ins and outs), `find_dependents`. Later: card-based search for business-language questions. |
| **A correct answer contains** | The touched flows and interfaces with their consumers, the spaces involved, existing similar capabilities, and explicit unknowns. |
| **Needs** | Graph (Ownership in `v2` for "which team") |
| **Testable from** | POC stage A (one application), stronger from stage B |

### 13 ★ Delta-driven testing, and understanding how to exercise a flow

A tester or test agent needs to know how a flow starts, what it touches, and what changed.

| | |
|---|---|
| **Questions** | What triggers this flow, and with what input (method and path with the request entity, a topic and its message, a schedule)? What does it read and write, and call? What changed in it (before and after)? What's downstream to regression-test? How does this team already test similar flows? |
| **Tools** | Today: `get_flow` (trigger, steps, effects), `view_flow_code`, `find_dependents` (downstream). Planned: `get_interface` (contract, entities, example callers), `get_changes` (before and after). |
| **A correct answer contains** | The trigger and its input shape, every effect (tables, topics, calls), the downstream flows, the change, and the existing tests or conventions to copy (test conventions come from a standards source outside Steno). |
| **Needs** | Graph; Δ; Projects for attribution |
| **Testable from** | POC stage A (triggers and effects), stage B (downstream) |

### 17 ★ From a stack trace to a flow

On call at 2 a.m.: a stack trace maps to its flow, trigger, and upstream callers in one call.

| | |
|---|---|
| **Questions** | Which flow does this frame belong to? What triggers it? Who calls it, and what's affected downstream? |
| **Tools** | Today: `search` on a function name (matches flow traces), then `get_flow`, `find_dependents`. Planned: `find_symbols(stack_trace)`. |
| **A correct answer contains** | The flows whose traces contain the frames, their triggers, upstream callers and downstream effects, and, once Environments exist (`v2`), whether production runs the same commit. |
| **Needs** | Graph |
| **Testable from** | POC stage A |

## The full catalog

What each one needs:

| Tag | Needs |
|---|---|
| **Graph** | The static graph only (the POC) |
| **Δ** | The delta store (before/after of changed facts) |
| **Schemas** | Data store schema detail (Data V2, `v2`) |
| **Runtime** | Runtime evidence (OBSERVED, `v2`) |
| **Projects** | Contextualized project deltas |

★ = lead use cases: need only the graph, no strong existing tool, easy to demonstrate.

### Agentic development

| # | Use case | Needs | What it enables |
|---|---|---|---|
| 1 ★ | **PR review with context** | Graph | A reviewer agent gets the flows that pass through the changed functions, their downstream consumers, and any contract changes, instead of reviewing the diff in isolation. |
| 2 ★ | **Breaking-change check in CI** | Graph | "This PR changes an Entity published on `order.created`, which 3 services in 2 spaces consume." Warn, or fail the build. |
| 3 | **Coordinated changes across repos** | Graph | An agent changing a contract knows every consumer to update and **the order to deploy** (producer or consumer first). |
| 4 | **Integration guides** | Graph | "How do I call X?", answered from the interface, its request/response entities, how it's triggered, and existing callers as examples. |
| 5 | **Guided onboarding** | Graph | A generated walkthrough of a space: its purpose, key flows, ins and outs, and glossary. It's a living document instead of one written once. |
| 6 | **Feature implementation with org awareness** | Graph | An agent building a feature knows which services to integrate with, where similar endpoints already live, and which conventions the space follows. |

### Planning and design

| # | Use case | Needs | What it enables |
|---|---|---|---|
| 7 ★ | **"Grill me" project planning** | Graph | A dev manager describes a project. An agent finds the flows, interfaces, consumers, and spaces it will likely touch, then asks the questions the plan hasn't answered. "You're changing `user.created`. Four services in two other spaces consume it. Who's coordinating with them?" |
| 8 | **"Does this already exist?"** | Graph | Search cards and flows for an existing capability before building one. Catches duplicate work early. |
| 9 | **Sizing and coordination** | Graph | How many flows, interfaces, spaces, and teams a change touches: a rough complexity signal, and a list of who needs to be involved. |
| 10 | **Where should this live?** | Graph | Which space owns the concept, and where similar code already lives. |
| 11 | **Deprecation planning** | Graph | Can this endpoint or topic be turned off? Who still calls it? Topics that are produced but never consumed, and the reverse. |
| 12 | **Decomposition / migration planning** | Graph | Coupling and clustering across modules (community detection) show what can be extracted cleanly. |

### Testing

| # | Use case | Needs | What it enables |
|---|---|---|---|
| 13 ★ | **Delta-driven testing** | Δ (+ Projects for attribution) | Project A changed X. The agent sees the **before and after**, and **how to start the flow**: an HTTP method and path with the request entity, a topic with its message schema, or a schedule. It can then generate the request or message and its assertions. |
| 14 | **Choosing what to regression-test** | Graph | After a change, walk the flows downstream to find which flows to re-run, across services. |
| 15 | **Contract tests** | Graph | Every producer/consumer and caller/endpoint pair is an edge, so each pair can get a generated contract test. |
| 16 | **Test data setup** | Graph (Schemas for columns) | The tables a flow reads and writes say what to seed and what to clean up. |

### Operations and incidents

| # | Use case | Needs | What it enables |
|---|---|---|---|
| 17 ★ | **From a stack trace to a flow** | Graph | Stack frames are function symbols, and every flow's trace lists the function symbols it runs. A Splunk error maps straight to its flow, trigger, and upstream callers. |
| 18 | **Blast radius during an incident** | Graph | Service X is failing: who calls it, which flows break, which business processes are affected. |
| 19 | **Runbooks and replay** | Graph | How to re-trigger a failed flow, e.g. which topic and message to republish. |
| 20 | **Who owns this?** | Graph (+ Ownership, `v2`) | Route an alert to the owning space or team from the failing function. |

### Security and compliance

| # | Use case | Needs | What it enables |
|---|---|---|---|
| 21 | **Reachability for CVEs** | Graph+ | A vulnerable library matters only if a flow reaches the vulnerable function. Cuts false positives. Needs calls to third-party symbols recorded as references. |
| 22 | **ACL drift** | Graph + a PLATFORM provider (ACL definitions) | Grants compared with actual produce/consume in code: unused grants to remove, and usage nobody declared. |
| 23 | **Third-party inventory** | Graph | Every `ExternalSystem` call and the flows that make it, for vendor risk reviews. |
| 24 | **PII and data lineage** | Schemas | Which flows read a sensitive column, and where the data goes next. |

### Architecture health

| # | Use case | Needs | What it enables |
|---|---|---|---|
| 25 | **Governance checks** | Graph | Another space's database accessed directly, dependency cycles between services, sync call chains that are too deep, single points of failure with very high fan-in. |
| 26 | **Unused code** | Graph | Endpoints with no callers, and functions nothing reaches. |

---

## Scope boundary

Steno supplies **grounded, current, attributable context** through MCP. The generators and automations above (test generators, CI bots, planning workflows, incident copilots) are **built on top of** that context by agents and workflows. They aren't features inside Steno. That keeps Steno from turning into "every SDLC tool." How those tools combine into an agent-first SDLC is designed in a separate project (D98).

## Illustrative scenarios

- **A dev manager planning a project:** runs a "grill me" session against Steno before the project starts, and comes out with the affected flows, the teams to involve, and the open questions.
- **An engineer changing a shared topic:** CI flags the three consumers, an agent drafts the consumer changes in the right order, and the reviewer agent sees the stitched flow.
- **On call at 2 a.m.:** a stack trace from Splunk maps to a flow, its trigger, and everything upstream in one call.
- **A new hire or contractor:** has a guided onboarding tour of their space on day one, generated from the living graph.

## Open questions

- **Access control:** a contractor should see less than a full-time employee. How are scopes enforced on MCP results?
- **Showing confidence:** a low-confidence or inferred edge must never look as certain as a deterministic one.
- **Positioning next to catalogs** (Backstage, Cortex): settled by D96. Catalogs record what people *declare*; Steno captures what's *actually in the code*, and reads catalogs as providers.
