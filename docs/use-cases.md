# Use Cases

What Steno is for: the value of having a continuously updated, layered model of an organization's architecture, especially once agents are the ones using it. How it works is covered in the [Design Doc](./DESIGN_DOC.md).

---

## The core thesis

Today's AI coding agents are highly capable within a single repository, and largely blind beyond it. They don't know what else exists, which patterns are already established, or what depends on the code they're changing. That blindness is a ceiling on agentic software development, no matter how good the model gets.

**Steno removes that ceiling.** It turns a repo-scoped agent into an org-scoped one, by making "what exists, how it fits together, and why" something you can query instead of something that lives in scattered docs and people's heads.

**Why it's worth the investment.** Engineers are used to working within their own small domain. As agents get more context, **one engineer with an agent can have impact at organization scale**: changing things across services and spaces while keeping, not breaking, the patterns that have been in place for years. That only works if the agent can see the whole organization.

## What it gives an organization

1. **Tribal knowledge stops decaying.** "Why is it built this way?" lives in senior engineers' heads and is lost when they leave. A living graph, and later project deltas from Contextualized, make every project add to what's known instead.
2. **Discovery is paid for once.** Every new hire, contractor, and fresh agent session re-derives "how does this system fit together?" from scratch. A shared graph pays that cost once.
3. **Senior judgment scales.** Checks like "this breaks something downstream," "this duplicates something that exists," or "this violates a pattern" run on every PR, without waiting for the few people who hold the context.
4. **Norms become automatic.** An agent can see "this crosses a space boundary, so it needs an integration test" without anyone telling it.
5. **Transitions get cheap.** Onboarding, reorgs, and contractor handoffs no longer depend on a rushed write-up that's stale the day it's delivered.

---

## Use cases

What each one needs:

| Tag | Needs |
|---|---|
| **V1** | The static graph only (Phase 1–3) |
| **Δ** | The delta store (before/after of changed facts) |
| **V2** | Data store schema detail |
| **RT** | Runtime signals |
| **Ctx** | Contextualized project deltas |

★ = lead use cases: V1-only, no strong existing tool, easy to demonstrate.

### Agentic development

| # | Use case | Needs | What it enables |
|---|---|---|---|
| 1 ★ | **PR review with context** | V1 | A reviewer agent gets the flows that pass through the changed functions, their downstream consumers, and any contract changes, instead of reviewing the diff in isolation. |
| 2 ★ | **Breaking-change check in CI** | V1 | "This PR changes an Entity published on `order.created`, which 3 services in 2 spaces consume." Warn, or fail the build. |
| 3 | **Coordinated changes across repos** | V1 | An agent changing a contract knows every consumer to update and **the order to deploy** (producer or consumer first). |
| 4 | **Integration guides** | V1 | "How do I call X?", answered from the interface, its request/response entities, how it's triggered, and existing callers as examples. |
| 5 | **Guided onboarding** | V1 | A generated walkthrough of a space: its purpose, key flows, ins and outs, and glossary. It's a living document instead of one written once. |
| 6 | **Feature implementation with org awareness** | V1 | An agent building a feature knows which services to integrate with, where similar endpoints already live, and which conventions the space follows. |

### Planning and design

| # | Use case | Needs | What it enables |
|---|---|---|---|
| 7 ★ | **"Grill me" project planning** | V1 | A dev manager describes a project. An agent finds the flows, interfaces, consumers, and spaces it will likely touch, then asks the questions the plan hasn't answered. "You're changing `user.created`. Four services in two other spaces consume it. Who's coordinating with them?" |
| 8 | **"Does this already exist?"** | V1 | Search cards and flows for an existing capability before building one. Catches duplicate work early. |
| 9 | **Sizing and coordination** | V1 | How many flows, interfaces, spaces, and teams a change touches: a rough complexity signal, and a list of who needs to be involved. |
| 10 | **Where should this live?** | V1 | Which space owns the concept, and where similar code already lives. |
| 11 | **Deprecation planning** | V1 | Can this endpoint or topic be turned off? Who still calls it? Topics that are produced but never consumed, and the reverse. |
| 12 | **Decomposition / migration planning** | V1 | Coupling and clustering across modules (community detection) show what can be extracted cleanly. |

