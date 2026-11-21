"""The 100% branch-coverage gate on `risk` can't be switched off quietly (security review, PR 3).

This file is a high-risk path (`tests/risk/**`), so weakening it needs the owner's approval.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_stack_mk_runs_unit_tests_with_the_risk_coverage_gate() -> None:
    stack = (ROOT / "stack.mk").read_text()
    for flag in ("--cov=tradingbot.risk", "--cov-branch", "--cov-fail-under=100"):
        assert flag in stack


def test_no_coverage_or_pytest_setting_in_pyproject_can_weaken_the_gate() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert "coverage" not in config.get("tool", {}), "coverage settings would apply to the risk gate"
    addopts = str(config["tool"]["pytest"]["ini_options"].get("addopts", ""))
    assert "cov" not in addopts and "-p no:" not in addopts


def test_no_coverage_config_file_exists() -> None:
    assert not (ROOT / ".coveragerc").exists()


def test_risk_code_has_no_coverage_pragmas() -> None:
    for path in (ROOT / "src" / "tradingbot" / "risk").rglob("*.py"):
        assert "pragma: no" not in path.read_text(), path
