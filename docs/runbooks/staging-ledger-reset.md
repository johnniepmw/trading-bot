# Runbook: reset staging's ledger (owner or agent with owner credentials)

**When**: staging's fake account or ledger is in a state you no longer want to test from.
Staging trades fake money on synthetic prices; nothing real is at stake.

**The catch**: `setup` refuses wherever a ledger or a reference mark ever existed, including
deleted ones still in the 30-day soft-delete window. That protects prod (ADR-0017) and
applies to staging too, so staging can't be reset in place today.

Options, simplest first:
1. **Don't reset**: let staging carry on. Its fake broker and ledger stay consistent with
   each other; drift in fake equity doesn't matter.
2. **Wait it out**: pause the staging schedule (`ops` → `pause`, `env=staging`), delete the
   ledger, fake account, lock and reference mark:
   ```
   gcloud storage rm --all-versions gs://trading-bot-515509-ledger-staging/** gs://trading-bot-515509-locks-staging/**
   ```
   then after 30 days (soft delete expires) run the setup execution:
   ```
   gcloud run jobs execute trading-bot-staging --region europe-west2 --args=setup,--equity,1000 --wait
   ```
   and resume the schedule.
3. **A staging-only reset command**: not built yet (tech debt). Ask the agent if you need
   it sooner.

Never do any of this in prod.
