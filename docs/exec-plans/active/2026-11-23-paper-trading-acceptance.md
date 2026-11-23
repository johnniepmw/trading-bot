# Paper-trading acceptance test plan

- **Status**: active (runs once Phase 2 step 4 lands; owner asked for it, 2026-11-23)
- **Risk class**: standard (a plan; the steps run against the Trading 212 demo account only)
- **Started**: 2026-11-23

## Goal
Before the bot paper-trades unattended in prod, prove every safety mechanism works end to
end against the real Trading 212 demo account, real EODHD prices and the real cloud
setup. Each test has steps, the expected result and where the evidence goes. Nothing here
touches the live account (ADR-0009).

## Ground rules
- Demo account only. Prod's `operator` secret stays `__unset__` (paper) throughout.
- Account figures (cash, positions, orders) stay in Cloud Logging, the ledger and this
  chat; evidence in the repo names runs, kinds and outcomes, never amounts (ADR-0014).
- Each test is ticked in the evidence log at the end with the date, the run or execution
  name and the result. A failure stops the plan: fix forward, then re-run that test.
- "Owner" steps are yours (Trading 212 app, Cloud Shell, GitHub app); "agent" steps are
  mine; a few need both.

## Stage A: readiness (before prod runs anything)
| # | Test | Steps | Expected |
|---|---|---|---|
| A1 | Adapter contract | Agent: the Trading 212 adapter passes the fake broker's contract tests on recorded demo responses (step 4) | Green in CI |
| A2 | Units and steps verified | Agent: compare `bot.toml` with the API check's instrument data; set `instruments_verified = true` (PR, owner approval) | Every fund's currency and quantity step match |
| A3 | Demo key stored | Owner: `make secret-set NAME=t212-demo-key ENV=prod` (after its infra PR) | Prod's apply passes |
| A4 | Prod deploys on | Owner: decide the dry-run exit-code question (Phase 2 Decisions), then set `DEPLOY_PROD=true`; agent: run `deploy` | `production` job green: window, image, `run --dry-run` against prod |
| A5 | Prod ledger setup | Owner: check equity in the demo app, then `gcloud run jobs execute trading-bot-prod --region europe-west2 --args=setup,--equity,<GBP> --wait` | "setup completed"; a second setup is refused |
| A6 | Prod dry run | Agent or owner: a `run --dry-run` execution | "dry run: N order(s) would be sent", no ERROR |

## Stage B: the normal cycle (schedule still paused; executions started by hand)
| # | Test | Steps | Expected |
|---|---|---|---|
| B1 | A quiet day | Owner: `--args=run` on a mid-month weekday | "run completed: 0 order(s) sent"; equity recorded |
| B2 | Non-trading day | Owner: `--args=run` on a weekend or bank holiday | "not a trading day" |
| B3 | Month-end rebalance | On the first trading day after a month end, a `run` execution (or the schedule, if on) | Orders appear in the demo app; ledger intents FILLED; next run sends nothing more |
| B4 | Fills and fees recorded | Agent: read the ledger copy for B3 | Fill prices and fees match the demo app (monitoring AC4) |
| B5 | Rebalance band | A month end where weights barely moved | No orders (inside the band) |

## Stage C: deposits and withdrawals (the automatic mark)
Run each on the demo account, with the schedule paused, then one `run` execution after
each. You see an alert email for every adjustment.

| # | Test | Steps | Expected |
|---|---|---|---|
| C1 | Small deposit | Owner: deposit about 5% of equity in the demo app; `run` | Alert `mark_adjusted`; mark scaled by the same proportion as equity; drawdown % unchanged; no "implausible new peak" |
| C2 | Large deposit | Owner: deposit about 30% of equity; `run` | As C1; no stop although equity jumps more than 10% |
| C3 | Withdrawal | Owner: withdraw about 25% of equity; `run` | Alert `mark_adjusted`; mark scaled down; the drawdown stop does **not** fire |
| C4 | Two flows between runs | Owner: one deposit and one withdrawal, then a single `run` | One adjustment per transaction, in order; net effect matches |
| C5 | No double counting | Owner: `run` twice in a row after C1 | The second run adjusts nothing (the cursor moved) |
| C6 | A mismatch stops | Agent: in staging, a fake broker whose equity change doesn't match its transactions | The run stops with an alert; the mark is unchanged |
| C7 | Restore after an adjustment | Agent and owner: restore the ledger version before C3 (ledger-restore.md) in staging first | The older ledger is refused (reference mark); restoring the latest version works |

## Stage D: stopping and recovering
| # | Test | Steps | Expected |
|---|---|---|---|
| D1 | Kill switch | Agent: `ops kill new-orders` (prod), then a `run` on a month-end day | No orders; "new orders off"; owner restores with `ops restore` |
| D2 | Pause request | Owner: add a pause request; `run` | Alert `paused`; no orders; `ops clear-pause` resumes |
| D3 | Pause the schedule | Agent: `ops pause` | Scheduler PAUSED; owner resumes in the console |
| D4 | Stale lock | Agent and owner: in staging, plant `run.lock`, wait past an hour or plant an old one, `run` | Alert `stale_lock`; stale-lock.md clears it; the next run works |
| D5 | Ledger restore | Owner (with the agent): ledger-restore.md in staging | The restored ledger runs; an older one is refused |
| D6 | Rollback | Agent: `ops rollback` (prod) | Job on the previous image; smoke passes; redeploy returns it |
| D7 | Deploy window | Agent: a deploy within 30 minutes of a scheduled run (schedule on) | Refused; re-run later succeeds |

## Stage E: things going wrong
| # | Test | Steps | Expected |
|---|---|---|---|
| E1 | Broker unreachable | Agent: in staging, the fake broker raising errors; in prod, a `run` with the demo key temporarily replaced by an invalid one (owner: secret-set, then restore) | Run stops with an alert; no orders; the next good run reconciles |
| E2 | Order outcome unknown | Agent: staging fake broker timing out after accepting | Intent UNKNOWN; next run matches it from the broker's records; nothing sent twice |
| E3 | Price data down | Owner: replace the EODHD key with an invalid one; a month-end `run` | Stop with an alert ("EODHD ... HTTP 401"); restore the key |
| E4 | Price disagrees with broker | Agent: staging prices more than 2% off the broker quote on a month end | "price cross-check failed" or the month skipped once, with an alert |
| E5 | An order placed by hand | Owner: place a small limit order in the demo app; `run` while it is working | "not reconciled" stop; after you cancel it the next run continues |
| E6 | Drawdown alerts | Agent: staging fake equity falling through 5% and 10% | One `drawdown` alert per level; positions capped at the halved size after 10% |
| E7 | Missed run | Owner: pause the schedule over a weekday, resume | The next run reports the gap (no day-start equity veto if more than 5 days) |

## Stage F: unattended
| # | Test | Steps | Expected |
|---|---|---|---|
| F1 | A clean week | Owner: resume prod's schedule; agent reads each day's outcome | Five completed runs, no unexpected alerts |
| F2 | A month end unattended | The first month end after F1 | Rebalance as B3, with no manual step |
| F3 | Paper period | Phase 3: 90 days of paper trading, the daily glance and dashboard | Per the Phase 3 plan |

## Evidence log
| Date | Test | Run or execution | Result |
|---|---|---|---|
| | | | |
