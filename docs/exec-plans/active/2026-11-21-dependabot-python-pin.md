# Dependabot: no automated Python minor or major bumps

- **Status**: blocked (owner decision)
- **Branch / PR**: `ci/dependabot-ignore-python-minor-major` / see PR
- **Risk class**: high (`.github/**`)
- **Started**: 2026-11-21

## Goal
Stop Dependabot proposing Python version jumps for the container base image (like the
closed PR #2, 3.13 → 3.15), while still taking patch and digest updates.

## Acceptance criteria
| # | Criterion (observable, pass/fail) | Evidence |
|---|---|---|
| AC1 | The `docker` entry in `.github/dependabot.yml` ignores `python` semver-major and semver-minor updates only | file diff; `make repo-lint` passes |
| AC2 | The dependency policy says why, in `docs/security.md` (Supply chain) | file diff; `make docs-check` passes |

## Non-goals
- Changing the Python version; the uv and github-actions entries; a Dependabot cooldown
  (already in tech-debt).

## Rollout and rollback
- Flag / kill switch: none (CI config only).
- Data changes: none.
- Rollback: revert the PR.

## Decisions
- Ignore `version-update:semver-major` and `version-update:semver-minor` for `python` in
  the `docker` ecosystem, not the whole dependency — the Python version moves only on
  purpose, with `mise.toml` and `pyproject.toml` in the same PR, but digest updates of
  `3.13-slim` carry Debian and CPython security fixes and should keep coming.
- Policy recorded in `docs/security.md` (Supply chain), where the other Dependabot rules live.

## Plan
1. Add the `ignore` block — check: `make repo-lint`, `make check`
2. Document the policy — check: `make docs-check`
3. PR with an owner brief — check: risk-gate pending for `/approve-risk`

## Progress log
- 2026-11-21 steps 1–2: done — evidence: `make check` and `make e2e` pass
- 2026-11-21 review: security-reviewer pass, 0 blocking; took its wording note (digest updates carry patch fixes) and added an end-of-life tech-debt line

## Outcome
<filled on merge>
