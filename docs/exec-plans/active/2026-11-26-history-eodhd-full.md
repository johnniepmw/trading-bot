# History export: EODHD's full history as the price source, Yahoo as the cross-check

- **Status**: active
- **Branch / PR**: ccr-d0003009-0e03lt / one PR
- **Risk class**: high (`scripts/**`, `research/universe.toml`, `tests/scripts/**`)
- **Started**: 2026-11-26

## Goal
The owner is buying one month of EODHD's paid plan (2026-11-26) so the backtest's data is
checked across the development period, not only the last year. With full history, EODHD is
the better primary source: it's the licensed provider prod already uses, and the first
export run showed Yahoo missing dividends (VWRL 2013, IGLT and INXG 2009). Yahoo becomes
the cross-check.

## Acceptance criteria
| # | Criterion (observable, pass/fail) | Evidence |
|---|---|---|
| AC1 | Every series with an EODHD symbol (the 8 funds and VWRL) is written from EODHD's full daily history: adjusted close, open scaled by the same factor, today's bar dropped, unusable rows skipped and counted | tests |
| AC2 | Each EODHD series is checked against Yahoo's: daily returns (the existing limits), the last common close within 2% (units), and EODHD's history starting no more than 31 days after Yahoo's (catches a free-plan key's single year) | tests |
| AC3 | Dividend yields, the skipped-row limit and the 10x and open-gap checks apply to the series used | tests |
| AC4 | A series with no EODHD symbol (the Vanguard fund under VAGP) is Yahoo's alone, never sent to EODHD; the manifest records, per series, which source was used and what checked it | tests |
| AC5 | Key handling unchanged: hidden prompt only; no key in errors or files | the existing tests |

## Non-goals
- Choosing proxies (done: #78), or repairing Yahoo's gaps by hand: EODHD replaces them.
- Keeping the free-plan mode: after the paid month, a re-export is refused (AC2's start check).

## Decisions
- Request from 1990-01-01, so each listing's whole history comes back.
- The level check uses the last common day, where both sources' adjusted closes equal the
  raw close, so a pence/pounds mix-up shows as a 100x gap.
- The Vanguard OEIC stays on Yahoo: EODHD doesn't list it on the LSE.

## Plan
1. Tests first, then `scripts/export_history.py` and the universe (VWRL's EODHD symbol).
   Check: `make check`; each new behaviour broken in turn fails a test.
2. Reviewer and security reviewer, PR, owner approval; then the owner runs the export.

## Progress log
- Review round 1 (reviewer): 1 blocking, fixed: the 10x and open-gap checks are now tested on EODHD data too. Taken: an EODHD row without an open keeps its close as the open and is counted (`opens_filled`), not dropped; a docstring warning against a free-plan run; the manifest names EODHD years before Yahoo's as unchecked; a test for the Yahoo-only series' dividend check; the earlier plan marked superseded. Recorded as tech-debt: a Yahoo bar fault still stops the export. 77 tests pass.
- 2026-11-26 built: 73 export tests pass. Six planted breaks (Yahoo still used, no level check, no start check, a later request date, unscaled opens, uncounted skipped rows) each fail a test.

## Outcome
