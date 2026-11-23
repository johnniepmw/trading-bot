"""Make the stdlib-only gate scripts in scripts/ importable from these tests."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

MOCK = Path(__file__).with_name("mock_t212.py")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def mock_api(request):
    market = getattr(request, "param", "open")
    port = _free_port()
    env = {**os.environ, "MOCK_MARKET": market}
    proc = subprocess.Popen([sys.executable, str(MOCK), str(port)], env=env)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
            break
        except OSError:
            time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/api/v0"
    proc.terminate()
    proc.wait(timeout=5)
