"""The ledger: intended and sent orders, and the high-water mark (ADR-0013).

SQLite. Phase 2 keeps the file in a versioned GCS bucket with a generation-match lock so
only one run writes at a time; here it is a local file. Every order is recorded *before*
it is sent, so a crash or timeout can always be reconciled (ARCHITECTURE.md, invariants).
Times are UTC. Amounts are stored as Decimal strings.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
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
CREATE TABLE IF NOT EXISTS signal_closes (
    signal_day TEXT NOT NULL,
    ticker TEXT NOT NULL,
    currency TEXT NOT NULL,
    close TEXT NOT NULL,
    PRIMARY KEY (signal_day, ticker, currency)
);
CREATE TABLE IF NOT EXISTS acted_signals (
    signal_day TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS daily_equity (
    day TEXT PRIMARY KEY,
    amount TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS marks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    amount TEXT NOT NULL,
    at TEXT NOT NULL
);
"""


# Monitoring's decision record (docs/exec-plans/active/2026-11-22-monitoring.md, step 1).
# Expand only: created on `open` too, so a ledger from before gains them; rows never change
# except a run's end, outcome and reason, and a snapshot's `equity_checked` flag.
_RECORD_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    outcome TEXT,
    reason TEXT,
    version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS signal_records (
    run_id TEXT NOT NULL,
    signal_day TEXT NOT NULL,
    ticker TEXT NOT NULL,
    currency TEXT NOT NULL,
    close REAL NOT NULL,
    sma REAL NOT NULL,
    in_trend INTEGER NOT NULL,
    weight_before REAL NOT NULL,
    weight_after REAL NOT NULL,
    scale REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS risk_decisions (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    currency TEXT NOT NULL,
    side TEXT NOT NULL,
    proposed TEXT NOT NULL,
    approved TEXT,
    reason TEXT NOT NULL,
    quote TEXT,
    quote_currency TEXT
);
CREATE TABLE IF NOT EXISTS snapshots (
    run_id TEXT PRIMARY KEY,
    at TEXT NOT NULL,
    cash TEXT NOT NULL,
    equity_checked INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshot_positions (
    run_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    currency TEXT NOT NULL,
    quantity TEXT NOT NULL,
    quote TEXT,
    value_gbp TEXT
);
CREATE TABLE IF NOT EXISTS fills (
    intent_id INTEGER PRIMARY KEY,
    average_price TEXT,
    price_currency TEXT,
    fees TEXT
);
CREATE TABLE IF NOT EXISTS previews (
    run_id TEXT NOT NULL,
    as_of TEXT NOT NULL,
    ticker TEXT NOT NULL,
    currency TEXT NOT NULL,
    in_trend INTEGER NOT NULL,
    weight REAL NOT NULL
);
"""


@dataclass(frozen=True, slots=True)
class RunRecord:
    run_id: str
    started_at: datetime
    ended_at: datetime | None
    outcome: str | None  # "completed", "stopped", or "died" (never finished; reported by a later run)
    reason: str | None
    version: str


@dataclass(frozen=True, slots=True)
class SignalRecord:
    run_id: str
    instrument: Instrument
    close: float
    sma: float
    in_trend: bool
    weight_before: float
    weight_after: float
    scale: float


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    instrument: Instrument
    side: Side
    proposed_quantity: Decimal
    approved_quantity: Decimal | None
    reason: str
    quote: Money | None


@dataclass(frozen=True, slots=True)
class PositionRecord:
    instrument: Instrument
    quantity: Decimal
    quote: Money | None
    value_gbp: Money | None


@dataclass(frozen=True, slots=True)
class Snapshot:
    at: datetime
    cash: Money
    positions: list[PositionRecord]
    equity_checked: bool  # False: stored before (or without) the equity check passing


@dataclass(frozen=True, slots=True)
class PreviewRecord:
    as_of: date
    instrument: Instrument
    in_trend: bool
    weight: float


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


@dataclass(frozen=True, slots=True)
class Fill:
    average_price: Money | None  # per unit, in the instrument's quote currency
    fees: Money | None  # GBP


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
        if (
            not {"intents", "marks", "daily_equity", "signal_closes", "acted_signals"} <= tables
        ):  # e.g. an empty or truncated download
            conn.close()
            raise LedgerError(f"not a ledger (missing tables): {path}")
        conn.executescript(_RECORD_SCHEMA)
        return cls(conn)

    @classmethod
    def create(cls, path: Path) -> Ledger:
        """Create a new, empty ledger (first-time setup only). Refuses to overwrite."""
        if path.exists():
            raise LedgerError(f"ledger already exists: {path}")
        conn = sqlite3.connect(path, isolation_level=None)  # autocommit: each write is durable at once
        conn.executescript(_SCHEMA)
        conn.executescript(_RECORD_SCHEMA)
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

    def settle(  # noqa: PLR0913 - one settled order's facts
        self,
        intent_id: int,
        state: IntentState,
        broker_order_id: str | None = None,
        filled_quantity: Decimal | None = None,
        *,
        average_price: Money | None = None,
        fees: Money | None = None,
    ) -> None:
        """Settle an intent; the fill price and fees too, when the broker reported them
        (monitoring AC4: never estimated)."""
        if state in OPEN:
            raise LedgerError(f"{state} is not a settled state")
        if fees is not None and fees.currency is not Currency.GBP:
            raise LedgerError("fees are in GBP")
        self._conn.execute("BEGIN")  # the state and its fill together, or neither
        try:
            self._set(intent_id, state, broker_order_id, filled_quantity)
            if average_price is not None or fees is not None:
                self._conn.execute(
                    "INSERT INTO fills (intent_id, average_price, price_currency, fees) VALUES (?, ?, ?, ?)",
                    (
                        intent_id,
                        None if average_price is None else str(average_price.amount),
                        None if average_price is None else average_price.currency.value,
                        None if fees is None else str(fees.amount),
                    ),
                )
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")

    def fill(self, intent_id: int) -> Fill:
        """A settled intent's fill price and fees, as far as the broker reported them."""
        row = self._conn.execute(
            "SELECT average_price, price_currency, fees FROM fills WHERE intent_id = ?", (intent_id,)
        ).fetchone()
        if row is None:
            return Fill(None, None)
        price = None if row[0] is None else Money(Decimal(row[0]), Currency(row[1]))
        return Fill(price, None if row[2] is None else Money(Decimal(row[2]), Currency.GBP))

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

    # --- daily equity (for the daily-loss pause) ---------------------------------------
    def record_equity(self, day: date, equity: Money) -> None:
        """Record a run's checked equity for `day` (UTC); a later run that day replaces it."""
        if equity.currency is not Currency.GBP:
            raise LedgerError("equity is in GBP")
        self._conn.execute(
            "INSERT INTO daily_equity (day, amount) VALUES (?, ?)"
            " ON CONFLICT (day) DO UPDATE SET amount = excluded.amount",
            (day.isoformat(), str(equity.amount)),
        )

    def previous_day_equity(self, day: date, max_age_days: int = 5) -> Money | None:
        """The day-start equity for `day`: the last equity recorded on an earlier day, as the
        backtest uses the previous close. None if there is none within `max_age_days` (a
        stale value could hide a real loss; risk then blocks buys for the day)."""
        oldest = (day - timedelta(days=max_age_days)).isoformat()
        row = self._conn.execute(
            "SELECT amount FROM daily_equity WHERE day < ? AND day >= ? ORDER BY day DESC LIMIT 1",
            (day.isoformat(), oldest),
        ).fetchone()
        return None if row is None else Money(Decimal(row[0]), Currency.GBP)

    def equity_on(self, day: date) -> Money | None:
        """The equity recorded for `day` itself (the latest run's), if any."""
        row = self._conn.execute(
            "SELECT amount FROM daily_equity WHERE day = ?", (day.isoformat(),)
        ).fetchone()
        return None if row is None else Money(Decimal(row[0]), Currency.GBP)

    # --- month-end signals -----------------------------------------------------------
    def store_verified_closes(self, signal_day: date, closes: Mapping[Instrument, Money]) -> None:
        """The signal-day closes that passed the cross-check against the broker's prices."""
        for instrument, close in closes.items():
            self._conn.execute(
                "INSERT INTO signal_closes (signal_day, ticker, currency, close) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (signal_day, ticker, currency) DO UPDATE SET close = excluded.close",
                (signal_day.isoformat(), instrument.ticker, instrument.currency.value, str(close.amount)),
            )

    def verified_closes(self, signal_day: date) -> dict[Instrument, Money]:
        rows = self._conn.execute(
            "SELECT ticker, currency, close FROM signal_closes WHERE signal_day = ?",
            (signal_day.isoformat(),),
        )
        return {Instrument(t, Currency(c)): Money(Decimal(v), Currency(c)) for t, c, v in rows}

    def mark_signal_acted(self, signal_day: date) -> None:
        """This month end's decision ran to completion; later runs only reconcile and mark."""
        self._conn.execute(
            "INSERT OR IGNORE INTO acted_signals (signal_day) VALUES (?)", (signal_day.isoformat(),)
        )

    def signal_acted(self, signal_day: date) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM acted_signals WHERE signal_day = ?", (signal_day.isoformat(),)
        ).fetchone()
        return row is not None

    # --- high-water mark --------------------------------------------------------------
    def seed_high_water_mark(self, equity: Money, at: datetime) -> None:
        """Set the first mark. An explicit, one-off owner step at first-time setup."""
        if self.high_water_mark() is not None:
            raise LedgerError("the high-water mark is already seeded")
        self._insert_mark(equity, at)
        self.record_equity(at.date(), equity)

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

    def first_high_water_mark(self) -> Money | None:
        """The mark setup seeded (the account's starting equity), if any."""
        row = self._conn.execute("SELECT amount FROM marks ORDER BY id LIMIT 1").fetchone()
        return None if row is None else Money(Decimal(row[0]), Currency.GBP)

    def high_water_mark(self) -> Money | None:
        """The current mark, or None if never recorded. A mark lower than any earlier one
        means the history was reset or tampered with: that is an error, not a new mark."""
        amounts = [Decimal(r[0]) for r in self._conn.execute("SELECT amount FROM marks ORDER BY id")]
        if not amounts:
            return None
        if amounts[-1] < max(amounts):
            raise LedgerError(f"high-water mark {amounts[-1]} is lower than an earlier {max(amounts)}")
        return Money(amounts[-1], Currency.GBP)

    # --- the decision record (monitoring step 1) -----------------------------------------
    def start_run(self, run_id: str, at: datetime, version: str) -> None:
        self._conn.execute(
            "INSERT INTO runs (run_id, started_at, version) VALUES (?, ?, ?)",
            (run_id, _utc(at), version),
        )

    def finish_run(self, run_id: str, at: datetime, outcome: str, reason: str | None) -> None:
        self._conn.execute(
            "UPDATE runs SET ended_at = ?, outcome = ?, reason = ? WHERE run_id = ?",
            (_utc(at), outcome, reason, run_id),
        )

    def unfinished_runs(self) -> list[RunRecord]:
        return [r for r in self.runs() if r.outcome is None]

    def mark_run_died(self, run_id: str) -> None:
        self._conn.execute(
            "UPDATE runs SET outcome = 'died', reason = 'never finished' WHERE run_id = ?", (run_id,)
        )

    def runs(self) -> list[RunRecord]:
        rows = self._conn.execute(
            "SELECT run_id, started_at, ended_at, outcome, reason, version FROM runs"
            " ORDER BY started_at, rowid"
        )
        return [
            RunRecord(
                r[0],
                datetime.fromisoformat(r[1]),
                None if r[2] is None else datetime.fromisoformat(r[2]),
                r[3],
                r[4],
                r[5],
            )
            for r in rows
        ]

    def record_signals(self, run_id: str, signal_day: date, rows: list[SignalRecord]) -> None:
        for r in rows:
            self._conn.execute(
                "INSERT INTO signal_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    signal_day.isoformat(),
                    r.instrument.ticker,
                    r.instrument.currency.value,
                    r.close,
                    r.sma,
                    int(r.in_trend),
                    r.weight_before,
                    r.weight_after,
                    r.scale,
                ),
            )

    def signal_records(self, signal_day: date) -> list[SignalRecord]:
        """Every run's records for the signal day, in order; `run_id` says which run wrote each."""
        rows = self._conn.execute(
            "SELECT run_id, ticker, currency, close, sma, in_trend, weight_before, weight_after, scale"
            " FROM signal_records WHERE signal_day = ? ORDER BY rowid",
            (signal_day.isoformat(),),
        )
        return [
            SignalRecord(r[0], Instrument(r[1], Currency(r[2])), r[3], r[4], bool(r[5]), r[6], r[7], r[8])
            for r in rows
        ]

    def record_decisions(self, run_id: str, rows: list[DecisionRecord]) -> None:
        for seq, r in enumerate(rows):
            self._conn.execute(
                "INSERT INTO risk_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    seq,
                    r.instrument.ticker,
                    r.instrument.currency.value,
                    r.side.value,
                    str(r.proposed_quantity),
                    None if r.approved_quantity is None else str(r.approved_quantity),
                    r.reason,
                    None if r.quote is None else str(r.quote.amount),
                    None if r.quote is None else r.quote.currency.value,
                ),
            )

    def decisions(self, run_id: str) -> list[DecisionRecord]:
        rows = self._conn.execute(
            "SELECT ticker, currency, side, proposed, approved, reason, quote, quote_currency"
            " FROM risk_decisions"
            " WHERE run_id = ? ORDER BY seq",
            (run_id,),
        )
        return [
            DecisionRecord(
                Instrument(r[0], Currency(r[1])),
                Side(r[2]),
                Decimal(r[3]),
                None if r[4] is None else Decimal(r[4]),
                r[5],
                None if r[6] is None else Money(Decimal(r[6]), Currency(r[7])),
            )
            for r in rows
        ]

    def record_snapshot(
        self, run_id: str, at: datetime, cash: Money, positions: list[PositionRecord], *, equity_checked: bool
    ) -> None:
        self._conn.execute(
            "INSERT INTO snapshots VALUES (?, ?, ?, ?)",
            (run_id, _utc(at), str(cash.amount), int(equity_checked)),
        )
        for p in positions:
            self._conn.execute(
                "INSERT INTO snapshot_positions VALUES (?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    p.instrument.ticker,
                    p.instrument.currency.value,
                    str(p.quantity),
                    None if p.quote is None else str(p.quote.amount),
                    None if p.value_gbp is None else str(p.value_gbp.amount),
                ),
            )

    def mark_snapshot_checked(self, run_id: str) -> None:
        self._conn.execute("UPDATE snapshots SET equity_checked = 1 WHERE run_id = ?", (run_id,))

    def snapshot(self, run_id: str) -> Snapshot:
        head = self._conn.execute(
            "SELECT at, cash, equity_checked FROM snapshots WHERE run_id = ?", (run_id,)
        ).fetchone()
        if head is None:
            raise LedgerError(f"no snapshot for {run_id}")
        rows = self._conn.execute(
            "SELECT ticker, currency, quantity, quote, value_gbp FROM snapshot_positions"
            " WHERE run_id = ? ORDER BY rowid",
            (run_id,),
        )
        positions = [
            PositionRecord(
                Instrument(r[0], Currency(r[1])),
                Decimal(r[2]),
                None if r[3] is None else Money(Decimal(r[3]), Currency(r[1])),
                None if r[4] is None else Money(Decimal(r[4]), Currency.GBP),
            )
            for r in rows
        ]
        return Snapshot(
            datetime.fromisoformat(head[0]), Money(Decimal(head[1]), Currency.GBP), positions, bool(head[2])
        )

    def record_preview(self, run_id: str, rows: list[PreviewRecord]) -> None:
        for r in rows:
            self._conn.execute(
                "INSERT INTO previews VALUES (?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    r.as_of.isoformat(),
                    r.instrument.ticker,
                    r.instrument.currency.value,
                    int(r.in_trend),
                    r.weight,
                ),
            )

    def preview(self, run_id: str) -> list[PreviewRecord]:
        rows = self._conn.execute(
            "SELECT as_of, ticker, currency, in_trend, weight FROM previews WHERE run_id = ? ORDER BY rowid",
            (run_id,),
        )
        return [
            PreviewRecord(date.fromisoformat(r[0]), Instrument(r[1], Currency(r[2])), bool(r[3]), r[4])
            for r in rows
        ]
