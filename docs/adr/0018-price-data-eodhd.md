# 0018. Live price data from EODHD's free tier, checked against the broker

- **Status**: accepted
- **Date**: 2026-11-22
- **Deciders**: owner (chose EODHD's free tier, 2026-11-22); agent (the adapter's design)
- **Enforcement**: `tests/app/test_eodhd.py` (recorded responses), `tests/app/test_job.py` (prod reads EODHD, never synthetic prices), the cycle's broker cross-check (`tests/app/test_cycle.py`); `src/tradingbot/app/**` and `infra/**` are high risk

## Context
The strategy signals on dividend-adjusted daily closes of seven London-listed ETFs
(ADR-0015), and Trading 212's API has no price history (ADR-0016). Prod needs a source
that covers LSE ETFs, adjusts for dividends, publishes the previous close before the
07:15 run, permits automated personal use, and costs little next to a £1,000 account: a
£190-a-year plan would be about 19% of capital every year.

## Options considered
1. **EODHD, free tier**: LSE coverage (`VWRP.LSE`), `adjusted_close`, updated 2–3 hours
   after the close, 20 calls a day and one year of history; £0.
2. **EODHD, EOD All-World** ($19.99 a month or $199 a year): the same data, more calls and
   history; the fallback if the free tier proves too tight.
3. **Marketstack** ($9.99 a month): LSE coverage unconfirmed.
4. **Alpha Vantage**: adjusted daily data needs a plan from about $49.99 a month.
5. **Yahoo Finance or Stooq**: free, but no terms for automated use and no stable API;
   blocked from cloud hosts at times. Kept for the owner's research downloads only.

## Decision
Option 1, behind the `PriceHistory` port, so a change of provider is one new adapter and
one line in the job.
- One request per fund per run (seven), and as many for a dry run: within 20 a day.
  Amended 2026-11-24 (dashboard slice 2): each run also fetches SSAC's latest close for
  the dashboard. A run fetches each instrument once (the month-end preview reuses the checked
  history's bars), so a run makes 8 calls (9 if a fetch fails partway): two attempts fit in
  20, but not two attempts plus a deploy's dry run on a month end's action day.
  Amended 2026-11-24 (history export): the owner's Phase 1 export (`scripts/export_history.py`)
  uses the same key for its cross-check, 8 calls a run, so it runs at most once a day, after
  a completed 07:15 run, and never on a month end's action day.
- Bars use `adjusted_close`, with the open scaled by the same factor, in the listing's
  units (pence for GBX funds).
- The key is a file (`/secrets/eodhd-key/value`, prod only). It travels in the query
  string, so no URL, body or transport text reaches an exception or a log line. The
  transport refuses redirects and ignores proxies.
- Staging and local runs stay on synthetic prices.
- No second data source: the cycle already compares the signal-day close with the
  broker's live price (2%), an independent check from the owner's own account, and stops
  the month when they disagree. A second source would add outages, not safety.

## Consequences
- Good: £0; one provider to learn; unit or data mistakes are caught by the broker
  cross-check and `validate_history`.
- Bad / costs:
  - One year of history is close to what the signal needs: 210 trading days up to a
    signal day up to 35 days back. The owner's `scripts/eodhd_check.py` run confirms the
    depth; if it falls short, the paid plan needs no code change.
  - Retries and manual dry runs share the 20-call budget.
- Revisit when: calls or history run short, EODHD changes its free tier or terms, or the
  strategy needs more instruments.
