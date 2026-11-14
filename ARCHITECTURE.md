# Architecture

A map for people and agents changing this codebase. Describes things that change
rarely; details live in code and ADRs. Update it in the same PR as any structural change.

## Bird's-eye view
<!-- bootstrap: 3–5 sentences. What the system does, its main inputs and outputs,
     and the one or two ideas that shape the design. -->
{{What the system does and how, in a few sentences.}}

## Codemap
<!-- One entry per important directory/module: what it is responsible for, and what it
     must not do. Name modules so they can be found with a symbol search. -->
| Path | Responsibility | Must not |
|---|---|---|
| `src/` | {{application code}} | |
| `e2e/` or `tests/` | tests (see docs/testing.md) | import production secrets |
| `infra/` | OpenTofu for GCP (see ADR-0004) | contain secret values |
| `scripts/` | repo tooling used by make and CI | contain business logic |

## Boundaries and dependency direction
<!-- e.g. domain ← application ← adapters (http, db, broker). Inner layers never import
     outer ones. Enforced by: dependency-cruiser / import-linter config, run in make check. -->
- {{core}} is pure: no I/O, no framework imports.
- Adapters (HTTP, storage, third-party APIs) implement interfaces owned by the core.
- Only the composition root (`main`/`server`) wires real adapters.

## Invariants
<!-- Things that must always be true. Each should have a test. -->
- Every external side effect is idempotent or guarded by a recorded intent.
- Configuration is validated at startup; the app refuses to start with invalid config.
- {{project-specific invariant}}

## Cross-cutting concerns
- **Config**: environment variables for non-secret settings; secrets as files under
  `/secrets/<name>/value` (ADR-0005).
- **Flags**: `flags.json` + `isEnabled()` (docs/feature-flags.md).
- **Logging**: structured JSON to stdout; no secrets or personal data.
- **Errors**: fail closed; errors carry context; no swallowed exceptions.
- **Time and money**: UTC with an injectable clock; decimal money with currency.
