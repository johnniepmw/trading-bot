---
name: incident
description: Respond to a production problem - failed deploy, rolled-back release, alert, or user-visible breakage. Stabilise first (rollback or kill switch), then diagnose, fix forward with a regression test, and write a short postmortem.
argument-hint: "[what is wrong]"
---

# Incident: stabilise, then understand, then fix

Problem: $ARGUMENTS

## 1. Stabilise (minutes, before diagnosis)
- Check what the pipeline already did: `gh run list --workflow deploy.yml --limit 5`.
  The deploy workflow rolls back automatically when smoke tests fail.
- If production is still bad, use the safe-direction levers, which you may pull without
  asking (tell the owner immediately after):
  - Roll back to the previous revision:
    `gh workflow run ops.yml -f action=rollback -f env=prod`
  - Turn a feature off with its kill switch:
    `gh workflow run ops.yml -f action=kill -f env=prod -f flag=<name>`
- Anything else in production (data fixes, infra changes, re-enabling features, secret
  rotation) is the owner's call: use the `escalate` skill with a recommendation.
- Never mutate production directly with gcloud or tofu; the guard hook blocks it.

## 2. Tell the owner (one short message)
What is broken, who is affected, what you did to stabilise, current state, next step.

## 3. Diagnose
Gather evidence before theorising: deploy run logs, `make logs ENV=prod` (or
`gcloud run services logs read`), recent merges (`git log --oneline -15 origin/main`),
the diff of the suspect change. Write a failing test that reproduces the problem.

## 4. Fix forward
Run the `deliver` loop for the fix. The reproduction test is AC1. Normal gates apply;
an incident does not justify skipping review or tests.

## 5. Postmortem
Copy `docs/postmortems/TEMPLATE.md` to `docs/postmortems/YYYY-MM-DD-<slug>.md` and fill it
in the fix PR: timeline, impact, root cause, why the gates missed it, and actions.
Actions must be mechanical where possible: a new test, lint rule, alert, smoke check or
hook, not "be more careful". Blameless, factual, under one screen.
