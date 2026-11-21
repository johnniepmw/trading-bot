# 0007. Our own small core on open-source libraries, not a trading engine

- **Status**: accepted
- **Date**: 2026-11-21
- **Deciders**: owner
- **Enforcement**: ARCHITECTURE.md package layout; import-linter contracts in `.importlinter`

## Context
The strategy is a long-or-cash trend system on 6–8 ETFs, trading weekly to monthly.
The goal is learning first, with strong safeguards. Engines bring event loops, order
types and abstractions sized for intraday multi-asset trading.

## Options considered
1. **Our own small core** (domain, strategy, portfolio, risk, execution, ledger) using
   libraries for data handling and statistics: every line understood; risk veto and
   ledger fit our invariants exactly; more code to write.
2. **A trading engine** (for example NautilusTrader, Lean, Backtrader): mature backtest
   and live parity; heavy, opinionated, and the risk layer and broker adapter would still
   be ours (Trading 212 is not a supported venue).
3. **Scripts with no structure**: fastest to start; untestable risk controls.

## Decision
Option 1. Libraries for maths and data; no engine. Strategy, portfolio and risk stay pure
functions over domain types so the same code runs in backtests and live.

## Consequences
- Good: small, auditable surface; the learning goal is served directly.
- Bad / costs: we write our own backtest loop and must guard against look-ahead bias
  ourselves (Phase 1 adds regression tests for it).
- Revisit when: the strategy needs intraday data, many instruments or complex orders.
