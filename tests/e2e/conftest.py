"""E2E tests run against E2E_BASE_URL (preview, staging) or a server started here."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="session")
def base_url() -> Iterator[str]:
    external = os.environ.get("E2E_BASE_URL")
    if external:
        yield external.rstrip("/")
        return
    port = _free_port()
    env = {**os.environ, "PORT": str(port), "PYTHONPATH": str(ROOT / "src"), "APP_VERSION": "e2e"}
    proc = subprocess.Popen([sys.executable, "-m", "app.server"], cwd=ROOT, env=env)
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"{url}/healthz", timeout=1):  # noqa: S310 - local
                    break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("local server did not become healthy")
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=10)
