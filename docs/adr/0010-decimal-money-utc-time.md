# 0010. Decimal money with an explicit currency, and UTC time

- **Status**: accepted
- **Date**: 2026-11-21
- **Deciders**: owner
- **Enforcement**: `tradingbot.domain.money.Money` and its tests (`tests/domain/test_money.py`); `.claude/rules/python.md`

## Context
London-listed ETFs quote in pence (GBX) while the account is in pounds (GBP): a silent
100x error waiting to happen. Floats round. Runs happen on a schedule across daylight
saving changes.

## Options considered
1. **`Decimal` amounts in a `Money` type with a currency that refuses to mix GBP and
   GBX; UTC everywhere with an injectable clock**.
2. **Floats with rounding at the edges**: simpler; accumulated error and no unit safety.
3. **Integer pence**: exact; awkward for fractional shares and FX.

## Decision
Option 1. Arithmetic and comparison across currencies raise `CurrencyMismatchError`;
pence to pounds is an explicit `to_gbp()`. Times are timezone-aware UTC; code takes a
clock rather than calling `now()`. Market hours come from the broker's schedule.

## Consequences
- Good: unit errors fail loudly in tests; time logic is deterministic in tests.
- Bad / costs: some verbosity at boundaries (parsing broker and data responses).
- Revisit when: never, short of a new account currency (add it to `Currency`).
