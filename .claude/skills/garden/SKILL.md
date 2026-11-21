---
name: garden
description: Weekly codebase and documentation hygiene - fix doc drift, remove expired flags, triage flaky tests and tech debt, review dependency updates, sync the agentic kit. Opens small, separate PRs. Meant to run on a schedule.
disable-model-invocation: true
---

# Garden: keep the harness sharp

Entropy accumulates quietly in agent-written codebases. This pass removes it in small,
reviewable PRs. Each item below is its own branch and PR (standard risk unless noted);
skip items with nothing to do. Cap the run at five PRs.

1. **Kit sync.** `bash scripts/kit-sync.sh --check` (in a cloud session the kit repo
   must be attached to the session). If it reports an update (exit 10), run
   `bash scripts/kit-sync.sh`, and open `chore(kit): sync agentic kit <version>` with the
   kit changelog excerpt in the PR body. (Changes under the kit's hooks are high risk;
   the escalate brief is the changelog in plain English.)
2. **Docs drift.** `make docs-check`. Then sample: do AGENTS.md commands still work? Does
   ARCHITECTURE.md's codemap match the tree? Are runbooks' commands still valid? Fix
   what is wrong; delete what is obsolete. Keep AGENTS.md under 150 lines.
3. **Flags.** `make flags-check`. For release flags fully rolled out or past expiry, run
   the `feature-flag` skill's Remove steps (one PR per flag).
4. **Flaky and slow tests.** Look at the last week of CI runs
   (`gh run list --status failure --limit 30`) for tests that failed then passed on rerun.
   Fix or quarantine per `docs/testing.md`. Note `check-fast` duration; if over 60s, fix.
5. **Dependency PRs.** For open Dependabot PRs that are green: patch and minor updates of
   dev dependencies can merge (`bash "${CLAUDE_SKILL_DIR}/../../scripts/gh-pr.sh" merge <pr>`). Majors: read the changelog, run the
   `deliver` loop to adapt code if needed.
6. **Tech debt.** Pick at most one item from `docs/exec-plans/tech-debt.md` that is small
   and clearly valuable; deliver it.
7. **Stale plans.** Anything in `docs/exec-plans/active/` untouched for 14+ days: finish,
   mark abandoned with a reason, or escalate.

Finish with a five-line report: PRs opened, anything needing the owner.
