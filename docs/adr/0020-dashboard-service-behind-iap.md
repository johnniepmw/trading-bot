# 0020. The dashboard: a Cloud Run service behind IAP, read-only but for a one-way pause

- **Status**: accepted
- **Date**: 2026-11-23
- **Deciders**: agent (within delegated authority; the owner chose the dashboard and runs the IAP setup); amends ADR-0011 ("a job, not a service"), ADR-0019 (deploys and rollback) and ADR-0004's ownership split
- **Enforcement**: `tests/app/test_dashboard.py`, `tests/e2e/test_dashboard.py`, `tests/scripts/test_job_ops.py` (the service follows the job), `tests/scripts/test_dashboard_iap.py`, `tests/infra/test_bootstrap.py`; `src/tradingbot/app/**`, `src/tradingbot/report/**`, `scripts/**` and `infra/**` are high risk

## Context
Monitoring AC7–AC9 want the owner to see the bot's state on a phone and to stop new orders with
one button, without trusting anyone else with account data (ADR-0014: a public repository).
The bot itself is a job (ADR-0011); a page needs something that answers HTTP. The GCP project
has no Google organisation, so IAP needs a custom OAuth client (the owner has run this setup
before for another dashboard).

## Options considered
1. **A static page in a private bucket**, written by the job: no service, but no pause button.
2. **A Cloud Run service behind IAP, the same image as the job**: a page per request from the
   latest ledger, and a button; one more resource to secure.
3. **A third-party dashboard host**: account data leaves Google Cloud.

## Decision
Option 2.
- `python -m tradingbot.app serve`, the job's image with another command; standard library
  HTTP, server-rendered HTML, no scripts, a strict CSP that also forbids framing.
- **Access**: IAP on the service; IAP's service agent is the only invoker; only the owner's
  account may sign in. All three are set by the owner's own `scripts/dashboard-iap.sh`, never by
  CI or OpenTofu state, so no CI identity is granted IAP rights or holds the OAuth client secret
  or the owner's address (CI can still change the service itself: see Consequences).
- **Identity** (owner-created in the bootstrap, like the jobs'): reads the ledger and lock
  buckets, lists and creates (never deletes) pause requests. No flags, secrets, broker key or
  write access to the ledger.
- **Reading**: a scratch copy of the latest ledger, no lock; the reference-mark check still
  refuses a restored ledger. The glance comes from `report.glance`, as the email's does.
- **The pause**: a same-origin POST behind a confirm page stores one create-only request with a
  unique name and raises a `pause_requested` alert; the job honours it at its next order check.
  Nothing on the page can resume.
- **Deploys**: the service follows the job's image only after the job's check passes; rollback
  moves both. A dashboard failure fails the workflow but never touches the job.

## Consequences
- Good: the owner sees the glance any time and can stop new orders from a phone; the job never
  depends on the dashboard.
- Bad / costs: a public URL that only IAP guards (a direct call with a Google ID token must be
  refused: part of the staging check). CI's infra identity (project `run.admin`, actAs on the
  dashboard identity, which also lets it invoke the service directly) could still switch IAP off
  or add a public invoker through a merged OpenTofu change. Guards: OpenTofu leaves IAP alone
  and never manages the invoker authoritatively; tests over every infra file forbid public
  members, disabled invoker IAM and IAP switched off; `scripts/dashboard-check.sh` checks the
  live service on every deploy and rollback (the image moves only while IAP is on), after every
  infra apply, and nightly (an incident issue if it is public or IAP is off). The app doesn't verify IAP's signed header:
  that needs an ES256 library (ADR-0007); revisit if the tripwire ever fires. The owner runs a short script per environment once.
  Cost: nothing at this traffic (IAP is free; the service scales to zero).
- Revisit when: the dashboard needs data the ledger doesn't hold (slice 2 stores benchmark
  closes in the ledger), or a second person needs access.
