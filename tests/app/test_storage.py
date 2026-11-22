"""The Cloud Storage adapter, against a recorded transport (no network)."""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from datetime import UTC, datetime

import pytest

from tradingbot.app import storage
from tradingbot.app.storage import (
    GcsObjectStore,
    ObjectInfo,
    PreconditionFailedError,
    StorageError,
    TransientStorageError,
    metadata_token,
)

META = json.dumps(
    {"generation": "1700", "md5Hash": "abc==", "timeCreated": "2026-11-23T07:15:01.250Z"}
).encode()
INFO = ObjectInfo(generation=1700, md5="abc==", created=datetime(2026, 11, 23, 7, 15, 1, 250000, tzinfo=UTC))


class Recorder:
    def __init__(self, *responses: tuple[int, bytes]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def __call__(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        self.calls.append((method, url, dict(headers), body))
        return self.responses.pop(0)


def gcs(*responses: tuple[int, bytes]) -> tuple[GcsObjectStore, Recorder]:
    recorder = Recorder(*responses)
    return GcsObjectStore(recorder, token=lambda: "tok"), recorder


def test_info_reads_generation_hash_and_creation_time() -> None:
    store, sent = gcs((200, META))
    assert store.info("b", "run.lock") == INFO
    [(method, url, headers, body)] = sent.calls
    assert (method, url, body) == ("GET", "https://storage.googleapis.com/storage/v1/b/b/o/run.lock", None)
    assert headers == {"Authorization": "Bearer tok"}


def test_info_of_a_missing_object_is_none() -> None:
    store, _ = gcs((404, b""))
    assert store.info("b", "run.lock") is None


def test_read_names_the_generation() -> None:
    store, sent = gcs((200, b"data"))
    assert store.read("b", "ledger.sqlite", 1700) == b"data"
    assert sent.calls[0][1].endswith("/o/ledger.sqlite?alt=media&generation=1700")


def test_write_is_conditional_on_the_generation() -> None:
    store, sent = gcs((200, META))
    assert store.write("b", "ledger.sqlite", b"db", if_generation_match=0) == INFO
    [(method, url, headers, body)] = sent.calls
    assert method == "POST"
    assert url == (
        "https://storage.googleapis.com/upload/storage/v1/b/b/o?uploadType=media&name=ledger.sqlite&ifGenerationMatch=0"
    )
    assert headers["Content-Type"] == "application/octet-stream"
    assert body == b"db"


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (412, PreconditionFailedError),
        (503, TransientStorageError),
        (429, TransientStorageError),
        (403, StorageError),
        (302, StorageError),  # a redirect is never followed
    ],
)
def test_write_failures_are_classified(status: int, error: type[StorageError]) -> None:
    store, _ = gcs((status, b""))
    with pytest.raises(error):
        store.write("b", "ledger.sqlite", b"db", if_generation_match=5)


def test_a_write_to_a_missing_bucket_is_not_a_lost_race() -> None:
    store, _ = gcs((404, b""))
    with pytest.raises(StorageError) as raised:
        store.write("no-such-bucket", "run.lock", b"x", if_generation_match=0)
    assert not isinstance(raised.value, TransientStorageError | PreconditionFailedError)


def test_a_broken_response_is_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_: object, **__: object) -> None:
        raise http.client.IncompleteRead(b"")

    monkeypatch.setattr(storage._OPENER, "open", fail)
    with pytest.raises(TransientStorageError):
        storage.urllib_transport(1.0)("POST", "https://storage.googleapis.com/x", {}, b"")


def test_a_403_is_not_retryable() -> None:
    store, _ = gcs((403, b""))
    with pytest.raises(StorageError) as raised:
        store.info("b", "run.lock")
    assert not isinstance(raised.value, TransientStorageError | PreconditionFailedError)


