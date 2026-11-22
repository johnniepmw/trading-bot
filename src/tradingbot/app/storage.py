"""Object storage for the ledger, its lock and the reference mark (ADR-0017).

`ObjectStore` is the port; `GcsObjectStore` speaks the Cloud Storage JSON API over HTTPS
with the standard library and the job's own identity. Every write and delete names the
generation it expects (0 means "must not exist"), so nothing is ever overwritten blindly.
Redirects are refused, so the bearer token never leaves storage.googleapis.com.
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

_API = "https://storage.googleapis.com/storage/v1/b/"
_UPLOAD = "https://storage.googleapis.com/upload/storage/v1/b/"
_METADATA_TOKEN = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"  # noqa: S105 - a URL, not a secret
_RETRYABLE = {408, 429, 500, 502, 503, 504}


class StorageError(Exception):
    """A storage call failed in a way retrying won't fix."""


class TransientStorageError(StorageError):
    """A timeout, connection failure or 5xx: the call may or may not have taken effect."""


class PreconditionFailedError(StorageError):
    """The object's generation wasn't the one named (or it doesn't exist)."""


@dataclass(frozen=True, slots=True)
class ObjectInfo:
    generation: int
    md5: str  # base64, as Cloud Storage reports `md5Hash`
    created: datetime  # UTC, the server's `timeCreated` for this generation


class ObjectStore(Protocol):
    def info(self, bucket: str, name: str) -> ObjectInfo | None: ...
    def read(self, bucket: str, name: str, generation: int) -> bytes: ...
    def write(self, bucket: str, name: str, data: bytes, *, if_generation_match: int) -> ObjectInfo: ...
    def delete(self, bucket: str, name: str, *, if_generation_match: int) -> None: ...
    def has_any_version(self, bucket: str, name: str) -> bool: ...
    def has_objects(self, bucket: str) -> bool: ...


# (method, url, headers, body) -> (status, body); raises TransientStorageError on a network failure
Transport = Callable[[str, str, Mapping[str, str], bytes | None], tuple[int, bytes]]


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_: object, **__: object) -> None:
        return None  # urllib then raises HTTPError with the 3xx status


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _RefuseRedirects())  # no env proxies


