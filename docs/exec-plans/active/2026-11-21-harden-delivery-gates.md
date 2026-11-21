# Harden three delivery-gate weaknesses before Phase 1

- **Status**: in progress
- **Branch / PR**: `fix/harden-delivery-gates` / see PR
- **Risk class**: high (`Makefile`, `scripts/**`, `.github/**`)
- **Started**: 2026-11-21

## Goal
Close three gaps in the delivery gates, agreed with the owner, in one high-risk PR:
1. the secret scan covers every fetched branch, so one leaked branch fails CI on every PR;
2. Dependabot's workflow exemption trusts the paths a PR touches, not what it changes;
3. risk-gate accepts an approval comment that the agent posted through the owner's
   identity via the Claude GitHub App.

## Acceptance criteria
| # | Criterion (observable, pass/fail) | Evidence |
|---|---|---|
| AC1 | `make secrets-scan` scans `HEAD`'s history by default, not `--all` | `tests/scripts/test_secrets_scan.py::test_default_range_is_head`; manual run below |
| AC2 | CI scans `base..head` on pull requests and `HEAD` (main) on pushes | `ci.yml` diff; actionlint passes; `test_range_is_passed_through` |
| AC3 | A scan range that does not resolve fails, before gitleaks runs (gitleaks itself exits 0 on a bad range) | `test_unresolvable_range_fails_closed` |
| AC4 | Missing gitleaks fails in CI, passes with a notice locally | `test_missing_gitleaks_fails_in_ci`, `test_missing_gitleaks_is_a_notice_locally` |
| AC5 | A fake secret on another local branch no longer fails `make secrets-scan` on this branch; one on the scanned branch still fails | manual evidence below |
| AC6 | A Dependabot PR touching `.github/workflows/` is standard only if every changed line is a `uses: owner/repo@<40-hex> # vX.Y.Z` line and the same actions are removed as added; anything else, or a missing patch, is high | `tests/scripts/test_risk_classify.py` |
| AC7 | Non-Dependabot authors classify exactly as before | `test_risk_classify.py::test_non_dependabot_*` |
| AC8 | risk-gate ignores approval comments posted through a GitHub App, and edited comments | `tests/scripts/test_risk_gate.py` |
| AC9 | `docs/security.md` describes the new behaviour; the self-approval tech-debt line is gone | file diff; `make docs-check` |

## Non-goals
- A Dependabot `cooldown` (its tech-debt line stays).
- A separate approver identity or GitHub Environment (not needed: the app field
  distinguishes the two; see evidence).
- Moving the PR-code steps of agent-review to a base checkout (separate tech-debt line).

## Rollout and rollback
- Flag / kill switch: none (CI and gate code; risk-gate runs from the default branch, so
  the new rules apply only after merge).
- Data changes: none.
- Rollback: revert the PR.

## Decisions
- Secret-scan logic moves from a one-line Makefile recipe into `scripts/secrets-scan.sh`
  so it can be tested and shellchecked. The range variable is `SECRETS_SCAN_RANGE`
  (default `HEAD`); `GITLEAKS_LOG_OPTS` was used nowhere else.
- gitleaks v8.28.0 prints `fatal: Invalid revision range` but **exits 0** with "0 commits
  scanned" for a range git can't resolve, so the script checks the range with
  `git rev-list` first and fails if it doesn't resolve. That's what keeps the PR-range
  scan fail-closed.
- CI passes the PR range through `env:` (not inline `${{ }}` in `run:`), from
  `github.event.pull_request.base.sha`/`head.sha`. `fetch-depth: 0` stays so both SHAs
  are present.
- "Its version comment" read as the trailing `# vX.Y.Z` on the `uses:` line; a standalone
  comment line is not exempt. Also required: per file, the actions removed and added are
  the same list (only the SHA and version move), so a push to a Dependabot branch can't
  swap `actions/checkout` for another action and stay standard. Small, and it is the point
  of the check.
- Renamed workflow files have no patch under their old name, so they are high risk.
- The CLI `--author dependabot[bot]` path has no patches, so it now returns high: fail
  closed rather than trust paths.
- risk-gate also ignores comments whose `updated_at` differs from `created_at`: an app
  edit of an owner's earlier web-UI comment would keep `performed_via_github_app: null`.
  The owner's three real approvals are unedited, so this costs nothing in practice; a
  typo fix just means posting a fresh comment.
- Approval lookup factored into `find_approval()` so it is unit-testable without the API.

## Evidence for item 3 (read-only `gh api repos/…/issues/{1,5}/comments`)
| Comment | Posted via | `user.login` | `performed_via_github_app` | `created_at == updated_at` |
|---|---|---|---|---|
| #1 7064577060 | owner, web UI | johnniepmw | `null` | yes |
| #5 7065098118 | owner, web UI | johnniepmw | `null` | yes |
| #5 7065164578 | owner, web UI | johnniepmw | `null` | yes |
| #1 7062505034 | agent, cloud session | johnniepmw | `claude` (app id 1236702) | yes |
| #1 7062548515 | agent, cloud session | johnniepmw | `claude` (app id 1236702) | yes |

The field tells them apart, so the check ships. Residual: an agent holding a personal
access token (not the app) would post with `null`; cloud sessions don't have one.

## Plan
1. Tests for secrets-scan, risk_classify patches, risk_gate approvals — check: they fail
2. `scripts/secrets-scan.sh`, Makefile, ci.yml — check: tests pass, actionlint, manual AC5
3. `classify(..., patches=)` and risk_gate passes patches — check: tests pass
4. `find_approval()` with the app and edit filters — check: tests pass
5. Docs and tech-debt — check: `make check`, `make e2e`
6. security-reviewer on the diff, PR with an owner brief

## Progress log
- 2026-11-21 step 1: 37 tests in `tests/scripts/` written first; they failed (no
  `find_approval`, `classify()` took no patches, no `scripts/secrets-scan.sh`)
- 2026-11-21 steps 2–5: done — `make check` exit 0 (incl. repo-lint with actionlint and
  shellcheck, secrets-scan "scanning HEAD … no leaks found"), `make e2e` 3 passed,
  `make check-fast` 70 passed
- 2026-11-21 AC5, real gitleaks v8.28.0 in a throwaway local clone (no hooks enabled,
  fake `ghp_` token generated at random on a `leak` branch, clone deleted, never pushed):
  old recipe on `main` → "leaks found: 1", exit 1; new script on `main` → "scanning HEAD
  … no leaks found", exit 0; new script on `leak` → "leaks found: 1", exit 1;
  `SECRETS_SCAN_RANGE=main..leak` → "leaks found: 1", exit 1; `CI=true` with an
  unresolvable range → "cannot resolve", exit 1

## Outcome
<filled on merge>
