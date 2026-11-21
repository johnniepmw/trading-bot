# 0006. Python with uv, ruff, mypy (strict), pytest and Hypothesis

- **Status**: accepted
- **Date**: 2026-11-21
- **Deciders**: owner
- **Enforcement**: `make check-fast` (ruff format and lint, `mypy --strict`, pytest); `uv.lock` with `uv sync --frozen`

## Context
A trading bot is numeric and data-heavy, and the owner's goal is learning first. The
research ecosystem (pandas, numpy, notebooks, backtesting libraries) is Python. Agents
write all the code, so the toolchain must catch mistakes mechanically.

## Options considered
1. **Python with uv, ruff, mypy strict, pytest, Hypothesis**: best data ecosystem; one
   fast tool for environments and locking; strict typing narrows the gap with compiled
   languages.
2. **TypeScript**: strong types and the template's other pack; weak numeric and research
   libraries.
3. **Rust or Go**: speed and safety we don't need for a bot that trades weekly.

## Decision
Option 1, using the template's python stack pack. Hypothesis property tests for
invariants (money arithmetic, risk limits). import-linter enforces package boundaries.

## Consequences
- Good: research and production share one language; mypy strict plus property tests
  catch the class of bug that loses money.
- Bad / costs: Python's runtime typing is advisory; boundaries need parsing into typed
  values. Strict mypy slows down notebook-style code (which lives in `research/`).
- Revisit when: performance matters (it shouldn't at daily frequency).
