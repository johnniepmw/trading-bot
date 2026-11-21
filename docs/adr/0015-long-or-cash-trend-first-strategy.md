# 0015. A long-or-cash ETF trend system as the first strategy

- **Status**: accepted
- **Date**: 2026-11-21
- **Deciders**: owner (strategy family, universe style, "learning first"); agent (parameters, within the validation rules below)
- **Enforcement**: `docs/strategies/trend-long-or-cash.md` holds the frozen spec; the `optimize` skill may not tune parameters without a held-out period it cannot see (AGENTS.md); the gate in the strategy spec decides whether paper trading may start

## Context
Up to £1,000, no leverage, Trading 212 Invest, a general account, daily data and trades
weekly to monthly. The evidence that has held up is slow, diversified trend following
(Hurst, Ooi & Pedersen; Faber's 10-month rule). A long-or-cash rule cannot short, so it
only protects against long drawdowns. Backtests lie by default (Bailey & López de Prado),
so the process matters as much as the rule.

## Options considered
1. **Faber-style trend filter on 6–8 ETFs across asset classes**: simple, few parameters,
   decades of evidence; cannot profit from falls, lags turning points.
2. **Cross-sectional momentum (rank and hold the top N)**: more turnover and more
   parameters; weaker evidence at this size.
3. **Mean reversion or ML signals**: more fitting risk, shorter evidence; ML is Phase 5.

## Decision
Option 1. At each month end, an asset is held only while its close is above its
10-month (about 210 trading-day) simple moving average, otherwise that sleeve is cash.
Held assets get equal weight, capped per position, and total exposure is scaled down
(never up, no leverage) when realised portfolio volatility exceeds a 10% annual target.
Orders are generated at the next session. Parameters are fixed in the strategy spec
**before** any real-data result is seen and are not tuned. The spec's validation
process and gate decide whether paper trading starts; the owner decides on real money.

## Consequences
- Good: few parameters, easy to test, matches the pure-core design, cheap to trade.
- Bad / costs: long-or-cash only; whipsaws in sideways markets; month-end signal lag;
  the universe is limited to what Trading 212 lists in GBP.
- Revisit when: the gate fails, paper results leave the backtest's expected range, or a
  second, uncorrelated strategy is added.
