"""The dashboard service (slice 1): pages from a real ledger made by a real run, and the pause
button's rules. Local store, fake data only (ADR-0014)."""

from __future__ import annotations

import logging
import sqlite3
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from pathlib import Path

import pytest

from tests.app.test_job import RUN_DAY, job_at, store
from tradingbot.app.dashboard import HEADERS, Dashboard, Response, make_server, route
from tradingbot.app.remote_ledger import LEDGER_OBJECT, LOCK_OBJECT, MARK_OBJECT
from tradingbot.app.storage import ObjectInfo, StorageError
from tradingbot.config.load import load_config
from tradingbot.ledger.store import Ledger

SAME = {"sec-fetch-site": "same-origin"}


@pytest.fixture
def ran(tmp_path: Path) -> Path:
    assert job_at(tmp_path, RUN_DAY - timedelta(days=1)).setup("1000") == 0
    assert job_at(tmp_path, RUN_DAY).run() == 0
    return tmp_path


def dashboard(root: Path, now: datetime = RUN_DAY + timedelta(hours=2)) -> Dashboard:
    return Dashboard(
        store=store(root),
        environment="test",
        ledger_bucket="ledger",
        lock_bucket="locks",
        pause_bucket="pause",
        config=load_config(),
        clock=lambda: now,
    )


def get(d: Dashboard, path: str = "/") -> Response:
    return route(d, "GET", path, {}, b"")


def post(d: Dashboard, path: str, body: bytes = b"", headers: dict[str, str] | None = None) -> Response:
    return route(d, "POST", path, SAME if headers is None else headers, body)


def pause_requests(root: Path) -> bool:
    return store(root).has_objects("pause")


def test_the_home_page_shows_the_latest_runs_glance(ran: Path) -> None:
    page = get(dashboard(ran))
    assert page.status is HTTPStatus.OK
    assert page.body.count("<li>") == 7
    assert "<b>Run</b>: completed" in page.body
    assert "<b>Since setup</b>:" in page.body
    assert '<form method=post action="/pause/confirm">' in page.body
    assert "<td>completed</td>" in page.body


def test_the_glance_uses_the_runs_own_day_even_on_a_weekend(ran: Path) -> None:
    saturday = datetime(2026, 12, 5, 10, tzinfo=UTC)
    page = get(dashboard(ran, now=saturday))
    assert "no equity recorded" not in page.body


def test_viewing_never_writes_the_ledger(ran: Path) -> None:
    before = store(ran).info("ledger", LEDGER_OBJECT)
    get(dashboard(ran))
    assert store(ran).info("ledger", LEDGER_OBJECT) == before
    assert store(ran).info("locks", LOCK_OBJECT) is None


def test_a_run_holding_the_lock_shows_as_in_progress(ran: Path) -> None:
    s = store(ran)
    s.write("locks", LOCK_OBJECT, b"{}", if_generation_match=0)
    # an unfinished run record, as a run in progress has uploaded
    info = s.info("ledger", LEDGER_OBJECT)
    assert info is not None
    path = ran / "copy.sqlite"
    path.write_bytes(s.read("ledger", LEDGER_OBJECT, info.generation))
    led = Ledger.open(path)
    led.start_run("run-live", RUN_DAY + timedelta(days=1, hours=7), "v")
    led.close()
    s.write("ledger", LEDGER_OBJECT, path.read_bytes(), if_generation_match=info.generation)
    assert "<b>Run</b>: in progress since" in get(dashboard(ran, now=RUN_DAY + timedelta(minutes=10))).body
    # past STALE_AFTER the lock belongs to a run that died (killed at its timeout, say)
    stale = get(dashboard(ran, now=RUN_DAY + timedelta(hours=2))).body
    assert "<b>Run</b>: did not finish" in stale
    assert "<td>died</td>" in stale  # the table agrees with the glance


