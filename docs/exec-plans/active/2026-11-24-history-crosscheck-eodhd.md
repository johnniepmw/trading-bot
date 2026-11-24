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
| AC3 | The key is asked for hidden only (never an environment variable or command line, so never in shell history), never written to the manifest, output or any error: fetch errors name the host and path only | tests: a failing fetch's error has no key; the manifest has no key |
| AC4 | Only https on Yahoo's and EODHD's hosts, redirects included; Stooq is removed from the script, the universe and the docs that describe the export | tests; grep |
| AC5 | `--list` uses Yahoo only (it exists to choose proxies, and EODHD's 20 calls a day are shared with prod) | tests |

## Non-goals
- Choosing VAGP's proxy: that stays the owner's `--list` decision before any prices are
  examined (Phase 1 step 8a).
- A paid-plan path: a paid key needs only a longer request window, a one-line change.

## Decisions
- Primary series only, to keep the export to 8 of the free plan's 20 calls a day. Prod's run
  uses 8 a run (ADR-0018), so run the export at most once a day, after that day's 07:15 run,
  and never on a month-end order day.
- Consequence the owner accepted: the free year falls entirely in the hold-out period (from
  2023-01-01), so the development period gets no second-source check. The manifest says so.
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
- 2026-11-24 built. Evidence, `tests/scripts/test_export_history.py` (64 pass):
  AC1 `test_only_the_second_sources_window_is_compared`, `test_too_few_compared_days_is_a_problem`,
  `test_the_real_minimum_of_compared_days_is_200`, `test_eodhds_adjusted_close_is_read_and_todays_bar_dropped`,
  `test_eodhd_is_asked_for_400_days_up_to_yesterday`, `test_a_second_source_covering_too_little_of_its_window_is_a_problem`;
  AC2 `test_proxies_are_not_sent_to_eodhd_and_the_manifest_says_what_was_checked`;
  AC3 `test_a_failed_fetch_never_shows_the_key`, `test_a_refused_redirect_never_shows_the_key`,
  `test_the_key_is_never_written`, `test_an_unexpected_eodhd_response_is_an_export_error_without_the_body`,
  `test_the_key_typed_at_the_prompt_reaches_eodhd`, `test_the_export_needs_an_eodhd_key`;
  AC4 `test_only_https_on_the_two_data_hosts_is_fetched_including_redirects` (stooq.com now refused);
  AC5 `test_list_mode_spends_no_eodhd_calls`. Six planted breaks (key in a fetch error, key in a
  redirect refusal, no window filter, proxies sent to EODHD, raw close for adjusted, no minimum)
  each fail a test. `make check` and `make e2e` pass.
- Review round 1 (reviewer): pass, 0 blocking. Taken: once a day after the 07:15 run; the
  manifest says the development period isn't cross-checked; a stale Stooq note in Phase 1
  removed; a test that the typed key reaches EODHD and one for the real 200-day minimum; `EOFError` from
  the key prompt is an export error. Not taken: counting EODHD's dropped rows (the coverage
  limit already catches a real gap).
- Security review (round 1): pass, 1 low, taken: the `EODHD_API_KEY` route invited the key onto
  a command line and into Cloud Shell's history, so the key is now asked for hidden only; the
  leak test raises a real `HTTPError` carrying the key in its URL. Noted, not changed: the
  script follows allowlisted redirects (ADR-0018's prod transport refuses them); urllib
  forwards no query or header, so the key can't follow one.

## Outcome
