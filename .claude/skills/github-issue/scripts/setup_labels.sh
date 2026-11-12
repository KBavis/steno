#!/usr/bin/env bash
# Create (or update) Steno's issue labels and milestones in the current GitHub repo.
# Safe to run again: labels are upserted with --force, existing milestones are left alone.
#   .claude/skills/github-issue/scripts/setup_labels.sh
set -euo pipefail

label() { gh label create "$1" --color "$2" --description "$3" --force >/dev/null && echo "label  $1"; }

# type: what kind of work
label "type:feature"  1d76db "New capability"
label "type:bug"      d73a4a "Something is wrong"
label "type:refactor" 5319e7 "Restructure or remove code without changing behavior"
label "type:docs"     0075ca "Documentation only"
label "type:design"   7057ff "Decide, don't build: alignment threads and design questions"

# effort: rough size for one person
label "effort:s" c2e0c6 "Up to half a day"
label "effort:m" a2d5ab "One to two days"
label "effort:l" 6fbf73 "Three to five days"

# milestones
existing=$(gh api "repos/{owner}/{repo}/milestones?state=all&per_page=100" --jq '.[].title')
for m in "Redesign" "v1" "v2"; do
  if grep -qxF "$m" <<<"$existing"; then
    echo "milestone $m (exists)"
  else
    gh api "repos/{owner}/{repo}/milestones" -f title="$m" >/dev/null && echo "milestone $m"
  fi
done
