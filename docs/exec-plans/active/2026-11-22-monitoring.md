# Monitoring: decision record, alerts, pause button, daily glance and dashboard

- **Status**: active (spec agreed with the owner on 2026-11-22; build is spread over Phases 2–3)
- **Branch / PR**: `docs/monitoring-spec` (this spec), then one PR per step in the Plan
- **Risk class**: this PR standard (docs only). Steps 1–5 high: they touch `ledger/**`, `app/**`, `brokers/**`, `infra/**` or secrets.
- **Started**: 2026-11-22

## Goal
In 30 seconds a day the owner can tell whether the bot is safe and working. When they
want to, they can see why it did what it did, and pause it from their phone. Anything
urgent reaches them as an alert. Account data goes only to these places: Cloud Logging,
the ledger (private, versioned bucket), the dashboard behind IAP (owner only), and the
owner's own inbox and phone (the daily email and alert notifications). Nowhere else,
including any public surface (ADR-0014); step 5's tests check the email goes only to the
configured owner address. The questions come from the owner's dashboard brainstorm
(planning chat, 2026-11-22).

## Acceptance criteria
| # | Criterion (observable, pass/fail) | Evidence |
|---|---|---|
| AC1 | Every run writes a run record to the ledger: run ID, start and end (UTC), outcome (completed, or stopped with its reason), app version. A run that dies leaves a start with no end, and the next run reports it | Step 1: each existing cycle drill also asserts its stopped record and reason; a killed-run test |
| AC2 | On a signal day the ledger stores, per ETF: the signal-day close, 210-day average, in/out, target weight before and after the volatility cap, and the scale factor. Per proposed order it stores the risk decision (passed, shrunk, rejected), the reason, the proposed and approved quantities and the quote used. Shrinks are also logged at WARNING; today nothing records them | Step 1: cycle test on the fake broker; the stored signals and weights equal the backtest's for the same synthetic bars |
| AC3 | Every run stores a snapshot: cash, and per ETF the quantity, quote and GBP value. It also stores a non-binding preview of what the month-end rule would do on today's latest closes. A preview failure logs WARNING and never stops the run or affects orders | Step 1: snapshot and preview tests; a planted preview error leaves orders unchanged |
| AC4 | Each settled order stores its average fill price and fees when the broker reports them. The fake broker reports them. If Trading 212 doesn't, the fields stay empty and the adapter contract says so; nothing is estimated | Step 1: ledger and contract tests; Trading 212's fields confirmed by the owner's Phase 0 API check |
| AC5 | Phone push alerts (Google Cloud app) fire for: a stopped run (keyed on the `cycle stopped` ERROR line, since a stopped cycle exits normally); a missed run; ledger and broker disagreeing (`not reconciled`, `equity check failed`); drawdown crossing 5%, 10% (cap halves), 15% and 20% (buy stop), once per crossing; the daily-loss pause; an order ending short or still open at the next run; any kill-switch or pause change; a failed or rolled-back prod deploy. Stale or bad data, a failed nightly contract check and the cloud budget alert go by email | Step 2: policies in OpenTofu; a staging drill plants each log line and the notification arrives |
| AC6 | New orders are off while any pause request exists in the pause location, or while that location can't be read. This is checked wherever `new-orders` is read. The dashboard's identity has create-only access there (`roles/storage.objectCreator`), so it can't delete or overwrite a request. Only the `ops` workflow clears requests | Step 3: unit tests (present, absent, list error = paused); staging drill: a delete or overwrite with the dashboard identity gets 403; resume via `ops` |
| AC7 | The dashboard is a Cloud Run service with IAP enabled on the service. Only the owner's Google account gets in; anyone else, or no sign-in, is refused. Its identity can only read the ledger and price history. It holds no broker credentials and never takes the ledger lock | Step 4: staging checks with no sign-in and with another account; IAM in OpenTofu; a test that a write with its identity fails |
| AC8 | On a 390 px-wide phone the dashboard shows the following. The glance: 7 lines, each green, amber or red, in £ and %, with an as-of time. The weekly view: inside the backtest's expected range; per ETF in/out, distance to the 210-day average and the month-end preview; risk decisions; value over time with drawdowns, against cash, 1/N buy-and-hold of the 7 ETFs and SSAC; days of paper trading out of 90; deploys and setting changes. The month-end view: each trade's reason, intended price against fill, and fees against the 0.15% assumption. One incident view per stop reason links to the logs, `docs/runbooks/cycle-alerts.md` and the kill workflow | Step 4: view-model tests from fake ledgers (all-green; each amber and red case); a phone-width e2e screenshot made with fake data |
| AC9 | The pause button needs a confirm step, works by POST only (a GET does nothing) and writes one pause request. The bot honours it at its next order check, and the change alert fires. There is no resume control anywhere outside `ops` | Step 4: e2e on staging; a test that a GET to the pause route changes nothing |
| AC10 | After each scheduled run the owner gets an email whose top block is the same 7 glance lines, with the as-of time and a link to the dashboard. It never contains a pause link | Step 5: rendering tests from fake ledgers; a staging email received |
| AC11 | No account data in Actions logs, PR or issue comments, check summaries or artifacts: dashboard and email tests run on fake ledgers only (ADR-0014) | Every step: the CI job's artifacts and logs contain fake data only |

