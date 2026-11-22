# Runbook: restore the ledger from an earlier version (owner, with the agent)

**When**: runs stop with "not a ledger", "ledger missing" or a corrupt-file error, or the
agent asks for it after an incident. Not for "the ledger's high-water mark ... is below the
reference mark": that means someone already restored an old version (see below).

**What protects you**: every version of `ledger.sqlite` is kept, and deleted ones stay
recoverable for 30 days. The reference mark (`reference-mark.json` in the lock bucket)
remembers the highest high-water mark ever saved. A ledger whose mark is lower is refused,
so a restore can't quietly loosen the 20% drawdown stop (ADR-0017).

Commands for prod; Cloud Shell, `gcloud config set project trading-bot-515509`.

1. Stop new runs: GitHub → Actions → **ops** → Run workflow → `action=pause`, `env=prod`.
2. Confirm nothing is running (see stale-lock.md step 1).
3. List the versions, newest last (generation numbers, sizes and times; no account data):
   ```
   gcloud storage ls -a -l gs://trading-bot-515509-ledger-prod/ledger.sqlite
   ```
4. Pick the newest good version with the agent: normally the one just before the problem.
   It must carry the current reference mark: a version from before the latest new peak
   will be refused at step 6, by design.
5. Make it current again (a copy; nothing is deleted):
   ```
   gcloud storage cp gs://trading-bot-515509-ledger-prod/ledger.sqlite#<generation> gs://trading-bot-515509-ledger-prod/ledger.sqlite
   ```
6. Dry run: `gcloud run jobs execute trading-bot-prod --region europe-west2 --args=run,--dry-run --wait`,
   then `make logs ENV=prod`. It must not say "below the reference mark" or "not a ledger".
7. Resume the schedule (console: Cloud Scheduler → `trading-bot-prod-daily` → Resume).
   The next run reconciles with the broker: orders placed after the restored version are
   matched again from the broker's records.

**"Below the reference mark"**: an older ledger is current. Find the version that matches
the mark (step 3, with the agent) and restore that instead. Never edit or delete
`reference-mark.json` to get past it: a run refuses a missing mark too, and recreating it
is a deliberate owner decision to talk through with the agent first.

**Never** re-run `setup` to "start fresh": it refuses wherever a ledger or a mark ever
existed (deleted ones included), and starting afresh would forget the peak the drawdown
stop is measured from.
