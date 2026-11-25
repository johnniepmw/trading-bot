# Automatic high-water-mark adjustment for deposits and withdrawals

- **Status**: deferred until before live (2026-11-23): the Trading 212 demo account can't deposit or withdraw, so paper trading never needs it. It is a precondition of ADR-0009's live switch
- **Branch / PR**: ccr-7fe1197d-l39xit / (after #58)
- **Risk class**: high (money and risk limits: the drawdown stop's reference point; ADR-0017's reference mark; setup)
- **Started**: 2026-11-23

## Goal
A deposit or withdrawal on the account the bot trades never changes the drawdown % or the
daily-loss check. It never trips the "implausible new peak" stop or a drawdown alert, and it
needs no owner step. The owner gets an alert for every adjustment ("manual steps are a
point of failure"; "an automatic email so we are aware, to guard against a bug").

## Definitions
- **Flow**: a broker cash transaction that moves money in or out. Deposits are positive,
  withdrawals negative. Each has the broker's unique reference and time.
- **Baseline**: the cash and transaction cursor as of the last run that checked its flows,
  or setup. Kept in the ledger and moved on only in the same write as that run's adjustments.
- **Cursor**: the newest flow time seen (broker time), plus every reference in a 7-day
  overlap window before it.
- **Residual**: cash now − baseline cash − Σ new flows − cash effect of fills since the
  baseline. The fill effect uses `filled_quantity` × average price (÷100 for GBX), buys
  negative and sells positive, minus fees, for the intents recorded by the baseline's run
  or later. Reconcile guarantees those are all settled by the time this check runs.

## Acceptance criteria
| # | Criterion (observable, pass/fail) | Evidence |
|---|---|---|
| AC1 | The broker port lists flows since a broker time, paged (at most 10 pages, else a stop). The fake has `deposit()` and `withdraw()`. The Trading 212 adapter reads `/history/transactions` and stops on: an unknown type; a sign that disagrees with the type; a non-GBP currency; an empty reference; or one reference seen with different details. `FEE` is not a flow | contract and adapter tests |
| AC2 | Setup stores the baseline (its cash and the cursor). A ledger without one (from before this change) has its baseline recorded by its next run, with nothing applied and a WARNING | setup and cycle tests |
| AC3 | Every run checks the residual against 2% of the current checked equity. Beyond it the run stops with an alert, the baseline stays put, and no mark changes. A later run passes once the cash and the list agree (a deposit listed before its cash arrives, or the reverse). A fill with no average price stops the run (never estimated) | cycle tests: mismatch then pass; the race between the `account()` and flow reads; unknown fill price |
| AC4 | Flows scale the mark: new mark = mark × E ÷ (E − ΣF). Each flow's record chains in broker-time order (reference as tie-break). The run stops if E − ΣF ≤ 0. A pure function in `risk`, with 100% branch coverage and a Hypothesis property: the drawdown % is unchanged, whatever the order of the flows | risk tests |
| AC5 | Day-start equity and the last recorded equity are scaled by the same factor as the mark, for every adjustment applied today (UTC). So the daily-loss check and drawdown alerts see the market-only change, same-day re-runs included. The MAX_NEW_PEAK check uses the adjusted mark | cycle tests: same-day re-run after a withdrawal; withdrawal in a drawdown raises no drawdown alert; a 30% deposit passes |
| AC6 | The ledger records each adjustment (reference, broker time, amount, mark before and after, equity), the new baseline and the cursor, then the mark, in one transaction. `adjust_high_water_mark` refuses when no mark exists; only `app/cycle.py` calls it. The mark may fall only through a recorded adjustment: "the mark only rises since the last adjustment, and adjustments chain to the marks" | ledger tests; an import test like the seed one |
| AC7 | Each adjustment logs a WARNING with the alert kind `mark_adjusted`, naming the direction, not amounts. A dry run reports at WARNING with no kind | alert tests |
| AC8 | The reference mark (ADR-0017 → ADR-0020) stores the latest mark and the adjustment count. A downloaded ledger is refused if it has fewer adjustments, or as many with a lower mark. More adjustments are accepted, and the reference catches up at the next checkpoint. `checkpoint()` writes when the mark or the count changes | remote-ledger tests; drills: death before checkpoint (applied once on the re-run), death between ledger upload and reference write (accepted, caught up), a restore from before an adjustment (refused) |
| AC9 | `scripts/t212_record.py` lists each transaction type with its sign (done in #58). The owner's live deposits and withdrawals confirm the names, signs and unique references before prod setup | done 2026-11-25: the owner's live run (progress log) |

## Non-goals
- Dividends and interest as flows: they are returns, a few pence a day against the 2%. If live
  shows types for them, they are added deliberately (tech debt), not guessed.
- A manual owner command to set the mark.

## Rollout and rollback
- Order of work: each step leaves main shippable, and the cycle applies nothing before step 4.
- Flag / kill switch: none new. `new-orders` and pause requests still stop orders.
- Data changes: expand only. New tables (`cash_flows`, `baseline`); the reference mark gains
  `adjustments`, read as 0 when absent.
- Rollback: reverting after a withdrawal leaves a mark row below an earlier one. The old
  code's `high_water_mark()` then raises inside the download: prod stops with an alert,
  which fails safe. The old code also rewrites `reference-mark.json` without `adjustments`
  when the mark next rises, so the count reads 0 until the next adjustment.

## Decisions
- **Cash, not equity, checks the flows**: equity moves with markets. A fixed baseline makes
  a mismatch heal itself, because cash doesn't drift. Checked on every run, it also catches
  a withdrawal that leaves cash before it is listed, a type wrongly treated as a fee, money
  moved into a Pie, and the owner's own trades in the bot's funds.
- **Proportional scaling**, applied to the mark, the day start and the last recorded equity.
  During a drawdown it lowers the mark by more than the withdrawal's £ amount; that is
  intended, since the drawdown % is what the stop measures.
- **A cursor in broker time** with a 7-day overlap of known references, so the bot's clock
  never decides which flows count.
- **Fail closed** on anything unrecorded: unknown types, odd signs, missing references.
- **ADR-0020** amends ADR-0017. The reference mark becomes "the latest mark plus an
  adjustment count", and the ledger's mark may fall only through a recorded adjustment.
- The Phase 2 plan's Decisions bullet is updated to match. The acceptance plan's C5 (cursor)
  and C6 (a cash mismatch) are reworded.

## Open questions (owner)
1. Answered 2026-11-23: the demo app can't deposit or withdraw (only a balance reset). So
   the transaction shapes are recorded on the live account (owner's proposal, 2026-11-23):
   small live deposits and withdrawals (no trading), then `scripts/t212_record.py
   --live-transactions` with a live key that has read permissions only. That mode can only
   GET `/history/transactions` and prints kinds, signs and reference shapes, never amounts. Pies are not
   a stand-in: moving cash into a pie is not a flow, and the bot's account must not use pies.

## Plan
1. Ledger: `adjust_high_water_mark`, the adjustment and baseline tables, the "falls only by
   adjustment" rule, one-transaction write (AC6). Remote ledger: the reference follows the
   count (AC8). Check: ledger and remote-ledger tests, drills.
2. `risk`: the scaling function (AC4). Check: unit and property tests, 100% branch coverage.
3. Port and brokers: `cash_flows(since)`, the fake's knobs, the Trading 212 reader (AC1).
   Setup stores the baseline (AC2). Check: contract tests; setup tests.
4. Cycle: residual check, scaling, day start, alerts, legacy baseline (AC2, AC3, AC5, AC7).
   Check: cycle tests, drills, an e2e on fakes (a deposit between two runs).
5. Docs: ADR-0020, ARCHITECTURE invariants, runbooks (ledger-setup, cycle-alerts,
   closing-positions), acceptance plan, tech debt.
6. Owner: the demo deposit and withdrawal plus a recorder run (AC9), then acceptance stage C.

## Progress log
- 2026-11-23 deferred: the demo can't deposit or withdraw (owner). Until this ships, the existing guards fail safe on any flow: a drop in equity counts as a loss (drawdown and daily-loss stops), a rise of more than 10% stops as an implausible new peak, and a smaller one lifts the mark. So no deposits or withdrawals on the account the bot trades, and never on live before this ships
- 2026-11-25 AC9 recorded on live (owner; read-only key, three deposits and two
  withdrawals, no trading): HTTP 200, one page (`nextPagePath` null). Kinds `DEPOSIT + x3`
  and `WITHDRAW - x2`. References are UUIDs, all 5 unique. Item fields: `type`, `amount`
  (a number), `currency` (`GBP`), `reference`, `dateTime`. There is no status field, so the
  list can't tell a pending withdrawal from a completed one. None is needed: the owner
  confirmed (2026-11-25) that Trading 212 completes a withdrawal when it is requested, and
  the app's "up to 3 days" is only the bank transfer, so cash and list change together.
  Whether a cancelled withdrawal stays
  listed is unknown. AC3's residual check stops the run safely if it does, because cash
  and list would disagree
- 2026-11-23 spec revised after the architect's review (10 blocking: comparison point, fill
  effect, the mark's only-rises rule, setup baseline and cursor, existing ledgers, same-day
  scaling, multi-flow formula, rollback, sequencing, strict parsing; all taken in)

## Review
- Architect, round 1: revise, 10 blocking and 7 suggestions. All 10 blocking points are
  folded into the definitions, ACs and decisions above. Suggestions 1 (check every run),
  3 (tolerance named), 4 (dry runs), 5 (e2e), 6 (docs) and 7 (in AC8's refusals) are taken;
  2 is open question 1.

## Outcome
