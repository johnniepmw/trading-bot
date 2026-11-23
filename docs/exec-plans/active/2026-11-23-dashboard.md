# Dashboard: the glance and the pause button on the owner's phone (monitoring step 4)

- **Status**: active (spec, slice 1 of 3; owner asked to continue after the daily email, 2026-11-23)
- **Branch / PR**: ccr-7fe1197d-l39xit / infra PR, then app PR, per slice
- **Risk class**: high (infra, IAM, a new public endpoint behind IAP, and the pause control)
- **Started**: 2026-11-23

## Goal
A page only the owner can open, on a 390 px phone, showing the latest glance and a one-way
pause button (monitoring AC7, AC9 and the glance part of AC8). Later slices add the weekly
view (value over time against benchmarks, signals, risk decisions) and the month-end and
incident views (the rest of AC8).

## Slices
1. **Glance and pause** (this spec): service, access, the seven lines, a run history list,
   the pause button.
2. Weekly view: value over time with drawdowns against cash, 1/N buy-and-hold and SSAC
   (needs the job to store the benchmarks' closes in the ledger), per-ETF in/out and distance
   to the 210-day average, the month-end preview, risk decisions, days of paper trading.
3. Month-end and incident views: each trade's reason, intended price against fill, fees
   against the 0.15% assumption; one view per stop reason linking the runbook and `ops`.

## Acceptance criteria (slice 1)
| # | Criterion (observable, pass/fail) | Evidence |
|---|---|---|
| AC1 | A Cloud Run service per environment (`trading-bot-dashboard-<env>`) runs the same image (`python -m tradingbot.app serve`). IAP is enabled on the service; the only invoker is IAP's service agent (no `allUsers`); IAP lets in only the owner's account (the bootstrap's alert email, sensitive). No sign-in or another account is refused | infra tests; staging check with no sign-in and the owner's account (owner) |
| AC2 | The service's own identity can only read the ledger bucket and create objects in the pause bucket (`roles/storage.objectCreator`): no lock, flags, secrets or broker key | infra tests; a test that the server never writes the ledger |
| AC3 | `GET /` renders the latest run's seven glance lines (the same `report.glance` as the email) with colours, the as-of time and the last 10 runs (time, outcome, reason escaped), readable at 390 px with no horizontal scroll, no scripts from anywhere and no external resources | rendering tests from fake ledgers; a 390 px screenshot from fake data |
| AC4 | The ledger is read from its latest generation into a scratch copy, never locked or written; an unreadable ledger shows an error page, never stale figures | tests |
| AC5 | Pause: `GET /` shows a "Pause new orders" button; it POSTs to `/pause/confirm`, which shows a confirm page; its POST to `/pause` writes one new object (unique name, create-only) to the pause bucket and shows "paused". A GET to `/pause` or `/pause/confirm` changes nothing. POSTs need `Sec-Fetch-Site: same-origin` (or a matching `Origin`), else 403. No resume control anywhere | tests: GET changes nothing; cross-site POST refused; one object per confirmed POST |
| AC6 | Responses carry `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, a strict Content-Security-Policy (`default-src 'none'; style-src 'unsafe-inline'; form-action 'self'`) and `Referrer-Policy: no-referrer` | tests |
| AC7 | Deploys update the service image with the job's, and roll back with it | deploy script tests |
| AC8 | Fake data only in tests and CI (ADR-0014) | tests |

## Non-goals (slice 1)
- Charts, benchmarks, month-end and incident views (slices 2–3).
- Verifying IAP's signed header in the app: the service accepts requests only from IAP's
  service agent (no public invoker), so an unsigned request can't reach it. Recorded as a
  possible hardening.

## Owner steps (one-off, before slice 1's infra applies)
1. Cloud console → Google Auth Platform: create the OAuth consent screen ("External",
   "Testing", the owner's address as the only test user).
2. Create an OAuth client (Web application) for IAP and give its ID and secret to IAP's
   settings (runbook with exact clicks, written with the infra PR).
3. After the apply: open the staging URL on the phone, sign in, see the glance.

## Rollout and rollback
- Infra PR first (service, identity, IAM, IAP), then the app PR (the `serve` command and
  views), each per slice. Staging first, then prod.
- Rollback: revert; or remove the owner's IAP access (the page goes dark, the bot is
  unaffected: the job never depends on the dashboard).

## Decisions
- Same image as the job, a different command, so one build and one deploy path.
- Standard library HTTP server (`ThreadingHTTPServer`), server-rendered HTML, inline CSS, no
  JavaScript: one user, a handful of requests a day; no new dependencies (ADR-0007).
- The pause button is a POST behind a confirm page (email security scanners open links;
  monitoring Decisions), with same-origin checks; IAP already authenticates every request.
- The glance on the page is built by `report.glance`, so the page and the email can never
  disagree.

## Open questions
- None for slice 1.

## Plan
1. Infra: a `cloud-run-dashboard` module (service, identity, IAM, IAP), staging and prod;
   owner runbook for the OAuth client (AC1, AC2, AC7). Check: infra tests, CI plan.
2. Owner: consent screen and OAuth client.
3. App: `serve` command, views, pause (AC3–AC6, AC8). Check: unit tests, a 390 px screenshot.
4. Deploy script: update the service's image with the job's (AC7).
5. Staging check on the owner's phone, then prod.

## Progress log

## Review

## Outcome
