"""The incident view from made-up reasons (ADR-0014)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tradingbot.report.incident import DIED, GUIDANCE, UNKNOWN, guidance, incident_card, logs_url

RUNBOOK = Path(__file__).resolve().parents[2] / "docs" / "runbooks" / "cycle-alerts.md"


def stop_reasons_table() -> str:
    text = RUNBOOK.read_text(encoding="utf-8")
    return text[text.index("## Stop reasons") :]


@pytest.mark.parametrize("g", GUIDANCE, ids=lambda g: g.key)
def test_every_stop_reason_the_page_knows_is_in_the_runbook(g) -> None:  # type: ignore[no-untyped-def]
    assert g.key in stop_reasons_table()


@pytest.mark.parametrize("g", GUIDANCE, ids=lambda g: g.key)
def test_each_stop_reason_finds_its_guidance(g) -> None:  # type: ignore[no-untyped-def]
    assert guidance(f"cycle stopped: {g.key.upper()}: details") is g


def test_a_run_that_died_gets_the_died_guidance() -> None:
    assert guidance("anything", died=True) is DIED


def test_an_unknown_reason_says_tell_the_agent() -> None:
    assert guidance("a brand new failure") is UNKNOWN
    assert guidance(None) is UNKNOWN
    assert "Tell the agent" in UNKNOWN.action


def test_the_card_escapes_the_reason_and_links_only_to_github_and_cloud_console() -> None:
    html = incident_card("prod", "stopped", "stale data: <b>VWRP</b>")
    assert "&lt;b&gt;VWRP&lt;/b&gt;" in html and "<b>VWRP</b>" not in html
    hosts = set(re.findall(r'href="https://([^/"]+)/', html))
    assert hosts == {"github.com", "console.cloud.google.com"}
    assert "The last run stopped" in html
    assert "Usually clears the next day" in html


def test_a_died_run_says_so() -> None:
    assert "The last run did not finish" in incident_card("prod", "died", None)


def test_the_logs_link_names_the_environments_job() -> None:
    assert "trading-bot-staging" in logs_url("staging").replace("%2D", "-")


def test_the_logs_query_is_url_encoded() -> None:
    assert logs_url("prod").endswith(
        "query=resource.type%3D%22cloud_run_job%22%0Aresource.labels.job_name%3D%22trading-bot-prod%22"
    )
