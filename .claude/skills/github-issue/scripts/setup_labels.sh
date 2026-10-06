#!/usr/bin/env bash
# Create (or update) Steno's issue labels and phase milestones in the current GitHub repo.
# Safe to run again: labels are upserted with --force, existing milestones are left alone.
#   .claude/skills/github-issue/scripts/setup_labels.sh
set -euo pipefail

label() { gh label create "$1" --color "$2" --description "$3" --force >/dev/null && echo "label  $1"; }

# type: what kind of work
label "type:feature" 1d76db "New capability"
label "type:bug"     d73a4a "Something is wrong"
label "type:chore"   cfd3d7 "Maintenance, refactoring, tooling; no behavior change"
label "type:docs"    0075ca "Documentation only"
label "type:design"  5319e7 "A design decision to make and record in docs/"
label "type:spike"   fbca04 "Time-boxed research that ends in a recommendation"
label "type:epic"    3e4b9e "A larger goal tracked through sub-issues"

# area: which part of Steno
for a in \
  "ingestion:Pipeline stages, job queue, worker" \
  "extractors:Rule engine, rule packs, assemblers" \
  "resolver:Symbol and DI resolution (symbol resolvers: JVM helper, Python)" \
  "graph:Neo4j schema, stable IDs, projection, graph writes" \
  "db:Postgres models and migrations" \
  "mcp:MCP server and tools" \
  "api:Admin API" \
  "ui:Frontend" \
  "llm:Cards, LLM and decision backends (Jev and stand-ins)" \
  "infra:Docker, Makefile, CI, dev tooling" \
  "docs:Design docs and READMEs"; do
  label "area:${a%%:*}" c5def5 "${a#*:}"
done

# priority: how soon
label "priority:p0" b60205 "Urgent: blocks current work or breaks the main path"
label "priority:p1" d93f0b "Needed for the current phase's exit criteria"
label "priority:p2" fbca04 "Should do soon; improves the current phase"
label "priority:p3" c2e0c6 "Nice to have; later"

# effort: rough size for one person
label "effort:xs" e6f4ea "Under an hour"
label "effort:s"  c2e0c6 "Up to half a day"
label "effort:m"  a2d5ab "One to two days"
label "effort:l"  6fbf73 "Three to five days"
label "effort:xl" 2da44e "More than a week: split it"

# state
label "needs-decision" 7057ff "Depends on a Proposed or Open design item; the owner must decide first"
label "blocked"        000000 "Waiting on another issue or something outside the repo"

# milestones: the roadmap phases (DESIGN_DOC §16)
existing=$(gh api "repos/{owner}/{repo}/milestones?state=all&per_page=100" --jq '.[].title')
for m in "Phase 1: Application" "Phase 2: Space" "Phase 3: Organization" "Incremental updates" "Later"; do
  if grep -qxF "$m" <<<"$existing"; then
    echo "milestone $m (exists)"
  else
    gh api "repos/{owner}/{repo}/milestones" -f title="$m" >/dev/null && echo "milestone $m"
  fi
done
