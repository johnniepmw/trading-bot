---
name: fix-ci
description: Diagnose and fix failing checks on the current pull request or main, including flaky tests, without weakening the checks. Use when gh pr checks shows a failure, a CI run is red, or the agent-review check requests changes.
argument-hint: "[PR number or run id]"
---

# Fix CI: find the real cause, keep the gates intact

1. Identify what failed: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/gh-pr.sh" checks <pr>` (or
   `gh run list --branch <branch> --limit 5`), then `gh run view <id> --log-failed`. For
   the agent-review check, read its PR comment
   (`gh api repos/{owner}/{repo}/issues/<pr>/comments`). `gh pr` commands fail in cloud
   sessions (GraphQL blocked); the helper and `gh run` use REST.
2. Reproduce locally with the same `make` target CI ran. CI and local run the same verbs;
   if they diverge, that divergence is the bug (tool versions in mise.toml, env, data).
3. Classify and act:
   - **Real failure** (your change broke something): fix the code, not the test. If the
     test was wrong, fix the test and say why in the commit message.
   - **Agent-review findings**: treat them like the `review` skill does: verify each,
     fix the confirmed blocking ones, reply on the PR with a reason for any you reject.
   - **Flaky test** (passes and fails on the same commit): do not retry until green.
     Find the cause (timing, order dependence, shared state, real network, clock). Fix
     it now if it is in scope; otherwise quarantine it per `docs/testing.md` (tag it,
     open an issue labelled `flaky`, link it in tech-debt) and keep the rest strict.
   - **Infrastructure** (runner outage, registry timeout, quota): re-run once with
     `gh run rerun <id> --failed`. If it fails again, escalate with the log excerpt.
4. Never: skip, delete or loosen a check to get green; add `--no-verify`; mark a test as
   expected-to-fail without an issue; edit CI to exclude your files. Those changes are
   high risk and need the owner.
5. Push the fix and watch again with the helper's `checks <pr> --wait 9`. Note the cause
   in the plan's Progress log.