### Testing

| # | Use case | Needs | What it enables |
|---|---|---|---|
| 13 ★ | **Delta-driven testing** | Δ (+ Ctx for attribution) | Project A changed X. The agent sees the **before and after**, and **how to start the flow**: an HTTP method and path with the request entity, a topic with its message schema, or a schedule. It can then generate the request or message and its assertions. |
| 14 | **Choosing what to regression-test** | V1 | After a change, walk the flows downstream to find which flows to re-run, across services. |
| 15 | **Contract tests** | V1 | Every producer/consumer and caller/endpoint pair is an edge, so each pair can get a generated contract test. |
| 16 | **Test data setup** | V1 (V2 for columns) | The tables a flow reads and writes say what to seed and what to clean up. |

### Operations and incidents

| # | Use case | Needs | What it enables |
|---|---|---|---|
| 17 ★ | **From a stack trace to a flow** | V1 | Stack frames are function symbols, and function symbols are nodes. A Splunk error maps straight to its flow, trigger, and upstream callers. |
| 18 | **Blast radius during an incident** | V1 | Service X is failing: who calls it, which flows break, which business processes are affected. |
| 19 | **Runbooks and replay** | V1 | How to re-trigger a failed flow, e.g. which topic and message to republish. |
| 20 | **Who owns this?** | V1 | Route an alert to the owning space or team from the failing function. |

### Security and compliance

| # | Use case | Needs | What it enables |
|---|---|---|---|
| 21 | **Reachability for CVEs** | V1+ | A vulnerable library matters only if a flow reaches the vulnerable function. Cuts false positives. Needs calls to third-party symbols recorded as references. |
| 22 | **ACL drift** | V1 + XLDeploy | Grants compared with actual produce/consume in code: unused grants to remove, and usage nobody declared. |
| 23 | **Third-party inventory** | V1 | Every `ExternalSystem` call and the flows that make it, for vendor risk reviews. |
| 24 | **PII and data lineage** | V2 | Which flows read a sensitive column, and where the data goes next. |

### Architecture health

| # | Use case | Needs | What it enables |
|---|---|---|---|
| 25 | **Governance checks** | V1 | Another space's database accessed directly, dependency cycles between services, sync call chains that are too deep, single points of failure with very high fan-in. |
| 26 | **Unused code** | V1 | Endpoints with no callers, and functions nothing reaches. |

---

## Scope boundary

Steno supplies **grounded, current, attributable context** through MCP. The generators and automations above (test generators, CI bots, planning workflows, incident copilots) are **built on top of** that context by agents and workflows. They aren't features inside Steno. That keeps Steno from turning into "every SDLC tool."

## Illustrative scenarios

- **A dev manager planning a project:** runs a "grill me" session against Steno before the project starts, and comes out with the affected flows, the teams to involve, and the open questions.
- **An engineer changing a shared topic:** CI flags the three consumers, an agent drafts the consumer changes in the right order, and the reviewer agent sees the stitched flow.
- **On call at 2 a.m.:** a stack trace from Splunk maps to a flow, its trigger, and everything upstream in one call.
- **A new hire or contractor:** has a guided onboarding tour of their space on day one, generated from the living graph.

## Open questions

- **Access control:** a contractor should see less than a full-time employee. How are scopes enforced on MCP results?
- **Showing confidence:** a low-confidence, Jev-resolved edge must never look as certain as a deterministic one.
- **Positioning next to existing tools** (e.g. Backstage): those catalog what people *declare*. Steno captures what's *actually in the code*.
