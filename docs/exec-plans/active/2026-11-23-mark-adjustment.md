# Automatic high-water-mark adjustment for deposits and withdrawals

- **Status**: active (spec; owner asked for it, 2026-11-23)
- **Branch / PR**: ccr-7fe1197d-l39xit / (next after #58)
- **Risk class**: high (money and risk limits: the drawdown stop's reference point; ADR-0017's reference mark)
- **Started**: 2026-11-23

## Goal
A deposit or withdrawal on the account the bot trades never changes the drawdown %, never
trips the "implausible new peak" or drawdown stops, and needs no owner step. The owner gets
an alert for every adjustment ("manual steps are a point of failure"; "an automatic
email so we are aware, to guard against a bug").

## Acceptance criteria
| # | Criterion (observable, pass/fail) | Evidence |
|---|---|---|
| AC1 | The broker port reports cash flows since a time: deposits positive, withdrawals negative, each with the broker's reference and time; the fake supports `deposit()` and `withdraw()`; the Trading 212 adapter reads `/history/transactions` (recorded shape: `type`, `amount`, `currency`, `reference`, `dateTime`) and pages it | contract tests on both brokers |
| AC2 | Each new flow scales the high-water mark by equity after ÷ equity before the flow, so the drawdown % is unchanged; a flow is applied once (by reference), and only flows after the ledger's setup count | cycle tests: deposit, withdrawal, two flows, a re-run |
| AC3 | A flow is accepted only if the cash change since the previous run's snapshot matches it: the flows plus the cash effect of fills settled since, within 2% of equity (cash, unlike equity, doesn't move with markets). A mismatch stops the run with an alert, and the mark is unchanged | cycle tests: a flow with no cash change; one whose amount disagrees |
| AC4 | Each adjustment is recorded in the ledger (reference, time, amount, mark before and after, equity) and logged at WARNING with the alert kind `mark_adjusted`, naming the flow's direction, not amounts | ledger and alert tests |
| AC5 | The "implausible new peak" check and the drawdown alerts use the adjusted mark, so a deposit of any size passes and a withdrawal never alerts as a drawdown | cycle tests |
| AC6 | The reference mark (ADR-0017) follows recorded adjustments: it stores the adjustment count, and a ledger with fewer adjustments, or as many with a lower mark, is refused as restored. A withdrawal's lower mark is not refused | remote-ledger tests and drills |
| AC7 | A transaction type the bot doesn't know stops the run with an alert (fail closed); fees are not flows (a loss, like a market move) | adapter tests |
| AC8 | `scripts/t212_record.py` lists every transaction type it sees, so the owner's demo deposit and withdrawal confirm the names and signs before prod relies on them | script test; the owner's run |

## Non-goals
- Dividends and interest as flows: they are returns. They show up as small unexplained cash, inside AC3's 2%.
- A manual owner command to set the mark.

## Rollout and rollback
- Flag / kill switch: none new. `new-orders` and pause requests still stop orders. An unknown transaction type or a mismatch stops the run.
- Data changes: expand only. A new `cash_flows` table, created on open; the reference mark gains an `adjustments` count, read as 0 when absent.
- Rollback: revert the PR. A ledger with adjustments then fails the old code's check (mark below the reference), so prod would stop with an alert. That is fail-safe; the owner restores with the agent.

## Decisions
- **Cash, not equity, checks a flow** (changes the Phase 2 plan's "equity change must match within 2%"). Equity moves with markets, so a 3% market day next to a deposit would stop the run. It would also keep stopping: the comparison point doesn't move until a run completes. Cash moves only with flows, fills, fees, dividends and interest. The last three are small against 2% of equity for a monthly long-only ETF bot.
- **Proportional scaling**: new mark = mark × equity ÷ (equity − flow). It uses this run's figures only, so a market move between runs can't distort it.
- **Withdrawals and the risk layer**: after scaling, the drawdown stop sees the same % as before. The daily-loss check also gets its day-start equity shifted by the flow, so a withdrawal is not a "loss".
- **Fees**: Trading 212's `FEE` transactions are losses, not flows. They are ignored here and show up as cash.
- **Unknown types** stop the run with an alert until the code knows them. `TRANSFER` stays unknown until its sign is recorded.

## Open questions
- How Trading 212 names and signs a withdrawal (`WITHDRAW`? negative amount?). Default: type `WITHDRAW`, sign taken from the type, with `abs(amount)`. The owner's demo withdrawal (AC8) confirms it before prod setup.

## Plan
1. Port and brokers: `cash_flows(since)`, the fake's knobs, the Trading 212 reader and recorder change (AC1, AC7, AC8). Check: contract tests.
2. Ledger: `cash_flows` table, adjustment record, adjustment count (AC4). Check: ledger tests.
3. Cycle: detect, check against cash, scale the mark and the day start, alert (AC2, AC3, AC5). Check: cycle tests and drills.
4. Remote ledger: the reference mark follows adjustments (AC6). Check: remote-ledger tests and drills; ADR-0017 amendment.
5. Owner: a small demo deposit and withdrawal, then the recorder run (AC8); acceptance plan stage C after prod setup.

## Progress log

## Review

## Outcome
