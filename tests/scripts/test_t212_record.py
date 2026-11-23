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
from t212_record import (
    LIVE_BASE,
    LIVE_PAGES,
    TransactionsOnly,
    _live_base,
    _NoRedirects,
    kinds,
    live_transactions,
    reference_kind,
    shape,
)  # scripts/ is on the path (conftest)

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
    assert "=== GET /history/transactions?limit=50: HTTP 200" in res.stdout
    assert "  DEPOSIT + x2" in res.stdout
    assert "  WITHDRAW - x1" in res.stdout
    assert '"type": "DEPOSIT"' in res.stdout
    assert "5000" not in res.stdout  # the balance
    assert "1000" not in res.stdout  # the deposit amount
    assert "ref-2" not in res.stdout
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


def test_kinds_are_listed_without_amounts() -> None:
    page = {"items": [{"type": "FEE", "amount": -0.5}, {"type": "INTEREST", "amount": "x"}, "junk"]}
    assert kinds(page) == ["FEE - x1", "INTEREST ? x1"]
    assert kinds({"items": []}) == ["(none)"]


# --- --live-transactions: read-only, one endpoint --------------------------------------
class Spy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def call(self, method: str, path: str, bucket: str, body: dict | None = None) -> dict:
        self.calls.append((method, path))
        return {"status": 200, "data": {"items": [], "nextPagePath": None}}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/equity/orders/market"),
        ("DELETE", "/equity/orders/1"),
        ("GET", "/equity/account/summary"),
        ("GET", "/equity/positions"),
        ("GET", "/history/transactionsX?limit=1"),
    ],
)
def test_live_mode_refuses_everything_but_reading_transactions(method: str, path: str) -> None:
    spy = Spy()
    with pytest.raises(SystemExit, match="Refusing"):
        TransactionsOnly(spy).call(method, path, "x")  # type: ignore[arg-type]
    assert spy.calls == []


def test_live_mode_refuses_a_body_even_on_a_get() -> None:
    spy = Spy()
    with pytest.raises(SystemExit, match="Refusing"):
        TransactionsOnly(spy).call("GET", "/history/transactions?limit=1", "x", {"a": 1})  # type: ignore[arg-type]
    assert spy.calls == []


def test_live_mode_reads_transactions() -> None:
    spy = Spy()
    TransactionsOnly(spy).call("GET", "/history/transactions?limit=50", "history")  # type: ignore[arg-type]
    assert spy.calls == [("GET", "/history/transactions?limit=50")]


def test_live_mode_targets_the_live_host_without_a_test_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("T212_TEST_BASE_URL", raising=False)
    assert _live_base() == LIVE_BASE == "https://live.trading212.com/api/v0"


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        ("12345678", "digits(8)"),
        ("0b9f0b9e-1a2b-4c3d-9e8f-001122334455", "uuid"),
        ("Payment from J Bloggs", "text(21)"),
        (None, "missing"),
        ("", "missing"),
    ],
)
def test_references_are_described_never_shown(value: object, kind: str) -> None:
    assert reference_kind(value) == kind


def test_live_transactions_lists_kinds_and_reference_shapes_only(mock_api: str) -> None:
    res = _run(mock_api, "--live-transactions")
    assert res.returncode == 0, res.stderr
    assert "  DEPOSIT + x2" in res.stdout
    assert "  WITHDRAW - x1" in res.stdout
    assert "References: text(5) x3" in res.stdout
    assert "References unique: yes" in res.stdout
    assert "positions" not in res.stdout and "summary" not in res.stdout
    for value in ("1000", "-10", "2026-11-23", "ref-1", "ref-2", "ref-3"):  # no amounts, dates or references
        assert value not in res.stdout


def test_live_mode_never_places_the_quantity_check(mock_api: str) -> None:
    res = _run(mock_api, "--live-transactions", "--order", "ISF_EQ")
    assert res.returncode != 0
    assert "demo only" in res.stderr


class Pager:
    """Returns the scripted next-page links in turn."""

    def __init__(self, *links: object) -> None:
        self.links = list(links)
        self.calls: list[str] = []

    def call(self, method: str, path: str, bucket: str, body: dict | None = None) -> dict:
        self.calls.append(path)
        following = self.links.pop(0) if self.links else None
        return {
            "status": 200,
            "data": {"items": [{"type": "DEPOSIT", "amount": 1}], "nextPagePath": following},
        }


def test_live_mode_follows_next_pages_within_the_endpoint() -> None:
    pager = Pager("/api/v0/history/transactions?cursor=2&limit=50")
    live_transactions(TransactionsOnly(pager))  # type: ignore[arg-type]
    assert pager.calls == ["/history/transactions?limit=50", "/history/transactions?cursor=2&limit=50"]


def test_live_mode_stops_after_its_page_limit(capsys: pytest.CaptureFixture[str]) -> None:
    pager = Pager(*["/api/v0/history/transactions?cursor=same"] * 10)
    live_transactions(TransactionsOnly(pager))  # type: ignore[arg-type]
    assert len(pager.calls) == LIVE_PAGES
    assert "older transactions not read" in capsys.readouterr().out


@pytest.mark.parametrize(
    "link",
    [
        "/api/v0/equity/orders/market",
        "https://evil.example/history/transactions?x",
        "/api/v0/history/transactionsX?x",
    ],
)
def test_live_mode_refuses_a_next_page_outside_the_endpoint(link: str) -> None:
    pager = Pager(link)
    with pytest.raises(SystemExit, match="Refusing"):
        live_transactions(TransactionsOnly(pager))  # type: ignore[arg-type]
    assert len(pager.calls) == 1


def test_live_mode_follows_no_redirects() -> None:
    assert _NoRedirects().redirect_request() is None  # type: ignore[call-arg, func-returns-value]


def test_a_leftover_test_server_setting_is_refused_in_live_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("T212_TEST_BASE_URL", "http://127.0.0.1:1/api/v0")
    monkeypatch.delenv("T212_TEST_FAST", raising=False)
    with pytest.raises(SystemExit, match="unset it"):
        _live_base()
