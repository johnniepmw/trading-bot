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
    return {
        "LEDGER_BUCKET": "ledger",
        "LOCK_BUCKET": "locks",
        "PAUSE_BUCKET": "pause",
        "LOCAL_STORE_DIR": str(tmp_path / "store"),
    }


def log_lines(result: subprocess.CompletedProcess[str]) -> list[dict[str, str]]:
    return [json.loads(line) for line in result.stderr.splitlines() if line.strip()]


SETUP_DAY = "2026-11-30T07:15:00+00:00"  # a Monday, the last trading day of November
RUN_DAY = "2026-12-01T07:15:00+00:00"  # the first run after the month end


def test_setup_then_a_month_end_run_then_a_dry_run(run_job: RunJob, tmp_path: Path) -> None:
    # fixed dates (FAKE_NOW, local and test only), so the outcome is the same on any day
    setup = run_job("setup", "--equity", "1000", env={**wiring(tmp_path), "FAKE_NOW": SETUP_DAY})
    assert setup.returncode == 0, setup.stderr
    assert "setup completed" in [line["message"] for line in log_lines(setup)][-1]

    run = run_job("run", env={**wiring(tmp_path), "FAKE_NOW": RUN_DAY})
    assert run.returncode == 0, run.stderr
    assert any(line["message"].startswith("run completed") for line in log_lines(run))

    dry = run_job("run", "--dry-run", env={**wiring(tmp_path), "FAKE_NOW": RUN_DAY})
    assert dry.returncode == 0, dry.stderr
    assert any(line["message"].startswith("dry run: ") for line in log_lines(dry))
    assert all(line["severity"] != "ERROR" for line in log_lines(dry))


def test_a_missing_ledger_fails_closed(run_job: RunJob, tmp_path: Path) -> None:
    # the dry run checks the ledger on any day (a real-clock `run` on a weekend does nothing)
    result = run_job("run", "--dry-run", env=wiring(tmp_path))
    assert result.returncode == 1
    assert any("missing" in line["message"] for line in log_lines(result))  # the account or the ledger


def test_prod_refuses_to_start_on_fakes(run_job: RunJob, tmp_path: Path) -> None:
    env = {
        "APP_ENV": "prod",
        "LEDGER_BUCKET": "l",
        "LOCK_BUCKET": "k",
        "PAUSE_BUCKET": "p",
        "FLAGS_BUCKET": "f",
    }
    result = run_job("run", env=env)
    assert result.returncode == 1
    assert any("refusing to start" in line["message"] for line in log_lines(result))
