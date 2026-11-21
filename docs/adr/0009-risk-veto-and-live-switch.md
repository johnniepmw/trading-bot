# 0009. The risk layer has a veto; ceilings live in code; the live switch is owner-only

- **Status**: accepted
- **Date**: 2026-11-21
- **Deciders**: owner
- **Enforcement**: `src/tradingbot/risk/**` and `config/**` are high risk (`.github/risk-paths.txt`); 100% branch coverage of `risk` once it exists; `operator` secret not writable by the deploy identity

## Context
Real money with no leverage, up to £1,000, and an owner who doesn't review code. The
worst outcomes are a bug that trades far too much, and an agent that switches on live
trading or raises limits. Both must be impossible without the owner.

## Options considered
1. **Risk veto in code, ceilings as constants, live switch in an owner-only secret**:
   agents can tighten limits but never loosen past the ceilings; going live needs the
   owner's own action outside CI.
2. **Limits in config or flags**: easy to tune; one bad PR or override raises them.
3. **Broker-side limits only**: Trading 212 offers few controls; no defence in depth.

## Decision
Option 1. Every order passes the risk layer, which may reject or shrink it and can
never be bypassed. Hard ceilings are constants in code: no leverage, a hard stop at a
20% drawdown from peak, position-size caps. Day-to-day limits may only be tighter. The
live switch and capital allocation sit in the Secret Manager secret `operator`, which
only the owner can write; missing or invalid means paper trading. The `ops` workflow's
`kill` action turns off the `new-orders` kill switch; closing positions is an owner
action.

## Consequences
- Good: a single choke point for safety, testable offline; no CI path to live money.
- Bad / costs: changing a ceiling needs a high-risk PR and the owner's approval.
- Revisit when: capital or strategy changes enough that the ceilings are wrong.
