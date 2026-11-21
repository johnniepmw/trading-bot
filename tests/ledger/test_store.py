from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from tradingbot.domain.market import Instrument, Order, Side
from tradingbot.domain.money import Currency, Money
from tradingbot.ledger.store import IntentState, Ledger, LedgerError

VWRP = Instrument("VWRP", Currency.GBP)
SGLN = Instrument("SGLN", Currency.GBX)
NOW = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
BUY = Order(VWRP, Side.BUY, Decimal("1.5"))


def gbp(x: str) -> Money:
    return Money(Decimal(x), Currency.GBP)


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[Ledger]:
    opened = Ledger.open(tmp_path / "ledger.sqlite")
    yield opened
    opened.close()


def test_an_intent_is_recorded_before_anything_is_sent(ledger: Ledger) -> None:
    intent = ledger.record_intent("run-1", BUY, NOW)
    [row] = ledger.open_intents()
    assert (row.intent_id, row.order, row.state, row.broker_order_id) == (
        intent,
        BUY,
        IntentState.INTENDED,
        None,
    )


def test_intents_move_through_sent_and_settled_states(ledger: Ledger) -> None:
    intent = ledger.record_intent("run-1", BUY, NOW)
    ledger.mark_sent(intent, "b-1")
    assert ledger.open_intents()[0].state is IntentState.SENT
    ledger.settle(intent, IntentState.FILLED)
    assert ledger.open_intents() == []


def test_an_unknown_outcome_stays_open(ledger: Ledger) -> None:
    intent = ledger.record_intent("run-1", BUY, NOW)
    ledger.mark_unknown(intent)
    assert [i.state for i in ledger.open_intents()] == [IntentState.UNKNOWN]


def test_settled_intents_cannot_be_reopened(ledger: Ledger) -> None:
    intent = ledger.record_intent("run-1", BUY, NOW)
    ledger.settle(intent, IntentState.NOT_SENT)
    with pytest.raises(LedgerError, match="settled"):
        ledger.mark_sent(intent, "b-1")


def test_the_ledger_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite"
    first = Ledger.open(path)
    intent = first.record_intent("run-1", Order(SGLN, Side.SELL, Decimal(2)), NOW)
    first.close()
    reopened = Ledger.open(path)
    [row] = reopened.open_intents()
    reopened.close()
    assert (row.intent_id, row.order) == (intent, Order(SGLN, Side.SELL, Decimal(2)))


def test_the_high_water_mark_only_rises(ledger: Ledger) -> None:
    assert ledger.high_water_mark() is None  # unknown until first recorded
    ledger.update_high_water_mark(gbp("1000"), NOW)
    ledger.update_high_water_mark(gbp("900"), NOW)
    assert ledger.high_water_mark() == gbp("1000")
    ledger.update_high_water_mark(gbp("1100.50"), NOW)
    assert ledger.high_water_mark() == gbp("1100.50")


def test_a_high_water_mark_must_be_in_pounds(ledger: Ledger) -> None:
    with pytest.raises(LedgerError, match="GBP"):
        ledger.update_high_water_mark(Money(Decimal(100000), Currency.GBX), NOW)


def test_a_mark_lower_than_ever_recorded_is_detected(tmp_path: Path) -> None:
    # a reset or tampered mark must not lift the drawdown stop (AC9 drill)
    path = tmp_path / "ledger.sqlite"
    ledger = Ledger.open(path)
    ledger.update_high_water_mark(gbp("1000"), NOW)
    # a re-seed from current equity after losing state appends a lower mark
    ledger._conn.execute("INSERT INTO marks (amount, at) VALUES ('700', '2026-11-24T09:00:00+00:00')")
    with pytest.raises(LedgerError, match="lower than"):
        ledger.high_water_mark()
    ledger.close()


def test_times_must_be_utc(ledger: Ledger) -> None:
    with pytest.raises(LedgerError, match="UTC"):
        ledger.record_intent("run-1", BUY, datetime(2026, 11, 23, 9, 0))
