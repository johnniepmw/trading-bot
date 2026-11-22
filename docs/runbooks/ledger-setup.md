# Runbook: set up the ledger and seed the high-water mark (owner, once)

**When**: once, before the bot's first run in an environment (staging, then prod). Never
again for the same account. The command that does this arrives with Phase 2's job wiring;
this page states the rules it follows.

**Why it is an owner step**: the high-water mark is the peak the 20% drawdown stop is
measured from. If code could set it, a lost or damaged ledger could be "repaired" by
re-seeding from today's lower equity, which would silently lift the stop. So the bot
refuses to start without a ledger file, never creates one during a run, and never sets
the first mark itself (`tests/execution/test_executor.py` checks no daily code can).

1. Confirm the account's current equity in the Trading 212 app (the paper or live account
   this environment uses).
2. Run the setup command (Phase 2) with that equity. It creates the ledger file and records
   the equity as the first high-water mark and as that day's equity. It refuses if a
   ledger already exists.
3. The next run starts from that mark. Day-start equity for the daily-loss pause is the
   previous day's recorded equity.

**If the ledger is lost or damaged later**: the bot stops (no orders) and alerts. Do not
re-run setup. Restore the latest version from the versioned bucket (ADR-0013) and check its
high-water mark against your own records before restarting; if in doubt, tell the agent
and keep the kill switch off.
