---
name: github-issue
description: Create well-formed GitHub issues for Steno from a plain-language request, such as "make an issue for the clone stage" or "break the rule engine into issues". Grounds each issue in the design docs and code, checks for duplicates, assigns a type and effort label and a milestone, adds the issue to the Steno Project, writes a structured body with acceptance criteria, links dependencies and sub-issues natively, and shows a draft for approval before creating anything. Use whenever someone asks to file, draft, or plan issues, a bug report, or an epic.
---

# GitHub issue

You turn a request into one or more GitHub issues on the Steno repository that someone could pick up and finish without asking questions. Creating an issue is outward-facing, so **always show the draft and wait for approval before creating it.**

## 0. Preflight

1. `gh --version` and `gh auth status`. If `gh` is missing or not logged in, stop and tell the person: install the GitHub CLI (https://cli.github.com, e.g. `sudo apt install gh` on Ubuntu/WSL), then run `gh auth login`.
2. `gh label list --limit 100`. If the `type:` and `effort:` labels below are missing, offer to run the one-time setup, and run it only after the person says yes:
   ```sh
   .claude/skills/github-issue/scripts/setup_labels.sh
   ```
   It creates every label below plus the milestones, and is safe to re-run.
3. Every issue goes on the **Steno Project** (https://github.com/users/KBavis/projects/5). If `gh project list --owner KBavis` fails for missing scopes, tell the person to run `gh auth refresh -s project,read:project`.

## 1. Understand and ground the request

- Work out **what** should change and **why**. Decide whether it's **one issue or several**. Anything larger than `effort:l` becomes an **epic**: a parent issue with sub-issues.
- **Read before writing.** Check `docs/plan/decisions.md`, `docs/plan/roadmap.md`, and the relevant page under `docs/` and code, so the issue cites real decisions (`D40`), doc sections, and files (`backend/src/steno/ingestion/pipeline.py`). During the redesign, also check `docs/POC/` (local notes: `plan.md`, `categories.md`, and agreed decisions in `threads/`).
- **Design status matters.** If the work depends on a design point that isn't agreed yet, don't file it as buildable work: file a `type:design` issue for the question instead, or note the dependency on one. Design choices belong to the repository owner; an issue never decides them.
- **Check for duplicates:** `gh issue list --state all --search "<keywords>"`. If a match exists, show it and ask whether to update it, link to it, or create a new one anyway.

## 2. Classify

Keep it small: exactly one **type**, one **effort** (except on epics), a **milestone**, and a **parent epic** when one fits. Explain the effort in one line in the draft so the person can push back.

| Type | Use for |
|---|---|
| `type:feature` | New capability |
| `type:bug` | Something behaves wrong |
| `type:refactor` | Restructure or remove code, tooling, upgrades; no behavior change |
| `type:docs` | Documentation only |
| `type:design` | Decide, don't build: an alignment thread or a design question. Ends with the decision recorded |

| Effort | Rough size for one person |
|---|---|
| `effort:s` | Up to half a day |
| `effort:m` | One to two days |
| `effort:l` | Three to five days. **Anything bigger isn't filed; split it into an epic.** |

**Epics** are parent issues with sub-issues, not a label. An epic carries the type of most of its work and no effort. Candidate epics live in `docs/POC/categories.md` until agreed; ask before creating a new epic.

**Milestones:** `Redesign` (alignment threads, the docs rewrite, cleanup), then the POC milestones agreed in thread T1, `v1`, and `v2`. Run `gh api 'repos/{owner}/{repo}/milestones' --jq '.[].title'` for the current list.

**Blocked work:** use native *blocked by* links (§5), not a label. Priority isn't a label either: the person orders the Project's views by hand.

## 3. Write the issue

**Title:** brief and in Title Case: an imperative summary, ideally under 50 characters and never over 70, with no type or area prefix. Capitalize the main words; keep short articles, conjunctions, and prepositions lowercase unless they come first. *"Implement the Clone Stage for Dry Runs"*, not *"[Ingestion] Clone stage"* or *"Implement the clone stage so that dry runs can record clone time"*.

**Body:** use the template for the type. Keep every section concrete; leave a section out rather than writing "N/A".

### Feature, refactor, docs

```markdown
## Summary
One or two sentences: what changes, for whom.

## Why
The problem or goal. Link the design: [D40](docs/plan/decisions.md), [rules §4](docs/how-it-works/ingestion/rules-and-packs.md#4-clues-decided).

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

### Epic (a parent issue)

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
Title:      Implement the Clone Stage for Dry Runs
Labels:     type:feature, effort:m
Milestone:  Redesign
Links:      blocked by #12 · sub-issue of #20
Why m:      one new stage plus tests
<body>
```

For several issues, show them all, plus the dependency order. Create nothing until the person approves; apply their edits first.

## 5. Create and link

1. Write each body to a temporary file, then create:
   ```sh
   gh issue create --title "<title>" --body-file <file> \
     --label "type:feature,effort:m" \
     --milestone "Redesign"
   gh project item-add 5 --owner KBavis --url <issue url>
   ```
   Don't use `gh issue create --project`: it looks up classic Projects and fails. Note the number in the URL it prints. For an epic, create the epic first, then its sub-issues.
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
- Don't decide design questions in an issue; frame them as `type:design`.
- One issue, one outcome. If the acceptance criteria describe two independent results, split it.
