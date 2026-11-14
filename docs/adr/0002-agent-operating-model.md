# 0002. Agents deliver end to end; the owner decides only high-risk changes

- **Status**: accepted
- **Date**: 2026-11-14
- **Deciders**: owner
- **Enforcement**: required checks `agent-review` and `risk-gate`; guard hooks; AGENTS.md

## Context
The owner sets direction and makes strategic calls but does not want to read code or
diffs. Changes must still be high quality and must not create outsized risk. On a
personal GitHub account, Claude acts under the owner's identity, and GitHub does not let
anyone approve their own pull request, so approval-based branch rules can't separate
"agent" from "owner".

## Options considered
1. **Owner reviews every PR** — high quality bar but contradicts the goal.
2. **Full autonomy, no human gate** — fastest; one bad change to money, secrets, infra or
   the gates themselves could be costly or self-reinforcing.
3. **Autonomy with an independent agent reviewer and a path-based risk gate** — the owner
   sees only a plain-English brief for high-risk changes.
4. **Separate bot GitHub account for the agent** — enables real GitHub approvals, but
   complicates cloud and mobile sessions, which authenticate as the owner.

## Decision
Option 3. Every PR gets (a) the authoring session's own fresh-eyes subagent review,
(b) an independent CI agent review with no access to the authoring session, and (c) the
`risk-gate` check. Paths in `.github/risk-paths.txt` make a PR high risk; it then needs
an `/approve-risk <head-sha>` comment from the owner, bound to that exact commit. The
agent is blocked (hooks and instructions) from posting that comment, approving PRs,
admin-merging or editing gate configuration without the change itself being high risk.

## Consequences
- Good: the owner's time goes only to consequential decisions; everything else flows.
- Bad / costs: the risk gate is a strong guard against mistakes, not against a determined
  adversary with the owner's credentials. Real-money and other irreversible switches
  therefore also live outside the repo's automated reach (owner-only operations).
- Revisit when: a second human joins, or the project handles other people's money or
  data (then use a bot identity with real required approvals, or GitHub Team).
