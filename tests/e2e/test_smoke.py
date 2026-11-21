from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

import pytest

pytestmark = pytest.mark.e2e


def get(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310 - test target URL
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def test_health_reports_ok_and_a_version(base_url: str) -> None:
    status, body = get(f"{base_url}/healthz")
    assert status == 200
    data = json.loads(body)
    assert data["status"] == "ok"
    assert data["version"]


def test_home_page_greets_by_name_and_escapes_input(base_url: str) -> None:
    status, body = get(f"{base_url}/?name=" + urllib.parse.quote("<b>Ada</b>"))
    assert status == 200
    assert "<h1>Hello, &lt;b&gt;Ada&lt;/b&gt;!</h1>" in body


def test_unknown_path_is_404(base_url: str) -> None:
    status, _ = get(f"{base_url}/definitely-not-here")
    assert status == 404
