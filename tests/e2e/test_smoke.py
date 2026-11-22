from __future__ import annotations

import json
import subprocess
from collections.abc import Callable

import pytest

RunJob = Callable[..., subprocess.CompletedProcess[str]]

pytestmark = pytest.mark.e2e


def test_smoke_command_reports_ok_with_the_real_flag_registry(run_job: RunJob) -> None:
    result = run_job("smoke")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "status": "ok",
        "version": "e2e",
        "new_orders": True,
        "paused": False,
    }


def test_kill_switch_override_is_visible_to_the_job(run_job: RunJob) -> None:
    result = run_job("smoke", env={"FLAG_NEW_ORDERS": "off"})
    assert json.loads(result.stdout)["new_orders"] is False


def test_unknown_command_fails_closed(run_job: RunJob) -> None:
    result = run_job("place-orders")
    assert result.returncode != 0


def test_deployed_job_without_a_flags_bucket_refuses_to_start(run_job: RunJob) -> None:
    result = run_job("smoke", env={"APP_ENV": "prod", "FLAGS_BUCKET": ""})
    assert result.returncode == 1
    assert result.stdout == ""
