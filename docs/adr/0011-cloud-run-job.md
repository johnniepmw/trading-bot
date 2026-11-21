# 0011. Run as a scheduled Cloud Run job, with a deploy window

- **Status**: accepted
- **Date**: 2026-11-21
- **Deciders**: owner
- **Enforcement**: Phase 2 job module and deploy workflow (not yet built); until then the deploy workflow is inactive (no cloud variables)

## Context
The bot runs once or a few times a day: fetch data, check risk, maybe trade, record.
ADR-0004 targets Cloud Run services. A deploy that lands mid-run, or while the London
market is open, could interrupt a half-finished order sequence.

## Options considered
1. **Cloud Run job started by Cloud Scheduler**: pays only while running; no HTTP surface;
   clean one-shot semantics with retries off.
2. **Cloud Run service with a scheduled HTTP call**: fits the template as is; an always
   reachable endpoint and request timeouts we don't need.
3. **A VM with cron**: simple; patching and uptime are ours.

## Decision
Option 1, in europe-west2. Production deploys only inside a window: never while an
execution is running, and only while the London market is closed. The template's
`cloud-run-service` module needs a job variant; Phase 2 builds it with the deploy
workflow changes. Until then the container's command is `python -m tradingbot.app smoke`
and e2e tests run that entry point as a subprocess.

## Consequences
- Good: no public endpoint; cheap; one run at a time (with the ledger lock, ADR-0013).
- Bad / costs: the template's preview, smoke and traffic-based rollback assume a service
  and must be adapted (tracked in `docs/exec-plans/tech-debt.md`).
- Revisit when: the bot needs to react to events intraday.
