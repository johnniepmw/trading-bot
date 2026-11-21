# 0016. Order-path types, the market-data boundary and numeric types

- **Status**: accepted
- **Date**: 2026-11-21
- **Deciders**: agent (within delegated authority; the live data source is an open owner question)
- **Enforcement**: import-linter contract (protected module `domain.approval`); type and import tests; risk-layer property tests

## Context
Phase 1 builds the risk layer, strategy and a backtest before the Trading 212 adapter
exists. Three choices shape every later PR: how the code guarantees an order passed the
risk layer, where price history comes from (Trading 212's API has none, and `data` may not
import `brokers`), and which numeric type is used outside `Money`.

## Options considered
1. **`ApprovedOrder` in `domain`, constructible only by `risk`; the order port accepts only
   it; a price-history port in `data`, separate from `Broker`; `Decimal` for money,
   quantities and order prices, `float` for returns, volatility and weights in analysis.**
2. **Plain `Order` everywhere and a runtime check**: simpler; a bypass becomes a test
   failure at best, not a type error.
3. **`Decimal` everywhere**: one rule; slow and awkward for statistics.

## Decision
Option 1. `domain/approval.py` defines `ApprovedOrder`; only `risk` imports its
constructor (protected contract), `execution` and `brokers` may import the type, and the
order port takes nothing else. Price history is a `PriceHistory` port in `data`,
implemented by a data adapter and wired in `app`; `Broker` carries only account,
positions and orders. Weights are floats and become `Decimal` quantities at one explicit,
tested rounding step in `portfolio`; fractional-share and minimum-order rules are inputs
learned from the API check, not constants. The backtest has its own fill simulator and
shares only `strategy`, `portfolio` and `risk` with live code.

## Consequences
- Good: a bypass of risk does not type-check; the backtest and live paths share the pure core.
- Bad / costs: one more import contract and a new `backtest` package to add to contracts 1
  and 3a; `.importlinter` is high risk, so the first PR touching it needs approval.
- Revisit when: the live data source is chosen (its terms and storage get their own ADR),
  or Trading 212 limits force a different order shape.
