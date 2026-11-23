"""The dashboard's pages (dashboard slice 1; docs/exec-plans/active/2026-11-23-dashboard.md).

Server-rendered HTML with inline CSS and no scripts, readable on a 390 px phone. Pure:
built from the glance lines and run rows the app reads from the ledger. Everything that
came from the ledger is escaped (run reasons can carry broker error text). The only control
is the one-way pause, a POST behind a confirm page; there is no resume anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape

from tradingbot.report.glance import Level, Line

_COLOUR = {Level.OK: "#1b7f3b", Level.WARN: "#a66300", Level.STOP: "#b3261e"}
_STYLE = (
    "body{font:16px/1.4 system-ui,sans-serif;margin:0;padding:12px;max-width:560px;"
    "color:#1d1d1f;background:#fff}"
    "h1{font-size:18px;margin:0 0 4px}p.sub{margin:0 0 12px;color:#555;font-size:14px}"
    "ul{list-style:none;padding:0;margin:0 0 16px}"
    "li{padding:8px 0;border-bottom:1px solid #eee;overflow-wrap:anywhere}"
    "b.m{display:inline-block;min-width:3.2em;font-size:13px}"
    "table{width:100%;border-collapse:collapse;font-size:14px}"
    "td{padding:4px 6px 4px 0;vertical-align:top;white-space:nowrap}"
    "td.r{white-space:normal;overflow-wrap:anywhere}"
    "button{font:inherit;padding:10px 14px;border-radius:8px;border:1px solid #b3261e;"
    "background:#fff;color:#b3261e}"
    "button.go{background:#b3261e;color:#fff}form{margin:16px 0}a{color:#0b57d0}"
)


@dataclass(frozen=True, slots=True)
class RunRow:
    started_at: datetime
    outcome: str | None  # completed, stopped, died; None: still running
    reason: str | None


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title><style>{_STYLE}</style></head><body>{body}</body></html>"
    )


def _pause_button() -> str:
    return '<form method=post action="/pause/confirm"><button type=submit>Pause new orders…</button></form>'


def glance_page(
    lines: list[Line], *, as_of: datetime, environment: str, runs: list[RunRow], paused: bool
) -> str:
    items = "".join(
        f'<li><b class=m style="color:{_COLOUR[x.level]}">[{escape(x.level.value)}]</b> {escape(x.text)}</li>'
        for x in lines
    )
    rows = "".join(
        f"<tr><td>{r.started_at:%d %b %H:%M}</td><td>{escape(r.outcome or 'running')}</td>"
        f"<td class=r>{escape(r.reason or '')}</td></tr>"
        for r in runs
    )
    state = (
        "<p><b>New orders are paused.</b> Only the owner's <code>ops clear-pause</code> resumes them.</p>"
        if paused
        else _pause_button()
    )
    body = (
        f"<h1>Trading bot ({escape(environment)})</h1>"
        f"<p class=sub>As of {as_of:%a %d %b %Y %H:%M} UTC, from the bot's ledger</p>"
        f"<ul>{items}</ul>{state}<h1>Recent runs</h1><table>{rows}</table>"
    )
    return _page(f"Trading bot ({environment})", body)


def confirm_page(environment: str) -> str:
    body = (
        f"<h1>Pause new orders in {escape(environment)}?</h1>"
        "<p>The bot places no new orders from its next order check. Positions are left as they are. "
        "Only the owner's <code>ops clear-pause</code> workflow resumes trading.</p>"
        '<form method=post action="/pause"><input type=hidden name=confirm value=yes>'
        "<button class=go type=submit>Yes, pause new orders</button></form>"
        '<p><a href="/">Cancel</a></p>'
    )
    return _page("Confirm pause", body)


def paused_page(environment: str) -> str:
    body = (
        f"<h1>Paused ({escape(environment)})</h1>"
        "<p>A pause request is stored. The bot places no new orders from its next order check, "
        "and the paused alert emails you. Resume with <code>ops clear-pause</code>.</p>"
        '<p><a href="/">Back</a></p>'
    )
    return _page("Paused", body)


def error_page(message: str) -> str:
    return _page("Unavailable", f"<h1>Unavailable</h1><p>{escape(message)}</p>")
