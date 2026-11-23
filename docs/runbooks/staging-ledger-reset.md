# Runbook: reset staging's ledger (owner, or agent with owner credentials)

**When**: staging's fake account or ledger is in a state you no longer want to test from.
Staging trades fake money on synthetic prices; nothing real is at stake.

**Why a special option**: an ordinary `setup` refuses wherever a ledger or a reference mark
ever existed, deleted ones included. That protects prod (ADR-0017). Staging gets
`setup --reset` instead, which prod refuses before touching anything.

1. Pause the schedule so no run starts meanwhile: `ops` → `action=pause`, `env=staging`.
2. Reset (Cloud Shell, project set):
   ```
   gcloud run jobs execute trading-bot-staging --region europe-west2 --args=setup,--equity,1000,--reset --wait
   ```
   It takes the lock, removes the live ledger, reference mark and fake account (earlier
   versions stay in the buckets), then sets up afresh with a fake £1,000. You get a
   `ledger_reset` alert email, so a reset is never silent.
3. Resume the schedule (console: Cloud Scheduler → `trading-bot-staging-daily` → Resume).

The first run after a reset has no day-start equity, so buys that day are vetoed (an
`account_veto` alert): expected.

Never in prod: the job refuses `--reset` there ("setup --reset is for staging").
