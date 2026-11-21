---
name: spec
description: Turn a goal into a short spec with numbered, testable acceptance criteria, non-goals and a risk class, saved as the exec plan for the change. Use before building anything non-trivial, or when the owner wants to shape an idea without building it yet.
argument-hint: "[goal]"
---

# Spec: make "done" checkable before writing code

Goal: $ARGUMENTS

1. Gather context yourself first: read the relevant code, ARCHITECTURE.md, related ADRs,
   open issues, and any plan in `docs/exec-plans/`. Do not ask the owner anything the
   repo can answer.
2. Copy [plan-template.md](plan-template.md) to `docs/exec-plans/active/<slug>.md`
   (slug: short kebab-case, prefixed with today's date `YYYY-MM-DD-`).
3. Fill in:
   - **Goal**: one or two sentences, in the owner's terms.
   - **Acceptance criteria**: numbered AC1..ACn. Each is observable and pass/fail,
     e.g. "GET /orders?status=open returns only open orders (integration test)".
     Cover the error and edge paths, not just the happy path. Prefer 3–8 criteria.
   - **Non-goals**: what you will deliberately not do, so scope cannot creep.
   - **Risk class**: run `python3 scripts/risk_classify.py --paths <files you expect to touch>`
     if it exists. High if it touches money, secrets, auth, infra, data deletion,
     migrations, the delivery gates, or anything listed in `.github/risk-paths.txt`.
   - **Rollout**: flag or kill switch? migration (expand/contract)? backfill?
   - **Decisions**: every judgement call you made, one line each, with the reason.
   - **Open questions**: only those that change what gets built and are costly to reverse.
4. If there are open questions and the owner is present, ask them all at once with a
   recommended default for each. If the owner is not present (scheduled or unattended run),
   take the defaults, record them as Decisions, and continue.
5. When invoked on its own (not from `deliver`), stop after writing the spec and give the
   owner a three-line summary plus the file path.

A good spec is short. If it runs past one screen, the change is probably two changes:
split it and note the follow-up in the plan.
