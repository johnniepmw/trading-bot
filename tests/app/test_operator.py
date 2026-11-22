"""The owner's `operator` secret: missing or invalid means paper (ADR-0009; Phase 2 AC8)."""

from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path

import pytest

from tradingbot.app.operator import Operator, read_operator


def secret(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "value"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_missing_secret_means_paper(tmp_path: Path) -> None:
    assert read_operator(tmp_path / "absent") == Operator(live=False, capital_gbp=None)


def test_the_placeholder_means_paper_without_an_alert(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR):
        assert read_operator(secret(tmp_path, "__unset__\n")) == Operator(live=False, capital_gbp=None)
    assert caplog.text == ""  # the expected state until the owner goes live


def test_an_unreadable_secret_means_paper(tmp_path: Path) -> None:
    assert read_operator(tmp_path).live is False  # a directory: reading it fails


@pytest.mark.parametrize(
    "text",
    [
        "live",
        '{"mode": "LIVE", "capital_gbp": "500"}',
        '{"mode": "live"}',
        '{"mode": "live", "capital_gbp": 500}',  # a number, not a decimal string
        '{"mode": "live", "capital_gbp": "-1"}',
        '{"mode": "live", "capital_gbp": "1000.01"}',  # above the £1,000 ceiling
        '{"mode": "live", "capital_gbp": "NaN"}',
        '{"mode": "live", "capital_gbp": "500", "extra": 1}',
        "[]",
    ],
)
def test_an_invalid_secret_means_paper_with_an_alert(
    tmp_path: Path, text: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR):
        assert read_operator(secret(tmp_path, text)).live is False
    assert "operator secret invalid" in caplog.text
    assert "500" not in caplog.text  # the value is never logged


def test_a_valid_live_setting_is_parsed(tmp_path: Path) -> None:
    assert read_operator(secret(tmp_path, '{"mode": "live", "capital_gbp": "500"}')) == Operator(
        live=True, capital_gbp=Decimal("500")
    )


def test_paper_can_be_written_explicitly(tmp_path: Path) -> None:
    assert read_operator(secret(tmp_path, '{"mode": "paper"}')) == Operator(live=False, capital_gbp=None)
