# Daily email: the glance after every scheduled run (monitoring step 5)

- **Status**: shipped (2026-11-24: staging and prod emails received after the 07:15 runs)
- **Branch / PR**: ccr-7fe1197d-l39xit / infra PR, then app PR
- **Risk class**: high (infra and secrets; `app/**`)
- **Started**: 2026-11-23

## Goal
After every scheduled run, the owner gets one short email: seven lines on the account's
state, each marked green, amber or red, in £ and %, with the as-of time (monitoring AC10).
It is the owner's window onto paper trading until the dashboard exists. Sent from a Gmail
account used only by the bot (owner's choice, 2026-11-23).

## The seven lines (proposed here; the monitoring spec named the count, not the lines)
| # | Line | Green | Amber | Red |
|---|---|---|---|---|
| 1 | Run: outcome and time | completed | — | stopped (or died), with its reason |
| 2 | Today: equity £, change since day start £ and % | ≥ 0 | a loss under the 2% daily-loss limit | daily-loss pause reached, or no day start |
| 3 | Drawdown: % and £ below the high-water mark; risk state | < 5% | 5–10% | ≥ 10% (positions halve), ≥ 20% (buy stop) |
| 4 | Since setup: return £ and % against the seeded equity | ≥ 0 | < 0 | (never red: drawdown covers it) |
| 5 | Holdings: invested % and cash £; funds held (n of 7) | snapshot checked | — | snapshot unverified (equity check failed) |
| 6 | Orders: sent today (this run's awaiting fill), open from earlier runs | none open from earlier | — | any open from earlier, or unknown |
| 7 | Next month end: date, and how many funds are in trend on today's preview (vs held) | preview stored | no preview today | — |

Every amount comes from the ledger the run just wrote (no broker or price calls), so the
email can never disagree with the decision record.

## Acceptance criteria
| # | Criterion (observable, pass/fail) | Evidence |
|---|---|---|
| AC1 | A pure `report` module builds the seven lines and their colours from ledger records; no I/O (import-linter: like contract 6) | unit tests from fake ledgers: all-green; each amber and red case |
| AC2 | After a scheduled `run` (completed or stopped once the ledger is open), the job sends the email over SMTP to `smtp.gmail.com:465` (TLS) from the bot's address to `EMAIL_TO`. The sender and its app password come from the mounted secret `gmail-sender` (`ADDRESS:APP_PASSWORD`); the recipient from `EMAIL_TO`, set by OpenTofu from the bootstrap's alert email, never written in the repo | tests with a fake SMTP; infra tests |
| AC3 | Sending never changes the run's outcome or exit code: a failure logs WARNING with the alert kind `email_failed` (so Cloud Monitoring's alert email says the glance didn't arrive) | tests: SMTP refuses, times out, secret missing |
| AC4 | Behind the release flag `daily-email` (default off; expires 60 days after creation); the owner turns it on with `ops set` once the staging email looks right | flag tests both ways |
| AC5 | Dry runs, `setup` and `smoke` send nothing | tests |
| AC6 | No account data in Actions logs, PRs or artifacts: tests use fake ledgers and a fake SMTP only (ADR-0014); the email body holds no pause link and no secrets | tests |
| AC7 | Staging (fakes) sends a real email to the owner before prod's flag is turned on | owner sees it |

## Non-goals
- HTML styling beyond simple coloured markers; charts (the dashboard does those).
- A dashboard link: added when the dashboard exists (monitoring step 4).

## Rollout and rollback
- Infra PR first: the secret `gmail-sender` (staging and prod) and the `EMAIL_TO` env var.
  The owner stores the secret before the apply (or the apply fails and is re-run, as before).
- Then the app PR with the flag off; `ops set daily-email true` in staging, check the email,
  then prod.
- Rollback: flag off, or revert.

## Decisions
- Gmail over SMTP with an app password (owner, 2026-11-23; the monitoring plan's default): no
  new third party sees account data; only the bot's mailbox is exposed if the password leaks.
- One secret holding `ADDRESS:APP_PASSWORD`, like the Trading 212 key; spaces in the app
  password are ignored (Google shows it in groups of four).
- Sent after the run's final checkpoint and before the lock is released, from the ledger in
  memory; the email is plain text with an `[OK]` / `[!]` / `[STOP]` marker per line, so it
  reads the same in any mail app.

## Open questions
- None.

## Plan
1. Infra: secret `gmail-sender` in staging and prod; `EMAIL_TO` env var (AC2). Check: infra tests, CI plan.
2. Owner: create the Gmail account, turn on 2-step verification, make an app password, `make secret-set NAME=gmail-sender ENV=staging` and `ENV=prod`.
3. App: `report` module, SMTP sender, flag, wiring (AC1–AC6). Check: unit tests, `make check`.
4. Staging email (AC7), then prod's flag.

## Progress log
- 2026-11-23 step 1: infra (#62); the owner made the bot's Gmail account and stored `gmail-sender` in staging and prod; both applies green
- 2026-11-23 step 3: `report/glance.py` (pure, contract 8), `app/daily_email.py` (secret, SMTP over TLS, figures from the ledger), the job sends after a run that opened the ledger, behind `daily-email` (off) — evidence: `tests/report/test_glance.py` (every colour of every line), `tests/app/test_daily_email.py` (fake SMTP), `tests/app/test_job.py` email tests (flag off sends nothing; a stopped run still emails; a failed send never changes the run and alerts `email_failed`; dry runs and setup send nothing)

- 2026-11-23 step 4 (staging email): the first send failed (`email_failed` alert worked). The logged `SMTPServerDisconnected` came from closing a connection Gmail had already dropped after refusing the login; the owner's Cloud Shell check found both stored app passwords were 17 characters (Google's are 16 letters). Fix: closing never hides the real error, the log names the stage and SMTP code, and a stored password that isn't 16 letters is refused before any SMTP attempt with a plain message. The owner re-stores both values

- 2026-11-23 AC7 passed: after the owner re-stored both app passwords (Cloud Shell check: 16 letters, login OK for staging and prod), a staging `run` execution sent the glance and the owner received it. Prod's `daily-email` is switched on by the owner with `ops set`

## Review
- Round 1 (reviewer): 2 blocking, both fixed: this run's orders awaiting the 08:00 open no longer turn the Orders line red (only earlier runs' open orders and unknown outcomes do); `glance_input` now has a test on a real ledger checking every figure. Non-blocking fixed: the run's own stop reason wins over its record (a failed final save); a broker that can't be built still emails a STOP; the flag check sits inside the guard; the "skipped" amber branch removed (non-trading days and duplicates never open the ledger, so they never email: the alert policy covers missed runs); tests that a failed email keeps a stopped run stopped and that an error gathering figures is caught. Recorded: `invested` leaves out a position with no GBP value (the line is red then anyway)

- Security review: 1 medium (the owner's address printed in plan and apply logs, which are public: `alert_email` is now sensitive in the module and the bootstrap output; old logs to be deleted with the owner's agreement), 3 low fixed or recorded (the email is sent after the lock is released; a runbook note on revoking a leaked app password without disabling the secret; staging and prod get separate app passwords), hardening: ASCII-only secret; `src/tradingbot/report/**` is now high risk

## Outcome
