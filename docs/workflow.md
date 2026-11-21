# Delivery workflow

How a goal becomes running software in this repo. The `deliver` skill automates this
page; follow it by hand when the skill isn't loaded.

## Roles
| Who | Does | Never does |
|---|---|---|
| Owner | Sets goals and priorities, decides high-risk changes (`/approve-risk`), owns accounts, money, secrets and production switches | Reads diffs (unless they want to) |
| Coding agent | Specs, plans, builds, tests, reviews (via subagents), ships, monitors, fixes, documents | Approves its own high-risk work, touches secrets or production directly |
| CI / GitHub | Enforces checks, independent agent review, risk gate, branch rules, deploys, rollbacks | Gets bypassed |

Guardrails come in three layers, and each assumes the one before can fail:
1. **Instructions**: AGENTS.md, skills, rules. Followed nearly always.
2. **Harness**: hooks and permission rules in Claude Code. Enforced on every tool call,
   on surfaces where they load.
3. **Pipeline**: CI checks, agent-review, risk-gate, branch ruleset, deploy smoke tests
   and automatic rollback. Enforced for every change, however it was written.

## The loop
```
goal ─► 1 frame (spec + acceptance criteria) ─► 2 plan (+ architect review, ADR?)
     ─► 3 build: test first → code → make check-fast → commit   (repeat per step)
     ─► 4 verify: make check, make e2e, run it, AC → evidence
     ─► 5 review: reviewer (+ security-reviewer) → fix → repeat until pass
     ─► 6 ship: PR + auto-merge ─► CI + agent-review + risk-gate
     ─► 7 land: deploy staging → e2e → prod candidate → smoke → 100% (auto rollback)
     ─► 8 report to owner (≤5 lines)
```
State lives in files, not in the conversation: the plan in
`docs/exec-plans/active/<date>-<slug>.md` (spec, plan, progress log, decisions, review
notes), small commits, and the PR. Any session can resume from them.

## Decide or ask?
Decide (and record in the plan) when the choice is reversible, conventional, or a
sensible engineer could go either way. Ask when the answer changes what gets built and
is costly to undo. Then ask once, with all questions together, each with a recommended
default, and continue with independent work. Unattended runs take the defaults.

## Branches, commits, PRs
- Trunk-based: `main` is always releasable. Short-lived branches
  `<type>/<slug>` (feat, fix, refactor, perf, test, docs, chore), ideally merged within a day.
- Conventional Commits (`feat: ...`, `fix(scope): ...`); the `commit-msg` hook checks.
  PR titles follow the same format because PRs squash-merge and the title becomes the
  commit on main.
- One concern per PR. Large work lands as a sequence of small PRs, each shippable, with
  unfinished behaviour behind a release flag.
- PR description uses `.github/pull_request_template.md`. The AC → evidence table is what
  makes review possible without reading code.

## Merging
`main` is protected by a ruleset: PR required, linear history, no force pushes, and
three required checks:
- `ci-required` aggregates `make check` (including the secret scan), e2e against the
  app started in CI, and the image build and vulnerability scan
- `agent-review` is an independent Claude review in CI with no access to the authoring
  session. It blocks on confirmed correctness, security or spec problems.
- `risk-gate` passes standard-risk PRs immediately. High-risk PRs wait for the owner's
  `/approve-risk <sha>` comment on the current head commit; any new push needs a fresh
  approval.

The agent arms auto-merge on every PR with the kit's `gh-pr.sh merge` (in cloud
sessions, where auto-merge isn't available, the same command merges once checks are
green). Branches are not required to be up to date with main (no merge queue on personal
plans), so main's own CI and the staged deploy catch the rare semantic conflict.

## Escalation format
High-risk PRs carry an "Owner decision needed" section: decision, why it is flagged,
what changes, safety net, options, recommendation, and what happens if there's no
answer. The owner reads that, not the code. See the `escalate` skill.

## Steering (for the owner)
- Give goals as outcomes plus constraints: "Users can export orders as CSV from the
  orders page. Large exports must not time out. No new paid services."
- Long or overnight work: start the session with `/goal <done condition>`, for example
  `/goal the PR for CSV export is merged and the deploy is green`.
- Parallel work: one worktree session per goal in the desktop app, or cloud sessions.
- Correcting the agent twice about the same thing means the harness is missing
  something: add an AGENTS.md line, a `.claude/rules/` file, a test, a lint rule or a
  hook, so it never needs a third correction.
- Weekly upkeep runs as a scheduled task: `/schedule weekly /garden`.
