"""The job's commands end to end, as Cloud Run runs them: setup, run and a dry run on fakes."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

RunJob = Callable[..., subprocess.CompletedProcess[str]]

pytestmark = pytest.mark.e2e


def wiring(tmp_path: Path) -> dict[str, str]:
    return {"LEDGER_BUCKET": "ledger", "LOCK_BUCKET": "locks", "LOCAL_STORE_DIR": str(tmp_path / "store")}


def log_lines(result: subprocess.CompletedProcess[str]) -> list[dict[str, str]]:
    return [json.loads(line) for line in result.stderr.splitlines() if line.strip()]


def test_setup_then_run_then_dry_run(run_job: RunJob, tmp_path: Path) -> None:
    setup = run_job("setup", "--equity", "1000", env=wiring(tmp_path))
    assert setup.returncode == 0, setup.stderr
    assert "setup completed" in [line["message"] for line in log_lines(setup)][-1]

    run = run_job("run", env=wiring(tmp_path))
    assert run.returncode == 0, run.stderr  # today's real date: a run, or "not a trading day"
    # on a trading day, buys made the same day as setup are vetoed at ERROR ("no day-start
    # equity", ledger-setup.md): expected, so this checks only that the run didn't stop
    assert all("cycle stopped" not in line["message"] for line in log_lines(run))

    dry = run_job("run", "--dry-run", env=wiring(tmp_path))
    assert dry.returncode in {0, 1}, dry.stderr
    assert any("dry run" in line["message"] for line in log_lines(dry))
    assert all("cycle stopped" not in line["message"] for line in log_lines(dry))


def test_a_missing_ledger_fails_closed(run_job: RunJob, tmp_path: Path) -> None:
    # the dry run checks the ledger on any day (a real-clock `run` on a weekend does nothing)
    result = run_job("run", "--dry-run", env=wiring(tmp_path))
    assert result.returncode == 1
    assert any("missing" in line["message"] for line in log_lines(result))  # the account or the ledger


def test_prod_refuses_to_start_on_fakes(run_job: RunJob, tmp_path: Path) -> None:
    env = {"APP_ENV": "prod", "LEDGER_BUCKET": "l", "LOCK_BUCKET": "k", "FLAGS_BUCKET": "f"}
    result = run_job("run", env=env)
    assert result.returncode == 1
    assert any("refusing to start" in line["message"] for line in log_lines(result))
