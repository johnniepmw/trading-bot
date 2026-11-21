---
name: escalate
description: Hand a decision to the owner with a plain-English brief instead of code. Use when a change is high risk, irreversible, needs a secret or access you lack, has conflicting requirements, or is stuck after repeated attempts.
---

# Escalate: one clear decision, no code reading required

The owner does not read diffs. Your brief must let them decide in under two minutes,
from a phone.

## When (and only when)
- The PR is high risk (risk-gate or `scripts/risk_classify.py`), e.g. money, secrets,
  auth, infra, data deletion, migrations, delivery gates, risk limits.
- An action is irreversible or touches production directly.
- Requirements conflict, or the goal cannot be met without changing a stated constraint.
- You need a credential, account, payment or access that you must not create yourself.
- You are stuck after three genuinely different attempts plus one re-plan.

Everything else: decide, record the decision, and keep going.

## Write the brief
Put it in the PR description under `## Owner decision needed` (and in the plan file),
using this shape:

```markdown
## Owner decision needed
**Decision:** <one sentence: what you are being asked to approve or choose>
**Why it is flagged:** <the risk in plain words: what could go wrong, how badly, for whom>
**What changes if approved:** <2–4 bullets, behaviour not code>
**Safety net:** <tests/evidence, flag or kill switch, rollback path and how fast>
**Options:** A) <recommended> — <trade-off>  B) <alternative> — <trade-off>
**Recommendation:** <A/B and the reason in one line>
**If no answer:** <what you will do meanwhile; never the risky option>
```

## Then
- For a high-risk PR: run `bash "${CLAUDE_SKILL_DIR}/../../scripts/gh-pr.sh" merge <pr>` anyway.
  Where auto-merge is available it is now armed, and the risk-gate check holds the merge
  until the owner comments `/approve-risk <first 7+ chars of the head commit SHA>`. In
  cloud sessions (no auto-merge), end the brief with: "After approving, tap Squash and
  merge, or ask me to merge." Never post that comment, approve, relabel, or otherwise
  satisfy the gate yourself.
- Set the plan Status to `blocked (owner decision)`.
- Keep working on anything that does not depend on the decision; otherwise finish the
  turn with the brief as your final message (keep it to the brief).
- For deliberate stops with failing checks (only when escalating), create the file
  `.agentic/allow-stop` first so the stop gate lets the session end. It is single-use.
