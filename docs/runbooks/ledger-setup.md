# Runbook: set up the ledger and seed the high-water mark (owner, once)

**When**: once, before the bot's first run in an environment (staging, then prod). Never
again for the same account. It runs as a one-off execution of the environment's job (its
own identity and broker):
```
gcloud run jobs execute trading-bot-prod --region europe-west2 --args=setup,--equity,<GBP> --wait
```
(staging was set up this way on 2026-11-22 with its fake £1,000). Prod reads the Trading 212
demo account (Phase 2 step 4). Before prod's setup, the demo account must hold only the
bot's funds (`config/bot.toml`) and have no working limit or stop orders on them: any other
position, or such an order, stops every run until it is closed or cancelled in the app.

**Why it is an owner step**: the high-water mark is the peak the 20% drawdown stop is
measured from. If code could set it, a lost or damaged ledger could be "repaired" by
re-seeding from today's lower equity, which would silently lift the stop. So the bot
refuses to start without a ledger file, never creates one during a run, and never sets
the first mark itself (`tests/execution/test_executor.py` checks no daily code can).

1. Confirm the account's current equity in the Trading 212 app (the paper or live account
   this environment uses).
2. Run the setup command with that equity (`--equity 1000.00`). It takes the run lock,
   checks your figure against the broker's equity (within 2%), creates the ledger, records
   the broker's equity as the first high-water mark and as that day's equity, saves it, and
   writes the reference mark (ADR-0017). It refuses if a ledger ever existed in the bucket
   (any version) or a reference mark exists, and if your figure doesn't match.
3. The next run starts from that mark. Day-start equity for the daily-loss pause is the
   previous day's recorded equity, so seed at least a day before the first run: a run on
   the seed's own day has no previous day and pauses buys (an ERROR "no day-start equity"
   is expected then). After more than 5 days without a recorded run, the same happens once.

**Deposits and big rises**: the bot refuses a new peak more than 10% above the mark in one
step (it looks like a bad equity reading), so after a deposit of more than about 10%, or a
long gap during a strong rise, every run stops with "implausible new peak". Raise the mark
explicitly after checking the account; the bot never does it itself. That owner command
isn't built yet (tech debt): tell the agent before a large deposit. A withdrawal looks like a drawdown: plan it with the agent first.

**If the ledger is lost or damaged later** (ledger-restore.md): the bot stops (no orders) and alerts. Do not
re-run setup. Restore the latest version from the versioned bucket (ADR-0013) and check its
high-water mark against your own records before restarting; if in doubt, tell the agent
and keep the kill switch off.
