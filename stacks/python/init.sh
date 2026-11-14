#!/usr/bin/env bash
# Add current versions of the dev toolchain and write uv.lock.
set -euo pipefail
uv add --dev ruff mypy pytest hypothesis import-linter mutmut
uv sync
