"""The incident view (dashboard slice 3): what a stopped or died run means and what to do.

Pure. The guidance is `docs/runbooks/cycle-alerts.md`'s "Stop reasons" table, word for word, and
a test compares the two both ways, so the page and the runbook can't drift apart.
Links only: nothing is loaded from those sites (the CSP forbids it).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape

REPO = "https://github.com/johnniepmw/trading-bot"
RUNBOOK = f"{REPO}/blob/main/docs/runbooks/cycle-alerts.md#stop-reasons"
OPS = f"{REPO}/actions/workflows/ops.yml"


@dataclass(frozen=True, slots=True)
class Guidance:
    keys: tuple[str, ...]  # the runbook's "Alert contains" phrases; any of them in the reason matches
    meaning: str
    action: str


# The runbook's "Stop reasons" rows, word for word (markdown removed); a test compares the two
# both ways, so a change to either fails until the other matches.
GUIDANCE = (
    Guidance(
        ("new orders off (kill switch or pause request)",),
        "new-orders is off, a pause request exists, or the pause location can't be read",
        "Nothing, unless you want trading back: ops → clear-pause after a pause, ops → restore new-orders after the kill switch (kill-switch.md). If neither applies, tell the agent",  # noqa: E501 - the runbook's words
    ),
    Guidance(
        ("not reconciled",),
        "An earlier order's outcome is unknown or still working, or the broker has a working order the bot doesn't know (including one you placed by hand)",  # noqa: E501 - the runbook's words
        "Wait for it to finish; if it persists, check the order in the Trading 212 app and tell the agent",
    ),
    Guidance(
        ("order ended short",),
        "An order the broker accepted was rejected or cancelled before filling. That month end's decision is not retried until the next one",  # noqa: E501 - the runbook's words
        "Check the order in the app; tell the agent if it repeats",
    ),
    Guidance(
        ("stale data", "too short", "implausible jump"),
        "The price data is missing, short or wrong",
        "Usually clears the next day; tell the agent if it doesn't",
    ),
    Guidance(
        ("price cross-check failed",),
        "The data source's latest close is more than 2% from the broker's live price",
        "Clears on its own if it was a real move; persistent means bad data",
    ),
    Guidance(
        ("month end skipped",),
        "The first run after the month end couldn't check the month-end prices (blocked, or prices moved more than 2%), so this month's rebalance is skipped, exits included, until the next month end",  # noqa: E501 - the runbook's words
        "Decide whether to act by hand (closing positions is yours) or wait. One alert per month end",
    ),
    Guidance(
        ("equity check failed", "implausible new peak"),
        "The broker's equity doesn't add up, or jumped more than 10%",
        "A deposit of more than about 10%: raise the mark (see ledger-setup.md). Otherwise tell the agent",
    ),
    Guidance(
        ("no high-water mark", "lower than an earlier", "ledger file missing"),
        "The ledger isn't set up, or was reset or damaged",
        "See ledger-setup.md; never re-seed after a loss",
    ),
    Guidance(
        ("rejected: daily loss limit", "drawdown stop", "no day-start equity"),
        "Risk vetoed buys for the day (sells still go through)",
        "Expected after a bad day or a gap of more than 5 days; the drawdown stop needs you to decide",
    ),
)
DIED = Guidance(
    ("did not finish",),
    "The run never finished: usually a timeout or a crash. An order it was sending may have reached "
    "the broker; the next run checks before anything else",
    "Don't place orders by hand. Tell the agent; if runs keep stopping on the lock, see stale-lock.md",
)
UNKNOWN = Guidance((), "A stop the runbook's table doesn't list", "Tell the agent, with the time of the run")


def guidance(reason: str | None, *, died: bool = False) -> Guidance:
    if died:
        return DIED
    text = (reason or "").lower()
    return next((g for g in GUIDANCE if any(k.lower() in text for k in g.keys)), UNKNOWN)


def _link_runbooks(text: str) -> str:
    """Runbook names in the guidance ("kill-switch.md") become links to them on GitHub."""
    return re.sub(
        r"\b([a-z0-9-]+\.md)\b", lambda m: f'<a href="{REPO}/blob/main/docs/runbooks/{m[1]}">{m[1]}</a>', text
    )


def logs_url(environment: str) -> str:
    query = f'resource.type="cloud_run_job"\nresource.labels.job_name="trading-bot-{environment}"'
    return "https://console.cloud.google.com/logs/query;query=" + _percent(query)


def _percent(text: str) -> str:
    """URL-encode everything but unreserved characters (report has no urllib: contract 3a)."""
    return "".join(c if c.isascii() and (c.isalnum() or c in "-_.~") else f"%{ord(c):02X}" for c in text)


def incident_card(environment: str, outcome: str, reason: str | None) -> str:
    """The card shown first when the latest run stopped or died."""
    g = guidance(reason, died=outcome == "died")
    links = (
        f'<a href="{escape(RUNBOOK)}">Runbook</a> · <a href="{escape(OPS)}">ops workflow</a> · '
        f'<a href="{escape(logs_url(environment))}">Logs</a>'
    )
    return (
        "<section class='card incident'><h2>The last run "
        f"{'did not finish' if outcome == 'died' else 'stopped'}</h2>"
        f"<p class=reason>{escape(reason or 'no reason recorded')}</p>"
        f"<p><b>What it means.</b> {escape(g.meaning)}.</p>"
        f"<p><b>What to do.</b> {_link_runbooks(escape(g.action))}.</p>"
        f"<p class=small>{links}</p></section>"
    )
