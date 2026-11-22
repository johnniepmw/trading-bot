# 0017. The ledger in GCS: a lock object, per-intent checkpoints and a reference mark

- **Status**: accepted
- **Date**: 2026-11-22
- **Deciders**: agent (within delegated authority; refines ADR-0013, which the owner accepted)
- **Enforcement**: `tests/app/test_remote_ledger.py`, `tests/app/test_ledger_drills.py`, `tests/execution/test_executor.py` (checkpoint order; only `app/remote_ledger.py` creates a ledger); `src/tradingbot/app/**` and `src/tradingbot/ledger/**` are high risk

## Context
ADR-0013 keeps the SQLite ledger in a versioned bucket and uploads it with a generation
match. That alone doesn't stop two runs: both can download the same generation and work
for minutes before the second upload fails, and by then both may have sent orders. A run
that dies between sending an order and its single end-of-run upload leaves no remote trace
of the intent. And restoring an older ledger version (by mistake, or to "fix" something)
would quietly bring back an older, lower high-water mark and loosen the drawdown stop.

## Options considered
1. **Generation match on one upload at the end** (ADR-0013 as written): simplest; misses
   all three problems above.
2. **A create-only lock object, an upload at every checkpoint, and a reference mark kept
   outside the file**: a few more storage calls per order (pennies); covers all three.
3. **A database with transactions (Firestore, Cloud SQL)**: rejected in ADR-0013 on cost
   and fit; nothing here changes that.

## Decision
Option 2, in `app/remote_ledger.py` over the `ObjectStore` port in `app/storage.py`:
- **Lock**: a run first creates `run.lock` in its own lock bucket with
  `ifGenerationMatch=0`, holding the execution name, run ID and holder; it deletes it last,
  by generation, so it never removes someone else's. The lock bucket is separate so the
  runtime identity's delete permission can't touch ledger versions. A lock younger than
  `STALE_AFTER` (1 hour, measured from the object's `timeCreated`) stops the run with a
  warning, `run skipped: lock held by …` (a duplicate trigger, not an alert); an older one
  stops it with an error, `stale lock: …`, and only the owner clears it (`ops unlock`,
  Phase 2 step 6). A failed release is logged as an error and never raised.
- **Checkpoints**: `execute` takes a `checkpoint` callback and calls it after reconcile,
  after each intent is recorded (before its order is sent) and after each change of an
  intent's state. Each checkpoint uploads the ledger with the generation this run last saw.
  Timeouts, connection errors, 408, 429 and 5xx are retried (3 attempts); a 412 after an
  attempt that may have landed is compared with the stored object's MD5, so our own landed
  upload is success and anything else a lost race. Any failure stops the run before the next
  order. (The plan named CRC32C; MD5 is equivalent here and in the standard library.)
- **Reference mark**: `reference-mark.json` in the lock bucket holds the highest high-water
  mark ever saved. It is raised only after the ledger upload that carries the new mark
  succeeds. A downloaded ledger whose mark is below it was restored from an older version,
  and the run stops. Ledger setup refuses when the ledger object has any version, live or
  not, or a reference mark exists.
- **Transport**: the Cloud Storage JSON API over HTTPS with the standard library from
  `app` (as `app/flags.py` does), the job identity's token from the metadata server, and
  redirects refused so the token never leaves `storage.googleapis.com`. No Google client
  library.

## Consequences
- Good: one run at a time; no order goes out without its intent saved remotely; a crash
  at any point leaves state the next run's reconcile can settle (drills in
  `tests/app/test_ledger_drills.py`); a restored ledger can't loosen the drawdown stop.
- Bad / costs: two or three uploads per order (the file is small); a run that dies holding
  the lock blocks runs until the owner clears it, by design (the stale-lock alert says so).
  The reference mark is only as safe as the lock bucket: deleting it by hand removes the
  check (the bucket's IAM, Phase 2 step 5, limits who can).
- Revisit when: the ledger file grows past a few MB (uploads per order get slow), or a
  second writer is ever needed.
