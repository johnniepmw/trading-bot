"""Alert kinds on the bot's log lines (Phase 2 step 7a; monitoring AC5).

Every ERROR line from the bot carries an `alert` field naming its kind; a WARNING carries
one only when the code asks for it (a drawdown crossing). Cloud Monitoring alerts on the
field, never on Cloud Run's own entries, so a dry run that stops correctly pages nobody.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal

import pytest

from tradingbot.app.__main__ import JsonFormatter
from tradingbot.app.alerts import alert_kind, drawdown_crossings


def record(message: str, level: int = logging.ERROR, **extra: object) -> logging.LogRecord:
    rec = logging.LogRecord("tradingbot.app.cycle", level, __file__, 1, message, None, None)
    for key, value in extra.items():
        setattr(rec, key, value)
    return rec


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        ("cycle stopped: not reconciled: intent 3", "cycle_stopped"),
        ("stale lock: held by x (run y) since t", "stale_lock"),
        ("run abc started at t never finished", "run_died"),
        ("order ended short: intent 4", "order_short"),
        ("setup refused: a ledger existed before", "setup_refused"),
        ("refusing to start: APP_ENV must be one of ...", "refused_to_start"),
        ("lock release failed: 503", "lock_release"),
        ("operator secret invalid: paper trading", "error"),
    ],
)
def test_every_error_line_names_its_kind(message: str, kind: str) -> None:
    assert alert_kind(record(message)) == kind


def test_an_explicit_kind_wins() -> None:
    assert alert_kind(record("BUY VWRP: rejected: drawdown stop", alert="account_veto")) == "account_veto"


def test_a_warning_alerts_only_when_asked() -> None:
    assert alert_kind(record("dry run stopped: stale data", logging.WARNING)) is None
    assert alert_kind(record("drawdown crossed 10%", logging.WARNING, alert="drawdown")) == "drawdown"


def test_the_json_line_carries_the_kind() -> None:
    line = json.loads(JsonFormatter().format(record("cycle stopped: kill switch off")))
    assert line["alert"] == "cycle_stopped"
    assert line["severity"] == "ERROR"


def test_an_info_line_has_no_alert_field() -> None:
    assert "alert" not in json.loads(JsonFormatter().format(record("run completed", logging.INFO)))


@pytest.mark.parametrize(
    ("previous", "now", "crossed"),
    [
        ("960", "940", [5]),  # 4% -> 6% below the mark
        ("960", "790", [5, 10, 15, 20]),  # a crash through every level at once
        ("940", "930", []),  # already past 5%: once per crossing
        ("890", "960", []),  # recovering crosses nothing
        ("1000", "950", [5]),  # exactly 5% counts
    ],
)
def test_drawdown_crossings_are_reported_once(previous: str, now: str, crossed: list[int]) -> None:
    assert drawdown_crossings(Decimal(previous), Decimal(now), mark=Decimal(1000)) == crossed


def test_no_previous_day_means_no_crossing() -> None:
    assert drawdown_crossings(None, Decimal(700), mark=Decimal(1000)) == []


def test_a_dry_runs_account_veto_carries_no_alert(tmp_path: object, caplog: pytest.LogCaptureFixture) -> None:
    from pathlib import Path  # noqa: PLC0415

    from tests.app.test_job import RUN_DAY, job_at  # noqa: PLC0415

    assert isinstance(tmp_path, Path)
    job_at(tmp_path, RUN_DAY).setup("1000")  # seeded today: buys are vetoed for want of day-start equity
    with caplog.at_level(logging.WARNING):
        job_at(tmp_path, RUN_DAY).dry_run()
    assert {alert_kind(r) for r in caplog.records if "no day-start equity" in r.getMessage()} == {None}


def test_a_runs_account_veto_is_an_account_veto_alert(
    tmp_path: object, caplog: pytest.LogCaptureFixture
) -> None:
    from pathlib import Path  # noqa: PLC0415

    from tests.app.test_job import RUN_DAY, job_at  # noqa: PLC0415

    assert isinstance(tmp_path, Path)
    job_at(tmp_path, RUN_DAY).setup("1000")
    with caplog.at_level(logging.WARNING):
        job_at(tmp_path, RUN_DAY).run()
    kinds = {alert_kind(r) for r in caplog.records if "no day-start equity" in r.getMessage()}
    assert kinds == {"account_veto"}
