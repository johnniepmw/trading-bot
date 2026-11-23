"""Pause requests (Phase 2 step 7b; monitoring AC6): a one-way stop for new orders.

Any object in the environment's pause bucket is a request. While one exists, or while the
bucket can't be read, no new orders are sent: this is checked wherever the `new-orders`
kill switch is. Requests are created by the owner (and, in Phase 3, the dashboard's
create-only identity) and cleared only by the owner's `ops` action. Local and test runs
without a pause location are never paused.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable
from datetime import datetime

from tradingbot.app.storage import (
    ObjectStore,
    PreconditionFailedError,
    StorageError,
    TransientStorageError,
)

ATTEMPTS = 3

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
            log.error(
                "pause location unreadable (%s): no new orders",
                type(error).__name__,
                extra={"alert": "pause_unreadable"},
            )
            return True


class PauseRequestError(Exception):
    """The pause request couldn't be stored: new orders are NOT paused."""


def request_pause(
    store: ObjectStore,
    bucket: str,
    now: datetime,
    *,
    source: str = "dashboard",
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Store one new pause request (create-only, a unique name) and return its name. A write
    whose outcome was lost and then finds the name taken means our own write landed."""
    name = f"{source}-{now:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:12]}"
    body = json.dumps({"source": source, "at": now.isoformat()}).encode()
    uncertain = False
    for attempt in range(ATTEMPTS):
        try:
            store.write(bucket, name, body, if_generation_match=0)
        except TransientStorageError as error:
            uncertain = True
            if attempt == ATTEMPTS - 1:
                raise PauseRequestError(f"storage unavailable ({type(error).__name__})") from error
            sleep(2.0**attempt)
            continue
        except PreconditionFailedError as error:
            if uncertain:
                return name
            raise PauseRequestError("a request with this name already exists") from error
        except StorageError as error:
            raise PauseRequestError(f"storage refused the write ({type(error).__name__})") from error
        log.warning(
            "pause requested from the %s: no new orders from the next order check",
            source,
            extra={"alert": "pause_requested"},
        )
        return name
    raise AssertionError("unreachable")  # pragma: no cover
