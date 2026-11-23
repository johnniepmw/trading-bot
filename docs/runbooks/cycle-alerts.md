# Runbook: what the daily cycle's alerts mean

Every check that fails stops the run with **no new orders** and logs `cycle stopped: <reason>`
at ERROR. An alert email names the **kind**; read the line itself with `make logs ENV=prod`
(Cloud Logging only, never GitHub). Nothing here needs a code change.

## Alert kinds (the email's `kind`)
| Kind | Meaning | What to do |
|---|---|---|
| `cycle_stopped` | A run stopped; the reason is in the table below | See the reason |
| *(empty)* | Cloud Scheduler couldn't start the job: today's run is missing | Check the schedule and job in the console; tell the agent |
| `stale_lock` | A run died holding the ledger's lock; runs stop until it is cleared | stale-lock.md |
| `run_died` | An earlier run never finished (reported by the next run) | Tell the agent; usually a timeout or a crash |
| `crash` | Something unexpected; the run stopped | Tell the agent |
| `pause_requested` | Someone pressed Pause on the dashboard (only you can sign in). No new orders from the next order check | If it wasn't you, revoke the dashboard's access (dashboard-access.md) and tell the agent. Resume with `ops clear-pause` |
| `email_failed` | The daily email wasn't sent (the run itself is unaffected) | The log line names why: a bad `gmail-sender` secret (re-run `make secret-set NAME=gmail-sender`), Gmail refusing the login (app password revoked), or `EMAIL_TO` unset. Tell the agent if unsure |
| `order_short` | An order ended short of a full fill | Check the order in the Trading 212 app |
| `account_veto` | Risk blocked buys for the whole account (daily loss, drawdown stop, no day-start equity) | Table below |
| `drawdown` | Equity fell through 5, 10, 15 or 20% below the peak (once per crossing; 10% halves new positions, 20% stops buys) | Read it; nothing to do unless you want to step in |
| `paused` | A pause request exists, so no new orders | Expected if you paused; `ops` → `clear-pause` to resume |
| `calendar_ending` | Mondays, once the holiday list in `config/bot.toml` has under 60 days left; past it, runs stop | Ask the agent to add next year's LSE holidays |
| `ledger_reset` | Staging's ledger was reset with `setup --reset` | Expected if you (or the agent) reset staging; otherwise tell the agent |
| `pause_unreadable` | The pause bucket can't be read, so no new orders | Tell the agent |
| `refused_to_start` / `setup_refused` / `lock_release` / `error` | Configuration or storage trouble | Tell the agent with the time of the alert |

A dry run never sends an alert. A deploy that fails opens a GitHub `incident` issue instead.

## Stop reasons

| Alert contains | Meaning | What to do |
|---|---|---|
| `new orders off (kill switch or pause request)` | `new-orders` is off, or a pause request exists | Nothing, unless you want trading back (kill-switch.md) |
| `not reconciled` | An earlier order's outcome is unknown or still working, or the broker has a working order the bot doesn't know (including one you placed by hand) | Wait for it to finish; if it persists, check the order in the Trading 212 app and tell the agent |
| `order ended short` | An order the broker accepted was rejected or cancelled before filling. That month end's decision is not retried until the next one | Check the order in the app; tell the agent if it repeats |
| `stale data` / `too short` / `implausible jump` | The price data is missing, short or wrong | Usually clears the next day; tell the agent if it doesn't |
| `price cross-check failed` | The data source's latest close is more than 2% from the broker's live price | Clears on its own if it was a real move; persistent means bad data |
| `not verified ... month end skipped` | The first run after the month end couldn't check the month-end prices (blocked, or prices moved more than 2%), so **this month's rebalance is skipped**, exits included, until the next month end | Decide whether to act by hand (closing positions is yours) or wait. One alert per month end |
| `equity check failed` / `implausible new peak` | The broker's equity doesn't add up, or jumped more than 10% | A deposit of more than about 10%: raise the mark (see ledger-setup.md). Otherwise tell the agent |
| `no high-water mark` / `lower than an earlier` / `ledger file missing` | The ledger isn't set up, or was reset or damaged | See ledger-setup.md; never re-seed after a loss |
| `rejected: daily loss limit` / `drawdown stop` / `no day-start equity` | Risk vetoed buys for the day (sells still go through) | Expected after a bad day or a gap of more than 5 days; the drawdown stop needs you to decide |

**If the bot's Gmail app password may have leaked**: revoke it at Google (the bot's Google
Account → Security → App passwords), make a new one, and store it as a new version
(secret-set runbook, `NAME=gmail-sender`). Never disable or destroy the secret's only
version: Cloud Run mounts it, so the job (trading included) couldn't start. Staging and prod
use separate app passwords, so each can be revoked on its own.
