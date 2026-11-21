# 0014. Public repository

- **Status**: accepted
- **Date**: 2026-11-21
- **Deciders**: owner
- **Enforcement**: AGENTS.md non-negotiables; CI and tests use fake data only; owner repository settings below

## Context
Rulesets, auto-merge, secret scanning and push protection need GitHub Pro ($4 a month)
on a private repository, but are free on a public one. No secrets live in the repo, and
the strategy is a well-known public approach.

## Options considered
1. **Public repository**: free gates and scanning; code, Actions logs, PR and issue
   comments, check summaries and workflow artifacts are world-readable.
2. **Private repository with GitHub Pro**: private; $4 a month.
3. **Private without Pro**: no rulesets or auto-merge; the delivery pipeline doesn't work.

## Decision
Option 1.

## Consequences
- Account data (balances, positions, orders, fills, P&L, raw broker responses) never
  goes to Actions logs, PR or issue comments, check summaries or artifacts. It goes to
  Cloud Logging and the ledger only. CI and tests use fake data.
- Owner settings: PR and issue creation restricted to collaborators; fork PR workflows
  need approval for all external contributors; secret scanning and push protection on;
  the owner's commit email kept private.
- Revisit when: the strategy becomes something worth keeping private. Then GitHub Pro
  and a private repo.
