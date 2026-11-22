"""Alert kinds for the bot's log lines (Phase 2 step 7a; monitoring AC5).

The JSON formatter adds `alert: <kind>` to every ERROR line and to any line logged with
`extra={"alert": ...}`. Cloud Monitoring's policy matches that field only, never Cloud
Run's own entries, so a dry run that stops correctly (WARNING) pages nobody.
"""

from __future__ import annotations

import logging
from decimal import Decimal

DRAWDOWN_LEVELS = (5, 10, 15, 20)  # % below the high-water mark: 10 halves the cap, 20 stops buys

_PREFIXES = (
    ("cycle stopped", "cycle_stopped"),
    ("stale lock", "stale_lock"),
    ("order ended short", "order_short"),
    ("setup refused", "setup_refused"),
    ("refusing to start", "refused_to_start"),
    ("lock release failed", "lock_release"),
    ("crashed", "crash"),
)


def alert_kind(record: logging.LogRecord) -> str | None:
    explicit = getattr(record, "alert", None)
    if isinstance(explicit, str):
        return explicit
    if record.levelno < logging.ERROR:
        return None
    message = record.getMessage()
    if message.startswith("run ") and message.endswith(" never finished"):
        return "run_died"
    return next((kind for prefix, kind in _PREFIXES if message.startswith(prefix)), "error")


def drawdown_crossings(previous: Decimal | None, now: Decimal, *, mark: Decimal) -> list[int]:
    """The levels (in %) that equity fell through since the previous recorded day."""
    if previous is None or mark <= 0:
        return []
    before, after = (1 - previous / mark) * 100, (1 - now / mark) * 100
    return [level for level in DRAWDOWN_LEVELS if before < level <= after]
