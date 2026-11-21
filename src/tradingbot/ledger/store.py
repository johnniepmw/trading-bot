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
    broker_order_id TEXT UNIQUE,
    filled_quantity TEXT
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
_COLUMNS = (
    "SELECT intent_id, run_id, created_at, ticker, currency, side, quantity, state, broker_order_id,"
    " filled_quantity FROM intents"
)
_OPEN_INTENTS = _COLUMNS + " WHERE state IN (?, ?, ?) ORDER BY intent_id"
_SETTLED_INTENTS = _COLUMNS + " WHERE state NOT IN (?, ?, ?) ORDER BY intent_id"


@dataclass(frozen=True, slots=True)
class Intent:
    intent_id: int
    run_id: str
    created_at: datetime
    order: Order
    state: IntentState
    broker_order_id: str | None
    filled_quantity: Decimal | None  # known once settled from the broker


def _utc(at: datetime) -> str:
    # UTC itself, not just a zero offset: a London clock would pass all winter
    if at.tzinfo is not UTC:
        raise LedgerError(f"times must be UTC, got {at!r}")
    return at.isoformat()


class Ledger:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @classmethod
    def open(cls, path: Path) -> Ledger:
        """Open an existing ledger. A missing file is an error, never a fresh start: an empty
        ledger has no mark history and would let a reset lift the drawdown stop."""
        if not path.is_file():
            raise LedgerError(f"ledger file missing: {path}")
        # mode=rw: SQLite must not create the file if it vanishes between the check and here
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=rw", uri=True, isolation_level=None)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if not {"intents", "marks"} <= tables:  # e.g. an empty or truncated download
            conn.close()
            raise LedgerError(f"not a ledger (missing tables): {path}")
        return cls(conn)

    @classmethod
    def create(cls, path: Path) -> Ledger:
        """Create a new, empty ledger (first-time setup only). Refuses to overwrite."""
        if path.exists():
            raise LedgerError(f"ledger already exists: {path}")
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

    def _set(
        self,
        intent_id: int,
        state: IntentState,
        broker_order_id: str | None = None,
        filled_quantity: Decimal | None = None,
    ) -> None:
        row = self._conn.execute("SELECT state FROM intents WHERE intent_id = ?", (intent_id,)).fetchone()
        if row is None:
            raise LedgerError(f"no intent {intent_id}")
        if IntentState(row[0]) not in OPEN:
            raise LedgerError(f"intent {intent_id} is already settled as {row[0]}")
        self._conn.execute(
            "UPDATE intents SET state = ?, broker_order_id = COALESCE(?, broker_order_id),"
            " filled_quantity = COALESCE(?, filled_quantity) WHERE intent_id = ?",
            (
                state.value,
                broker_order_id,
                None if filled_quantity is None else str(filled_quantity),
                intent_id,
            ),
        )

    def mark_sent(self, intent_id: int, broker_order_id: str) -> None:
        self._set(intent_id, IntentState.SENT, broker_order_id)

    def mark_unknown(self, intent_id: int) -> None:
        self._set(intent_id, IntentState.UNKNOWN)

    def settle(
        self,
        intent_id: int,
        state: IntentState,
        broker_order_id: str | None = None,
        filled_quantity: Decimal | None = None,
    ) -> None:
        if state in OPEN:
            raise LedgerError(f"{state} is not a settled state")
        self._set(intent_id, state, broker_order_id, filled_quantity)

    def _intents(self, sql: str) -> list[Intent]:
        rows = self._conn.execute(sql, tuple(s.value for s in OPEN)).fetchall()
        return [
            Intent(
                intent_id=r[0],
                run_id=r[1],
                created_at=datetime.fromisoformat(r[2]),
                order=Order(Instrument(r[3], Currency(r[4])), Side(r[5]), Decimal(r[6])),
                state=IntentState(r[7]),
                broker_order_id=r[8],
                filled_quantity=None if r[9] is None else Decimal(r[9]),
            )
            for r in rows
        ]

    def open_intents(self) -> list[Intent]:
        return self._intents(_OPEN_INTENTS)

    def settled(self) -> list[Intent]:
        return self._intents(_SETTLED_INTENTS)

    def has_run(self, run_id: str) -> bool:
        """Has this run already recorded any intent?"""
        row = self._conn.execute("SELECT 1 FROM intents WHERE run_id = ? LIMIT 1", (run_id,)).fetchone()
        return row is not None

    def linked_order_ids(self) -> set[str]:
        rows = self._conn.execute("SELECT broker_order_id FROM intents WHERE broker_order_id IS NOT NULL")
        return {r[0] for r in rows}

    # --- high-water mark --------------------------------------------------------------
    def seed_high_water_mark(self, equity: Money, at: datetime) -> None:
        """Set the first mark. An explicit, one-off owner step at first-time setup."""
        if self.high_water_mark() is not None:
            raise LedgerError("the high-water mark is already seeded")
        self._insert_mark(equity, at)

    def update_high_water_mark(self, equity: Money, at: datetime) -> None:
        """Record equity as the mark if it is a new peak. The mark only rises, and an update
        never creates the first one (that would re-seed a lost history from today's equity)."""
        if equity.currency is not Currency.GBP:
            raise LedgerError("the high-water mark is in GBP")
        current = self.high_water_mark()
        if current is None:
            raise LedgerError("no high-water mark: seed it first (owner step)")
        if equity.amount > current.amount:
            self._insert_mark(equity, at)

    def _insert_mark(self, equity: Money, at: datetime) -> None:
        if equity.currency is not Currency.GBP:
            raise LedgerError("the high-water mark is in GBP")
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