def test_delete_is_conditional_on_the_generation() -> None:
    store, sent = gcs((204, b""))
    store.delete("b", "run.lock", if_generation_match=1700)
    assert sent.calls[0][:2] == (
        "DELETE",
        "https://storage.googleapis.com/storage/v1/b/b/o/run.lock?ifGenerationMatch=1700",
    )


def test_delete_of_another_generation_fails() -> None:
    store, _ = gcs((412, b""))
    with pytest.raises(PreconditionFailedError):
        store.delete("b", "run.lock", if_generation_match=1700)


def test_a_noncurrent_version_counts_as_a_version() -> None:
    store, sent = gcs((200, json.dumps({"items": [{"name": "ledger.sqlite"}]}).encode()))
    assert store.has_any_version("b", "ledger.sqlite")
    assert "versions=true" in sent.calls[0][1]


def test_objects_that_only_share_the_prefix_dont_count() -> None:
    bak = json.dumps({"items": [{"name": "ledger.sqlite.bak"}]}).encode()
    store, _ = gcs((200, bak), (200, bak))
    assert not store.has_any_version("b", "ledger.sqlite")


def test_an_empty_listing_has_no_versions() -> None:
    store, _ = gcs((200, b"{}"), (200, b"{}"))
    assert not store.has_any_version("b", "ledger.sqlite")


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"{}",
        json.dumps({"generation": "x", "md5Hash": "a", "timeCreated": "2026-11-23T07:15:00Z"}).encode(),
    ],
)
def test_malformed_metadata_is_an_error(body: bytes) -> None:
    store, _ = gcs((200, body))
    with pytest.raises(StorageError, match="unexpected metadata"):
        store.info("b", "run.lock")


def test_metadata_without_a_time_zone_is_an_error() -> None:
    store, _ = gcs(
        (200, json.dumps({"generation": "1", "md5Hash": "a", "timeCreated": "2026-11-23T07:15:00"}).encode())
    )
    with pytest.raises(StorageError, match="unexpected metadata"):
        store.info("b", "run.lock")


def test_names_are_escaped_in_urls() -> None:
    store, sent = gcs((404, b""))
    store.info("my bucket", "a/b")
    assert sent.calls[0][1] == "https://storage.googleapis.com/storage/v1/b/my%20bucket/o/a%2Fb"


def test_the_metadata_token_is_read_with_the_metadata_header() -> None:
    sent = Recorder((200, b'{"access_token": "t1"}'))
    assert metadata_token(sent) == "t1"
    assert sent.calls[0][2] == {"Metadata-Flavor": "Google"}


@pytest.mark.parametrize("response", [(500, b""), (200, b"{}")])
def test_a_missing_metadata_token_is_an_error(response: tuple[int, bytes]) -> None:
    with pytest.raises(StorageError):
        metadata_token(Recorder(response))


def test_the_transport_refuses_redirects() -> None:
    handler = storage._RefuseRedirects()
    request = urllib.request.Request("https://storage.googleapis.com/x")
    assert handler.redirect_request(request, None, 302, "Found", {}, "https://elsewhere.example/") is None


def test_the_transport_maps_network_failures_to_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_: object, **__: object) -> None:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(storage._OPENER, "open", fail)
    with pytest.raises(TransientStorageError, match="connection refused"):
        storage.urllib_transport(1.0)("GET", "https://storage.googleapis.com/x?token=no", {}, None)


def test_a_soft_deleted_version_counts_as_a_version() -> None:
    store, sent = gcs((200, b"{}"), (200, json.dumps({"items": [{"name": "ledger.sqlite"}]}).encode()))
    assert store.has_any_version("b", "ledger.sqlite")
    assert "softDeleted=true" in sent.calls[1][1]


def test_no_live_noncurrent_or_soft_deleted_version_means_none() -> None:
    store, sent = gcs((200, b"{}"), (200, b"{}"))
    assert not store.has_any_version("b", "ledger.sqlite")
    assert len(sent.calls) == 2
