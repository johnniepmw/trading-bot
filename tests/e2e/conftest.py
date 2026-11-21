"""E2E tests run the job's container entry point as a subprocess, as Cloud Run will.

The bot is a Cloud Run job (ADR-0011), so there is no URL to target. Deployed checks
(running the job in staging) arrive with the job module in Phase 2.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

RunJob = Callable[..., subprocess.CompletedProcess[str]]


@pytest.fixture(scope="session")
def run_job() -> RunJob:
    if os.environ.get("E2E_BASE_URL"):
        pytest.skip("the bot is a Cloud Run job with no URL; deployed e2e arrives in Phase 2")

    def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        full_env = {
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "APP_VERSION": "e2e",
            "APP_ENV": "test",
            **(env or {}),
        }
        return subprocess.run(
            [sys.executable, "-m", "tradingbot.app", *args],
            cwd=ROOT,
            env=full_env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    return _run
