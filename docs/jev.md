# Jev: Decisions

How Steno uses [Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev) (TypeSafe's "System One" decision model) to make typed, calibrated decisions during ingestion and retrieval.

Part of the [Design Doc](./DESIGN_DOC.md). Status: **Decided** to use Jev. The individual decisions below are **Proposed**.

---

## 1. Why Jev is central

Navigating an organization's architecture is mostly **a series of choices made with uncertainty**: which space, which app, which flow, is this candidate really what was asked, which implementation does this call reach. With an LLM, each choice is a slow, expensive generation call whose confidence you can't trust. With Jev, each choice is:

- **typed**: the answer is always one of the allowed options, never free text to parse
- **calibrated**: a probability you can act on (auto-accept, flag, or ask a human)
- **fast and cheap**: 70–500 ms, $0.042 per million input tokens, output free, **many questions answered in one parallel pass**

That's what makes Steno's routing and verification affordable on every query, and it's Steno's main source of speed.

## 2. What Jev is, and its limits

- You send **state** (text or JSON) and **typed questions**. Jev returns typed answers with probabilities in one pass.
- Question types:
  - **Noul**: yes/no, returns a probability.
  - **Choice**: one of up to **255** options, returns the full distribution and a confidence.
  - **Score**: a position on a 2–10 level scale.
- **Input limits:** about **64k tokens shared across state and questions** per call, with any single question up to ~32k tokens. Text only.
- **Can't** generate text or extract strings. **Weak at** multi-step reasoning, counting, dates, contradictions, and **large irrelevant state**.
- Released 2026-09-15, so production evidence is thin. Its claims have to be checked on our own data.

## 3. Where Jev fits

| Work | Who | When |
|---|---|---|
| Extracting facts | Deterministic rules | Ingestion |
| **Decisions: classify, route, verify, gate, score** | **Jev** | **Ingestion and query time** |
| Writing cards, flow summaries, glossary definitions | LLM | Ingestion only |
| Reasoning over results, final answer | The client's LLM (Claude Code, Copilot) | Query time |

**Rule: deterministic first.** Jev decides only what rules can't. Where they meet: the LLM generates, Jev checks.

## 4. What Jev sees: state design

Jev performs best on **small, relevant state**, and a flow can be huge (hundreds of functions). So Steno **never sends raw flows or code to Jev**. It sends **digests**, built from the graph and bounded by construction:

| Digest | Contents | Size |
|---|---|---|
| **Space / app summary line** | Name + one-line purpose | ~30 tokens |
| **Flow digest** | Its trigger (method + path, topic, or schedule), its card (fixed size), and its **interactions list**: tables written/read, topics produced, endpoints called, entities used | ~300–600 tokens, **regardless of how many functions the flow has** |
| **Candidate implementation** | Class name, annotations, package, active profile | ~50 tokens |
| **Call site** | Enclosing method signature, the call expression, imports | ~150 tokens |

**Huge flows stay bounded** because the digest uses the flow's card and interactions, not its steps. A flow with 800 functions and a flow with 8 produce digests of similar size. If a single flow's interactions list is very long, it's truncated to the most significant interactions (writes before reads, cross-space before internal).

**Fitting the 64k budget:**
- Q1 across 200 spaces ≈ 200 × 30 tokens = ~6k tokens. One call.
- Q4 over 10 candidate flows ≈ 10 × 600 = ~6k tokens. One call.
- If a batch would exceed the budget, split it into several calls **run in parallel**.

**The card doubles as Jev's input**, which is one more reason flow cards must be precise, in business language, and fixed in size.

## 5. Confidence bands

Starting point, to be tuned against logged decisions:

| Confidence | Action |
|---|---|
| **> 0.9** | Act automatically |
| **0.5 – 0.9** | Act, and mark the result low-confidence |
| **< 0.5** | Don't decide: mark it `ambiguous`, return several options, or ask a human |

Every decision stores its confidence **on the resulting fact or result** and is logged in `jev_decision` (see [Ingestion §8](./ingestion.md#8-relational-database-postgres)).

## 6. Decisions at query time

These run inside the `search` tool (see [Retrieval](./retrieval-and-mcp.md#2-search-top-down-over-data-built-bottom-up)).

| # | Decision | Type | State | Result |
|---|---|---|---|---|
| **Q0** | **What kind of question is this?** | Choice: find a flow / what depends on X / what is X / how do I call X / how are A and B connected | The question | Narrows which node labels and retrieval signals are used |
| **Q1** | **Which spaces does it involve?** | **One Noul per space, in one call** | The question + a summary line per space | One space: search it. Several: fan out in parallel. None: search at the org layer. |
| **Q2** | **Which application within the space?** | Choice (apps in the space) | The question + a summary line per app | Jump straight to the app when confident |
| **Q2b** | **Which entity is the question about?** | Choice (candidate entities from keyword search) | The question + entity names and cards | Drives entity-anchor retrieval |
| **Q4** | **Does this candidate do what was asked?** | **One Noul per candidate**, batched | The question + each candidate's digest | A confidence for every candidate flow. This is how Steno "knows which one it is," or honestly says it isn't sure. |
| **Q3** | **How relevant is each result?** | Score | The question + each result's digest | Rank and trim to the `max_tokens` budget |

**Latency of one `search`:** Q0 and Q1 can run together, then Q2 / Q2b, then retrieval, then Q4 and Q3 together. That's **three rounds of Jev calls, ~0.2–1.5 s total**, compared with several seconds per step if an LLM made these decisions.

Routing layer by layer (Q1, then Q2) keeps every Choice under the 255-option limit at enterprise scale.

## 7. Decisions at ingestion

| # | Decision | Type | State | Runs only when… | Result |
|---|---|---|---|---|---|
| **I1** | Which implementation does this DI call resolve to? | Choice (candidates) | Call site + candidate digests + active config | The DI rules find more than one candidate | `INVOKES` to the choice, or to all of them, marked `ambiguous` |
| **I2** | Which transport does this outbound call use? | Choice (HTTP, gRPC, Kafka, internal framework(s), DB, other) | Call site digest | No extractor rule matched | The `transport` on `CALLS` |
| **I3** | Is this function an entry point? | Noul | Method, class, unrecognized annotations | An unrecognized framework looks like a trigger | A new `Flow`, or none |
| **I4** | Is this host inside the org or a vendor? | Noul | The host + known org domains | The host isn't on the known-domains list | A stub `Interface` or an `ExternalSystem` |
| **I5** | Does this change alter what the card says? | Noul | Old card + fact diff | Facts under an existing card changed | Regenerate the card or keep it. **Controls LLM spend.** |
| **I6** | Which space does this repository belong to? | Choice (spaces + "new") | Repo name, owners, communication clusters, space summaries | Onboarding (Phase 2+) | A proposal. **A human always confirms.** |
| **I7** | Which node does this glossary term refer to? | Choice (candidate nodes) | The term, where it appeared, candidate node summaries | A candidate term was found in docs, names, or queries | A proposed term → node mapping. **A human confirms.** |
| **I8** | Does this unexplained call look like I/O or an entry point? | Noul | Call site digest | A call site matched no rule | Whether it goes on the **coverage report** (see [Ingestion §4](./ingestion.md#4-extractors-rules-for-being-agnostic)) |

**Not a Jev decision:** "did this PR change structure?" Re-running the extractors and comparing facts answers it exactly.

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| New vendor, claims not yet proven | Every decision is logged. Calibration is measured on a labeled set before thresholds are trusted. |
| Weak with large or irrelevant state | Digests only, bounded by construction (§4) |
| Our org's data policy on sending code-derived state to a third-party API | Confirm before Phase 1. Digests contain names and summaries, rarely code. Check Cloudflare / LiteLLM availability. |
| Vendor lock-in | A small `Decision` interface in Steno, so an LLM with structured output can replace Jev for any single decision |
