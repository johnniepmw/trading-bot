# 0019. Deploying the job: image only, a check execution, prod behind a window and a dry run

- **Status**: accepted
- **Date**: 2026-11-22
- **Deciders**: agent (within delegated authority; refines ADR-0011's deploy window and ADR-0004's ownership split for the job)
- **Enforcement**: `scripts/cloudrun-job-deploy.sh` and `tests/scripts/test_job_ops.py`; `.github/workflows/**` and `scripts/**` are high risk; `tests/infra/test_job_module.py` (OpenTofu ignores only the image)

## Context
ADR-0011 runs the bot as a Cloud Run job with a deploy window "outside market hours" and
expects the deploy pipeline to be adapted. The template's pipeline assumed a service: a
URL, revisions, traffic splitting and HTTP smoke tests. A job has none of these. A job
update never touches an execution that is already running, but the next execution starts
on whatever image the job then holds, unchecked. Staging runs on fakes and prod on the
Trading 212 demo, so staging can't rehearse prod's broker or data (Phase 2 plan).

## Options considered
1. **Service-style pipeline** (candidate revision, traffic switch): doesn't exist for jobs.
2. **Image-only update plus a check execution, with rollback to the previous image**:
   one small script for both environments; status-only reads keep account data out of
   Actions (ADR-0014).
3. **A separate prod job per release** (blue/green jobs): more resources and IAM to keep
   in step, for no gain at one run a day.

## Decision
Option 2.
- **Build once** on every push to `main`; deploy by digest from our registry only.
- **Staging**: update the job's image, run a `smoke` execution (`--wait`), and on failure
  put the previous image back.
- **Prod**, only once the owner sets the repository variable `DEPLOY_PROD=true` (after
  Phase 2 steps 3–4, when prod can run at all):
  - It refuses when a prod execution is running, or when the scheduler is enabled and the
    next run is under 30 minutes away. This replaces "outside market hours": a job that
    runs before the open makes market hours irrelevant.
  - It updates the image and runs a `run --dry-run` execution with prod's identity, ledger
    and broker. That execution has a scratch ledger, takes no lock and can't send (AC6).
  - On failure it goes back to the previous image and opens an incident issue with no
    account data.
- **The check execution has a time limit** (15 minutes). Any way out of the script other
  than a passed check cancels unfinished executions and restores the previous image:
  failure, timeout, or the workflow being cancelled.
- **Deploys accept only digests from this project's registry**, derived from the project,
  region and app name, never from a separate variable.
- **The image has a fixed entrypoint** (`python -m tradingbot.app`), so the job's args
  choose the command.
- **The deploy reads execution status only**, never logs or outputs.
- **The replaced image is tagged `previous-<env>`** in Artifact Registry; `ops rollback`
  returns to it and runs a `smoke` execution, going back if that fails. Naming an older
  digest skips the deploy's other checks, so only the owner may.
- **The broken-deploy drill** is a `deploy` run with `drill: true`. After the normal staging
  deploy it deploys a copy whose entrypoint exits with an error, and passes only if that
  deploy fails and staging is back on the good image. It never touches prod. (Cloud Shell
  can't push to the registry, so the drill runs in Actions.)
- **`ops clear-pause`** (owner only, checked against the triggering actor, so another user's
  re-run doesn't count) removes pause requests; it doesn't touch the schedule.
- **`ops pause`** pauses the schedule (the deployer's custom role allows pause only). Only
  the owner resumes it.
- **Clearing a stale lock** is an owner `gcloud storage rm` of `run.lock`, done in person
  after checking that no execution is running (runbook, step 8). No CI identity gets
  delete on the lock bucket.
- **Ownership split for the job**: OpenTofu owns everything but the image (identity,
  timeout, retries, mounts, the wiring env vars). `APP_VERSION` is baked into the image at
  build time.

## Amendment (2026-11-23): business stops pass the prod check; window refusals aren't incidents
- Prod's check execution is `run --dry-run --deploy-check`. A dry run that stops for a reason
  the account or the outside world gives (`cycle.BUSINESS_STOPS`: a cycle stop such as the
  drawdown stop, a broker or data error, the ledger's state, a missing ledger before setup,
  calendar or wiring states the job reports as "dry run stopped") exits 0 there and logs
  "deploy check: a business stop". An unexpected exception (a bug in the image) still exits
  1, so the deploy goes back to the previous image. Without the flag, a dry run still exits
  1 on any stop (AC6), so an owner's own dry run reports a stop as a failure.
- Why: a fix-forward must be deployable while prod is deliberately stopped (the first prod
  deploy runs before the ledger exists, and a drawdown stop may last weeks).
- A window refusal (exit 75: an execution running or the next run within 30 minutes) fails
  the job with a warning but opens no incident issue; nothing changed. Re-run it later.

## Consequences
- Good:
  - A broken image never stays on a job: staging catches start-up failures, and prod's
    dry run catches wiring, permission and data problems before the next scheduled run.
  - No account data reaches Actions.
- Bad / costs:
  - Prod deploys wait for the window, so the owner re-runs a refused deploy.
  - The dry run costs one execution (seconds) per deploy.
  - Staging's `smoke` doesn't exercise the cycle. Staging's scheduled runs on fakes do
    that, once its schedule is on.
- Revisit when: the bot runs more than once a day, or deploys become frequent enough that
  waiting for the window hurts.
