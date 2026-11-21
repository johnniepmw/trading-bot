# Testing strategy

Tests are how the agent knows it is done and how the owner trusts changes they never
read. The suite is the specification; treat it that way.

## Layers
| Layer | What it proves | Where | Runs |
|---|---|---|---|
| Unit | Pure logic, edge cases, invariants | next to code (`*.test.ts`, `tests/unit/`) | `check-fast`, every commit |
| Property-based | Invariants over generated inputs (money maths, parsers, limits) | unit folders | `check-fast` |
| Integration | Our code against real local dependencies or faithful fakes behind our interfaces (DB in a container, recorded HTTP) | `tests/integration/` | `make check`, CI |
| Architecture | Module boundaries and dependency direction | config + `make arch` | `make check`, CI |
| Contract | Our assumptions about third-party APIs (recorded responses replayed; nightly live check against sandbox) | `tests/contract/` | CI (replay), nightly (live) |
| End-to-end | A user-visible flow through the deployed shape of the app (browser or HTTP) | `e2e/` | CI on every PR (app started locally), staging on every deploy, nightly against staging |
| Smoke | The deployed revision is alive and is the expected version | `scripts/smoke.sh` | every deploy, before traffic |

## Rules
1. **Test first for bugs.** Reproduce with a failing test, then fix. The test stays.
2. **Every behaviour change has a test that fails without it.** Coverage percentage is a
   smoke alarm, not a goal; critical modules (money, risk, auth) aim for full branch
   coverage and are mutation-tested nightly where the stack supports it. `src/tradingbot/risk`
   is held to 100% branch coverage by `make test-unit` (`--cov-fail-under=100` in `stack.mk`),
   so CI fails on any untested risk branch.
3. **Every user-visible flow has an end-to-end test.** New page, endpoint, CLI command or
   job → new or extended e2e test in the same PR.
4. **E2E tests target a URL.** `E2E_BASE_URL` selects local or staging, so the same tests
   run everywhere. They create their own data and clean up after themselves.
   This repo's bot is a job with no URL: its e2e tests run the entry point as a
   subprocess and skip when `E2E_BASE_URL` is set, until Phase 2 (ADR-0011).
5. **Mock at our boundaries, not inside.** Fake the broker, not our own order service.
   Never mock the code under test.
6. **Deterministic by construction.** Inject the clock and randomness; no sleeps (wait for
   a condition with a timeout); no reliance on test order; no real network in unit or
   integration tests.
7. **Fast feedback.** `make check-fast` under 60 seconds. The `garden` skill watches this.
8. **Never weaken a test to make it pass.** Changing an assertion requires the reason in
   the commit message; deleting or skipping a test is a high-risk change.

## Flaky tests
A test that passes and fails on the same commit is a bug.
1. Don't retry until green. Find the cause (timing, shared state, order, network, clock).
2. If it can't be fixed in the current change: quarantine it (tag `@flaky` /
   `pytest.mark.flaky` so it runs in nightly but not in required checks), open an issue
   labelled `flaky`, add it to `docs/exec-plans/tech-debt.md`. Fix within seven days or
   delete it with an ADR-lite note in the PR explaining what coverage was lost.
3. CI never auto-retries tests.

## Test data
Factories or builders, not shared fixtures that many tests mutate. Each e2e test creates
uniquely named data (prefix with the run ID) so tests can run in parallel against
shared environments.
