---
name: review
description: Fresh-eyes review of the current branch before opening or updating a pull request. Prepares the diff, runs the reviewer subagent (and security-reviewer for risky changes), then triages and fixes findings. Use after implementing and verifying a change.
---

# Review: someone who did not write it checks it

Reviewers only see files, never your reasoning. That is the point: they judge the code
and evidence as they are, not as you meant them.

## 1. Prepare inputs
```bash
mkdir -p .agentic/review
git fetch origin --quiet
git diff origin/main...HEAD > .agentic/review/diff.patch
git diff --stat origin/main...HEAD > .agentic/review/stat.txt
python3 scripts/risk_classify.py > .agentic/review/risk.json 2>/dev/null || true
```
Find the plan for this change in `docs/exec-plans/active/` (or completed/).

## 2. Run reviewers in parallel
- Always: the `reviewer` subagent (`agentic:reviewer` when loaded as a plugin). Give it the
  paths to `diff.patch`, `stat.txt` and the plan file, and say which acceptance criteria
  matter most.
- Also the `security-reviewer` subagent when risk.json says high, or the diff touches
  auth, secrets, input parsing, SQL or shell construction, file paths, dependencies,
  CI workflows, Dockerfiles or `infra/`.

Ask each for its standard output format. Do not tell them what you think is wrong;
let them find it.

## 3. Triage every finding
- **Verify before acting.** Read the code or write a quick test. Reviewers are sometimes
  wrong; a finding you cannot confirm gets a one-line rejection reason in the plan's
  Review section.
- **Blocking**: incorrect behaviour, an unmet acceptance criterion, security or
  data-loss risk, a changed behaviour without a test that would catch it, a broken
  ADR or architecture boundary. Fix it.
- **Non-blocking**: fix if trivial and inside the change's scope; otherwise add a line
  to `docs/exec-plans/tech-debt.md`. Never expand scope to satisfy a reviewer's taste.

## 4. Loop
After fixes, `make check-fast`, commit, and re-run only the reviewer whose findings you
fixed, pointing it at the new diff. Stop when it returns `VERDICT: pass`. After three
rounds without convergence, use the `escalate` skill and summarise the disagreement.

## 5. Record
Add a line per round to the plan's Review section: counts, what changed, what was rejected.
`.agentic/` is scratch space and is gitignored.
