"""The ledger: intended and sent orders, and the high-water mark (ADR-0013).

SQLite. Phase 2 keeps the file in a versioned GCS bucket with a generation-match lock so
only one run writes at a time; here it is a local file. Every order is recorded *before*
it is sent, so a crash or timeout can always be reconciled (ARCHITECTURE.md, invariants).
Times are UTC. Amounts are stored as Decimal strings.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from tradingbot.domain.market import Instrument, Order, Side
from tradingbot.domain.money import Currency, Money

_SCHEMA = """
CREATE TABLE IF NOT EXISTS intents (
    intent_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    ticker TEXT NOT NULL,
    currency TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity TEXT NOT NULL,
    state TEXT NOT NULL,
    broker_order_id TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS marks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    amount TEXT NOT NULL,
    at TEXT NOT NULL
);
"""


class LedgerError(Exception):
    """The ledger can't be trusted or was used wrongly: no new orders."""


class IntentState(StrEnum):
    INTENDED = "INTENDED"  # recorded, not yet sent
    SENT = "SENT"  # the broker gave an order ID
    UNKNOWN = "UNKNOWN"  # sent, outcome unknown (timeout)
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    NOT_SENT = "NOT_SENT"  # the broker never received it


OPEN = (IntentState.INTENDED, IntentState.SENT, IntentState.UNKNOWN)
_COLUMNS = "SELECT intent_id, run_id, ticker, currency, side, quantity, state, broker_order_id FROM intents"
_OPEN_INTENTS = _COLUMNS + " WHERE state IN (?, ?, ?) ORDER BY intent_id"
_SETTLED_INTENTS = _COLUMNS + " WHERE state NOT IN (?, ?, ?) ORDER BY intent_id"


@dataclass(frozen=True, slots=True)
class Intent:
    intent_id: int
    run_id: str
    order: Order
    state: IntentState
    broker_order_id: str | None


def _utc(at: datetime) -> str:
    if at.tzinfo is None or at.utcoffset() != UTC.utcoffset(at):
        raise LedgerError(f"times must be UTC, got {at!r}")
    return at.isoformat()


class Ledger:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @classmethod
    def open(cls, path: Path) -> Ledger:
        conn = sqlite3.connect(path, isolation_level=None)  # autocommit: each write is durable at once
        conn.executescript(_SCHEMA)
        return cls(conn)

    def close(self) -> None:
        self._conn.close()

    # --- intents -------------------------------------------------------------------
    def record_intent(self, run_id: str, order: Order, at: datetime) -> int:
        o = order
        cursor = self._conn.execute(
            "INSERT INTO intents (run_id, created_at, ticker, currency, side, quantity, state)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                _utc(at),
                o.instrument.ticker,
                o.instrument.currency.value,
                o.side.value,
                str(o.quantity),
                IntentState.INTENDED.value,
            ),
        )
        if cursor.lastrowid is None:  # sqlite sets it for every INSERT; kept for the type checker
            raise LedgerError("no intent id")
        return cursor.lastrowid

    def _set(self, intent_id: int, state: IntentState, broker_order_id: str | None = None) -> None:
        row = self._conn.execute("SELECT state FROM intents WHERE intent_id = ?", (intent_id,)).fetchone()
        if row is None:
            raise LedgerError(f"no intent {intent_id}")
        if IntentState(row[0]) not in OPEN:
            raise LedgerError(f"intent {intent_id} is already settled as {row[0]}")
        self._conn.execute(
            "UPDATE intents SET state = ?, broker_order_id = COALESCE(?, broker_order_id)"
            " WHERE intent_id = ?",
            (state.value, broker_order_id, intent_id),
        )

    def mark_sent(self, intent_id: int, broker_order_id: str) -> None:
        self._set(intent_id, IntentState.SENT, broker_order_id)

    def mark_unknown(self, intent_id: int) -> None:
        self._set(intent_id, IntentState.UNKNOWN)

    def settle(self, intent_id: int, state: IntentState, broker_order_id: str | None = None) -> None:
        if state in OPEN:
            raise LedgerError(f"{state} is not a settled state")
        self._set(intent_id, state, broker_order_id)

    def _intents(self, sql: str) -> list[Intent]:
        rows = self._conn.execute(sql, tuple(s.value for s in OPEN)).fetchall()
        return [
            Intent(
                r[0],
                r[1],
                Order(Instrument(r[2], Currency(r[3])), Side(r[4]), Decimal(r[5])),
                IntentState(r[6]),
                r[7],
            )
            for r in rows
        ]

    def open_intents(self) -> list[Intent]:
        return self._intents(_OPEN_INTENTS)

    def settled_states(self) -> list[IntentState]:
        return [i.state for i in self._intents(_SETTLED_INTENTS)]

    def linked_order_ids(self) -> set[str]:
        rows = self._conn.execute("SELECT broker_order_id FROM intents WHERE broker_order_id IS NOT NULL")
        return {r[0] for r in rows}

    # --- high-water mark --------------------------------------------------------------
    def update_high_water_mark(self, equity: Money, at: datetime) -> None:
        """Record equity as the mark if it is a new peak. The mark only rises."""
        if equity.currency is not Currency.GBP:
            raise LedgerError("the high-water mark is in GBP")
        current = self.high_water_mark()
        if current is None or equity.amount > current.amount:
            self._conn.execute("INSERT INTO marks (amount, at) VALUES (?, ?)", (str(equity.amount), _utc(at)))

    def high_water_mark(self) -> Money | None:
        """The current mark, or None if never recorded. A mark lower than any earlier one
        means the history was reset or tampered with: that is an error, not a new mark."""
        amounts = [Decimal(r[0]) for r in self._conn.execute("SELECT amount FROM marks ORDER BY id")]
        if not amounts:
            return None
        if amounts[-1] < max(amounts):
            raise LedgerError(f"high-water mark {amounts[-1]} is lower than an earlier {max(amounts)}")
        return Money(amounts[-1], Currency.GBP)