def urllib_transport(timeout: float) -> Transport:
    def send(method: str, url: str, headers: Mapping[str, str], body: bytes | None) -> tuple[int, bytes]:
        request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)  # noqa: S310 - fixed hosts
        try:
            with _OPENER.open(request, timeout=timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()
        except (TimeoutError, urllib.error.URLError, ConnectionError, http.client.HTTPException) as error:
            raise TransientStorageError(f"{method} {url.split('?', maxsplit=1)[0]}: {error}") from error

    return send


def metadata_token(transport: Transport) -> str:
    """The job identity's access token from the metadata server."""
    status, body = transport("GET", _METADATA_TOKEN, {"Metadata-Flavor": "Google"}, None)
    if status != 200:  # noqa: PLR2004
        raise _error(status, "metadata token")
    token = json.loads(body).get("access_token")
    if not isinstance(token, str) or not token:
        raise StorageError("metadata token: no access_token in the response")
    return token


def _error(status: int, what: str, *, missing_is_precondition: bool = True) -> StorageError:
    """404 means "that object or generation is gone" for reads and deletes; on a write it can
    only mean a missing bucket, which is a misconfiguration, never a lost race."""
    if status in _RETRYABLE:
        return TransientStorageError(f"{what}: HTTP {status}")
    if status == 412 or (status == 404 and missing_is_precondition):  # noqa: PLR2004
        return PreconditionFailedError(f"{what}: HTTP {status}")
    return StorageError(f"{what}: HTTP {status}")


def _parse_info(body: bytes, what: str) -> ObjectInfo:
    try:
        raw = json.loads(body)
        created = datetime.fromisoformat(raw["timeCreated"])
        info = ObjectInfo(
            generation=int(raw["generation"]), md5=str(raw["md5Hash"]), created=created.astimezone(UTC)
        )
    except (ValueError, KeyError, TypeError) as error:
        raise StorageError(f"{what}: unexpected metadata ({type(error).__name__})") from error
    if created.tzinfo is None or info.generation <= 0:
        raise StorageError(f"{what}: unexpected metadata")
    return info


class GcsObjectStore:
    def __init__(self, transport: Transport, token: Callable[[], str]) -> None:
        self._send = transport
        self._token = token

    def _call(self, method: str, url: str, body: bytes | None = None) -> tuple[int, bytes]:
        headers = {"Authorization": f"Bearer {self._token()}"}
        if body is not None:
            headers["Content-Type"] = "application/octet-stream"
        return self._send(method, url, headers, body)

    @staticmethod
    def _object_url(bucket: str, name: str, **params: str | int) -> str:
        url = f"{_API}{urllib.parse.quote(bucket, safe='')}/o/{urllib.parse.quote(name, safe='')}"
        return url + ("?" + urllib.parse.urlencode(params) if params else "")

    def info(self, bucket: str, name: str) -> ObjectInfo | None:
        status, body = self._call("GET", self._object_url(bucket, name))
        if status == 404:  # noqa: PLR2004
            return None
        if status != 200:  # noqa: PLR2004
            raise _error(status, f"info {name}")
        return _parse_info(body, f"info {name}")

    def read(self, bucket: str, name: str, generation: int) -> bytes:
        status, body = self._call("GET", self._object_url(bucket, name, alt="media", generation=generation))
        if status != 200:  # noqa: PLR2004
            raise _error(status, f"read {name}")
        return body

    def write(self, bucket: str, name: str, data: bytes, *, if_generation_match: int) -> ObjectInfo:
        query = urllib.parse.urlencode(
            {"uploadType": "media", "name": name, "ifGenerationMatch": if_generation_match}
        )
        status, body = self._call("POST", f"{_UPLOAD}{urllib.parse.quote(bucket, safe='')}/o?{query}", data)
        if status != 200:  # noqa: PLR2004
            raise _error(status, f"write {name}", missing_is_precondition=False)
        return _parse_info(body, f"write {name}")

    def delete(self, bucket: str, name: str, *, if_generation_match: int) -> None:
        status, _ = self._call(
            "DELETE", self._object_url(bucket, name, ifGenerationMatch=if_generation_match)
        )
        if status not in {200, 204}:
            raise _error(status, f"delete {name}")

    def has_objects(self, bucket: str) -> bool:
        """Whether the bucket holds any live object (pause requests)."""
        query = urllib.parse.urlencode({"maxResults": 1, "fields": "items(name),nextPageToken"})
        status, body = self._call("GET", f"{_API}{urllib.parse.quote(bucket, safe='')}/o?{query}")
        if status != 200:  # noqa: PLR2004
            raise _error(status, "list objects", missing_is_precondition=False)
        try:
            page = json.loads(body)
            return bool(
                page.get("items") or page.get("nextPageToken")
            )  # an empty page with more: fail closed
        except (ValueError, AttributeError) as error:
            raise StorageError("list objects: unexpected response") from error

    def has_any_version(self, bucket: str, name: str) -> bool:
        """Live, noncurrent or soft-deleted: any trace that the object existed (ADR-0017)."""
        return self._listed(bucket, name, versions="true") or self._listed(bucket, name, softDeleted="true")

    def _listed(self, bucket: str, name: str, **flags: str) -> bool:
        query = urllib.parse.urlencode({"prefix": name, **flags, "fields": "items(name)"})
        status, body = self._call("GET", f"{_API}{urllib.parse.quote(bucket, safe='')}/o?{query}")
        if status != 200:  # noqa: PLR2004
            raise _error(status, f"list {name}")
        try:
            items = json.loads(body).get("items", [])
            return any(item.get("name") == name for item in items)
        except (ValueError, AttributeError) as error:
            raise StorageError(f"list {name}: unexpected response") from error
