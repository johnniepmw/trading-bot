# trading-bot

Risk-first automated trading bot: a long-or-cash trend system on UK-listed ETFs via the
Trading 212 API, run as a scheduled Cloud Run job on GCP.

Status: Phase 0 (foundations). No strategy and no trading yet; demo account only until
the owner switches it on (ADR-0009). This repository is public; account data never
appears in it (ADR-0014).

This repository is built and maintained by AI coding agents under the agentic
engineering template: the owner sets goals and approves high-risk changes; agents do the
rest through a gated pipeline. Start with [AGENTS.md](AGENTS.md).

## Quick start
```bash
make setup      # toolchain via mise, dependencies, git hooks
make dev        # run the job's smoke command locally
make check      # what CI runs
make e2e        # end-to-end tests
```

## How changes ship
Branch → PR (spec + evidence) → CI, independent agent review, risk gate → auto-merge →
staging → production with smoke tests and automatic rollback (the job variant of the
deploy arrives in Phase 2, ADR-0011). Details:
[docs/workflow.md](docs/workflow.md) and [docs/operations.md](docs/operations.md).

## Owner quick reference
- New project setup: [docs/runbooks/first-time-setup.md](docs/runbooks/first-time-setup.md)
- Approve a high-risk PR: read its "Owner decision needed" section, then comment
  `/approve-risk <sha>` as instructed by the risk-gate comment.
- Something's wrong in production: Actions → **ops** → `rollback` or `kill`
  ([runbooks](docs/runbooks/)).
