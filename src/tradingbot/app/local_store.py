"""An `ObjectStore` in a local directory, for `APP_ENV=local|test` only (the job refuses it
elsewhere). Keeps every version, like a versioned bucket; one process at a time."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from tradingbot.app.storage import ObjectInfo, PreconditionFailedError


class LocalObjectStore:
    def __init__(self, root: Path, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._root = root
        self._clock = clock

    def _dir(self, bucket: str, name: str) -> Path:
        return self._root / bucket / name.replace("/", "%2F")

    def _index(self, bucket: str, name: str) -> dict[str, object]:
        path = self._dir(bucket, name) / "index.json"
        return json.loads(path.read_text()) if path.exists() else {"live": None, "versions": {}}

    def _save_index(self, bucket: str, name: str, index: dict[str, object]) -> None:
        folder = self._dir(bucket, name)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "index.json").write_text(json.dumps(index))

    def _next_generation(self) -> int:
        counter = self._root / "generation"
        value = int(counter.read_text()) + 1 if counter.exists() else 1
        self._root.mkdir(parents=True, exist_ok=True)
        counter.write_text(str(value))
        return value

    def info(self, bucket: str, name: str) -> ObjectInfo | None:
        index = self._index(bucket, name)
        live = index["live"]
        if live is None:
            return None
        versions = index["versions"]
        assert isinstance(versions, dict)  # noqa: S101 - our own file
        meta = versions[str(live)]
        return ObjectInfo(
            generation=int(str(live)), md5=meta["md5"], created=datetime.fromisoformat(meta["created"])
        )

    def read(self, bucket: str, name: str, generation: int) -> bytes:
        path = self._dir(bucket, name) / f"{generation}.bin"
        if not path.exists():
            raise PreconditionFailedError(f"{name}#{generation} not found")
        return path.read_bytes()

    def write(self, bucket: str, name: str, data: bytes, *, if_generation_match: int) -> ObjectInfo:
        current = self.info(bucket, name)
        if (current.generation if current else 0) != if_generation_match:
            raise PreconditionFailedError(f"{name}: generation mismatch")
        generation = self._next_generation()
        folder = self._dir(bucket, name)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{generation}.bin").write_bytes(data)
        md5 = base64.b64encode(hashlib.md5(data, usedforsecurity=False).digest()).decode()
        index = self._index(bucket, name)
        versions = index["versions"]
        assert isinstance(versions, dict)  # noqa: S101 - our own file
        versions[str(generation)] = {"md5": md5, "created": self._clock().isoformat()}
        index["live"] = generation
        self._save_index(bucket, name, index)
        info = self.info(bucket, name)
        assert info is not None  # noqa: S101 - just written
        return info

    def delete(self, bucket: str, name: str, *, if_generation_match: int) -> None:
        current = self.info(bucket, name)
        if current is None or current.generation != if_generation_match:
            raise PreconditionFailedError(f"{name}: generation mismatch")
        index = self._index(bucket, name)
        index["live"] = None
        self._save_index(bucket, name, index)

    def has_any_version(self, bucket: str, name: str) -> bool:
        versions = self._index(bucket, name)["versions"]
        return bool(versions)
