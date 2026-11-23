"""Offline tests for scripts/t212_record.py: it prints response shapes, never account values."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest
from t212_record import shape  # scripts/ is on the path (conftest)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "t212_record.py"


def test_numbers_and_free_text_are_hidden() -> None:
    raw = {"id": 1234567, "cash": {"availableToTrade": 987.65}, "name": "Vanguard", "ok": True}
    assert shape(raw) == {
        "id": "<number>",
        "cash": {"availableToTrade": "<number>"},
        "name": "<string>",
        "ok": True,
    }


def test_names_the_adapter_must_match_are_kept() -> None:
    raw = {"status": "FILLED", "side": "BUY", "ticker": "VWRPl_EQ", "currency": "GBP", "reference": "x"}
    assert shape(raw) == {
        "status": "FILLED",
        "side": "BUY",
        "ticker": "VWRPl_EQ",
        "currency": "GBP",
        "reference": "<string>",
    }


def test_lists_show_every_key_seen_and_a_count() -> None:
    raw = [{"type": "DEPOSIT", "amount": 5}, {"type": "WITHDRAW", "fee": 1}]
    assert shape(raw) == [{"type": "WITHDRAW", "amount": "<number>", "fee": "<number>"}, "<2 item(s)>"]


def test_a_next_page_path_keeps_its_path_but_not_its_cursor() -> None:
    assert shape({"nextPagePath": "/api/v0/history/transactions?cursor=123&limit=5"}) == {
        "nextPagePath": "/api/v0/history/transactions?<query>"
    }


def _run(base_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "T212_API_KEY": "test-key",
        "T212_API_SECRET": "test-secret",
        "T212_TEST_BASE_URL": base_url,
        "T212_TEST_FAST": "1",
    }
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60, check=False
    )


def _mock_get(base_url: str, path: str) -> object:
    token = base64.b64encode(b"test-key:test-secret").decode()
    request = urllib.request.Request(base_url + path, headers={"Authorization": f"Basic {token}"})  # noqa: S310 - the local mock
    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 - the local mock
        return json.loads(response.read())


def test_timestamps_show_their_format_only() -> None:
    assert shape({"createdAt": "2026-11-23T08:24:16.000Z"}) == {"createdAt": "9999-99-99T99:99:99.999Z"}


def test_read_only_run_prints_shapes_without_values(mock_api: str) -> None:
    res = _run(mock_api)
    assert res.returncode == 0, res.stderr
    assert "=== GET /history/transactions?limit=5: HTTP 200" in res.stdout
    assert '"type": "DEPOSIT"' in res.stdout
    assert "5000" not in res.stdout  # the balance
    assert "1000" not in res.stdout  # the deposit amount
    assert "ref-1" not in res.stdout
    assert "2026-11-23" not in res.stdout
    assert "POST" not in res.stdout


def test_the_order_check_reports_the_step_and_leaves_nothing_behind(mock_api: str) -> None:
    res = _run(mock_api, "--order", "ISF_EQ")
    assert res.returncode == 0, res.stdout
    assert "Quantity step: 0.12345 refused (precision)" in res.stdout
    assert "Quantity step: 0.1234 refused (precision)" in res.stdout
    assert "asked for 0.123, the order records 0.123" in res.stdout
    assert "Quantity step: filled 0.123" in res.stdout
    assert "Sold it back: FILLED" in res.stdout
    assert "No pending order from this run: yes" in res.stdout
    assert "ISF_EQ position unchanged since the start: yes" in res.stdout
    assert _mock_get(mock_api, "/equity/positions") == []


def test_the_order_check_gives_a_units_verdict_not_prices(mock_api: str) -> None:
    res = _run(mock_api, "--order", "ISF_EQ")
    assert "Units: the fill price is in pence" in res.stdout
    assert "820" not in res.stdout
    assert '"id": 1000' not in res.stdout


@pytest.mark.parametrize("mock_api", ["closed"], indirect=True)
def test_an_unfilled_order_is_cancelled(mock_api: str) -> None:
    res = _run(mock_api, "--order", "ISF_EQ")
    assert res.returncode == 0, res.stdout
    assert "Not filled" in res.stdout
    assert _mock_get(mock_api, "/equity/orders") == []


def test_live_is_refused() -> None:
    res = _run("https://live.trading212.com/api/v0")
    assert res.returncode != 0
    assert "Refusing" in res.stderr


def test_a_refusal_other_than_precision_stops_the_ladder(mock_api: str) -> None:
    res = _run(mock_api, "--order", "NOT_A_TICKER")
    assert "refused for another reason" in res.stdout
    assert "refused (precision)" not in res.stdout  # stopped at the first quantity
