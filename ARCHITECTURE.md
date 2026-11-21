# Architecture

A map for people and agents changing this codebase. Describes things that change
rarely; details live in code and ADRs. Update it in the same PR as any structural change.

## Bird's-eye view
A long-or-cash trend system for 6–8 UK-listed ETFs in a Trading 212 Invest account (GBP,
up to £1,000, no leverage). A Cloud Run job, started by Cloud Scheduler, loads daily
prices, computes signals and target weights, passes every proposed order through a risk
layer with a veto, records intended orders in a ledger and only then sends them to the
broker. Two ideas shape the design: the pure core (strategy, portfolio, risk) runs
unchanged in backtests and live, and every failure fails closed: no new orders, plus an
alert. Phase 0 has the package skeleton, the `Money` type and a smoke entry point.

## Codemap
| Path | Responsibility | Must not |
|---|---|---|
| `src/tradingbot/domain` | value types: `Money` (GBP/GBX/USD), later instruments, orders | import anything else from `tradingbot` |
| `src/tradingbot/data` | load and clean market data | place orders |
| `src/tradingbot/strategy` | signals from prices | do I/O, read env vars, import brokers or HTTP |
| `src/tradingbot/portfolio` | target weights and order sizing | as strategy |
| `src/tradingbot/risk` | veto, shrink or pass each order; hard ceilings as constants | as strategy; be bypassed |
| `src/tradingbot/execution` | send approved orders, ledger first | import a concrete broker |
| `src/tradingbot/brokers` | `Broker` interface; `trading212/` and `fake/` | leak broker JSON past the adapter |
| `src/tradingbot/ledger` | intended orders, fills, runs (SQLite in GCS, ADR-0013) | allow two concurrent writers |
| `src/tradingbot/config` | typed config, validated at start-up | hold secret values in code |
| `src/tradingbot/app` | entry points (`python -m tradingbot.app`), wiring, flags, alerts, kill switch | contain trading logic |
| `docs/strategies` | one spec per strategy: hypothesis, parameters, validation, gate | change parameters after results without a trial-log entry |
| `research/` | notebooks and experiments | be imported by `src` |
| `tests/` | unit, property, e2e (`tests/e2e` runs the job entry point) | use real account data |
| `scripts/phase0_api_check.py` | owner-run demo API check (stdlib only) | touch the live API |
| `infra/` | OpenTofu for GCP (ADR-0004; job module in Phase 2) | contain secret values |
| `scripts/` | repo tooling used by make and CI | contain business logic |

## Boundaries and dependency direction
`domain` ← `strategy` / `portfolio` / `risk` (pure) ← `execution` ← `app` (composition
root) → `brokers`, `data`, `ledger`. Enforced by import-linter (`make arch`,
contracts in `.importlinter`):
1. `domain` imports nothing else from the project.
2. `strategy`, `portfolio` and `risk` import only `domain` and config types, never each
   other, and never brokers, data I/O, HTTP libraries or `os` (environment variables).
3. Only `brokers` imports HTTP libraries to reach a broker; `app` may too for GCP
   adapters (flag overrides from Cloud Storage) (3a). Only `app` imports `execution`, and
   only `execution` and `app` import `brokers`, so every order goes through the path that
   applies risk (3c, 3d). Only `app` imports `brokers.trading212` or `brokers.fake` (3b).
4. `research` is never imported by `src`.

Contracts live in `.importlinter` (high risk; `make arch` names the file so no other
file can shadow it). Contracts 3b–3d are allow-lists, so a new subpackage is covered
automatically; it must still be added to contract 1's forbidden list and 3a's sources. Contract 2 checks indirect
imports too: keep config *types* in an `os`-free module (for example `config/types.py`)
and the loading that reads the environment elsewhere (`config/load.py`). GCP client
libraries (`google`) aren't on the HTTP list yet; decide where the GCS ledger adapter
may use them when it lands (ADR-0013, Phase 1–2).

## Invariants
Each should have a test; the ones marked (Phase 0) already do.
- **Money**: `Decimal` with an explicit currency; `Money` refuses to mix GBP with GBX
  (pence) and floats are rejected (Phase 0, `tests/domain/test_money.py`).
- **Time**: UTC only, with an injectable clock. Market hours come from the broker's
  schedule.
- **Fail closed**: any unexpected error means no new orders, plus an alert.
- **Idempotent runs**: intended orders are written to the ledger before they are sent;
  every run starts by reconciling the ledger with the broker.
- **Config**: typed and validated at start-up; invalid config means the bot refuses to
  start.
- **Kill switch**: the `new-orders` flag off means no new orders, and it fails closed: an
  unreadable, missing or malformed remote source, a missing `FLAGS_BUCKET` outside
  local/test, mean off or no start, and an env `on` is ignored in a deployed environment
  (`tests/app/test_flags.py`, `tests/app/test_main.py`).
- **Live trading is owner-only**: the live switch and capital allocation come from the
  `operator` secret; missing or invalid means paper trading (ADR-0009).
- **Risk ceilings are constants in code**: no leverage; a hard stop at a 20% drawdown
  from peak; position-size caps. Day-to-day limits may only be tighter.
- **Account data stays private**: balances, positions, orders, fills, P&L and raw broker
  responses never appear in Actions logs, PR or issue comments, check summaries or
  artifacts (ADR-0014).

## Cross-cutting concerns
- **Config**: environment variables for non-secret settings; secrets as files under
  `/secrets/<name>/value` (ADR-0005).
- **Flags**: `flags.json` + `is_enabled()` (docs/feature-flags.md).
- **Logging**: structured JSON to stdout, tagged with the run ID, collected by Cloud
  Logging; no secrets, and account data only there.
- **Errors**: fail closed; errors carry context; no swallowed exceptions.
- **Time and money**: UTC with an injectable clock; decimal money with currency (ADR-0010).
