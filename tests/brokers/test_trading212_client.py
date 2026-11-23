"""The Trading 212 client's HTTP rules: demo only, no redirects, no response bodies in errors."""

from __future__ import annotations

import base64
import http.server
import importlib
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator, Mapping
from decimal import Decimal
from pathlib import Path

import pytest

from tradingbot.brokers.port import BrokerError, BrokerTimeoutError
from tradingbot.brokers.trading212 import client as client_module
from tradingbot.brokers.trading212.client import (
    DEMO_BASE,
    RATE_LIMIT_WAIT,
    NotFoundError,
    Trading212Client,
    read_credentials,
    urllib_transport,
)

SECRET_BODY = b'{"code": "InsufficientFunds", "clarification": "cash 987.65 GBP", "accountId": 4242}'


class Recorder:
    def __init__(self, *responses: tuple[int, bytes]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def __call__(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        self.calls.append((method, url, dict(headers), body))
        return self.responses.pop(0)


class Clock:
    def __init__(self) -> None:
        self.now = 100.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def client(transport: Recorder, clock: Clock | None = None) -> Trading212Client:
    c = clock or Clock()
    return Trading212Client(transport, "key:secret", clock=c, sleep=c.sleep)


def test_reads_send_basic_auth_to_the_demo_host_and_parse_json() -> None:
    t = Recorder((200, b'{"a": 1}'))
    assert client(t).get("/equity/positions", "positions") == {"a": 1}
    method, url, headers, body = t.calls[0]
    assert (method, url, body) == ("GET", f"{DEMO_BASE}/equity/positions", None)
    assert headers["Authorization"] == "Basic " + base64.b64encode(b"key:secret").decode()


def test_numbers_are_parsed_exactly() -> None:
    assert client(Recorder((200, b'{"q": 0.1}'))).get("/x", "order") == {"q": Decimal("0.1")}


def test_not_found_is_its_own_error() -> None:
    with pytest.raises(NotFoundError):
        client(Recorder((404, b""))).get("/equity/orders/1", "order")


def test_posts_send_json() -> None:
    t = Recorder((200, b'{"id": 7}'))
    assert client(t).post("/equity/orders/market", {"ticker": "X", "quantity": 1}, "market") == {"id": 7}
    _, _, headers, body = t.calls[0]
    assert headers["Content-Type"] == "application/json"
    assert json.loads(body or b"") == {"ticker": "X", "quantity": 1}


def test_any_host_but_demo_is_refused() -> None:
    with pytest.raises(BrokerError, match="demo"):
        Trading212Client(Recorder(), "k:s", base="https://live.trading212.com/api/v0")


def test_a_refusal_is_a_broker_error_naming_the_code_but_not_the_body() -> None:
    with pytest.raises(BrokerError) as error:
        client(Recorder((400, SECRET_BODY))).post("/equity/orders/market", {}, "market")
    assert type(error.value) is BrokerError
    assert str(error.value) == "POST /equity/orders/market: HTTP 400 (InsufficientFunds)"


def test_a_problem_document_names_its_error_type() -> None:
    body = b'{"type": "/api-errors/quantity-precision-mismatch", "detail": "987.65", "traceId": "t"}'
    with pytest.raises(BrokerError) as error:
        client(Recorder((400, body))).post("/equity/orders/market", {}, "market")
    assert str(error.value) == "POST /equity/orders/market: HTTP 400 (quantity-precision-mismatch)"


@pytest.mark.parametrize("kind", ["https://evil/api-errors/x", "/api-errors/has space", 5])
def test_an_unexpected_problem_type_is_left_out(kind: object) -> None:
    with pytest.raises(BrokerError) as error:
        client(Recorder((400, json.dumps({"type": kind}).encode()))).get("/x", "order")
    assert str(error.value) == "GET /x: HTTP 400"


def test_a_body_that_is_not_an_object_names_no_code() -> None:
    with pytest.raises(BrokerError) as error:
        client(Recorder((400, b"[1]"))).get("/x", "order")
    assert str(error.value) == "GET /x: HTTP 400"


def test_a_code_that_is_not_a_short_identifier_is_left_out() -> None:
    with pytest.raises(BrokerError) as error:
        client(Recorder((400, b'{"code": "cash 987.65"}'))).get("/x", "order")
    assert str(error.value) == "GET /x: HTTP 400"


@pytest.mark.parametrize("status", [408, 500, 502, 503, 504])
def test_a_server_error_or_request_timeout_leaves_the_outcome_unknown(status: int) -> None:
    with pytest.raises(BrokerTimeoutError) as error:
        client(Recorder((status, SECRET_BODY))).post("/equity/orders/market", {}, "market")
    assert "987" not in str(error.value)


def test_a_redirect_is_refused() -> None:
    with pytest.raises(BrokerError, match="redirect refused"):
        client(Recorder((302, b""))).get("/equity/positions", "positions")


def test_a_success_that_is_not_json_leaves_the_outcome_unknown() -> None:
    with pytest.raises(BrokerTimeoutError, match="not JSON"):
        client(Recorder((200, b"<html>"))).post("/equity/orders/market", {}, "market")


def test_the_query_string_stays_out_of_errors() -> None:
    with pytest.raises(BrokerError) as error:
        client(Recorder((404, b""))).get("/history/transactions?cursor=99&limit=50", "transactions")
    assert str(error.value) == "GET /history/transactions: HTTP 404"


def test_a_rate_limited_read_waits_and_tries_once_more() -> None:
    clock = Clock()
    t = Recorder((429, b""), (200, b"[]"))
    assert client(t, clock).get("/equity/positions", "positions") == []
    assert RATE_LIMIT_WAIT in clock.slept
    assert len(t.calls) == 2


def test_a_read_rate_limited_twice_is_an_error() -> None:
    with pytest.raises(BrokerError, match="HTTP 429"):
        client(Recorder((429, b""), (429, b""))).get("/equity/positions", "positions")


def test_a_rate_limited_order_is_refused_not_retried() -> None:
    t = Recorder((429, b""))
    with pytest.raises(BrokerError) as error:
        client(t).post("/equity/orders/market", {}, "market")
    assert type(error.value) is BrokerError
    assert len(t.calls) == 1


def test_calls_to_one_endpoint_group_are_spaced_out() -> None:
    clock = Clock()
    c = client(Recorder((200, b"{}"), (200, b"{}")), clock)
    c.get("/equity/account/summary", "summary")
    clock.now += 1.5
    c.get("/equity/account/summary", "summary")
    assert clock.slept == [pytest.approx(4.0)]


def test_different_groups_do_not_wait_for_each_other() -> None:
    clock = Clock()
    c = client(Recorder((200, b"{}"), (200, b"[]")), clock)
    c.get("/equity/account/summary", "summary")
    c.get("/equity/positions", "positions")
    assert clock.slept == []


# --- credentials ---------------------------------------------------------------------
def test_credentials_are_read_from_the_mounted_secret(tmp_path: Path) -> None:
    (tmp_path / "value").write_text("abc:def\n")
    assert read_credentials(tmp_path / "value") == "abc:def"


@pytest.mark.parametrize("value", ["", "__unset__", "abc", ":def", "abc:", "a:b:c", "ab c:def", "abc:d\tf"])
def test_bad_credentials_are_refused_without_echoing_them(tmp_path: Path, value: str) -> None:
    (tmp_path / "value").write_text(value)
    with pytest.raises(BrokerError, match="KEY:SECRET") as error:
        read_credentials(tmp_path / "value")
    assert value.strip() == "" or value.strip() not in str(error.value)


def test_missing_credentials_are_an_error(tmp_path: Path) -> None:
    with pytest.raises(BrokerError, match="unreadable"):
        read_credentials(tmp_path / "missing")


# --- the real transport ---------------------------------------------------------------
class _Response:
    status = 200

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return b"{}"


def test_the_transport_returns_status_and_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_module._OPENER, "open", lambda request, timeout: _Response())
    assert urllib_transport()("GET", f"{DEMO_BASE}/x", {}, None) == (200, b"{}")


def test_the_transport_returns_http_errors_as_statuses(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(request: object, timeout: float) -> None:
        raise urllib.error.HTTPError(f"{DEMO_BASE}/x", 404, "nf", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(client_module._OPENER, "open", fail)
    assert urllib_transport()("GET", f"{DEMO_BASE}/x", {}, None)[0] == 404


def test_a_network_failure_is_an_unknown_outcome_without_the_query(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(request: object, timeout: float) -> None:
        raise TimeoutError("secret detail")

    monkeypatch.setattr(client_module._OPENER, "open", fail)
    with pytest.raises(BrokerTimeoutError) as error:
        urllib_transport()("POST", f"{DEMO_BASE}/x?cursor=1", {}, b"{}")
    assert str(error.value) == f"POST {DEMO_BASE}/x: TimeoutError"


class _Redirecting(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/moved":
            self.send_response(302)
            self.send_header("Location", "/elsewhere")
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *_: object) -> None:
        return None


@pytest.fixture
def local_server() -> Iterator[str]:
    server = http.server.HTTPServer(("127.0.0.1", 0), _Redirecting)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_the_transport_ignores_proxy_settings(local_server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # urllib reads proxy variables when the opener is built (at import), so rebuild it under them
    for name in ("HTTP_PROXY", "http_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")  # nothing listens there
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    fresh = importlib.reload(client_module)
    try:
        assert fresh.urllib_transport(timeout=5)("GET", f"{local_server}/ok", {}, None) == (200, b"{}")
    finally:
        monkeypatch.undo()
        importlib.reload(client_module)


def test_the_transport_does_not_follow_redirects(local_server: str) -> None:
    assert urllib_transport(timeout=5)("GET", f"{local_server}/moved", {}, None)[0] == 302
