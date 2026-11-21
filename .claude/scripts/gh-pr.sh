#!/usr/bin/env bash
# Pull-request helper that works on every surface, including Claude Code cloud sessions,
# where the GitHub proxy blocks GraphQL (so `gh pr ...` and `gh issue ...` fail) but REST
# calls through `gh api` work.
#
# Usage:
#   gh-pr.sh create <title> <body-file> [base]   open a PR for the current branch; prints its number
#   gh-pr.sh checks <pr> [--wait <minutes>]      exit 0 all green, 1 failed, 2 still running,
#                                                3 green except risk-gate (waiting for the owner)
#   gh-pr.sh merge <pr>                          enable auto-merge; where that's unavailable,
#                                                squash-merge via REST once checks are green
#   gh-pr.sh view <pr>                           PR summary plus a checks summary
#   gh-pr.sh issue <n>                           issue title, labels and body
# Env: REQUIRED_CHECKS (default "ci-required agent-review"), REQUIRED_STATUSES (default "risk-gate")
set -euo pipefail

REQUIRED_CHECKS="${REQUIRED_CHECKS:-ci-required agent-review}"
REQUIRED_STATUSES="${REQUIRED_STATUSES:-risk-gate}"

repo() {
  if [ -n "${GH_REPO:-}" ]; then echo "$GH_REPO"; return; fi
  git remote get-url origin | sed -e 's/\.git$//' | tr ':' '/' | awk -F/ '{print $(NF-1) "/" $NF}'
}
R="$(repo)"

checks_once() { # prints a summary; returns 0/1/2/3 as documented above
  local pr="$1" sha runs statuses
  sha="$(gh api "repos/$R/pulls/$pr" --jq .head.sha)"
  runs="$(gh api "repos/$R/commits/$sha/check-runs?per_page=100" --jq '[.check_runs[] | {name, status, conclusion}]')"
  statuses="$(gh api "repos/$R/commits/$sha/status" --jq '[.statuses[] | {context, state}]')"
  RUNS="$runs" STATUSES="$statuses" REQ_C="$REQUIRED_CHECKS" REQ_S="$REQUIRED_STATUSES" python3 - "$sha" <<'PY'
import json, os, sys
runs = json.loads(os.environ["RUNS"]); statuses = json.loads(os.environ["STATUSES"])
req_c = os.environ["REQ_C"].split(); req_s = os.environ["REQ_S"].split()
bad = {"failure", "cancelled", "timed_out", "action_required", "startup_failure"}
failed = [r["name"] for r in runs if r["status"] == "completed" and r["conclusion"] in bad]
failed += [s["context"] for s in statuses if s["state"] in ("failure", "error")]
running = [r["name"] for r in runs if r["status"] != "completed"]
names = {r["name"] for r in runs}
state = {s["context"]: s["state"] for s in statuses}
missing = [c for c in req_c if c not in names] + [s for s in req_s if s not in state]
pending_status = [c for c, st in state.items() if st == "pending"]
print(f"head {sys.argv[1][:7]}: {len(runs)} check run(s), {len(statuses)} status(es)")
for label, items in (("failed", failed), ("running", running), ("not reported yet", missing), ("pending", pending_status)):
    if items:
        print(f"  {label}: {', '.join(sorted(set(items)))}")
if failed:
    sys.exit(1)
if running or missing or [c for c in pending_status if c != "risk-gate"]:
    sys.exit(2)
if "risk-gate" in pending_status:
    print("  waiting for the owner's /approve-risk comment")
    sys.exit(3)
print("  all green")
sys.exit(0)
PY
}

cmd="${1:-}"; shift || true
case "$cmd" in
  create)
    title="${1:?title}"; body_file="${2:?body-file}"; base="${3:-main}"
    head="$(git rev-parse --abbrev-ref HEAD)"
    if url="$(gh pr create --title "$title" --body-file "$body_file" --base "$base" 2>/dev/null)"; then
      echo "${url##*/}"
    else
      gh api -X POST "repos/$R/pulls" -f title="$title" -f head="$head" -f base="$base" \
        -f body="$(cat "$body_file")" --jq .number
    fi
    ;;
  checks)
    pr="${1:?pr}"; wait_min=0
    [ "${2:-}" = "--wait" ] && wait_min="${3:-9}"
    deadline=$(( $(date +%s) + wait_min * 60 ))
    while :; do
      set +e; checks_once "$pr"; rc=$?; set -e
      if [ "$rc" -ne 2 ] || [ "$(date +%s)" -ge "$deadline" ]; then exit "$rc"; fi
      sleep 30
    done
    ;;
  merge)
    pr="${1:?pr}"
    if err="$(gh pr merge "$pr" --auto --squash 2>&1)"; then
      echo "auto-merge enabled for #$pr; it merges when the required checks pass"
      exit 0
    fi
    if ! printf '%s' "$err" | grep -qi 'graphql'; then
      echo "$err" >&2; exit 1
    fi
    echo "auto-merge isn't available here (GraphQL blocked); merging via REST once green"
    set +e; checks_once "$pr"; rc=$?; set -e
    if [ "$rc" -ne 0 ]; then
      echo "not merged yet (checks rc=$rc): run 'gh-pr.sh merge $pr' again when checks finish" >&2
      exit "$rc"
    fi
    sha="$(gh api "repos/$R/pulls/$pr" --jq .head.sha)"
    title="$(gh api "repos/$R/pulls/$pr" --jq .title)"
    gh api -X PUT "repos/$R/pulls/$pr/merge" -f merge_method=squash -f sha="$sha" \
      -f commit_title="$title (#$pr)" --jq .message
    ;;
  view)
    pr="${1:?pr}"
    gh api "repos/$R/pulls/$pr" --jq '"#\(.number) \(.title)\nstate: \(.state) merged: \(.merged) mergeable: \(.mergeable_state)\nbranch: \(.head.ref) -> \(.base.ref)"'
    set +e; checks_once "$pr"; set -e
    ;;
  issue)
    n="${1:?issue number}"
    gh api "repos/$R/issues/$n" --jq '"#\(.number) \(.title)\nlabels: \([.labels[].name] | join(", "))\n\n\(.body // "")"'
    ;;
  *)
    sed -n '2,15p' "$0"; exit 2 ;;
esac
