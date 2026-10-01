#!/usr/bin/env bash
# Link issues with GitHub's native relationships.
#   link_issues.sh blocked-by <issue> <blocker>   # <issue> is blocked by <blocker>
#   link_issues.sh sub-issue  <parent> <child>    # <child> becomes a sub-issue of <parent>
# Arguments are issue numbers. The API addresses the target by number and the linked issue
# by its internal id, so this looks the id up first.
set -euo pipefail

usage() { sed -n '2,6p' "$0" >&2; exit 2; }
[[ $# -eq 3 ]] || usage
kind=$1 target=$2 other=$3

other_id=$(gh api "repos/{owner}/{repo}/issues/$other" --jq .id)

case $kind in
  blocked-by)
    gh api -X POST "repos/{owner}/{repo}/issues/$target/dependencies/blocked_by" -F issue_id="$other_id" >/dev/null
    echo "#$target is blocked by #$other"
    ;;
  sub-issue)
    gh api -X POST "repos/{owner}/{repo}/issues/$target/sub_issues" -F sub_issue_id="$other_id" >/dev/null
    echo "#$other is a sub-issue of #$target"
    ;;
  *) usage ;;
esac
