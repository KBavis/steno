---
name: github-issue
description: Create well-formed GitHub issues for Steno from a plain-language request, such as "make an issue for the clone stage" or "break the extractor engine into issues". Grounds each issue in the design docs and code, checks for duplicates, assigns type, area, priority, and effort labels and a phase milestone, writes a structured body with acceptance criteria, links dependencies and sub-issues natively, and shows a draft for approval before creating anything. Use whenever someone asks to file, draft, or plan issues, a bug report, or an epic.
---

# GitHub issue

You turn a request into one or more GitHub issues on the Steno repository that someone could pick up and finish without asking questions. Creating an issue is outward-facing, so **always show the draft and wait for approval before creating it.**

## 0. Preflight

1. `gh --version` and `gh auth status`. If `gh` is missing or not logged in, stop and tell the person: install the GitHub CLI (https://cli.github.com, e.g. `sudo apt install gh` on Ubuntu/WSL), then run `gh auth login`.
2. `gh label list --limit 100`. If the `type:`, `area:`, `priority:`, and `effort:` labels below are missing, offer to run the one-time setup, and run it only after the person says yes:
   ```sh
   .claude/skills/github-issue/scripts/setup_labels.sh
   ```
   It creates every label below plus the phase milestones, and is safe to re-run.

## 1. Understand and ground the request

- Work out **what** should change and **why**. Decide whether it's **one issue or several**. Anything larger than `effort:l` becomes an **epic** with sub-issues.
- **Read before writing.** Check `docs/DESIGN_DOC.md` (decision log §20, open questions §21) and the relevant sub-doc and code, so the issue cites real decisions (`D40`), doc sections, and files (`backend/src/steno/ingestion/pipeline.py`).
- **Design status matters.** If the work depends on something marked **Proposed** or **Open**, or would change a **Decided** item, label it `needs-decision` and add a *Decision needed* section. Design choices belong to the repository owner; an issue never decides them.
- **Check for duplicates:** `gh issue list --state all --search "<keywords>"`. If a match exists, show it and ask whether to update it, link to it, or create a new one anyway.

## 2. Classify

Pick exactly one **type**, one or two **areas**, one **priority**, and one **effort**, plus a **milestone**. Explain the priority and effort in one line each in the draft so the person can push back.

| Type | Use for |
|---|---|
| `type:feature` | New capability |
| `type:bug` | Something behaves wrong |
| `type:chore` | Refactoring, tooling, upgrades; no behavior change |
| `type:docs` | Documentation only |
| `type:design` | A decision to make and record in `docs/` (ends with a decision-log entry) |
| `type:spike` | Time-boxed research that ends in a recommendation, not code |
| `type:epic` | A larger goal tracked through sub-issues |

| Area | Covers |
|---|---|
| `area:ingestion` | Pipeline stages, job queue, worker |
| `area:extractors` | Rule engine, rule packs, clue resolvers |
| `area:resolver` | Symbol and DI resolution (JVM helper, Python resolver) |
| `area:graph` | Neo4j schema, stable IDs, projection, graph writes |
| `area:db` | Postgres models and migrations |
| `area:mcp` | MCP server and tools |
| `area:api` | Admin API |
| `area:ui` | Frontend |
| `area:llm` | Cards, LLM and decision backends (Jev and stand-ins) |
| `area:infra` | Docker, Makefile, CI, dev tooling |
| `area:docs` | Design docs and READMEs |

| Priority | Meaning |
|---|---|
| `priority:p0` | Urgent: blocks current work or breaks the main path |
| `priority:p1` | Needed for the current phase's exit criteria (DESIGN_DOC §16) |
| `priority:p2` | Should do soon; improves the current phase |
| `priority:p3` | Nice to have; later |

| Effort | Rough size for one person |
|---|---|
| `effort:xs` | Under an hour |
| `effort:s` | Up to half a day |
| `effort:m` | One to two days |
| `effort:l` | Three to five days |
| `effort:xl` | More than a week. **Don't file it; split it into an epic.** |

**Milestones** are the roadmap phases: `Phase 1: Application`, `Phase 2: Space`, `Phase 3: Organization`, `Incremental updates`, `Later`.

**State labels:** `needs-decision` (see §1) and `blocked` (waiting on something *outside* the repo; for other issues, use dependency links instead).

## 3. Write the issue

**Title:** an imperative summary, 70 characters or fewer, no type or area prefix (labels carry those). *"Implement the clone stage for dry runs"*, not *"[Ingestion] Clone stage"*.

**Body:** use the template for the type. Keep every section concrete; leave a section out rather than writing "N/A".

### Feature, chore, docs

```markdown
## Summary
One or two sentences: what changes, for whom.

## Why
The problem or goal. Link the design: [DESIGN_DOC D40](docs/DESIGN_DOC.md), [extractor-rules §4](docs/extractor-rules.md#4-clues-decided).

## Scope
- [ ] Concrete task
- [ ] Concrete task

## Acceptance criteria
- [ ] Observable outcome someone can check, e.g. "a dry run of Contextualized records clone time in `ingestion_stage.metrics`"
- [ ] Tests or checks that pass

## Out of scope
What this deliberately leaves for later, with links to those issues.

## Dependencies
- Blocked by #12, because …
- Blocks #15

## Pointers
Files, functions, or docs to start from: `backend/src/steno/ingestion/pipeline.py` (`clone`).
```

### Bug

```markdown
## Summary
## Steps to reproduce
1.
## Expected
## Actual
Include exact error output in a code block.
## Environment
Commit, OS/WSL, how it was run (`make dev`, …).
## Suspected cause
Only if you have evidence; cite the file and line.
## Acceptance criteria
- [ ] The steps above produce the expected result
- [ ] A test covers it
```

### Design decision (`type:design`)

```markdown
## Question
The decision to make, in one sentence.
## Context
Why it matters now; what it blocks. Current doc status (Proposed / Open) with a link.
## Options
| Option | Pros | Cons |
|---|---|---|
## Recommendation
Optional, clearly marked as a recommendation.
## Done when
- [ ] The owner has decided
- [ ] The decision is recorded in `docs/` (decision log entry, affected sections updated)
```

### Spike (`type:spike`)

```markdown
## Question
## Time box
e.g. one day.
## What to try
## Output
A short write-up with a recommendation (comment on this issue, or a doc). Follow-up issues filed.
```

### Epic (`type:epic`)

```markdown
## Goal
The outcome, and how we'll know it's reached.
## Plan
Sub-issues are linked natively; list them here too, in order:
- [ ] #21 Clone stage
- [ ] #22 Rule engine core
## Out of scope
```

## 4. Show the draft and get approval

Present each issue as:

```
Title:      Implement the clone stage for dry runs
Labels:     type:feature, area:ingestion, priority:p1, effort:m
Milestone:  Phase 1: Application
Links:      blocked by #12 · sub-issue of #20
Why p1/m:   needed for the Phase 1 dry run; one new stage plus tests
<body>
```

For several issues, show them all, plus the dependency order. Create nothing until the person approves; apply their edits first.

## 5. Create and link

1. Write each body to a temporary file, then create:
   ```sh
   gh issue create --title "<title>" --body-file <file> \
     --label "type:feature,area:ingestion,priority:p1,effort:m" \
     --milestone "Phase 1: Application"
   ```
   Note the number in the URL it prints. For an epic, create the epic first, then its sub-issues.
2. Link with GitHub's native relationships:
   ```sh
   .claude/skills/github-issue/scripts/link_issues.sh blocked-by <issue> <blocker>
   .claude/skills/github-issue/scripts/link_issues.sh sub-issue  <epic> <child>
   ```
   Once all the issues exist, fill in the real numbers in any body that referred to them (`gh issue edit <n> --body-file <file>`). The *Dependencies* section stays in the body either way, so the links are readable even where native relationships don't show.
3. If linking fails (for example, the repository plan doesn't support it), say so; the written *Dependencies* section still records it.

## 6. Report back

List each created issue with its number, title, and URL, and the links made. Mention anything you assumed or that still needs the owner's decision.

## Guardrails

- Never create, edit, close, or label issues without the person's approval of the draft.
- Never put secrets, tokens, internal hostnames, or personal data in an issue. The repository may be public.
- Don't decide design questions in an issue; frame them with `type:design` or `needs-decision`.
- One issue, one outcome. If the acceptance criteria describe two independent results, split it.
