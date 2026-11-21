"""The 100% branch-coverage gate on `risk` can't be switched off quietly (security review, PR 3).

This file is a high-risk path (`tests/risk/**`), so weakening it needs the owner's approval.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_stack_mk_runs_unit_tests_with_the_risk_coverage_gate() -> None:
    stack = (ROOT / "stack.mk").read_text()
    for flag in ("--cov=tradingbot.risk", "--cov-branch", "--cov-fail-under=100"):
        assert flag in stack


def test_no_coverage_or_pytest_setting_in_pyproject_can_weaken_the_gate() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert "coverage" not in config.get("tool", {}), "coverage settings would apply to the risk gate"
    addopts = str(config["tool"]["pytest"]["ini_options"].get("addopts", ""))
    for weakener in ("cov", "-p no:", "--ignore", "--deselect", "-k ", "-k=", "--co"):
        assert weakener not in addopts, weakener


@pytest.mark.parametrize(
    "name",
    [".coveragerc", "pytest.ini", ".pytest.ini", "pytest.toml", ".pytest.toml", "setup.cfg", "tox.ini"],
)
def test_no_file_that_pytest_or_coverage_prefers_over_pyproject_exists(name: str) -> None:
    assert not (ROOT / name).exists()


def has_pragma(text: str) -> bool:
    # coverage matches "# pragma: no cover" loosely (spacing, case, "no branch"); refuse any pragma
    return "pragma" in text.lower()


@pytest.mark.parametrize(
    "planted", ["# pragma: no cover", "#pragma no cover", "# PRAGMA: NO COVER", "# pragma:no branch"]
)
def test_the_pragma_check_catches_each_spelling(planted: str) -> None:
    assert has_pragma(f"x = 1  {planted}")


def test_risk_code_has_no_coverage_pragmas() -> None:
    paths = list((ROOT / "src" / "tradingbot" / "risk").rglob("*.py"))
    assert paths
    assert [p.name for p in paths if has_pragma(p.read_text())] == []
