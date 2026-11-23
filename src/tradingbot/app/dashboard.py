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
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tradingbot.app.calendar import TradingCalendar
from tradingbot.app.glance_facts import glance_input
from tradingbot.app.job import JobConfigError, build_store, wiring_from
from tradingbot.app.pause import PauseRequestError, request_pause
from tradingbot.app.remote_ledger import (
    LOCK_OBJECT,
    STALE_AFTER,
    LedgerStorageError,
    RemoteLedger,
    RestoredLedgerError,
)
from tradingbot.app.storage import ObjectStore, StorageError
from tradingbot.config.load import load_config
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
        rows[0] = RunRow(latest.started_at, facts.outcome, facts.reason)  # running or died, as above
        page = glance_page(
            glance(facts),
            as_of=facts.as_of,
            environment=self.environment,
            runs=rows,
            paused=self._paused(),
        )
        return Response(HTTPStatus.OK, page)

    def _lock_held(self) -> bool:
        """A run holds a fresh lock; one older than STALE_AFTER belongs to a run that died."""
        try:
            info = self.store.info(self.lock_bucket, LOCK_OBJECT)
        except StorageError:
            return False
        return info is not None and self.clock() - info.created <= STALE_AFTER

    def _paused(self) -> bool | None:
        """Whether a pause request exists; None when the pause location can't be read. Unlike
        the job (which must fail closed), the page then says "unknown" and keeps the button."""
        try:
            return self.store.has_objects(self.pause_bucket)
        except StorageError as error:
            log.warning("dashboard: pause location unreadable (%s)", type(error).__name__)
            return None

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
    timeout = 15  # seconds per socket read: a slow client can't hold a thread
    sys_version = ""

    def _answer(self, method: str) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY:
            self._send(Response(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, error_page("Bad or too large body")))
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

    def do_HEAD(self) -> None:
        self._answer("HEAD")

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """Malformed requests too get the security headers, and no server text."""
        self.close_connection = True
        self._send(Response(HTTPStatus(code), error_page("Bad request")))

    def _send(self, response: Response) -> None:
        data = response.body.encode()
        self.send_response(response.status)
        for name, value in HEADERS.items():
            self.send_header(name, value)
        if response.location:
            self.send_header("Location", response.location)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - http.server's name
        log.info("dashboard %s", format % args)


class _Server(ThreadingHTTPServer):
    dashboard: Dashboard


def make_server(dashboard: Dashboard, port: int, host: str = "0.0.0.0") -> _Server:  # noqa: S104 - Cloud Run
    server = _Server((host, port), _Handler)
    server.dashboard = dashboard
    return server


DASHBOARD_TIMEOUT = 10.0  # seconds per storage call: a slow bucket shows an error page quickly


def build_dashboard(
    env: Mapping[str, str], clock: Callable[[], datetime] | None = None
) -> tuple[Dashboard, int]:
    """The dashboard service's wiring (`serve`): the same bucket names and checks as the job,
    no flags, no broker, no secrets (dashboard plan, slice 1)."""
    wiring = wiring_from(env)
    now = clock or (lambda: datetime.now(UTC))
    try:
        port = int(env.get("PORT", "8080"))
    except ValueError as error:
        raise JobConfigError("PORT must be a number") from error
    dashboard = Dashboard(
        store=build_store(wiring, now, timeout=DASHBOARD_TIMEOUT),
        environment=wiring.app_env,
        ledger_bucket=wiring.ledger_bucket,
        lock_bucket=wiring.lock_bucket,
        pause_bucket=wiring.pause_bucket,
        config=load_config(),
        clock=now,
    )
    return dashboard, port