## Non-goals
- Live or intraday prices: the Trading 212 app does that. The dashboard is a snapshot after each run.
- Any control except the one-way pause: no resume, trading or settings from the dashboard or email.
- CGT matching (same-day, 30-day, s104) and the tax view: separate plan with the ledger's CGT report; the dashboard adds it then.
- Rebuilding Trading 212's holdings and fills lists: link out to the app instead.
- Third-party alert apps (Telegram, ntfy, Pushover).

## Rollout and rollback
- Flags: a release flag `daily-email` (expires 60 days after it is created) until the owner confirms the email. The pause check fails closed, like the `new-orders` kill switch.
- Data changes: expand only. New ledger tables are added and existing rows never change. A ledger with only step 7's tables opens and gains the new ones (test).
- Rollback: revert the PR; the extra tables are ignored. Remove the dashboard service with OpenTofu. A reverted pause check leaves `new-orders` working.

## Decisions
- Step 1 must merge before the first scheduled run. Logs alone won't do: Cloud Logging's default bucket keeps 30 days, and the dashboard needs months, so everything it shows comes from the ledger (versioned GCS, ADR-0013). Phase 2's plan must schedule step 1 before the job's first run.
- The dashboard reads only what the job wrote (ledger, price history, stored backtest bands). It never calls the broker, so it needs no Trading 212 key. That is why the job stores the month-end preview and snapshot.
- Hosting: Cloud Run service with IAP enabled directly on the service, so no load balancer and no extra cost. A project without a Google organisation needs a custom OAuth client (owner step in the runbook).
- Server-rendered HTML with inline SVG charts; no third-party scripts or CDNs (privacy, supply chain).
- Benchmarks (owner, 2026-11-22): cash at 0% (money left idle, as research's default risk-free rate); 1/N buy-and-hold of the 7 ETFs (research's benchmark, used in the gate); SSAC, iShares MSCI ACWI on the LSE in GBP, held alone. The owner already holds SSAC, and it covers all countries like VWRP. SSAC is benchmark-only: it is added to the price-history export and live prices, but it is never in `CycleConfig.universe`, with a test that it can't reach an order. Drawdown is always shown beside return, because the strategy's claim is about drawdown (docs/strategies/trend-long-or-cash.md).
- Drawdown alerts at 5% and 15% (owner accepted, 2026-11-22), alongside the 10% and 20% risk levels. They are alert thresholds in config, not risk limits, so `risk/limits.py` doesn't change. Each upward crossing alerts once; the ledger remembers the last level alerted.
- Stops keep exit code 0, so the alert keys on the log line. A non-zero exit would make Cloud Run retry the task.
- The pause button has its own create-only location, never `flags.json`, because writing that file can switch trading either way. A button, not a link: email security scanners open links. Unreadable means paused, like the kill switch.
- Glance in the email, detail on the dashboard; £ and % both until capital grows (owner, 2026-11-22).
- New subpackages (for example a pure `report` module that builds the glance and view-models) are added to import-linter contracts 1 and 3a (ARCHITECTURE.md).

## Open questions
- How the daily email is sent. Cloud Monitoring emails alert notifications but can't carry a formatted summary. Default if unanswered: a dedicated Gmail account for the bot sends to the owner over SMTP, with an app password the owner sets via `make secret-set`. It needs no new third party, and only that mailbox is exposed if the password leaks. Alternatives: a transactional email service (a third party sees account data), or no email (a push notification plus the dashboard). Step 5 waits for this; steps 1–4 don't.

## Plan
0. This spec — check: `make docs-check`
1. Decision record in the ledger: AC1–AC4 (high; Phase 2, before the first scheduled run). AC4's Trading 212 fields wait for the owner's API check; the rest doesn't — check: `make check`, cycle drills
2. Alerts: drawdown-crossing log lines and OpenTofu policies, with Phase 2's job module (AC5) — check: staging drill
3. Pause requests: bucket and IAM, bot check, `ops` resume (AC6). The `smoke` health check reports the effective state too: `new_orders` is false while a pause request exists or the location can't be read — check: unit tests, staging drill
4. Dashboard service: access, views, pause button (AC7–AC9, AC11) — before paper trading starts (Phase 3) — check: view-model tests, staging e2e
5. Daily email (AC10, AC11), after the open question — check: rendering tests, staging email

## Progress log
- 2026-11-22 step 0: spec written from the owner's brainstorm and checked against step 7's code (`ledger/store.py`, `app/cycle.py`, `app/flags.py`) — evidence: this file
