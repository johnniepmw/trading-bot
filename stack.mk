# python stack: uv + ruff + mypy + pytest. Root Makefile targets call these.
STACK_SETUP      := uv sync --frozen
STACK_FMT        := uv run ruff format . && uv run ruff check --fix .
STACK_FMT_CHECK  := uv run ruff format --check .
STACK_LINT       := uv run ruff check .
STACK_TYPECHECK  := uv run mypy
STACK_TEST_UNIT  := uv run pytest -q -m "not e2e and not integration and not flaky" --ignore=tests/e2e
STACK_TEST_INT   := uv run pytest -q -m "integration and not flaky" || [ $$? -eq 5 ]
STACK_TEST_ONE   := uv run pytest -q $(T)
STACK_ARCH       := PYTHONPATH=src uv run lint-imports .importlinter
STACK_E2E_SETUP  := @echo "e2e-setup: nothing to install for the python stack"
STACK_E2E        := uv run pytest -q tests/e2e $(if $(E2E_INCLUDE_QUARANTINED),,-m "not flaky")
STACK_DEV        := APP_ENV=local PYTHONPATH=src uv run python -m tradingbot.app smoke
STACK_MUTATION   := uv run mutmut run
