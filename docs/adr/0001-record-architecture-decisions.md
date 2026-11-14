# 0001. Record architecture decisions

- **Status**: accepted
- **Date**: 2026-11-14
- **Deciders**: owner
- **Enforcement**: `make docs-check` (every ADR has a status and is in the index); reviewer agent checks changes against ADRs

## Context
Code in this repo is written by AI agents across many sessions that don't share memory.
Without written reasons, later sessions re-litigate settled choices or quietly drift
from them, and the codebase loses consistency.

## Options considered
1. **ADRs in the repo** — versioned with the code, read by agents; small writing cost.
2. **Decisions in chat or an external doc** — invisible to agents in later sessions.
3. **Code comments only** — local, can't capture alternatives or cross-cutting choices.

## Decision
Record significant decisions as short ADRs in `docs/adr/`, numbered, never rewritten
once accepted, superseded by newer ADRs when they change.

## Consequences
- Good: agents and the owner can see why things are the way they are; reviewers can
  check changes against decisions.
- Bad / costs: a few minutes per decision; the index needs upkeep (checked by CI).
- Revisit when: ADRs become a formality nobody reads (garden flags stale ones).
