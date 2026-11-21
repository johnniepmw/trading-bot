"""Feature flags (docs/feature-flags.md).

Resolution: env FLAG_<NAME> -> remote override (per-environment JSON in Cloud Storage,
cached 30s) -> registry default in flags.json.

Kill switches fail closed (ADR-0005, ADR-0009). A job starts cold, so there is no
"last good" value to fall back on. A kill switch is therefore off unless a remote read
succeeded within KILL_SWITCH_MAX_STALE_SECONDS; an unreadable, missing or malformed
source counts as a failed read. Outside a trusted (local/test) environment an env
variable can turn a kill switch off but never on. Other flags keep the last good
overrides, else their default.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

log = logging.getLogger(__name__)

FlagType = Literal["release", "kill-switch", "ops"]
REFRESH_SECONDS = 30.0
KILL_SWITCH_MAX_STALE_SECONDS = 300.0
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


def parse_overrides(body: object) -> dict[str, bool]:
    """Validate the overrides object. Anything but {"overrides": {name: bool}} is an error."""
    overrides = body.get("overrides") if isinstance(body, dict) else None
    if not isinstance(overrides, dict) or not all(isinstance(v, bool) for v in overrides.values()):
        raise ValueError('flags body must be {"overrides": {name: true|false}}')
    return dict(overrides)


class Flags:
    def __init__(
        self,
        registry: Mapping[str, FlagSpec],
        *,
        source: FlagSource | None = None,
        now: Callable[[], float] = time.monotonic,
        env: Mapping[str, str] | None = None,
        trusted_env: bool = False,
    ) -> None:
        self._registry = dict(registry)
        self._source = source
        self._now = now
        self._env = env if env is not None else os.environ
        self._overrides: dict[str, bool] = {}
        self._trusted_env = trusted_env
        self._fetched_at = float("-inf")
        self._last_good_at: float | None = None
        self._lock = threading.Lock()

    def is_enabled(self, name: str) -> bool:
        """Is the feature enabled? Raises for unregistered names (CI also checks this)."""
        spec = self._registry.get(name)
        if spec is None:
            raise KeyError(f'Unknown flag "{name}": add it to flags.json')
        if self._source is not None and self._now() - self._fetched_at > REFRESH_SECONDS:
            self.refresh()
        from_env = env_override(name, self._env)
        if spec.type != "kill-switch":
            return from_env if from_env is not None else self._overrides.get(name, spec.default)
        if from_env is False:
            return False
        if from_env is True and not self._trusted_env:
            log.error("kill switch env override ignored: %s: only local/test may turn it on", name)
        if self._source is not None and not self._remote_is_fresh():
            log.error("kill switch closed: %s: no recent good flag read", name)
            return False
        if self._source is None and not self._trusted_env:
            log.error("kill switch closed: %s: no flag source outside local/test", name)
            return False
        if from_env is True and self._trusted_env:
            return True
        return self._overrides.get(name, spec.default)

    def _remote_is_fresh(self) -> bool:
        return (
            self._last_good_at is not None
            and self._now() - self._last_good_at <= KILL_SWITCH_MAX_STALE_SECONDS
        )

    def refresh(self) -> None:
        """Fetch remote overrides now. Keeps the last good values on failure."""
        if self._source is None or not self._lock.acquire(blocking=False):
            return
        try:
            self._overrides = self._source.fetch_overrides()
            self._last_good_at = self._now()
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
        # A missing object (404) is a failed read, not "no overrides": infra seeds it.
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310 - fixed host
            return parse_overrides(json.load(resp))
