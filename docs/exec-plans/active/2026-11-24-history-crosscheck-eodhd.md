# History export: EODHD replaces Stooq as the cross-check, last year only

- **Status**: active
- **Branch / PR**: ccr-d0003009-0e03lt / one PR
- **Risk class**: high (`scripts/**`, `research/universe.toml`)
- **Started**: 2026-11-24

## Goal
Phase 1 step 8a's export cross-checks Yahoo's history against a second source, and Stooq
no longer serves data (Phase 2 log, 2026-11-23). The owner chose (2026-11-24) to check
against EODHD's free plan, which covers about the last year, rather than buy a month of
the paid plan for the full history. The years before that rely on Yahoo alone, and the
manifest says so.

## Acceptance criteria
| # | Criterion (observable, pass/fail) | Evidence |
|---|---|---|
| AC1 | Each fund's own series is compared, on daily returns, with EODHD's `adjusted_close` over the dates EODHD returns (about a year on the free plan); the existing limits apply inside that window (2% a day, over 1% of days disagreeing, any day over 10%, under 90% coverage), and fewer than 200 compared days is a problem | tests |
| AC2 | Proxies are not sent to EODHD: their spliced years lie before the fund's own history, outside the free year. The manifest records, per series, the checked window or that it was not checked, and a top-level note that earlier years rely on Yahoo alone | tests |
| AC3 | The key is asked for hidden (or read from `EODHD_API_KEY`), never written to the manifest, output or any error: fetch errors name the host and path only | tests: a failing fetch's error has no key; the manifest has no key |
| AC4 | Only https on Yahoo's and EODHD's hosts, redirects included; Stooq is removed from the script, the universe and the docs that describe the export | tests; grep |
| AC5 | `--list` uses Yahoo only (it exists to choose proxies, and EODHD's 20 calls a day are shared with prod) | tests |

## Non-goals
- Choosing VAGP's proxy: that stays the owner's `--list` decision before any prices are
  examined (Phase 1 step 8a).
- A paid-plan path: a paid key needs only a longer request window, a one-line change.

## Decisions
- Primary series only, to keep the export to 8 of the free plan's 20 calls a day. Prod's run
  uses 8 a run (ADR-0018), so don't run the export on a month-end order day.
- The window is what EODHD returns for a 400-day request (as `scripts/eodhd_check.py`
  found: about 252 bars), so the check can't silently shrink: fewer than 200 compared days
  is a problem.
- A wrong key, an EODHD outage or a symbol EODHD doesn't know stops the export with the
  host and path only; urllib's error text holds no URL, and the query is never shown.

## Plan
1. Failing tests for AC1–AC5, then the change to `scripts/export_history.py` and
   `research/universe.toml`. Check: `make check`.
2. Fresh-eyes review (reviewer and security-reviewer: the key handling), PR, owner approval.

## Progress log

## Outcome
