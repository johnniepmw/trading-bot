---
paths:
  - "**/migrations/**"
---
- Migrations are high risk. Use expand/contract: (1) add new columns/tables, nullable or
  defaulted; (2) deploy code that writes both and reads new; (3) backfill; (4) switch
  reads; (5) remove old in a later PR. Each step deploys safely on its own.
- Never drop or rename in the same release that stops using something.
- Every migration is idempotent and has a tested down path or a written reason why not.
- Back up (or confirm point-in-time recovery) before destructive steps; note it in the brief.
