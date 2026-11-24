"""The dashboard end to end: `serve` as a process, the page over HTTP, and the pause button
pressed through its confirm page, after which the job's own `smoke` reports paused."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

SETUP_DAY = "2026-11-30T07:15:00+00:00"  # as test_job.py: a Monday, the month's last trading day
RUN_DAY = "2026-12-01T07:15:00+00:00"


def wiring(tmp_path: Path) -> dict[str, str]:
    return {
        "LEDGER_BUCKET": "ledger",
        "LOCK_BUCKET": "locks",
        "PAUSE_BUCKET": "pause",
        "LOCAL_STORE_DIR": str(tmp_path / "store"),
    }


RunJob = Callable[..., subprocess.CompletedProcess[str]]
ROOT = Path(__file__).resolve().parents[2]
SAME = {"Sec-Fetch-Site": "same-origin"}

pytestmark = pytest.mark.e2e


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def dashboard(run_job: RunJob, tmp_path: Path) -> Iterator[str]:
    assert (
        run_job("setup", "--equity", "1000", env={**wiring(tmp_path), "FAKE_NOW": SETUP_DAY}).returncode == 0
    )
    assert run_job("run", env={**wiring(tmp_path), "FAKE_NOW": RUN_DAY}).returncode == 0
    port = _free_port()
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "APP_ENV": "test",
        "PORT": str(port),
        **wiring(tmp_path),
    }
    proc = subprocess.Popen([sys.executable, "-m", "tradingbot.app", "serve"], cwd=ROOT, env=env)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_: object, **__: object) -> None:
        return None


OPENER = urllib.request.build_opener(_NoRedirect())


def call(
    url: str, data: bytes | None = None, headers: dict[str, str] | None = None
) -> tuple[int, str, dict[str, str]]:
    request = urllib.request.Request(  # noqa: S310 - the local server
        url, data=data, headers=headers or {}, method="POST" if data is not None else "GET"
    )
    try:
        with OPENER.open(request, timeout=5) as response:
            return response.status, response.read().decode(), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode(), dict(error.headers)


def test_the_page_then_a_confirmed_pause_then_smoke_reports_paused(
    dashboard: str, run_job: RunJob, tmp_path: Path
) -> None:
    status, page, _ = call(dashboard + "/")
    assert status == 200 and "Run: completed" in page

    assert call(dashboard + "/pause")[0] == 303  # a GET changes nothing
    assert call(dashboard + "/pause", b"confirm=yes", {"Sec-Fetch-Site": "cross-site"})[0] == 403
    smoke = run_job("smoke", env=wiring(tmp_path))
    assert json.loads(smoke.stdout)["paused"] is False

    status, confirm, _ = call(dashboard + "/pause/confirm", b"", SAME)
    assert status == 200 and "Yes, pause new orders" in confirm
    status, _, headers = call(dashboard + "/pause", b"confirm=yes", SAME)
    assert (status, headers["Location"]) == (303, "/paused")

    smoke = run_job("smoke", env=wiring(tmp_path))
    assert json.loads(smoke.stdout)["paused"] is True
    assert "New orders are paused" in call(dashboard + "/")[1]
