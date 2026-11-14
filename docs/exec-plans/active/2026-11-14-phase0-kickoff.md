# Phase 0 kickoff brief: owner to the first Claude Code session

The owner starts the first Claude Code on the web session on `johnniepmw/trading-bot`
(after the one-time environment setup in `docs/runbooks/cloud-sessions.md`) with:

```
/agentic:bootstrap trading-bot python "Risk-first automated trading bot: a long-or-cash trend system on UK-listed ETFs via the Trading 212 API, run as a scheduled Cloud Run job on GCP"

Base your work on branch claude/phase0-seed and follow docs/exec-plans/active/2026-11-14-phase0-kickoff.md.
```

This file is that brief. Turn it into the bootstrap's exec plan as you go (same path). If
`/agentic:bootstrap` didn't load, follow `.claude/skills/agentic/skills/bootstrap/SKILL.md`
by hand, and check the harness as `docs/runbooks/cloud-sessions.md` describes.

## Context: decided by the owner, don't re-litigate
- **Goals:** learning first, profit second. Strong safeguards against outsized losses.
- **Account:** up to £1,000, no leverage, Trading 212 Invest (general investment account,
  so capital gains tax applies).
- **Strategy:** a long-or-cash trend system on 6–8 UK-listed ETFs. Daily data and risk
  checks; trades weekly to monthly.
- **Hosting:** GCP europe-west2. A Cloud Run **job** started by Cloud Scheduler (not a
  service). Direct VPC egress plus Cloud NAT gives a static IP for the IP-locked live key,
  added in Phase 4.
- **Repository:** public, instead of paying for GitHub Pro (ADR 0014). Code, Actions logs,
  PR and issue comments, check summaries and workflow artifacts are world-readable.
- **Owner role:** reviews no code. Approves high-risk PRs with `/approve-risk <sha>` after
  an escalate brief. Owner-only commands run in Google Cloud Shell.
- **Real-money controls stay owner-only and outside CI:** the live switch and capital
  allocation sit in an owner-only Secret Manager secret, `operator`, which the deploy
  identity can't write. Missing or invalid means paper trading only.
- **Kill switch:** the `ops` workflow's `kill` action stops new orders. Closing out
  positions is an action only the owner takes.
- **Alerts:** Cloud Monitoring, with the Google Cloud mobile app for urgent alerts and
  email for the daily summary. Both channels are free.
- **Full plan:** https://claude.ai/artifact/USLXmedSRi1myYH5HvVtUm (Engineering & CI/CD
  tab). This brief is the part this session needs.

## Package layout (`src/tradingbot/`)
- `domain`
- `data`
- `strategy`
- `portfolio`
- `risk`
- `execution`
- `brokers`: a Broker interface, with `trading212/` and `fake/` implementations
- `ledger`
- `config`
- `app`: entry points, scheduler, alerts, kill switch

`research/` holds notebooks and is never imported by `src`.

## import-linter contracts
1. `domain` imports nothing else from the project.
2. `strategy`, `portfolio` and `risk` import only `domain` and config types. They can't
   import brokers, data I/O, HTTP libraries or environment variables.
3. Only `brokers` imports HTTP libraries. Only `app` wires in concrete brokers.
4. `research` is never imported by `src`.

## Conventions (put these in ARCHITECTURE.md as invariants)
- **Money:** `Decimal` money with an explicit currency, and a `Money` type that refuses to
  mix GBP with GBX (pence).
- **Time:** UTC only, with an injectable clock. Market hours come from the broker's
  schedule.
- **Fail closed:** any unexpected error means no new orders, plus an alert.
- **Idempotent runs:** write intended orders to the ledger before sending them, and
  reconcile with the broker at the start of every run.
- **Config:** typed and validated at start-up. Invalid config means the bot refuses to
  start.
- **Logs:** structured JSON, tagged with the run ID, written to Cloud Logging.
- **Account data stays private:** balances, positions, orders, fills, P&L and raw broker
  responses never appear anywhere public (see ADR 0014).
- **Risk ceilings are constants in code:** no leverage; a hard stop at a 20% drawdown from
  peak; position-size caps. Day-to-day limits may only be tighter than the ceilings.

