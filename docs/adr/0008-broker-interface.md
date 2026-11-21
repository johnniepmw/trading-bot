# 0008. A broker interface, with Trading 212 first and IBKR as the fallback

- **Status**: accepted
- **Date**: 2026-11-21
- **Deciders**: owner
- **Enforcement**: import-linter contracts 3a and 3b (only `brokers` talks HTTP to a broker; only `app` wires concrete brokers)

## Context
Trading 212's Public API is in beta: endpoints, limits and order types may change, and
the live key is IP-locked. The owner's account is at Trading 212 (Invest, GBP). We need
to test everything without a network and be able to switch broker without touching
strategy or risk code.

## Options considered
1. **A `Broker` interface owned by our code**, with `brokers/trading212` and
   `brokers/fake` implementations: offline tests, swap brokers by wiring.
2. **Call Trading 212 directly from execution**: less code; couples everything to a
   beta API.
3. **Start on IBKR**: a mature API; a new account, more cost and complexity for £1,000.

## Decision
Option 1. Trading 212 first (demo, then live once the owner's `operator` secret says
so). Interactive Brokers is the fallback if the beta API proves unreliable. The Phase 0
API check (`scripts/phase0_api_check.py`) shapes the interface.

## Consequences
- Good: CI never needs a broker; a fake broker drives paper runs and tests.
- Bad / costs: an interface designed around one broker may leak its quirks; recorded
  contract tests (Phases 1–3) keep the fake honest.
- Revisit when: Trading 212 changes or withdraws the API, or limits block the strategy.
