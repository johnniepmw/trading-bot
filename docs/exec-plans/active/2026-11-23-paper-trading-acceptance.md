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

## Stage B: the normal cycle
The prod schedule is on from 2026-11-23 (owner): B1 is the first scheduled weekday run,
B3 the first run after the November month end (Tuesday 2026-12-01, 07:15 UK).
| # | Test | Steps | Expected |
|---|---|---|---|
| B1 | A quiet day | Owner: `--args=run` on a mid-month weekday | "run completed: 0 order(s) sent"; equity recorded |
| B2 | Non-trading day | Owner: `--args=run` on a weekend or bank holiday | "not a trading day" |
| B3 | Month-end rebalance | On the first trading day after a month end, a `run` execution (or the schedule, if on) | Orders appear in the demo app; ledger intents FILLED; next run sends nothing more |
| B4 | Fills and fees recorded | Agent: read the ledger copy for B3 | Fill prices and fees match the demo app (monitoring AC4) |
| B5 | Rebalance band | A month end where weights barely moved | No orders (inside the band) |

> Stage C runs before the live switch, not during paper trading: the demo account can't deposit or withdraw (owner, 2026-11-23). C6 and C7 still run in staging on fakes once the mark adjustment is built.

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

## Stage G: before live (owner asked, 2026-11-26)
| # | Test | Steps | Expected |
|---|---|---|---|
| G1 | End-to-end technical audit | When the code is stable, after the paper period and before ADR-0009's live switch. Agent: a fresh session with no memory of building it reads the whole system as one: code, infra and IAM, workflows, secrets handling, dependencies, runbooks. Security first (threat model: a leaked key, a compromised dependency or CI token, a malicious PR, a wrong price, a public repo), then bugs and inefficiencies. Security reviewer, reviewer and architect over the whole repo, not a diff; the dependency audit; an IAM listing from the owner. Findings ranked, each with a fix or an owner decision | No open high or medium security finding; every other finding fixed or accepted by the owner; a written report in `docs/` (no account data) |

## Evidence log
| Date | Test | Run or execution | Result |
|---|---|---|---|
| 2026-11-23 | A2 | owner run of `scripts/t212_record.py` (demo) | pass: currencies match Trading 212 and EODHD; quantity step 0.001; `instruments_verified = true` in the step 4 PR |
| 2026-11-23 | A3 | `make secret-set NAME=t212-demo-key ENV=prod`; infra run 45141804879 (re-run) | pass: prod apply green |
| 2026-11-23 | A4 | `DEPLOY_PROD=true`; deploy run 45234536351 | pass: build, staging and production green; prod's deploy check passed before setup (business stop: no ledger) |
| 2026-11-23 | A5 | `setup --equity 1000.00` (execution trading-bot-prod-gbgms) | pass: "setup completed" |
| 2026-11-23 | A6 | `run --dry-run` (execution trading-bot-prod-bzkld) | pass: account, positions and EODHD history read; first-buy prices from the latest close; October's month end skipped (closes not verified on the day), as designed |
| 2026-11-25 | B1 | scheduled run, Wed 07:15 UTC (the owner's daily email) | pass: run completed, 0 orders sent, none open, equity and drawdown recorded, month-end preview shown; every glance line OK |
| | | | |
