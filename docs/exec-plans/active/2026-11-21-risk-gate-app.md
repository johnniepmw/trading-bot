# risk-gate status from a dedicated GitHub App

- **Status**: in progress
- **Branch / PR**: `ci/risk-gate-app-status` / see PR
- **Risk class**: high (`.github/**`, `scripts/**`)
- **Started**: 2026-11-21

## Goal
Close the gap the #10 security review found: the required `risk-gate` status is matched
by name, so any workflow on any branch with `statuses: write` could set it to success and
skip the owner's approval. The owner asked to set up the GitHub App now.

## Acceptance criteria
| # | Criterion (observable, pass/fail) | Evidence |
|---|---|---|
| AC1 | risk-gate posts its status with a token of the dedicated app, not the workflow token | `test_set_status_uses_the_status_token`; `risk-gate.yml` diff (no `statuses: write`) |
| AC2 | Without the app token, risk-gate fails before any API call (fail closed) | `test_main_fails_closed_without_a_status_token` |
| AC3 | The app key is readable only from `main` | `risk-gate.yml` uses environment `risk-gate` (`deployment: false`); runbook step 3 sets the branch policy to `main` |
| AC4 | `github-setup.sh` pins `risk-gate` to the app when `RISK_GATE_APP_ID` is set, refuses a non-numeric ID, and keeps today's ruleset otherwise | rendered ruleset JSON for both cases (progress log); shellcheck |
| AC5 | The owner has a step-by-step runbook; security docs and tech-debt updated | `docs/runbooks/risk-gate-app.md`; `make docs-check` |
| AC6 | After merge, a PR's `risk-gate` status is created by the app, then the ruleset is pinned | read-only `gh api …/statuses` (`creator.login`) and `…/rulesets` after the owner's step 5 |

## Non-goals
- `agent-review` (a check run, still matched by name; tech-debt line kept, reworded).
- Creating the app, the environment or the secret: owner only (no admin, no secrets).

## Rollout and rollback
- Order: owner does runbook steps 1–3 **before** this PR merges (otherwise every later
  PR's risk-gate job fails and waits, fail closed). Merge. Check step 4 on the next PR.
  Owner runs step 5 (pin).
- Rollback: re-run repo settings without `RISK_GATE_APP_ID`, then revert this PR.

## Decisions
- Environment secret with a `main`-only branch policy, not a repo secret: a repo secret
  is readable by a workflow pushed to any branch, which is the attack itself. Docs
  (github/docs source, read via git because docs.github.com is blocked here) confirm
  `GITHUB_REF` is the default branch for `pull_request_target` and `issue_comment`, that
  branch policies match `GITHUB_REF`, and that `deployment: false` keeps secrets and
  branch policies but skips deployment records.
- `actions/create-github-app-token` v3.4.0 (`f418cff3…`, from `git ls-remote`), with
  `client-id` (`app-id` is deprecated there) and `permission-statuses: write` only.
- The app token is used for the status call only; comments and reads keep `github.token`.
  The workflow token loses `statuses: write`.
- The ruleset pin is opt-in through `RISK_GATE_APP_ID` so re-running the setup script
  before the app exists doesn't lock every PR.

## Plan
1. Tests for the status token and fail-closed start — check: they fail first
2. `risk_gate.py`, `risk-gate.yml`, `github-setup.sh` — check: tests, actionlint, shellcheck
3. Runbook, security docs, tech-debt — check: `make docs-check`
4. `make check`, `make e2e`, security-reviewer, PR with owner brief
5. After merge: AC6

## Progress log
- 2026-11-21 step 1: 2 new tests failed before the change (no token routing, no start check)
- 2026-11-21 step 2: rendered ruleset — without the ID: `{'context': 'risk-gate'}` plus a
  warning; with `123456`: `{'context': 'risk-gate', 'integration_id': 123456}`; `12a`
  refused
- 2026-11-21 step 4: `make check` exit 0 (81 passed, actionlint, shellcheck, docs, no leaks), `make e2e` 3 passed

## Outcome
<filled on merge>
