# Template sync: agent-review config restore and audit-pass wording

- **Status**: blocked (owner decision)
- **Branch / PR**: `chore/template-sync-agent-review` / see PR
- **Risk class**: high (`.github/**`, `.claude/**` rules)
- **Started**: 2026-11-21

## Goal
Bring in agentic-dev-template commit `b7a7c7f` ("fix(agent-review): no hooks in CI review;
explain the config restore") and the audit-pass wording in two rule files, keeping every
trading-bot-specific line.

## Acceptance criteria
| # | Criterion (observable, pass/fail) | Evidence |
|---|---|---|
| AC1 | agent-review's header explains the action's config restore and why hooks are off; the prompt tells the reviewer that restored paths are expected and where the PR's versions are | `.github/workflows/agent-review.yml` diff; `make repo-lint` |
| AC2 | Exactly one `--settings '{"disableAllHooks": true}'` line; SHA pin unchanged | `grep -c -- --settings` = 1; diff shows no `uses:` change |
| AC3 | `docs/security.md` describes the agent-review isolation, lists the kit as high risk, and splits file guard and Bash guard | file diff; `make docs-check` |
| AC4 | `.claude/rules/tests.md` drops "preview"; `.claude/rules/workflows.md` requires a full SHA with a version comment | file diff |
| AC5 | `.claude/kit.lock` is already a risk path | `.github/risk-paths.txt` line 11; unchanged |

## Non-goals
- Other template changes; kit files listed in `.claude/kit.lock` (the two rule files
  aren't listed there).

## Rollout and rollback
- Flag / kill switch: none (CI prompt and docs).
- Data changes: none.
- Rollback: revert the PR.

## Decisions
- Dropped the old two-line "Hooks off" comment above `claude_args`; the new header covers
  it. Kept trading-bot's own `--settings` line and the SHA pin from PR #3.
- The config restore is the action's behaviour, not this repo's; claude-code-action has
  `src/github/operations/restore-config.ts` and `test/restore-config.test.ts` on its default
  branch (GitHub code search). The file at the pinned SHA couldn't be read from this session.
- Branched after PR #3 merged (both touch `agent-review.yml`). After PR #4 merged, main
  was merged in (no rebase); `docs/exec-plans/tech-debt.md` conflicted and was resolved by
  keeping both new top lines (#5's agent-review line, #4's python end-of-life line),
  newest first. `docs/security.md` merged cleanly with both PRs' additions.

## Plan
1. Apply the five edits — check: diff matches the brief
2. Verify — check: `make check`, `make repo-lint`, `make e2e`
3. security-reviewer on the diff — check: 0 blocking
4. PR with owner brief — check: risk-gate pending

## Progress log
- 2026-11-21 step 1: edits applied — evidence: `git diff --stat` (4 files)
- 2026-11-21 step 2: `make check`, `make repo-lint`, `make e2e`, `make kit-check` exit 0
- 2026-11-21 step 3: security-reviewer round 1: 1 blocking (medium) — the new security.md
  bullet over-claimed ("a PR can't run its own code"): the job's input-prep and comment
  steps run the PR's `scripts/` beside the token. Reworded to what's true and logged the gap
  in tech-debt (fix: run those scripts from a base-branch checkout). Pre-existing, not
  widened by this PR.

## Outcome
<filled on merge>
