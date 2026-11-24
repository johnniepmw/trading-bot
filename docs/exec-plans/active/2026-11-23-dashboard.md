# Dashboard: the glance and the pause button on the owner's phone (monitoring step 4)

- **Status**: active (slice 1 shipped 2026-11-24; slices 2 and 3 to come)
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
| AC2 | The service's own identity can only read the ledger and lock buckets (`objectViewer`: the reference mark, whether a run holds the lock) and list and create objects in the pause bucket (`objectViewer` + `objectCreator`, never delete): no flags, secrets, broker key or ledger writes | infra tests; a test that the server never writes the ledger |
| AC3 | `GET /` renders the latest run's seven glance lines (the same `report.glance` as the email) with colours, the as-of time and the last 10 runs (time, outcome, reason escaped), readable at 390 px with no horizontal scroll, no scripts from anywhere and no external resources | rendering tests from fake ledgers; a 390 px screenshot from fake data |
| AC4 | The ledger is read from its latest generation into a scratch copy, never locked or written; an unreadable ledger shows an error page, never stale figures | tests |
| AC5 | Pause: `GET /` shows a "Pause new orders" button; it POSTs to `/pause/confirm`, which shows a confirm page; its POST to `/pause` writes one new object (unique name, create-only) to the pause bucket and shows "paused". A GET to `/pause` or `/pause/confirm` changes nothing. POSTs need `Sec-Fetch-Site: same-origin`, else 403 (no `Origin` fallback: every current browser sends the header, so the stricter rule costs nothing). No resume control anywhere | tests: GET changes nothing; cross-site POST refused; one object per confirmed POST |
| AC6 | Responses carry `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, a strict Content-Security-Policy (`default-src 'none'; style-src 'unsafe-inline'; form-action 'self'`) and `Referrer-Policy: no-referrer` | tests |
| AC7 | Deploys update the service image with the job's, and roll back with it | deploy script tests |
| AC8 | Fake data only in tests and CI (ADR-0014) | tests |

## Non-goals (slice 1)
- Charts, benchmarks, month-end and incident views (slices 2–3).
- Verifying IAP's signed header in the app: the service accepts requests only from IAP's
  service agent (no public invoker), so an unsigned request can't reach it. Recorded as a
  possible hardening.

## Owner steps (one-off, in this order)
1. Bootstrap PR (the dashboards' identities, the IAP API): `make infra-plan-bootstrap` preview, then `make infra-bootstrap`.
2. Cloud console: OAuth consent screen (External, Testing, the owner as the only test user) and an OAuth client (Web application) whose redirect URI is `https://iap.googleapis.com/v1/oauth/clientIds/<CLIENT_ID>:handleRedirect` (the owner has done this before for another dashboard).
3. After the service exists: the owner's `scripts/dashboard-iap.sh <env>` (modelled on the owner's own setup script): `gcloud run services update --iap`, `gcloud iap settings set` with the OAuth client (the secret never enters the repo, state, CI or Secret Manager), the IAP service agent as the only invoker, `roles/iap.httpsResourceAccessor` for the owner only; and it removes any `allUsers` binding.
4. Staging check on the phone: the owner gets in; another Google account and no sign-in are refused; a direct call to the run.app URL with an ID token is refused.

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
- 2026-11-24 slice 1 live and checked: #69 applied (staging and prod); the owner ran `scripts/dashboard-iap.sh` for both; the deploy moved both dashboards to the real image after `dashboard-check` reported "behind IAP and not public". Owner checks passed: the page on the phone (screenshots of both), a private window refused, a direct call with an ID token refused (not 200), and in staging Pause → confirm → "paused", the `pause_requested` alert email, `ops clear-pause`, button back. AC1–AC8 met
- 2026-11-24 the owner merged #68 and applied the bootstrap (a disconnected Cloud Shell left a state lock: cleared with `tofu force-unlock`). The service PR: `infra/modules/cloud-run-dashboard`, staging and prod wiring, the alert filter for the dashboard's lines, `scripts/dashboard-check.sh` after every infra apply and nightly (security review of the module: 1 medium, 2 low, all taken) — evidence: `tests/infra/test_dashboard_module.py` (every infra file), `tests/scripts/test_job_ops.py` (the check, IAP gating)
- 2026-11-23 (overnight, owner asleep) app side of slice 1, with the bootstrap PR: `app/dashboard.py` and `serve`, `report/page.py`, `app/glance_facts.py` (shared with the email; a run in progress or one that died), `app.pause.request_pause` (create-only, unique name, `pause_requested` alert), deploy and rollback move the service after the job's check, `scripts/dashboard-iap.sh` (the owner's IAP script), ADR-0020, `docs/runbooks/dashboard-access.md`, a 390 px screenshot from fake data (`docs/images/dashboard-slice1-390px.png`) — evidence: `tests/app/test_dashboard.py` (18), `tests/e2e/test_dashboard.py` (serve as a process, pause through confirm, smoke reports paused), `tests/scripts/test_job_ops.py` (7 new), `tests/scripts/test_dashboard_iap.py` (6). Waiting for the owner's bootstrap apply before the service's infra PR (CI's plan needs the new identities)

## Review
- Architect, round 1: revise, 10 blocking. Taken: (1) the bootstrap creates the dashboards' identities and enables IAP (owner PR); (2) and (3) IAP settings and the owner's access are set by the owner's own script, never CI or OpenTofu state (the owner's working setup from another project shows IAP on Cloud Run works without an organisation); the staging check includes a direct-call refusal; (4) the dashboard reads the lock bucket too (the reference-mark check), read-only; (5) the glance uses the run's own date, shows a run in progress or one that died; (6) CSP adds `frame-ancestors 'none'; base-uri 'none'`, `X-Frame-Options: DENY`, POSTs fail closed on `Sec-Fetch-Site`/`Origin`; (7) a `pause_requested` alert, create-only write with a unique name and retries, a 303 after the POST, "NOT paused" on failure; (8) the service follows the job's image only after the job's check passes, and a service failure never touches the job; rollback moves both; (9) `serve` is routed before the job's flags and wiring, server in `app/dashboard.py`, HTML in `report/page.py`; (10) an e2e test of the pause flow on a local store. ADR-0020 records the dashboard as a service

- Round 2 on the PR (reviewer + security-reviewer), all fixed: a lock older than STALE_AFTER means the run died (was: "in progress" for days after a timeout); an unreadable pause location shows "unknown" and keeps the button (was: the job's fail-closed check said "paused"); the run table's latest row matches the glance; bad or negative Content-Length, HEAD and malformed requests answered with the security headers; a 15 s socket timeout; the job never imports the dashboard (lazy import in `serve`); the email passes `in_progress` for its own run; AC2 and AC5 texts corrected; a deploy/rollback tripwire fails loudly if the dashboard is ever public (the deployer may read the service's IAM policy); ADR-0020 states CI's residual power over the service; IAP script removes `allAuthenticatedUsers` too and lists the accessors; docs (operations, rollback, cycle-alerts `pause_requested`); the screenshot is labelled fake. For the infra PR: pin IAP (or ignore its changes), never manage the invoker authoritatively, tests forbidding public members, and the alert filter for the service's `pause_requested`

## Outcome
