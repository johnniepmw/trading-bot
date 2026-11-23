"""Offline tests for scripts/t212_record.py: it prints response shapes, never account values."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from t212_record import shape  # scripts/ is on the path (conftest)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "t212_record.py"


def test_numbers_and_free_text_are_hidden() -> None:
    raw = {"id": 58500460886, "cash": {"availableToTrade": 987.65}, "name": "Vanguard", "ok": True}
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


def test_read_only_run_prints_shapes_without_the_balance(mock_api: str) -> None:
    res = _run(mock_api)
    assert res.returncode == 0, res.stderr
    assert "=== GET /history/transactions?limit=5: HTTP 200" in res.stdout
    assert '"type": "DEPOSIT"' in res.stdout
    assert "5000" not in res.stdout
    assert "POST" not in res.stdout


def test_the_order_check_reports_the_recorded_quantity_and_sells_back(mock_api: str) -> None:
    res = _run(mock_api, "--order", "ISF_EQ")
    assert res.returncode == 0, res.stderr
    assert "asked for 0.12345, the order records 0.12345" in res.stdout
    assert "Quantity step: filled 0.12345" in res.stdout
    assert "Sold it back: HTTP 200" in res.stdout


def test_the_order_check_shows_the_units_of_prices(mock_api: str) -> None:
    res = _run(mock_api, "--order", "ISF_EQ")
    assert "Units: ISF_EQ current price 820.0" in res.stdout
