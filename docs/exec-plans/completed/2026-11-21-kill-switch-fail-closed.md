# Kill switch fails closed in the job

- **Status**: shipped
- **Branch / PR**: `ccr-7fe1197d-l39xit` / https://github.com/johnniepmw/trading-bot/pull/15 (merged as `ed9e363`, owner approved 2026-11-21)
- **Risk class**: high (`src/tradingbot/app/**` is a risk path: kill switch and entry point)
- **Started**: 2026-11-21

## Goal
Make the `new-orders` kill switch fail closed. A Cloud Run job starts cold every run, so
today an unreadable, missing or malformed remote flag means "orders allowed", and an env
variable can beat a remote "off". This must be fixed before any order path reads the flag
(tech-debt 2026-11-21 "flags"; strict-xfail tripwire in `tests/app/test_flags.py`).

## Acceptance criteria
| # | Criterion (observable, pass/fail) | Evidence |
|---|---|---|
| AC1 | A kill-switch flag is off when the first remote fetch fails (error, timeout), until a fetch succeeds | `test_flags.py::test_kill_switch_is_off_when_the_first_fetch_fails` passes (was strict xfail) |
| AC2 | A missing overrides object (404) and a malformed body or non-bool value make kill switches off; non-kill-switch flags keep their default | `test_parse_overrides_*`, `test_gcs_source_treats_a_missing_object_as_a_failure`, `test_a_malformed_body_closes_kill_switches`, `test_release_flags_keep_their_default_when_the_fetch_fails` |
| AC3 | After one good fetch, later failures keep the last good value for at most 5 minutes, then kill switches are off | `test_last_good_overrides_survive_a_failure_for_a_limited_time` |
| AC4 | An env `FLAG_X=on` never turns a kill switch on unless the process is local/test; env `off` still turns it off | `test_env_off_always_turns_a_kill_switch_off`, `test_env_on_does_not_beat_a_remote_off_when_deployed`, `test_env_on_does_not_rescue_an_unreadable_source_when_deployed`, `test_env_on_works_in_a_trusted_local_or_test_environment` |
| AC5 | The job refuses to start (non-zero exit, no report) when `FLAGS_BUCKET` is unset and `APP_ENV` is not `local` or `test` | `test_main.py::test_job_refuses_to_start_without_a_flags_bucket_when_deployed` (5 cases), `tests/e2e/test_smoke.py::test_deployed_job_without_a_flags_bucket_refuses_to_start` |
| AC6 | Every fail-closed decision logs an ERROR with a stable message so Cloud Monitoring can alert on it (Phase 2 wires the alert) | `test_every_fail_closed_decision_logs_an_error`, `test_env_on_ignored_when_deployed_logs_an_error`, `test_closing_for_lack_of_a_source_logs_an_error`, `test_main.py::test_job_refuses_to_start_...` (asserts the `refusing to start:` ERROR) |
| AC7 | `make check` and `make e2e` pass; docs (feature-flags.md, ARCHITECTURE.md, kill-switch runbook, tech-debt) updated | `make check-fast`: 123 passed; `make e2e`: 4 passed; `make check`: ruff, mypy, 8 contracts kept, docs-check and flags-check 0 errors, kit-check ok (gitleaks, actionlint, shellcheck not installed in this session; CI runs them) |

## Non-goals
- The alert policy itself and the flags bucket (Phase 2 infra). Seeding `flags.json` in
  the bucket is a Phase 2 infra requirement, recorded below.
- Reading the flag on any order path (later change).
- Changing release or ops flag behaviour on purpose. Side effect, accepted: a 404 or malformed body now keeps their last good overrides (or defaults) instead of dropping to defaults or ignoring only the bad entry.

## Rollout and rollback
- Flag / kill switch: this *is* the kill switch's safety. Nothing trades yet.
- Data changes: none. Rollback: revert the PR.

## Decisions
- Fail closed applies to flags of type `kill-switch` only; release/ops flags keep
  "last good, else default" — they are not safety controls.
- A 404 on the overrides object is treated as a failure, not "no overrides". Infra must
  seed `{"overrides": {}}` at env-up (added to the Phase 2 notes). Otherwise a deleted or
  misnamed object would silently re-enable trading.
- Any non-bool value in the overrides body fails the whole fetch (a typo in a kill value
  must not be ignored).
- Last good values expire after 300 s for kill switches. A job runs for minutes; an hour-old
  "on" is not evidence.
- `APP_ENV` (`local`, `test`, anything else = deployed) decides whether env may turn a kill
  switch on and whether a missing bucket is allowed. Unset counts as deployed, so the safe
  behaviour is the default. Flags takes `trusted_env: bool = False` explicitly.
- Env `off` still works everywhere: it can only make things safer.
- Security review round 1: unrecognised env values for a kill switch close it; overrides naming an unregistered flag fail the read; `load_registry` rejects unknown types and non-bool defaults (the open path is for release/ops only); `local`/`test` are untrusted when `CLOUD_RUN_JOB`/`K_SERVICE` is set; `APP_ENV=local` moved to `STACK_DEV` (`make dev`) and commented out in `.env.example`. Not done: a JSON log formatter (Phase 2 alert matches the message text) and a lock-contention false alert (single-threaded job).
- "Alert" is an ERROR log line for now; the job has no alerting channel until Phase 2.

## Open questions
- None that are costly to undo.

## Plan
1. Tests first (AC1–AC6), confirm they fail — check: `make test-one T=tests/app`
2. `flags.py`: any failed read, staleness, kill-switch resolution, `trusted_env`; `GcsSource` 404/malformed raise — check: tests pass
3. `__main__.py`: `APP_ENV`, refuse to start without bucket; e2e conftest sets `APP_ENV=test` — check: tests, `make e2e`
4. Docs and tech-debt entry removal — check: `make docs-check`, `make flags-check`
5. `make check`, review, PR, escalate brief

## Progress log
- 2026-11-21 steps 1–4: tests first (failed on missing `KILL_SWITCH_MAX_STALE_SECONDS`), then `flags.py`, `__main__.py`, e2e conftest sets `APP_ENV=test`, docs, tech-debt entry removed — evidence: AC table

## Review
- Round 1: reviewer — 1 blocking (AC6 tests), 7 non-blocking, all applied except the timeout parametrization note that is included too; security-reviewer — 1 medium and 3 low, all applied (see Decisions). 140 unit + 4 e2e pass.

## Outcome
Shipped in PR #15 after the owner approved. CI, agent-review and risk-gate green. `make check-fast` 140 passed, `make e2e` 4 passed. Phase 2 must seed `{"overrides": {}}` in the flags bucket and alert on the ERROR lines `kill switch closed:` and `refusing to start:`.

## Learnings
- Fail-closed behaviour needs a test per closing path *and* per log line, or the alert silently breaks later.
- A hard reset plus force-push of the session branch after a squash merge is refused by the auto-mode classifier; merging `origin/main` into the branch gives an identical tree without rewriting history.
