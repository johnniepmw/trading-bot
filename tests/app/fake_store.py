"""An in-memory object store with GCS's generation semantics, plus fault injection."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from tradingbot.app.storage import ObjectInfo, PreconditionFailedError, TransientStorageError


@dataclass
class _Version:
    info: ObjectInfo
    data: bytes
    live: bool


def md5_of(data: bytes) -> str:
    return base64.b64encode(hashlib.md5(data, usedforsecurity=False).digest()).decode()


class FakeStore:
    """`faults` is consumed one per call to `write`/`delete`: "before" fails without writing,
    "after" writes and then fails (the caller can't tell which happened). `before_write`
    runs before each write, e.g. to let a rival writer in."""

    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock
        self._objects: dict[tuple[str, str], list[_Version]] = {}
        self._next_generation = 1000
        self.faults: list[str] = []
        self.before_write: Callable[[str, str], None] | None = None
        self.writes = 0

    def _live(self, bucket: str, name: str) -> _Version | None:
        versions = self._objects.get((bucket, name), [])
        return versions[-1] if versions and versions[-1].live else None

    def _fault(self) -> str | None:
        return self.faults.pop(0) if self.faults else None

    # --- ObjectStore ---------------------------------------------------------------
    def info(self, bucket: str, name: str) -> ObjectInfo | None:
        live = self._live(bucket, name)
        return live.info if live else None

    def read(self, bucket: str, name: str, generation: int) -> bytes:
        for version in self._objects.get((bucket, name), []):
            if version.info.generation == generation:
                return version.data
        raise PreconditionFailedError(f"{bucket}/{name}#{generation} not found")

    def write(self, bucket: str, name: str, data: bytes, *, if_generation_match: int) -> ObjectInfo:
        if self.before_write is not None:
            self.before_write(bucket, name)
        fault = self._fault()
        if fault == "before":
            raise TransientStorageError("503 before writing")
        return self.put(bucket, name, data, if_generation_match=if_generation_match, fault=fault)

    def delete(self, bucket: str, name: str, *, if_generation_match: int) -> None:
        fault = self._fault()
        if fault == "before":
            raise TransientStorageError("503 before deleting")
        live = self._live(bucket, name)
        if live is None or live.info.generation != if_generation_match:
            raise PreconditionFailedError(f"{bucket}/{name}: generation mismatch")
        live.live = False
        if fault == "after":
            raise TransientStorageError("timeout after deleting")

    def has_objects(self, bucket: str) -> bool:
        return any(self._live(b, n) is not None for (b, n) in self._objects if b == bucket)

    def has_any_version(self, bucket: str, name: str) -> bool:
        return bool(self._objects.get((bucket, name)))

    # --- test helpers --------------------------------------------------------------
    def put(
        self,
        bucket: str,
        name: str,
        data: bytes,
        *,
        if_generation_match: int | None = None,
        fault: str | None = None,
    ) -> ObjectInfo:
        """Write directly (a rival writer, or the test's arrangement)."""
        live = self._live(bucket, name)
        current = live.info.generation if live else 0
        if if_generation_match is not None and current != if_generation_match:
            raise PreconditionFailedError(f"{bucket}/{name}: generation mismatch")
        self._next_generation += 1
        self.writes += 1
        info = ObjectInfo(generation=self._next_generation, md5=md5_of(data), created=self._clock())
        if live:
            live.live = False
        self._objects.setdefault((bucket, name), []).append(_Version(info, data, live=True))
        if fault == "after":
            raise TransientStorageError("timeout after writing")
        return info

    def remove(self, bucket: str, name: str) -> None:
        """Delete the live version (versions stay, as in a versioned bucket)."""
        live = self._live(bucket, name)
        if live:
            live.live = False

    def data(self, bucket: str, name: str) -> bytes | None:
        live = self._live(bucket, name)
        return live.data if live else None
