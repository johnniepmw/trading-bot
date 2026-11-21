---
paths:
  - "src/**/*.py"
  - "tests/**/*.py"
---
- Python 3.13, typed throughout; `mypy --strict` must pass. No `Any` unless at a boundary
  with a comment explaining why.
- Run everything through uv (`uv run ...`); add dependencies with `uv add`, never pip.
- `src/app/core/` is pure: no I/O, no adapters (import-linter enforces).
- Parse untrusted input at the boundary into dataclasses or typed values.
- Money: `decimal.Decimal` with explicit currency, never float. Time: timezone-aware UTC
  datetimes with an injectable clock.
- Feature checks go through `FLAGS.is_enabled("<name>")` with the name in `flags.json`.
- Errors: specific exception types with context; never bare `except:`; log with `logging`
  (JSON in production), never `print`.
- Tests: pytest; property-based tests (Hypothesis) for invariants; e2e in `tests/e2e`
  marked `e2e`.
