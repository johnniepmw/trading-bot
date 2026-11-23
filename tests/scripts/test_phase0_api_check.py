"""Offline tests for scripts/phase0_api_check.py against a local mock of the Trading 212 API."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "phase0_api_check.py"
MOCK = Path(__file__).with_name("mock_t212.py")


def _run(base_url: str, tmp_path: Path, key: str = "test-key") -> tuple[int, dict]:
    out = tmp_path / "report.json"
    env = {
        **os.environ,
        "T212_API_KEY": key,
        "T212_API_SECRET": "test-secret",
        "T212_TEST_BASE_URL": base_url,
        "T212_TEST_FAST": "1",
    }
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--out", str(out)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return res.returncode, json.loads(out.read_text())


@pytest.mark.parametrize("mock_api", ["open", "closed"], indirect=True)
def test_all_checks_pass_and_nothing_left_pending(mock_api, tmp_path):
    code, report = _run(mock_api, tmp_path)
    assert code == 0
    assert report["summary"]["fail"] == 0
    assert report["steps"][-1]["step"] == "Cleanup: no test orders left pending"
    assert report["steps"][-1]["result"] == "PASS"


def test_bad_key_fails_cleanly(mock_api, tmp_path):
    code, report = _run(mock_api, tmp_path, key="wrong")
    assert code == 1
    assert report["steps"][0]["step"] == "Authenticate"


def test_report_never_contains_credentials(mock_api, tmp_path):
    _, report = _run(mock_api, tmp_path)
    text = json.dumps(report)
    assert "test-key" not in text and "test-secret" not in text


def test_refuses_non_local_override(tmp_path):
    env = {
        **os.environ,
        "T212_API_KEY": "a",
        "T212_API_SECRET": "b",
        "T212_TEST_BASE_URL": "https://live.trading212.com/api/v0",
    }
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--out", str(tmp_path / "r.json")],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert res.returncode != 0
    assert "Refusing" in res.stderr
