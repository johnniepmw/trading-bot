# Strategy: long-or-cash trend (ADR-0015)

Status: **spec frozen 2026-11-21, not yet run on real data.** Changing a parameter after
seeing results is a new trial and must be logged (`research/trial-log.md`).

## Hypothesis
Assets tend to keep moving in the direction of their recent trend because investors
herd, react slowly to news and hedge mechanically. Holding an asset only while it trades
above its long-run average, and holding cash otherwise, should cut deep drawdowns at a
modest cost in return. Not a forecast: the historical evidence (Faber 1973–2011:
return about 11%, volatility about 7%, worst drawdown about 10%) is what we test against.

## Rule (all parameters fixed)
| Item | Value |
|---|---|
| Signal date | last trading day of each month, using that day's close |
| Trend filter | close > simple moving average of the last 210 trading days (about 10 months); fewer than 210 days of history means "not held" |
| Weights | equal weight across assets that pass, each capped at 25% of equity; the rest is cash |
| Volatility cap | estimate portfolio volatility from the last 60 days of daily returns of the held weights; if above 10% a year, scale all weights by `10% / estimate`; never scale up |
| Execution | orders for the next trading session; rebalance only when a position differs from target by more than 10% of its target or 2% of equity (whichever is larger), to avoid churn |
| Leverage, shorting | none |
| Costs in backtests | 0.15% of traded value per side (spread and any FX), doubled in the stress run |

Risk-layer ceilings (ADR-0009: no leverage, 20% drawdown hard stop, position caps) sit
after this rule and can only shrink or veto its orders.

## Universe
Selected for asset-class spread, GBP pricing and Trading 212 availability. **Candidates,
unverified until the Phase 0 API check confirms each is tradable and its quote
currency**: global equity (`VWRP`), UK equity (`VUKE`), emerging markets (`EMIM`),
UK gilts (`IGLT`), global bonds GBP-hedged (`VAGP`), index-linked gilts (`INXG`), gold
(`SGLN`). The list is data, not code. Instruments quoted in GBX (pence) go through
`Money`'s GBX type.

## Validation (in order; a failure stops the pipeline)
1. Hypothesis and these parameters written down first (this file).
2. Hold-out: the most recent 3 years of history are loaded only by the final-test
   command, once, after everything else is frozen. Development code cannot read them.
3. Trial log: every variant tried is recorded; the count feeds the Deflated Sharpe Ratio.
4. Walk-forward test on rolling windows; parameter-stability check on neighbours (150, 180,
   240, 270-day averages, 5% and 15% volatility targets) that must stay within 25% of the
   chosen Sharpe, otherwise treated as curve-fit.
5. Costs at 1x and 2x. Lookahead, survivorship and corporate-action checks as tests.
6. Bootstrap the returns 5,000 times for drawdown and losing-streak distributions; these
   set the circuit-breaker levels in the risk layer.
7. Paper trade at least 3 months on the demo account (Phase 3) and compare with the backtest.

## Gate (proposed; the owner may change it **before** the hold-out is opened)
Paper trading may start only if, on the hold-out and with 2x costs: net Sharpe above 0.5;
Deflated Sharpe significant for the logged number of trials; worst simulated 95th-percentile
drawdown no deeper than 20% (£200 on £1,000); and results inside the walk-forward range.
Real money is a separate owner decision (Phase 4).
