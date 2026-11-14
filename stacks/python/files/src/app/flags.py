"""Feature flags (docs/feature-flags.md).

Resolution: env FLAG_<NAME> -> remote override (per-environment JSON in Cloud Storage,
cached 30s) -> registry default in flags.json. If the remote source fails, the last
good overrides are kept.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Literal, Protocol

log = logging.getLogger(__name__)

FlagType = Literal["release", "kill-switch", "ops"]
REFRESH_SECONDS = 30.0
_TRUE = {"1", "true", "on", "yes"}
_FALSE = {"0", "false", "off", "no"}


@dataclass(frozen=True)
class FlagSpec:
    type: FlagType
    default: bool


class FlagSource(Protocol):
    def fetch_overrides(self) -> dict[str, bool]: ...


def load_registry(path: str | Path = "flags.json") -> dict[str, FlagSpec]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))["flags"]
    return {name: FlagSpec(type=spec["type"], default=bool(spec["default"])) for name, spec in raw.items()}


def env_override(name: str, env: Mapping[str, str]) -> bool | None:
    raw = env.get("FLAG_" + name.upper().replace("-", "_"))
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return None


class Flags:
    def __init__(
        self,
        registry: Mapping[str, FlagSpec],
        *,
        source: FlagSource | None = None,
        now: Callable[[], float] = time.monotonic,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._registry = dict(registry)
        self._source = source
        self._now = now
        self._env = env if env is not None else os.environ
        self._overrides: dict[str, bool] = {}
        self._fetched_at = float("-inf")
        self._lock = threading.Lock()

    def is_enabled(self, name: str) -> bool:
        """Is the feature enabled? Raises for unregistered names (CI also checks this)."""
        spec = self._registry.get(name)
        if spec is None:
            raise KeyError(f'Unknown flag "{name}": add it to flags.json')
        if self._source is not None and self._now() - self._fetched_at > REFRESH_SECONDS:
            self.refresh()
        from_env = env_override(name, self._env)
        if from_env is not None:
            return from_env
        return self._overrides.get(name, spec.default)

    def refresh(self) -> None:
        """Fetch remote overrides now. Keeps the last good values on failure."""
        if self._source is None or not self._lock.acquire(blocking=False):
            return
        try:
            self._overrides = self._source.fetch_overrides()
        except Exception as exc:  # noqa: BLE001 - any failure keeps last good values
            log.warning("flag refresh failed: %s", exc)
        finally:
            self._fetched_at = self._now()
            self._lock.release()


class GcsSource:
    """Remote overrides from gs://<bucket>/flags.json using the Cloud Run identity."""

    _METADATA_URL = (
        "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
    )

    def __init__(self, bucket: str, timeout: float = 2.0) -> None:
        self._bucket = bucket
        self._timeout = timeout

    def fetch_overrides(self) -> dict[str, bool]:
        token_req = urllib.request.Request(self._METADATA_URL, headers={"Metadata-Flavor": "Google"})  # noqa: S310
        with urllib.request.urlopen(token_req, timeout=self._timeout) as resp:  # noqa: S310 - fixed URL
            token = json.load(resp)["access_token"]
        url = (
            "https://storage.googleapis.com/storage/v1/b/"
            f"{urllib.parse.quote(self._bucket, safe='')}/o/flags.json?alt=media"
        )
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})  # noqa: S310
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310 - fixed host
                body = json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code == HTTPStatus.NOT_FOUND:
                return {}
            raise
        overrides = body.get("overrides") or {}
        return {k: v for k, v in overrides.items() if isinstance(v, bool)}
