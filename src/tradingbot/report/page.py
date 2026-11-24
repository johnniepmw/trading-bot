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
from tradingbot.report.weekly import STYLE as CHART_STYLE

_STATUS = {
    Level.OK: ("good", "✓", "OK"),
    Level.WARN: ("warning", "!", "Check"),
    Level.STOP: ("critical", "✕", "Stop"),
}
_STYLE = (
    "*{box-sizing:border-box}"
    "body{font:16px/1.45 system-ui,-apple-system,'Segoe UI',sans-serif;margin:0;padding:16px;"
    "color:var(--ink);background:var(--plane)}"
    "main{max-width:640px;margin:0 auto}"
    "header{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin:4px 2px 14px}"
    "h1{font-size:20px;margin:0;letter-spacing:-.01em}h2{font-size:15px;margin:0 0 10px;color:var(--ink2);"
    "font-weight:600;letter-spacing:.01em}"
    ".env{font-size:12px;font-weight:600;padding:2px 8px;border-radius:999px;background:var(--grid);"
    "color:var(--ink2);vertical-align:3px;margin-left:6px;text-transform:uppercase;letter-spacing:.05em}"
    "p{margin:0 0 8px}p.sub{margin:2px 0 0;color:var(--ink2);font-size:13px}"
    ".muted{color:var(--muted)}"
    ".card{background:var(--card);border:1px solid var(--ring);border-radius:16px;padding:16px;"
    "margin:0 0 12px;"
    "box-shadow:0 1px 2px var(--ring)}"
    ".pill{display:inline-flex;align-items:center;gap:6px;font-size:13px;font-weight:600;"
    "padding:4px 10px 4px 5px;"
    "border-radius:999px;background:var(--card);border:1px solid var(--ring);white-space:nowrap}"
    ".checks{list-style:none;padding:0;margin:0}"
    ".checks li{display:flex;gap:10px;align-items:flex-start;padding:9px 0;"
    "border-bottom:1px solid var(--grid);"
    "overflow-wrap:anywhere;font-variant-numeric:tabular-nums}.checks li:last-child{border:0;"
    "padding-bottom:0}"
    ".checks .status{flex:none;margin-top:3px}"
    ".status{display:inline-grid;place-items:center;width:18px;height:18px;border-radius:50%;font-size:11px;"
    "font-weight:700;color:#fff}.status.good{background:var(--good)}"
    ".status.warning{background:var(--warning);color:#0b0b0b}.status.critical{background:var(--critical)}"
    ".runs{width:100%;border-collapse:collapse;font-size:14px;font-variant-numeric:tabular-nums}"
    ".runs td{padding:6px 8px 6px 0;vertical-align:top;white-space:nowrap;"
    "border-bottom:1px solid var(--grid)}"
    ".runs tr:last-child td{border:0}.runs td.r{white-space:normal;overflow-wrap:anywhere;color:var(--ink2)}"
    "button{font:inherit;font-weight:600;padding:12px 16px;border-radius:12px;"
    "border:1.5px solid var(--critical);"
    "background:transparent;color:var(--ink);width:100%}"
    "button.go{background:var(--critical);color:#fff}form{margin:0}a{color:var(--s-bot)}"
    ".paused{display:flex;gap:10px;align-items:flex-start}"
    "code{font-size:14px}"
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
        f"<title>{escape(title)}</title><style>{CHART_STYLE}{_STYLE}</style></head>"
        f"<body><main>{body}</main></body></html>"
    )


def _status(level: Level) -> str:
    cls, icon, label = _STATUS[level]
    return f"<span class='status {cls}' role=img aria-label={label}>{icon}</span>"


def _overall(lines: list[Line]) -> str:
    """The page's one-word answer: the worst line's level, as icon and label."""
    worst = (
        Level.STOP
        if any(x.level is Level.STOP for x in lines)
        else (Level.WARN if any(x.level is Level.WARN for x in lines) else Level.OK)
    )
    words = {Level.OK: "All clear", Level.WARN: "Worth a look", Level.STOP: "Needs you"}
    return f"<span class=pill>{_status(worst)}{words[worst]}</span>"


def _labelled(text: str) -> str:
    """ "Drawdown: 5.3% ..." with its label in bold, so the eye can scan the labels."""
    label, sep, rest = text.partition(": ")
    return f"<b>{escape(label)}</b>{sep}{escape(rest)}" if sep else escape(text)


def _pause_button() -> str:
    return '<form method=post action="/pause/confirm"><button type=submit>Pause new orders…</button></form>'


def glance_page(  # noqa: PLR0913 - the page's parts
    lines: list[Line],
    *,
    as_of: datetime,
    environment: str,
    runs: list[RunRow],
    paused: bool | None,
    weekly: tuple[str, str] | None = None,
) -> str:
    """`paused` None: the pause location couldn't be read; the button stays (pressing is harmless).
    `weekly`: the hero and the weekly cards (`report.weekly.weekly_html`), when there are figures."""
    hero, cards = weekly or ("", "")
    checks = "".join(f"<li>{_status(x.level)}<span>{_labelled(x.text)}</span></li>" for x in lines)
    rows = "".join(
        f"<tr><td>{r.started_at:%d %b %H:%M}</td><td>{escape(r.outcome or 'running')}</td>"
        f"<td class=r>{escape(r.reason or '')}</td></tr>"
        for r in runs
    )
    if paused:
        state = (
            f"<div class=paused>{_status(Level.STOP)}<p><b>New orders are paused.</b> Only the owner's "
            "<code>ops clear-pause</code> resumes them.</p></div>"
        )
    elif paused is None:
        state = "<p><b>Pause state unknown</b> (couldn't be read just now).</p>" + _pause_button()
    else:
        state = (
            "<p class=muted>Stops new orders from the next order check; positions stay.</p>" + _pause_button()
        )
    body = (
        f"<header><div><h1>Trading bot<span class=env>{escape(environment)}</span></h1>"
        f"<p class=sub>As of {as_of:%a %d %b %Y %H:%M} UTC, from the bot's ledger</p></div>"
        f"{_overall(lines)}</header>"
        + (f"<section class=card>{hero}</section>" if hero else "")
        + f"<section class=card><h2>Today's checks</h2><ul class=checks>{checks}</ul></section>"
        + cards
        + f"<section class=card><h2>Recent runs</h2><table class=runs>{rows}</table></section>"
        + f"<section class=card><h2>Pause</h2>{state}</section>"
    )
    return _page(f"Trading bot ({environment})", body)


def confirm_page(environment: str) -> str:
    body = (
        f"<section class=card><h1>Pause new orders in {escape(environment)}?</h1>"
        "<p>The bot places no new orders from its next order check. Positions are left as they are. "
        "Only the owner's <code>ops clear-pause</code> workflow resumes trading.</p>"
        '<form method=post action="/pause"><input type=hidden name=confirm value=yes>'
        "<button class=go type=submit>Yes, pause new orders</button></form>"
        '<p><a href="/">Cancel</a></p></section>'
    )
    return _page("Confirm pause", body)


def paused_page(environment: str) -> str:
    body = (
        f"<section class=card><h1>Paused ({escape(environment)})</h1>"
        "<p>A pause request is stored. The bot places no new orders from its next order check, "
        "and the paused alert emails you. Resume with <code>ops clear-pause</code>.</p>"
        '<p><a href="/">Back</a></p></section>'
    )
    return _page("Paused", body)


def error_page(message: str) -> str:
    return _page("Unavailable", f"<section class=card><h1>Unavailable</h1><p>{escape(message)}</p></section>")
