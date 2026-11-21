#!/usr/bin/env bash
# Owner-only, run once per repo (needs admin; private repos need GitHub Pro or above).
# Sets merge settings, the `main` ruleset with required checks, labels, and Dependabot
# security features. Idempotent: re-running updates the ruleset in place.
set -euo pipefail
repo="${REPO:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"
echo "Configuring $repo"

# Merge settings: squash only, PR title as commit, auto-merge, tidy branches.
gh api -X PATCH "repos/$repo" \
  -F allow_squash_merge=true -F allow_merge_commit=false -F allow_rebase_merge=false \
  -F allow_auto_merge=true -F delete_branch_on_merge=true \
  -f squash_merge_commit_title=PR_TITLE -f squash_merge_commit_message=PR_BODY >/dev/null

# Security features.
gh api -X PUT "repos/$repo/vulnerability-alerts" >/dev/null || echo "warn: vulnerability alerts"
gh api -X PUT "repos/$repo/automated-security-fixes" >/dev/null || echo "warn: automated security fixes"

# The required `risk-gate` status is pinned to the dedicated risk-gate GitHub App (its
# numeric App ID, not the client ID), so a status posted by any other token (e.g. a
# workflow on another branch) doesn't count. Without RISK_GATE_APP_ID it is matched by
# name only: set it once the app is installed and risk-gate is posting through it.
if [ -n "${RISK_GATE_APP_ID:-}" ]; then
  case "$RISK_GATE_APP_ID" in *[!0-9]*) echo "RISK_GATE_APP_ID must be the numeric App ID" >&2; exit 1 ;; esac
  risk_gate_check="{ \"context\": \"risk-gate\", \"integration_id\": $RISK_GATE_APP_ID }"
else
  echo "warn: RISK_GATE_APP_ID not set; the risk-gate status is matched by name only"
  risk_gate_check='{ "context": "risk-gate" }'
fi

# Ruleset for the default branch.
ruleset="$(cat <<JSON
{
  "name": "main-protection",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] } },
  "bypass_actors": [],
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    { "type": "required_linear_history" },
    { "type": "pull_request", "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": false,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": false,
        "allowed_merge_methods": ["squash"] } },
    { "type": "required_status_checks", "parameters": {
        "strict_required_status_checks_policy": false,
        "required_status_checks": [
          { "context": "ci-required" },
          { "context": "agent-review" },
          $risk_gate_check ] } }
  ]
}
JSON
)"
existing="$(gh api "repos/$repo/rulesets" -q '.[] | select(.name=="main-protection") | .id' || true)"
if [ -n "$existing" ]; then
  echo "$ruleset" | gh api -X PUT "repos/$repo/rulesets/$existing" --input - >/dev/null
  echo "updated ruleset main-protection ($existing)"
else
  echo "$ruleset" | gh api -X POST "repos/$repo/rulesets" --input - >/dev/null
  echo "created ruleset main-protection"
fi

# Labels used by workflows and skills.
while IFS='|' read -r name color desc; do
  gh label create "$name" --repo "$repo" --color "$color" --description "$desc" --force >/dev/null
done <<'LABELS'
risk:high|b60205|Touches high-risk paths; needs /approve-risk from the owner
incident|d93f0b|Production problem (opened by deploy/ops workflows)
nightly-failure|fbca04|A nightly check failed
drift|fbca04|Infrastructure differs from code
flaky|c5def5|Test that passes and fails on the same commit
LABELS

echo "Done. Remaining owner step: claude setup-token && gh secret set CLAUDE_CODE_OAUTH_TOKEN"
