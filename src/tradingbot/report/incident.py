"""The incident view (dashboard slice 3): what a stopped or died run means and what to do.

Pure. The guidance mirrors `docs/runbooks/cycle-alerts.md` ("Stop reasons"), and a test
keeps every key here present in that table, so the page and the runbook can't drift apart.
Links only: nothing is loaded from those sites (the CSP forbids it).
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

REPO = "https://github.com/johnniepmw/trading-bot"
RUNBOOK = f"{REPO}/blob/main/docs/runbooks/cycle-alerts.md#stop-reasons"
OPS = f"{REPO}/actions/workflows/ops.yml"


@dataclass(frozen=True, slots=True)
class Guidance:
    key: str  # the text the runbook's "Alert contains" column holds, found in the reason
    meaning: str
    action: str


GUIDANCE = (
    Guidance(
        "new orders off (kill switch or pause request)",
        "New orders are switched off: the kill switch, or a pause request.",
        "Nothing, unless you want trading back: ops → clear-pause (or set new-orders true).",
    ),
    Guidance(
        "not reconciled",
        "An earlier order's outcome is unknown or still working, or the broker has an order the bot "
        "doesn't know.",
        "Wait for it to finish; if it persists, tell the agent.",
    ),
    Guidance(
        "order ended short",
        "An accepted order was rejected or cancelled before filling. This month end isn't retried.",
        "Check the order in the Trading 212 app; tell the agent.",
    ),
    Guidance(
        "stale data",
        "The price data is missing for the signal day.",
        "Usually clears the next day; tell the agent if it doesn't.",
    ),
    Guidance(
        "too short",
        "The price history is too short.",
        "Usually clears the next day; tell the agent if it doesn't.",
    ),
    Guidance(
        "implausible jump",
        "A price jumped implausibly: bad data.",
        "Usually clears the next day; tell the agent if it doesn't.",
    ),
    Guidance(
        "price cross-check failed",
        "The data's latest close is more than 2% from the broker's live price.",
        "Clears on its own if it was a real move; if it persists, the data is bad: tell the agent.",
    ),
    Guidance(
        "month end skipped",
        "The month-end prices couldn't be verified, so this month's rebalance is skipped.",
        "Nothing to do; the next month end trades as normal. Tell the agent if it repeats.",
    ),
    Guidance(
        "equity check failed",
        "The broker's equity doesn't add up from cash and holdings.",
        "Tell the agent.",
    ),
    Guidance(
        "implausible new peak",
        "Equity jumped more than 10%: usually a deposit.",
        "After a deposit, raise the mark (ledger-setup runbook). Otherwise tell the agent.",
    ),
    Guidance(
        "no high-water mark",
        "The ledger isn't set up.",
        "See the ledger-setup runbook; never re-seed after a loss.",
    ),
    Guidance(
        "ledger file missing",
        "The ledger is missing.",
        "See the ledger-setup and ledger-restore runbooks; never re-seed after a loss.",
    ),
)
DIED = Guidance(
    "did not finish",
    "The run never finished: usually a timeout or a crash.",
    "Tell the agent. If runs keep stopping on the lock, see the stale-lock runbook.",
)
UNKNOWN = Guidance("", "A stop the runbook doesn't list.", "Tell the agent, with the time of the run.")


def guidance(reason: str | None, *, died: bool = False) -> Guidance:
    if died:
        return DIED
    text = (reason or "").lower()
    return next((g for g in GUIDANCE if g.key in text), UNKNOWN)


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
        f"<p><b>What it means.</b> {escape(g.meaning)}</p>"
        f"<p><b>What to do.</b> {escape(g.action)}</p>"
        f"<p class=small>{links}</p></section>"
    )