def test_an_unreadable_pause_location_is_unknown_and_keeps_the_button(
    ran: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(bucket: str) -> bool:
        raise StorageError("503")

    d = dashboard(ran)
    monkeypatch.setattr(d.store, "has_objects", broken)
    with caplog.at_level(logging.WARNING):
        page = get(d).body
    assert "Pause state unknown" in page
    assert '<form method=post action="/pause/confirm">' in page
    assert "New orders are paused" not in page
    assert all(getattr(r, "alert", None) is None for r in caplog.records)  # not the job's alert


def test_only_the_last_ten_runs_are_listed(ran: Path) -> None:
    s = store(ran)
    info = s.info("ledger", LEDGER_OBJECT)
    assert info is not None
    path = ran / "copy.sqlite"
    path.write_bytes(s.read("ledger", LEDGER_OBJECT, info.generation))
    led = Ledger.open(path)
    for n in range(12):
        led.start_run(f"old-{n}", RUN_DAY - timedelta(days=30 - n), "v")
        led.finish_run(f"old-{n}", RUN_DAY - timedelta(days=30 - n, minutes=-1), "completed", None)
    led.close()
    s.write("ledger", LEDGER_OBJECT, path.read_bytes(), if_generation_match=info.generation)
    body = get(dashboard(ran)).body
    runs = body[body.index("<table class=runs>") :]
    assert runs[: runs.index("</table>")].count("<tr>") == 10


def test_an_unreadable_lock_never_shows_a_live_run_as_dead(
    ran: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = dashboard(ran)
    original = d.store.info

    def info(bucket: str, name: str) -> ObjectInfo | None:
        if bucket == "locks" and name == LOCK_OBJECT:
            raise StorageError("503")
        return original(bucket, name)

    monkeypatch.setattr(d.store, "info", info)
    assert d._lock_held() is True  # amber "in progress", not red "did not finish"


def test_no_ledger_is_an_error_page(tmp_path: Path) -> None:
    page = get(dashboard(tmp_path))
    assert page.status is HTTPStatus.SERVICE_UNAVAILABLE
    assert "can&#x27;t be read" in page.body


def test_a_restored_ledger_is_refused_like_the_bot_refuses_it(ran: Path) -> None:
    s = store(ran)
    mark = s.info("locks", MARK_OBJECT)
    assert mark is not None
    s.delete("locks", MARK_OBJECT, if_generation_match=mark.generation)
    page = get(dashboard(ran))
    assert page.status is HTTPStatus.SERVICE_UNAVAILABLE
    assert "restored" in page.body


@pytest.mark.parametrize("path", ["/pause", "/pause/confirm"])
def test_a_get_to_the_pause_routes_changes_nothing(ran: Path, path: str) -> None:
    page = get(dashboard(ran), path)
    assert (page.status, page.location) == (HTTPStatus.SEE_OTHER, "/")
    assert not pause_requests(ran)


@pytest.mark.parametrize("headers", [{}, {"sec-fetch-site": "cross-site"}, {"sec-fetch-site": "same-site"}])
def test_a_post_from_anywhere_but_the_page_is_refused(ran: Path, headers: dict[str, str]) -> None:
    page = post(dashboard(ran), "/pause", b"confirm=yes", headers)
    assert page.status is HTTPStatus.FORBIDDEN
    assert not pause_requests(ran)


def test_the_button_leads_to_a_confirm_page(ran: Path) -> None:
    page = post(dashboard(ran), "/pause/confirm")
    assert page.status is HTTPStatus.OK
    assert "<input type=hidden name=confirm value=yes>" in page.body
    assert not pause_requests(ran)


def test_confirming_stores_one_request_and_redirects(ran: Path) -> None:
    page = post(dashboard(ran), "/pause", b"confirm=yes")
    assert (page.status, page.location) == (HTTPStatus.SEE_OTHER, "/paused")
    assert pause_requests(ran)
    home = get(dashboard(ran))
    assert "New orders are paused" in home.body
    assert "action=" not in home.body  # no button, no resume


def test_a_pause_without_confirmation_changes_nothing(ran: Path) -> None:
    assert post(dashboard(ran), "/pause", b"").status is HTTPStatus.BAD_REQUEST
    assert not pause_requests(ran)


def test_a_failed_pause_says_not_paused(ran: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: object, **__: object) -> ObjectInfo:
        raise StorageError("403")

    d = dashboard(ran)
    monkeypatch.setattr(d.store, "write", refuse)
    page = post(d, "/pause", b"confirm=yes")
    assert page.status is HTTPStatus.SERVICE_UNAVAILABLE
    assert "NOT paused" in page.body


def test_other_methods_and_paths(ran: Path) -> None:
    d = dashboard(ran)
    assert route(d, "DELETE", "/pause", SAME, b"").status is HTTPStatus.METHOD_NOT_ALLOWED
    assert get(d, "/admin").status is HTTPStatus.NOT_FOUND
    assert post(d, "/other").status is HTTPStatus.NOT_FOUND


# --- over HTTP ---------------------------------------------------------------------------
@pytest.fixture
def served(ran: Path) -> Iterator[str]:
    server = make_server(dashboard(ran), 0, host="127.0.0.1")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_every_response_carries_the_security_headers(served: str) -> None:
    with urllib.request.urlopen(served + "/", timeout=5) as response:  # noqa: S310 - local server
        headers = dict(response.headers)
    for name, value in HEADERS.items():
        assert headers[name] == value
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]


def test_head_and_bad_lengths_get_the_security_headers(served: str) -> None:
    request = urllib.request.Request(served + "/", method="HEAD")  # noqa: S310 - local server
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request, timeout=5)  # noqa: S310 - local server
    assert error.value.code == HTTPStatus.METHOD_NOT_ALLOWED
    assert error.value.headers["X-Frame-Options"] == "DENY"
    bad = urllib.request.Request(  # noqa: S310 - local server
        served + "/pause", data=b"x", method="POST", headers={**SAME, "Content-Length": "-1"}
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(bad, timeout=5)  # noqa: S310 - local server
    assert error.value.code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE


def test_an_oversized_post_is_refused(served: str) -> None:
    request = urllib.request.Request(served + "/pause", data=b"x" * 5000, method="POST", headers=SAME)  # noqa: S310
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request, timeout=5)  # noqa: S310 - local server
    assert error.value.code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE


