# 0013. Ledger storage: SQLite in a versioned GCS bucket with a generation-match lock

- **Status**: accepted
- **Date**: 2026-11-21
- **Deciders**: owner
- **Enforcement**: `src/tradingbot/ledger/**` is high risk; tests for the lock arrive with the ledger (Phase 1–2)

## Context
Runs must be idempotent: write intended orders before sending them, reconcile with the
broker at the start of every run. That needs durable, transactional storage, written by
one run at a time, at near-zero cost for a £1,000 account.

## Options considered
1. **SQLite file in a versioned GCS bucket**, downloaded at the start of a run and
   uploaded with `ifGenerationMatch` so only one run can write: pennies a month; full
   SQL locally; every version kept.
2. **Firestore**: serverless and cheap; awkward for relational queries and reconciliation.
3. **Cloud SQL**: real database; about £7–10 a month, too costly for this account.

## Decision
Option 1. A run that loses the generation-match race aborts and alerts (fail closed).

## Consequences
- Good: cheap, inspectable, versioned history doubles as a backup.
- Bad / costs: one writer at a time by design; a crash between upload attempts needs
  the reconcile step to recover.
- Revisit when: more than one process needs to write, or the file grows past a few MB.
