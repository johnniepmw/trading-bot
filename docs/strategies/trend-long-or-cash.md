# Strategy: long-or-cash trend (ADR-0015)

Status: **spec frozen on merge, not yet run on real data.** Changing anything here after
results exist is a new trial and goes in `research/trial-log.md`.

## Hypothesis
Assets tend to keep moving in the direction of their recent trend because investors herd,
react slowly to news and hedge mechanically. Holding an asset only while it trades above
its long-run average, and cash otherwise, should cut deep drawdowns at a modest cost in
return. The claim is about **drawdown**, so the benchmark is 1/N buy-and-hold of the same
assets. Historical evidence (Faber 1973–2011: about 11% a year, 7% volatility, worst
drawdown about 10%, using monthly closes and a 10-month average) is history to test
against, not a forecast.

## Rule (every choice below is a-priori, not fitted)
| Item | Value |
|---|---|
| Sleeves | N assets, each a fixed sleeve of `min(1/N, 25%)` of equity. A sleeve that fails the filter stays cash; it is **not** redistributed |
| Signal date | last trading day of each month, using that day's close |
| Trend filter | close > simple average of the last 210 trading closes **including** the signal day; fewer than 210 closes means not held |
| Volatility cap | evaluated only on the signal date: estimate the volatility of the held weights from the last 60 daily returns; above 10% a year, scale all weights by `10% / estimate`; never scale up. Weights stay fixed between signal dates |
| Price series | signals use **adjusted (total-return)** daily closes from the data source; execution and sizing use the broker's live quote |
| Backtest fills | next trading session's **open**, plus costs of 0.15% of traded value per side (spread and any FX), doubled in the stress run |
| Rebalance band | trade only if a position differs from target by more than 10% of the target or 2% of equity, whichever is larger |
| Cash | earns nothing in the base case; a second run credits Trading 212's published interest rate. Sharpe is measured over the risk-free rate used in that run |
| Leverage, shorting | none |

Risk-layer ceilings (ADR-0009) sit after this rule and can only shrink or veto its
orders. The risk position cap must be at least this rule's 25%.

## Universe and history
Candidates, **unverified until the owner's API check** confirms each is tradable and its
quote currency: global equity `VWRP`, UK equity `VUKE`, emerging markets `EMIM`, UK
gilts `IGLT`, global bonds GBP-hedged `VAGP`, index-linked gilts `INXG`, gold `SGLN`.
Several launched only in 2017–2019, which is too short for the validation below, so each
asset needs a longer **proxy** series (an older fund tracking the same index, or an index
total-return series converted to GBP) spliced under the ETF's own history; the proxy map
and any splice date are recorded with the data. The list is data, not code.

## Data split (stated honestly)
- **Development**: from the start of the proxy history to 2022-12-31.
- **Hold-out**: 2023-01-01 onward. It is *not* blind: the author knows in general terms
  that 2022 was a bond crash and 2023–26 a recovery. Its value is that no parameter was
  chosen with it. The export writes the two periods to **separate files**; only the final
  command is given the hold-out file. Nothing technical can stop a later session reading it
  again, so a second look is a new trial and is logged.

## Validation (in order; a failure stops the pipeline; tooling lives in `research/`)
1. Hypothesis and parameters written first (this file).
2. Trial log: every variant tried, including the stability neighbours below. The count
   feeds the Deflated Sharpe Ratio.
3. Rolling sub-period analysis on the development data (parameters are fixed, so there is
   no refitting): results by 5-year window.
4. Stability neighbours, each logged as a trial: averages of 150, 180, 240 and 270 days;
   volatility targets of 5% and 15%. Each must reach at least 75% of the chosen Sharpe,
   otherwise the choice is treated as curve-fit.
5. Costs at 1x and 2x. Tests for lookahead, survivorship and corporate actions.
6. Stationary block bootstrap (5,000 resamples, mean block about 20 days) for drawdown and
   losing-streak distributions; PR 7 feeds the result into the risk layer's circuit-breaker
   levels.
7. Paper trading for at least 3 months on the demo account (Phase 3), compared with the
   backtest.

## Gate (owner approved 2026-11-21; may change only before the hold-out file is first opened)
Statistics need long samples, so significance is tested on the **full** history and the
hold-out is only a sanity check. Paper trading may start only if, with 2x costs:
1. full-history net Sharpe above 0.5 and Deflated Sharpe above 0.90 for the logged trial
   count;
2. the 95th-percentile simulated maximum drawdown is no deeper than 20% (£200 on £1,000);
3. the strategy's maximum drawdown is clearly below the 1/N buy-and-hold benchmark's, which
   is what the hypothesis claims;
4. the hold-out Sharpe and drawdown are no worse than the worst 5-year window of the
   development period.
Real money is a separate owner decision (Phase 4).
