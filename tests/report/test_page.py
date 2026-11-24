"""The dashboard's pages from made-up figures (ADR-0014)."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from tradingbot.report.glance import Level, Line
from tradingbot.report.page import RunRow, confirm_page, error_page, glance_page, paused_page

AS_OF = datetime(2026, 11, 24, 7, 17, tzinfo=UTC)
LINES = [
    Line(Level.OK, "Run: completed"),
    Line(Level.WARN, "Drawdown: 6.0%"),
    Line(Level.STOP, "Orders: 1 open"),
]
RUNS = [
    RunRow(datetime(2026, 11, 24, 7, 15, tzinfo=UTC), "completed", None),
    RunRow(datetime(2026, 11, 23, 7, 15, tzinfo=UTC), "stopped", "<script>alert(1)</script> broker said no"),
    RunRow(datetime(2026, 11, 22, 7, 15, tzinfo=UTC), None, None),
]


def page(paused: bool = False) -> str:
    return glance_page(LINES, as_of=AS_OF, environment="prod", runs=RUNS, paused=paused)


def test_the_glance_shows_each_line_with_its_marker_and_colour() -> None:
    html = page()
    assert '<b class=m style="color:#1b7f3b">[OK]</b> Run: completed' in html
    assert '<b class=m style="color:#a66300">[!]</b> Drawdown: 6.0%' in html
    assert '<b class=m style="color:#b3261e">[STOP]</b> Orders: 1 open' in html
    assert "As of Tue 24 Nov 2026 07:17 UTC" in html


def test_run_reasons_are_escaped() -> None:
    html = page()
    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt; broker said no" in html


def test_an_unfinished_run_shows_as_running() -> None:
    assert "<td>22 Nov 07:15</td><td>running</td>" in page()


def test_the_pause_button_posts_to_the_confirm_page() -> None:
    assert '<form method=post action="/pause/confirm">' in page()


def test_when_paused_there_is_no_button_and_no_resume() -> None:
    html = page(paused=True)
    assert "action=" not in html
    assert "New orders are paused" in html


def test_the_confirm_page_posts_the_pause_and_offers_a_way_back() -> None:
    html = confirm_page("prod")
    assert '<form method=post action="/pause"><input type=hidden name=confirm value=yes>' in html
    assert '<a href="/">Cancel</a>' in html


def test_no_page_has_scripts_or_external_resources() -> None:
    for html in (page(), page(paused=True), confirm_page("prod"), paused_page("prod"), error_page("x")):
        assert "<script" not in html
        assert not re.search(r"(src|href)=\"?https?:", html)


def test_pages_fit_a_phone() -> None:
    assert '<meta name=viewport content="width=device-width,initial-scale=1">' in page()


def test_the_error_page_escapes_its_message() -> None:
    assert "&lt;b&gt;" in error_page("<b>ledger unreadable</b>")
