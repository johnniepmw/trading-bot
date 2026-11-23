from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tradingbot.domain.market import Instrument, Order, Side
from tradingbot.domain.money import Currency, Money
from tradingbot.ledger.store import DecisionRecord, IntentState, Ledger, LedgerError

VWRP = Instrument("VWRP", Currency.GBP)
SGLN = Instrument("SGLN", Currency.GBX)
NOW = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
BUY = Order(VWRP, Side.BUY, Decimal("1.5"))


def gbp(x: str) -> Money:
    return Money(Decimal(x), Currency.GBP)


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[Ledger]:
    opened = Ledger.create(tmp_path / "ledger.sqlite")
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
    ledger.settle(intent, IntentState.FILLED, filled_quantity=Decimal("1.5"))
    assert ledger.open_intents() == []
    assert ledger.settled()[0].filled_quantity == Decimal("1.5")


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
    first = Ledger.create(path)
    intent = first.record_intent("run-1", Order(SGLN, Side.SELL, Decimal(2)), NOW)
    first.close()
    reopened = Ledger.open(path)
    [row] = reopened.open_intents()
    reopened.close()
    assert (row.intent_id, row.order) == (intent, Order(SGLN, Side.SELL, Decimal(2)))


def test_the_high_water_mark_only_rises(ledger: Ledger) -> None:
    assert ledger.high_water_mark() is None  # unknown until seeded
    ledger.seed_high_water_mark(gbp("1000"), NOW)
    ledger.update_high_water_mark(gbp("900"), NOW)
    assert ledger.high_water_mark() == gbp("1000")
    ledger.update_high_water_mark(gbp("1100.50"), NOW)
    assert ledger.high_water_mark() == gbp("1100.50")


def test_a_high_water_mark_must_be_in_pounds(ledger: Ledger) -> None:
    with pytest.raises(LedgerError, match="GBP"):
        ledger.seed_high_water_mark(Money(Decimal(100000), Currency.GBX), NOW)


def test_a_mark_lower_than_ever_recorded_is_detected(tmp_path: Path) -> None:
    # a reset or tampered mark must not lift the drawdown stop (AC9 drill)
    path = tmp_path / "ledger.sqlite"
    ledger = Ledger.create(path)
    ledger.seed_high_water_mark(gbp("1000"), NOW)
    # a re-seed from current equity after losing state appends a lower mark
    ledger._conn.execute("INSERT INTO marks (amount, at) VALUES ('700', '2026-11-24T09:00:00+00:00')")
    with pytest.raises(LedgerError, match="lower than"):
        ledger.high_water_mark()
    ledger.close()


def test_times_must_be_utc(ledger: Ledger) -> None:
    with pytest.raises(LedgerError, match="UTC"):
        ledger.record_intent("run-1", BUY, datetime(2026, 11, 23, 9, 0))


# --- security review round 1 -------------------------------------------------------
def test_opening_a_missing_ledger_is_an_error_not_a_fresh_start(tmp_path: Path) -> None:
    # a lost file must never become an empty ledger with no mark history
    with pytest.raises(LedgerError, match="missing"):
        Ledger.open(tmp_path / "gone.sqlite")
    assert not (tmp_path / "gone.sqlite").exists()


def test_creating_a_ledger_is_explicit_and_refuses_to_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "new.sqlite"
    Ledger.create(path).close()
    with pytest.raises(LedgerError, match="exists"):
        Ledger.create(path)


def test_an_update_never_creates_the_first_mark(ledger: Ledger) -> None:
    # only an explicit seed (an owner step) sets the first mark; otherwise a lost history
    # would be re-seeded from today's lower equity and lift the drawdown stop
    with pytest.raises(LedgerError, match="seed"):
        ledger.update_high_water_mark(gbp("750"), NOW)
    ledger.seed_high_water_mark(gbp("1000"), NOW)
    with pytest.raises(LedgerError, match="already"):
        ledger.seed_high_water_mark(gbp("750"), NOW)
    assert ledger.high_water_mark() == gbp("1000")


def test_london_time_is_refused_even_in_winter(ledger: Ledger) -> None:
    london = datetime(2026, 12, 1, 9, 0, tzinfo=ZoneInfo("Europe/London"))  # offset 0 in winter
    with pytest.raises(LedgerError, match="UTC"):
        ledger.record_intent("run-1", BUY, london)


def test_an_empty_or_truncated_file_is_not_a_ledger(tmp_path: Path) -> None:
    empty = tmp_path / "empty.sqlite"
    empty.write_bytes(b"")
    with pytest.raises(LedgerError, match="not a ledger"):
        Ledger.open(empty)


def test_a_mark_in_pence_is_refused_even_when_lower(ledger: Ledger) -> None:
    ledger.seed_high_water_mark(gbp("1000"), NOW)
    with pytest.raises(LedgerError, match="GBP"):
        ledger.update_high_water_mark(Money(Decimal(500), Currency.GBX), NOW)


