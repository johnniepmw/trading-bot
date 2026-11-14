---
name: deliver
description: Take a goal from the owner to a merged, deployed and verified change without further prompting. Use for any feature, bug fix, refactor or other change request that should end in a pull request.
argument-hint: "[goal text or issue number]"
---

# Deliver: goal → merged, deployed, verified

You own this goal end to end. The owner wants the outcome, not progress updates or
permission requests. Work through every stage below without stopping, except for the
escalation triggers in AGENTS.md. Goal: $ARGUMENTS

Repo conventions assumed below (from the agentic template): `make` verbs, plans in
`docs/exec-plans/`, `scripts/risk_classify.py`, the PR template. If the repo lacks one,
use the closest equivalent and note the gap in the plan's Decisions section.

GitHub: use the kit's PR helper, `GHPR` below = `bash "${CLAUDE_PLUGIN_ROOT}/scripts/gh-pr.sh"`.
It works everywhere, including cloud sessions, where `gh pr` and `gh issue` fail because
the GitHub proxy blocks GraphQL (`gh api` REST calls and `gh run`/`gh workflow` work).

## 0. Orient
- Read AGENTS.md, then any plan in `docs/exec-plans/active/` that relates to this goal.
  If one exists, you are resuming: continue from its Progress log rather than starting over.
- If the goal is an issue number: `GHPR issue <n>`.
- `git fetch origin && git switch -c <type>/<slug> origin/main`
  (type: feat, fix, refactor, perf, test, docs, chore). Skip if resuming on the branch.

## 1. Frame: write the spec
Run the `spec` skill. It writes `docs/exec-plans/active/<slug>.md` with numbered,
testable acceptance criteria (AC1, AC2, ...), non-goals and a risk class.

Ambiguity rule: if the choice is reversible or a sensible engineer could go either way,
decide, record it under "Decisions", and keep going. Ask the owner only when the answer
changes what gets built *and* would be expensive to undo. Then ask everything in one
message, give your recommended default for each, and continue with work that does not
depend on the answer.

## 2. Plan
- Append a numbered plan to the same file. Each step is small, independently
  verifiable, and names its check (a test, a command, an observable behaviour).
- Read ARCHITECTURE.md and the ADRs the change touches. If the change adds a dependency,
  service, datastore, pattern or crosses a boundary, include an ADR step (`adr` skill).
- For plans over three steps or touching architecture, have the `architect` subagent
  review the plan. Apply its blocking points; note any you reject and why.

## 3. Build: one verified step at a time
For each step:
1. Write or extend a test that fails for the right reason. For bugs, reproduce first.
2. Make the minimum change that passes. Follow existing patterns; no speculative
   abstraction, no drive-by refactors (log those in `docs/exec-plans/tech-debt.md`).
3. `make check-fast` until green.
4. Commit with a Conventional Commit message. Add one line to the plan's Progress log:
   step, what changed, evidence.

Risky behaviour ships dark behind a flag or kill switch (`feature-flag` skill).
If a step fails after three genuinely different attempts, re-plan once. If still stuck,
escalate with what you tried.

## 4. Verify: prove every acceptance criterion
- `make check` (full suite) is green.
- User-visible behaviour has an end-to-end test that passes (`make e2e`).
- For UI or API changes, run the app and exercise the change (`make dev`, `/verify`,
  curl, Playwright). For several user-visible paths, have the `qa` subagent try to break it.
- Fill the plan's AC table: each AC → evidence (test name, command + result, screenshot
  path). An AC without evidence is not done.

## 5. Review: fresh eyes
Run the `review` skill (reviewer subagent, plus security-reviewer when risky). Fix every
blocking finding that you can confirm. Re-review until clean, at most three rounds, then
escalate the disagreement.

## 6. Ship
- Update docs the change affects: README, ARCHITECTURE.md, runbooks, `flags.json`, ADRs.
- Close the plan in this PR: set Status to shipped, fill Outcome and Learnings, and
  `git mv` it to `docs/exec-plans/completed/`.
- If you hit something a future session needs to know, write it where it belongs
  (AGENTS.md gotcha, a `.claude/rules/` file, a doc, or a test or lint rule).
- `git push -u origin HEAD`, then write the PR body from `.github/pull_request_template.md`
  (summary, AC → evidence, risk class, rollout and rollback, decisions) to
  `.agentic/pr.md` and open it: `GHPR create "<conventional title>" .agentic/pr.md`.
- `GHPR merge <pr>`: turns on auto-merge where available; in cloud sessions it merges via
  REST once checks are green (exit 2 = still running: run it again later; exit 3 = waiting
  for the owner). CI, agent-review and risk-gate decide either way.
- If `python3 scripts/risk_classify.py` says high risk, run the `escalate` skill so the
  owner gets a plain-English brief. Keep working on anything that does not depend on it.
- Watch checks: `GHPR checks <pr> --wait 9` (repeat while it exits 2). Failing check →
  `fix-ci` skill. Review comments (`gh api repos/{owner}/{repo}/issues/<pr>/comments`)
  → address them or reply with a reason. In cloud sessions, finish with `GHPR merge <pr>`
  once green.

## 7. Land and confirm
Skip this stage if the deploy workflow isn't configured yet (no cloud variables).
- After merge, watch the deploy: `gh run list --branch main --limit 3`, then
  `gh run watch <id>`.
- If smoke tests fail or the pipeline rolls back, switch to the `incident` skill.
- Where the change is observable in production (endpoint, page, log line), confirm it.

## 8. Report
Final message to the owner, at most five lines: what shipped, PR link, how it was
verified (in production where observable), and anything they must decide or know.
No step-by-step narrative; the plan file and PR hold the detail.