# --- slice 2: the weekly view ---------------------------------------------------------
def test_the_home_page_shows_the_weekly_view_from_the_runs_records(ran: Path) -> None:
    body = get(dashboard(ran)).body
    for heading in ("Value since setup", "Drawdown", "Funds", "Month end", "Paper trading"):
        assert f"<h2>{heading}</h2>" in body
    assert "<p class=hero>" in body
    assert "<b>VWRP</b>" in body and "<b>SGLN</b>" in body
    assert "Risk decisions at the last month end" in body  # the run was a month end
    assert "<b>Day 1</b> of 90" in body  # counted from the first run


def test_a_failing_weekly_view_still_shows_the_glance(
    ran: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(*_: object, **__: object) -> None:
        raise RuntimeError("weekly broke")

    monkeypatch.setattr("tradingbot.app.dashboard.weekly_input", boom)
    page = get(dashboard(ran))
    assert page.status is HTTPStatus.OK
    assert "<b>Run</b>: completed" in page.body
    assert "<h2>Funds</h2>" not in page.body
    assert "weekly view failed" in caplog.text


def test_a_weekly_view_that_cant_be_drawn_still_shows_the_glance_and_pause(
    ran: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(*_: object, **__: object) -> None:
        raise IndexError("drawing broke")

    monkeypatch.setattr("tradingbot.app.dashboard.weekly_html", boom)
    page = get(dashboard(ran))
    assert page.status is HTTPStatus.OK
    assert '<form method=post action="/pause/confirm">' in page.body
    assert "weekly view failed" in caplog.text


def test_a_ledger_from_before_slice_2_still_shows_the_page(ran: Path) -> None:
    assert job_at(ran, RUN_DAY + timedelta(days=1)).run() == 0  # two days: the chart draws
    s = store(ran)
    info = s.info("ledger", LEDGER_OBJECT)
    assert info is not None
    path = ran / "old.sqlite"
    path.write_bytes(s.read("ledger", LEDGER_OBJECT, info.generation))
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE levels")
    conn.execute("DELETE FROM previews")
    conn.commit()
    conn.close()
    s.write("ledger", LEDGER_OBJECT, path.read_bytes(), if_generation_match=info.generation)
    page = get(dashboard(ran))
    assert page.status is HTTPStatus.OK
    assert "<b>Run</b>: completed" in page.body
    assert '<form method=post action="/pause/confirm">' in page.body
    assert "aria-label='no preview'" in page.body
    assert 'class="line s-bot"' in page.body
    assert 'class="line s-ew"' not in page.body and 'class="line s-bm"' not in page.body