def test_day_start_equity_is_the_last_equity_recorded_on_an_earlier_day(ledger: Ledger) -> None:
    monday, tuesday = NOW.date(), NOW.date().replace(day=24)
    assert ledger.previous_day_equity(monday) is None  # nothing earlier: unknown
    ledger.record_equity(monday, gbp("1000"))
    ledger.record_equity(monday, gbp("990"))  # a later run the same day replaces it
    assert ledger.previous_day_equity(monday) is None  # today never counts
    assert ledger.previous_day_equity(tuesday) == gbp("990")


def test_seeding_the_mark_also_records_that_days_equity(ledger: Ledger) -> None:
    ledger.seed_high_water_mark(gbp("1000"), NOW)
    assert ledger.previous_day_equity(NOW.date().replace(day=24)) == gbp("1000")


def test_verified_closes_and_acted_signals_are_kept(ledger: Ledger) -> None:
    day = NOW.date()
    closes = {VWRP: gbp("101.5"), SGLN: Money(Decimal(4000), Currency.GBX)}
    assert ledger.verified_closes(day) == {}
    ledger.store_verified_closes(day, closes)
    assert ledger.verified_closes(day) == closes
    assert not ledger.signal_acted(day)
    ledger.mark_signal_acted(day)
    assert ledger.signal_acted(day)


def test_a_day_start_older_than_the_limit_is_unknown(ledger: Ledger) -> None:
    ledger.record_equity(NOW.date().replace(day=10), gbp("1000"))
    assert ledger.previous_day_equity(NOW.date(), max_age_days=5) is None
    assert ledger.previous_day_equity(NOW.date(), max_age_days=20) == gbp("1000")


def test_a_missing_snapshot_is_an_error(ledger: Ledger) -> None:
    with pytest.raises(LedgerError, match="no snapshot"):
        ledger.snapshot("nope")


def test_a_rejected_decision_and_a_pence_quote_round_trip(ledger: Ledger) -> None:
    rows = [
        DecisionRecord(
            SGLN, Side.BUY, Decimal(2), None, "rejected: no cash", Money(Decimal(4000), Currency.GBX)
        ),
        DecisionRecord(VWRP, Side.SELL, Decimal(1), Decimal(1), "passed", None),
    ]
    ledger.record_decisions("run-1", rows)
    assert ledger.decisions("run-1") == rows


# --- monitoring AC4: fill price and fees ---------------------------------------------
def test_a_settled_order_keeps_its_fill_price_and_fees(ledger: Ledger) -> None:
    intent = ledger.record_intent("run-1", Order(SGLN, Side.BUY, Decimal(2)), NOW)
    ledger.settle(
        intent,
        IntentState.FILLED,
        "o-1",
        Decimal(2),
        average_price=Money(Decimal("4012.5"), Currency.GBX),
        fees=gbp("0.04"),
    )
    fill = ledger.fill(intent)
    assert fill.average_price == Money(Decimal("4012.5"), Currency.GBX)
    assert fill.fees == gbp("0.04")


def test_a_fill_the_broker_did_not_price_stays_empty(ledger: Ledger) -> None:
    intent = ledger.record_intent("run-1", BUY, NOW)
    ledger.settle(intent, IntentState.REJECTED, "o-1", Decimal(0))
    fill = ledger.fill(intent)
    assert (fill.average_price, fill.fees) == (None, None)


def test_fees_must_be_in_pounds(ledger: Ledger) -> None:
    intent = ledger.record_intent("run-1", BUY, NOW)
    with pytest.raises(LedgerError, match="GBP"):
        ledger.settle(intent, IntentState.FILLED, "o-1", Decimal("1.5"), fees=Money(Decimal(4), Currency.GBX))
    assert ledger.open_intents()[0].intent_id == intent  # nothing settled


def test_a_ledger_from_before_fills_were_recorded_gains_the_table(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite"
    Ledger.create(path).close()
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE fills")
    conn.commit()
    conn.close()
    reopened = Ledger.open(path)
    intent = reopened.record_intent("run-1", BUY, NOW)
    reopened.settle(intent, IntentState.FILLED, "o-1", Decimal("1.5"), average_price=gbp("100"))
    assert reopened.fill(intent).average_price == gbp("100")
    reopened.close()


def test_a_failed_fill_write_leaves_the_intent_open(ledger: Ledger) -> None:
    intent = ledger.record_intent("run-1", BUY, NOW)
    ledger._conn.execute("INSERT INTO fills (intent_id) VALUES (?)", (intent,))  # forces the insert to fail
    with pytest.raises(sqlite3.IntegrityError):
        ledger.settle(intent, IntentState.FILLED, "o-1", Decimal("1.5"), average_price=gbp("100"))
    assert [i.intent_id for i in ledger.open_intents()] == [intent]  # state and fill together, or neither
