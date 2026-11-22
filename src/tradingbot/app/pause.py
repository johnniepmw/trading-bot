"""Pause requests (Phase 2 step 7b; monitoring AC6): a one-way stop for new orders.

Any object in the environment's pause bucket is a request. While one exists, or while the
bucket can't be read, no new orders are sent: this is checked wherever the `new-orders`
kill switch is. Requests are created by the owner (and, in Phase 3, the dashboard's
create-only identity) and cleared only by the owner's `ops` action. Local and test runs
without a pause location are never paused.
"""

from __future__ import annotations

import logging

from tradingbot.app.storage import ObjectStore

log = logging.getLogger(__name__)


class PauseRequests:
    def __init__(self, store: ObjectStore | None, bucket: str | None) -> None:
        self._store = store
        self._bucket = bucket

    def present(self) -> bool:
        if self._store is None or self._bucket is None:
            return False
        try:
            return self._store.has_objects(self._bucket)
        except Exception as error:  # noqa: BLE001 - unreadable means paused (fail closed)
            log.error("pause location unreadable (%s): no new orders", type(error).__name__)
            return True
