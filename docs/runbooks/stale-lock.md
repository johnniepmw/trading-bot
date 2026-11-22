# Runbook: clear a stale ledger lock (owner)

**When**: an alert with kind `stale_lock`, or runs stopping with "stale lock: held by ...".
A run died holding the ledger's lock (the container was killed, ran out of memory or hit
its timeout). Until the lock is cleared, every run stops with no new orders. Nothing is at
risk while it waits.

**Why it is yours**: clearing a lock while its run is still going would let two runs write
the ledger at once (ADR-0017). No CI identity can delete it; you check, then clear.

Commands for prod; for staging replace `prod` with `staging`. Cloud Shell, project set
(`gcloud config set project trading-bot-515509`).

1. Confirm nothing is running (it must print nothing):
   ```
   gcloud run jobs executions list --job trading-bot-prod --region europe-west2 --filter='NOT status.completionTime:*' --format='value(metadata.name)'
   ```
   If it prints an execution, wait for it to finish (at most 30 minutes, the job's
   timeout) and check again.
2. See who held the lock (an execution name and run ID; no account data):
   ```
   gcloud storage cat gs://trading-bot-515509-locks-prod/run.lock
   ```
3. Clear it:
   ```
   gcloud storage rm gs://trading-bot-515509-locks-prod/run.lock
   ```
4. Check with a dry run (no lock, no orders):
   ```
   gcloud run jobs execute trading-bot-prod --region europe-west2 --args=run,--dry-run --wait
   ```
   Then `make logs ENV=prod` shows "dry run: N order(s) would be sent" or the reason it
   stopped. The next scheduled run takes over; nothing else to do.
5. Tell the agent which execution died (step 2), so the cause can be found in its logs.

Never delete `reference-mark.json` in the same bucket: it guards the drawdown stop's
memory (ledger-restore.md).
