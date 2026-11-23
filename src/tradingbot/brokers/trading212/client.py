"""HTTP for the Trading 212 public API (Phase 2 step 4): demo only, until ADR-0009's live switch.

Basic auth with the key and secret from one mounted secret (`KEY:SECRET`). Redirects are
refused and proxy settings ignored, so the credentials only ever go to the demo host.
Response bodies never reach exception text or logs (they hold account data, and this
repository is public: ADR-0014); an error names the status and, at most, Trading 212's
short error code.

How failures map onto the broker port:
- a 4xx other than 408 means the request was refused and nothing happened: `BrokerError`;
- a timeout, a dropped connection, a 408 or a 5xx leaves the outcome unknown:
  `BrokerTimeoutError` (for an order, it may have been placed);
- a redirect is refused: `BrokerError`.
Reads that hit the rate limit (429) wait and try once more; an order that hits it is
refused (`BrokerError`): Trading 212 rejects it before looking at it.
"""

from __future__ import annotations

import base64
import http.client
import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from decimal import Decimal
from pathlib import Path

from tradingbot.brokers.port import BrokerError, BrokerTimeoutError

DEMO_BASE = "https://demo.trading212.com/api/v0"
CREDENTIALS_PATH = Path("/secrets/t212-demo-key/value")  # mounted in prod only (infra/envs/prod)
TIMEOUT_SECONDS = 30.0
RATE_LIMIT_WAIT = 10.0  # seconds before the one retry of a rate-limited read

# Seconds between calls to each endpoint group, from Trading 212's published limits plus a
# margin (the owner's API check used the same figures without a 429).
MIN_INTERVAL: Mapping[str, float] = {
    "summary": 5.5,
    "positions": 1.2,
    "orders": 5.5,
    "order": 1.2,
    "history": 10.5,
    "transactions": 10.5,
    "market": 1.3,
}

_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,59}")

# (method, url, headers, body) -> (status, body); raises BrokerTimeoutError on a network failure
Transport = Callable[[str, str, Mapping[str, str], bytes | None], tuple[int, bytes]]


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_: object, **__: object) -> None:
        return None  # urllib then raises HTTPError with the 3xx status


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _RefuseRedirects())  # no env proxies


def urllib_transport(timeout: float = TIMEOUT_SECONDS) -> Transport:
    def send(method: str, url: str, headers: Mapping[str, str], body: bytes | None) -> tuple[int, bytes]:
        request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)  # noqa: S310 - fixed host
        try:
            with _OPENER.open(request, timeout=timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()
        except (TimeoutError, urllib.error.URLError, ConnectionError, http.client.HTTPException) as error:
            # the path only: no query, no body; the error type, not its text
            raise BrokerTimeoutError(
                f"{method} {url.split('?', maxsplit=1)[0]}: {type(error).__name__}"
            ) from error

    return send


class NotFoundError(BrokerError):
    """HTTP 404: for an order ID, it is no longer active (look in the history)."""


def read_credentials(path: Path | None = None) -> str:
    """The mounted `KEY:SECRET`. Error messages never include any of it."""
    try:
        value = (path or CREDENTIALS_PATH).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as error:
        raise BrokerError(f"Trading 212 key unreadable ({type(error).__name__})") from error
    key, sep, secret = value.partition(":")
    if not sep or not key or not secret or ":" in secret or not value.isprintable() or " " in value:
        raise BrokerError(
            "Trading 212 key missing or not KEY:SECRET (make secret-set NAME=t212-demo-key ENV=prod)"
        )
    return value


def _code(body: bytes) -> str:
    """Trading 212's error code, when it is a short identifier; never the body itself."""
    try:
        parsed = json.loads(body)
    except ValueError:
        return ""
    code = parsed.get("code") if isinstance(parsed, dict) else None
    return f" ({code})" if isinstance(code, str) and _CODE.fullmatch(code) else ""


class Trading212Client:
    def __init__(
        self,
        transport: Transport,
        credentials: str,
        *,
        base: str = DEMO_BASE,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if base != DEMO_BASE:
            raise BrokerError("only the Trading 212 demo API is allowed (ADR-0009)")
        self._send = transport
        self._auth = "Basic " + base64.b64encode(credentials.encode()).decode()
        self._base = base
        self._clock = clock
        self._sleep = sleep
        self._last: dict[str, float] = {}

    def get(self, path: str, group: str) -> object:
        for attempt in range(2):
            status, body = self._call("GET", path, group, None)
            if status == 429 and attempt == 0:  # noqa: PLR2004 - HTTP status
                self._sleep(RATE_LIMIT_WAIT)
                continue
            return self._parse("GET", path, status, body)
        raise AssertionError("unreachable")  # pragma: no cover

    def post(self, path: str, payload: Mapping[str, object], group: str) -> object:
        status, body = self._call("POST", path, group, json.dumps(payload).encode())
        return self._parse("POST", path, status, body)

    def _call(self, method: str, path: str, group: str, data: bytes | None) -> tuple[int, bytes]:
        last = self._last.get(group)
        if last is not None:
            wait = MIN_INTERVAL[group] - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        headers = {"Authorization": self._auth, "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        try:
            return self._send(method, self._base + path, headers, data)
        finally:
            self._last[group] = self._clock()

    @staticmethod
    def _parse(method: str, path: str, status: int, body: bytes) -> object:
        where = f"{method} {path.split('?', maxsplit=1)[0]}"
        if 200 <= status < 300:  # noqa: PLR2004 - HTTP status
            try:
                return json.loads(body, parse_float=Decimal) if body else None  # exact quantities
            except ValueError as error:
                raise BrokerTimeoutError(f"{where}: HTTP {status} with a body that is not JSON") from error
        if 300 <= status < 400:  # noqa: PLR2004 - HTTP status
            raise BrokerError(f"{where}: redirect refused (HTTP {status})")
        if status == 408 or status >= 500:  # noqa: PLR2004 - HTTP status
            raise BrokerTimeoutError(f"{where}: HTTP {status}{_code(body)}")
        if status == 404:  # noqa: PLR2004 - HTTP status
            raise NotFoundError(f"{where}: HTTP 404{_code(body)}")
        raise BrokerError(f"{where}: HTTP {status}{_code(body)}")
