# Runbook: what the daily cycle's alerts mean

Every check that fails stops the run with **no new orders** and logs `cycle stopped: <reason>`
at ERROR (Cloud Logging; Phase 2 turns these into alerts). Nothing here needs a code change.

| Alert contains | Meaning | What to do |
|---|---|---|
| `kill switch off` | `new-orders` is off | Nothing, unless you want trading back (owner: `ops` restore) |
| `not reconciled` | An earlier order's outcome is unknown or still working, or the broker has a working order the bot doesn't know (including one you placed by hand) | Wait for it to finish; if it persists, check the order in the Trading 212 app and tell the agent |
| `order ended short` | An order the broker accepted was rejected or cancelled before filling. That month end's decision is not retried until the next one | Check the order in the app; tell the agent if it repeats |
| `stale data` / `too short` / `implausible jump` | The price data is missing, short or wrong | Usually clears the next day; tell the agent if it doesn't |
| `price cross-check failed` | The data source's latest close is more than 2% from the broker's live price | Clears on its own if it was a real move; persistent means bad data |
| `not verified ... month end skipped` | The first run after the month end couldn't check the month-end prices (blocked, or prices moved more than 2%), so **this month's rebalance is skipped**, exits included, until the next month end | Decide whether to act by hand (closing positions is yours) or wait. One alert per month end |
| `equity check failed` / `implausible new peak` | The broker's equity doesn't add up, or jumped more than 10% | A deposit of more than about 10%: raise the mark (see ledger-setup.md). Otherwise tell the agent |
| `no high-water mark` / `lower than an earlier` / `ledger file missing` | The ledger isn't set up, or was reset or damaged | See ledger-setup.md; never re-seed after a loss |
| `rejected: daily loss limit` / `drawdown stop` / `no day-start equity` | Risk vetoed buys for the day (sells still go through) | Expected after a bad day or a gap of more than 5 days; the drawdown stop needs you to decide |
