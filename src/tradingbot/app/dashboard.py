"""The dashboard service (dashboard plan, slice 1; docs/exec-plans/active/2026-11-23-dashboard.md).

`python -m tradingbot.app serve` answers on $PORT behind IAP (owner only; ADR-0020). It reads
the latest ledger into a scratch copy, never takes the lock and never writes the ledger
(the reference-mark check still refuses a restored ledger), and shows the latest run's glance
and recent runs. Its one control is the one-way pause: a POST behind a confirm page that
stores a create-only request; nothing here can resume. The trading job never depends on it.

Requests that change anything must be POSTs from the page itself: `Sec-Fetch-Site` must be
`same-origin` (every current browser sends it), so another site can't press the button, and
the CSP forbids framing, so it can't trick the owner into pressing it either.
"""

from __future__ import annotations

import logging
import tempfile
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tradingbot.app.calendar import TradingCalendar
from tradingbot.app.glance_facts import glance_input
from tradingbot.app.pause import PauseRequestError, PauseRequests, request_pause
from tradingbot.app.remote_ledger import (
    LOCK_OBJECT,
    LedgerStorageError,
    RemoteLedger,
    RestoredLedgerError,
)
from tradingbot.app.storage import ObjectStore, StorageError
from tradingbot.config.types import BotConfig
from tradingbot.report.glance import glance
from tradingbot.report.page import RunRow, confirm_page, error_page, glance_page, paused_page
from tradingbot.risk.limits import Limits

log = logging.getLogger(__name__)

RECENT_RUNS = 10
MAX_BODY = 2048
HEADERS = {
    "Content-Type": "text/html; charset=utf-8",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'none'"
    ),
}


@dataclass(frozen=True)
class Response:
    status: HTTPStatus
    body: str = ""
    location: str | None = None


@dataclass(frozen=True)
class Dashboard:
    store: ObjectStore
    environment: str
    ledger_bucket: str
    lock_bucket: str
    pause_bucket: str
    config: BotConfig
    clock: Callable[[], datetime]

    def home(self) -> Response:
        with tempfile.TemporaryDirectory() as scratch:
            remote = RemoteLedger(
                self.store,
                ledger_bucket=self.ledger_bucket,
                lock_bucket=self.lock_bucket,
                workdir=Path(scratch),
                clock=self.clock,
            )
            try:
                ledger = remote.open_scratch()
            except RestoredLedgerError:
                return _unavailable("The ledger looks restored from an older version: the bot refuses it too")
            except LedgerStorageError:
                return _unavailable("The ledger can't be read right now (or setup hasn't run)")
            try:
                runs = sorted(ledger.runs(), key=lambda r: r.started_at)
                if not runs:
                    return _unavailable("No runs yet")
                latest = runs[-1]
                facts = glance_input(
                    ledger,
                    latest.run_id,
                    today=latest.started_at.date(),
                    calendar=TradingCalendar(self.config.holidays, until=self.config.calendar_until),
                    universe=self.config.universe,
                    limits=Limits(**vars(self.config.limits)),
                    now=self.clock(),
                    in_progress=self._lock_held(),
                )
            finally:
                ledger.close()
        rows = [RunRow(r.started_at, r.outcome, r.reason) for r in reversed(runs[-RECENT_RUNS:])]
        page = glance_page(
            glance(facts),
            as_of=facts.as_of,
            environment=self.environment,
            runs=rows,
            paused=PauseRequests(self.store, self.pause_bucket).present(),
        )
        return Response(HTTPStatus.OK, page)

    def _lock_held(self) -> bool:
        try:
            return self.store.info(self.lock_bucket, LOCK_OBJECT) is not None
        except StorageError:
            return False

    def confirm(self) -> Response:
        return Response(HTTPStatus.OK, confirm_page(self.environment))

    def pause(self, form: dict[str, list[str]]) -> Response:
        if form.get("confirm") != ["yes"]:
            return Response(HTTPStatus.BAD_REQUEST, error_page("Not confirmed: nothing changed"))
        try:
            request_pause(self.store, self.pause_bucket, self.clock())
        except PauseRequestError as error:
            log.error("pause request failed: %s", error)
            return Response(
                HTTPStatus.SERVICE_UNAVAILABLE,
                error_page(
                    "NOT paused: the request couldn't be stored. Stop new orders with the ops workflow "
                    "instead (kill new-orders)."
                ),
            )
        return Response(HTTPStatus.SEE_OTHER, location="/paused")  # a reload can't press it again

    def paused(self) -> Response:
        return Response(HTTPStatus.OK, paused_page(self.environment))


def _unavailable(message: str) -> Response:
    return Response(HTTPStatus.SERVICE_UNAVAILABLE, error_page(message))


def route(  # noqa: PLR0911 - one return per route
    dashboard: Dashboard, method: str, path: str, headers: dict[str, str], body: bytes
) -> Response:
    """The whole HTTP surface. GETs never change anything; POSTs need `Sec-Fetch-Site:
    same-origin`."""
    path = path.split("?", 1)[0]
    if method == "GET":
        if path == "/":
            return dashboard.home()
        if path == "/paused":
            return dashboard.paused()
        if path in ("/pause", "/pause/confirm"):  # e.g. a POST that IAP replayed as a GET: nothing
            return Response(HTTPStatus.SEE_OTHER, location="/")
        return Response(HTTPStatus.NOT_FOUND, error_page("Not found"))
    if method != "POST":
        return Response(HTTPStatus.METHOD_NOT_ALLOWED, error_page("Not allowed"))
    if headers.get("sec-fetch-site") != "same-origin":
        return Response(HTTPStatus.FORBIDDEN, error_page("Refused: press the button on the dashboard itself"))
    if path == "/pause/confirm":
        return dashboard.confirm()
    if path == "/pause":
        return dashboard.pause(urllib.parse.parse_qs(body.decode("ascii", "replace")))
    return Response(HTTPStatus.NOT_FOUND, error_page("Not found"))


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    server_version = "dashboard"
    sys_version = ""

    def _answer(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self._send(Response(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, error_page("Too large")))
            return
        body = self.rfile.read(length) if length else b""
        headers = {k.lower(): v for k, v in self.headers.items()}
        try:
            response = route(self.server.dashboard, method, self.path, headers, body)
        except Exception:  # an error page, never a traceback in the browser
            log.exception("dashboard request failed")
            response = _unavailable("Something went wrong; the details are in Cloud Logging")
        self._send(response)

    def do_GET(self) -> None:
        self._answer("GET")

    def do_POST(self) -> None:
        self._answer("POST")

    def do_PUT(self) -> None:
        self._answer("PUT")

    def do_DELETE(self) -> None:
        self._answer("DELETE")

    def _send(self, response: Response) -> None:
        data = response.body.encode()
        self.send_response(response.status)
        for name, value in HEADERS.items():
            self.send_header(name, value)
        if response.location:
            self.send_header("Location", response.location)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - http.server's name
        log.info("dashboard %s", format % args)


class _Server(ThreadingHTTPServer):
    dashboard: Dashboard


def make_server(dashboard: Dashboard, port: int, host: str = "0.0.0.0") -> _Server:  # noqa: S104 - Cloud Run
    server = _Server((host, port), _Handler)
    server.dashboard = dashboard
    return server