## High-risk paths to add to `.github/risk-paths.txt`
```
src/tradingbot/risk/**
src/tradingbot/brokers/**
src/tradingbot/execution/**
src/tradingbot/ledger/**
src/tradingbot/config/**
src/tradingbot/app/**
flags.json
```

## ADRs (the template keeps 0001–0005)
Write these now:
- **0006:** Python with uv, ruff, mypy in strict mode, pytest and Hypothesis.
- **0007:** our own small core built on open-source libraries, not a trading engine.
- **0008:** a broker interface, with Trading 212 first and IBKR as the fallback.
- **0009:** the risk layer has a veto; ceilings live in code; the live switch is owner-only
  and outside CI.
- **0010:** `Decimal` money with an explicit currency, and UTC time.
- **0011:** run as a scheduled Cloud Run job, with a deploy window. Never deploy prod while
  an execution is running, and only while the London market is closed. The template's
  module targets services, so this needs a job variant (the module itself is built in
  Phase 2).
- **0013 (accepted):** ledger storage is SQLite in a versioned GCS bucket, with a
  generation-match lock so only one run writes at a time. Rejected alternatives:
  Firestore, and Cloud SQL (about £7–10 a month, too costly for this account).
- **0014 (accepted): public repository.** Chosen over GitHub Pro ($4 a month) because a
  public repo gets rulesets, auto-merge, secret scanning and push protection on the free
  plan. Exposure is small: no secrets live in the repo, and the strategy is a well-known
  public approach. Consequences:
  - Account data (balances, positions, orders, fills, P&L, raw broker responses) never
    goes to Actions logs, PR or issue comments, check summaries or artifacts. It goes to
    Cloud Logging and the ledger only. CI and tests use fake data.
  - Owner settings: PR and issue creation restricted to collaborators; fork PR workflows
    need approval for all external contributors; secret scanning and push protection on;
    the owner's commit email kept private.
  - Revisit if the strategy ever becomes something worth keeping private: then GitHub Pro
    and a private repo.

Leave **0012 as proposed, deferred**: a static egress IP via Direct VPC egress and Cloud
NAT, about $4 a month. The owner reviews it at Phase 4.

ADR 0015, the first strategy, comes in Phase 1.

## Extra lines for AGENTS.md
- Never place orders against the live Trading 212 API. Demo only until the owner's
  `operator` secret says otherwise.
- This repo is public. Never write account data (balances, positions, orders, fills, P&L,
  raw broker responses) to Actions logs, PR or issue comments, check summaries or
  artifacts. Cloud Logging and the ledger only.
- The `optimize` skill must never tune strategy parameters without a held-out period it
  can't see.
- Bug fixes in the risk layer start with a failing test. The risk layer must have 100%
  branch coverage once it exists.

## Phase 0 scripts (already on `claude/phase0-seed`)
- `scripts/phase0_api_check.py`: demo-only, standard library only. The owner runs it in
  Cloud Shell with a demo key; its report shapes the broker adapter.
- `tests/scripts/mock_t212.py` and `tests/scripts/test_phase0_api_check.py` test it
  offline with no network. They pass and are ruff-clean under the Python pack's config;
  move them if the pack's layout wants other paths.
- Note in the exec plan that recorded broker contract tests, the nightly demo contract
  check and backtest regression arrive in Phases 1–3.

## Template text to update for this repo
- The bootstrap skill's owner checklist and `docs/runbooks/first-time-setup.md` assume a
  private repo on GitHub Pro. This repo is public: drop the Pro step and list the
  public-repo settings from ADR 0014 instead.

## Out of scope this session
- Strategy code
- The GCP job module (Phase 2)
- Any API keys or GCP changes

## Done when
- The bootstrap PR is merged. It needs the owner's `/approve-risk`.
- Follow-up PRs have merged through all three gates.
- A deliberately planted fake secret and a deliberately broken import rule each fail CI.
- The owner checklist is reported:
  - `make github-setup`
  - the public-repo settings (ADR 0014)
  - the agent-review token
  - the GCP project
