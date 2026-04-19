"""Unit tests for :class:`S3ArtifactSink` using a mocked S3 client.

These tests DO NOT call AWS. They pass a ``client_factory`` that
returns a hand-rolled stub, verifying the sink's translation of
protocol operations into ``put_object`` / ``get_object`` calls.

The actual S3 wire protocol is exercised by the gated integration
tests in ``tests/integration/storage/test_s3_sink.py`` (which run
against LocalStack).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest


class _FakeClientError(Exception):
    """Stand-in for ``botocore.exceptions.ClientError``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class _StubBody:
    """Mimics the ``Body`` streaming interface of a GetObject response."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data

    def close(self) -> None:  # pragma: no cover - covered implicitly
        return None


class _StubPaginator:
    """Mimics the S3 paginator — yields dict pages with ``Contents``."""

    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self._pages = pages

    def paginate(self, **_: Any) -> list[dict[str, Any]]:
        return list(self._pages)


class _StubS3Client:
    """Record operations without touching the network."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.store_meta: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        key = kwargs["Key"]
        body = kwargs["Body"]
        self.store[key] = body if isinstance(body, bytes) else bytes(body)
        meta = {
            "ContentLength": len(self.store[key]),
            "ContentType": kwargs.get("ContentType"),
            "LastModified": datetime.now(tz=UTC),
            "ETag": '"stub-etag"',
        }
        self.store_meta[key] = meta
        self.calls.append(("put_object", dict(kwargs)))
        return {}

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:
        self.calls.append(("get_object", {"Bucket": Bucket, "Key": Key}))
        if Key not in self.store:
            raise _FakeClientError("NoSuchKey")
        return {"Body": _StubBody(self.store[Key])}

    def head_object(self, Bucket: str, Key: str) -> dict[str, Any]:
        self.calls.append(("head_object", {"Bucket": Bucket, "Key": Key}))
        if Key not in self.store:
            raise _FakeClientError("404")
        return self.store_meta[Key]

    def delete_object(self, Bucket: str, Key: str) -> dict[str, Any]:
        self.calls.append(("delete_object", {"Bucket": Bucket, "Key": Key}))
        self.store.pop(Key, None)
        self.store_meta.pop(Key, None)
        return {}

    def get_paginator(self, op: str) -> _StubPaginator:
        assert op == "list_objects_v2"
        page = {"Contents": [{"Key": k} for k in sorted(self.store.keys())]}
        return _StubPaginator([page])


@pytest.fixture
def stub_client() -> _StubS3Client:
    return _StubS3Client()


@pytest.fixture(autouse=True)
def patch_botocore_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route S3ArtifactSink's botocore.exceptions import to the stub."""
    import sys
    import types

    module = types.ModuleType("botocore.exceptions")
    module.ClientError = _FakeClientError  # type: ignore[attr-defined]
    botocore_parent = types.ModuleType("botocore")
    botocore_parent.exceptions = module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "botocore", botocore_parent)
    monkeypatch.setitem(sys.modules, "botocore.exceptions", module)


@pytest.mark.asyncio
async def test_write_calls_put_object(stub_client: _StubS3Client) -> None:
    from bernstein.core.storage.sinks.s3 import S3ArtifactSink

    sink = S3ArtifactSink(
        bucket="my-bucket",
        client_factory=lambda: stub_client,
    )
    await sink.write("k.txt", b"hello", content_type="text/plain")
    assert stub_client.store["k.txt"] == b"hello"
    assert stub_client.store_meta["k.txt"]["ContentType"] == "text/plain"


@pytest.mark.asyncio
async def test_write_applies_prefix(stub_client: _StubS3Client) -> None:
    from bernstein.core.storage.sinks.s3 import S3ArtifactSink

    sink = S3ArtifactSink(
        bucket="my-bucket",
        prefix="runs/abc",
        client_factory=lambda: stub_client,
    )
    await sink.write("runtime/state.json", b"{}")
    assert "runs/abc/runtime/state.json" in stub_client.store


@pytest.mark.asyncio
async def test_read_returns_stored_bytes(stub_client: _StubS3Client) -> None:
    from bernstein.core.storage.sinks.s3 import S3ArtifactSink

    sink = S3ArtifactSink(
        bucket="my-bucket",
        client_factory=lambda: stub_client,
    )
    await sink.write("k.txt", b"payload")
    assert await sink.read("k.txt") == b"payload"


@pytest.mark.asyncio
async def test_read_missing_raises_fnf(stub_client: _StubS3Client) -> None:
    from bernstein.core.storage.sinks.s3 import S3ArtifactSink

    sink = S3ArtifactSink(
        bucket="my-bucket",
        client_factory=lambda: stub_client,
    )
    with pytest.raises(FileNotFoundError):
        await sink.read("missing.txt")


@pytest.mark.asyncio
async def test_exists_head_path(stub_client: _StubS3Client) -> None:
    from bernstein.core.storage.sinks.s3 import S3ArtifactSink

    sink = S3ArtifactSink(
        bucket="my-bucket",
        client_factory=lambda: stub_client,
    )
    assert await sink.exists("k.txt") is False
    await sink.write("k.txt", b"x")
    assert await sink.exists("k.txt") is True


@pytest.mark.asyncio
async def test_stat_returns_metadata(stub_client: _StubS3Client) -> None:
    from bernstein.core.storage.sinks.s3 import S3ArtifactSink

    sink = S3ArtifactSink(
        bucket="my-bucket",
        client_factory=lambda: stub_client,
    )
    await sink.write("k.txt", b"abc", content_type="text/plain")
    st = await sink.stat("k.txt")
    assert st.size_bytes == 3
    assert st.etag == "stub-etag"
    assert st.content_type == "text/plain"


@pytest.mark.asyncio
async def test_stat_missing_raises_fnf(stub_client: _StubS3Client) -> None:
    from bernstein.core.storage.sinks.s3 import S3ArtifactSink

    sink = S3ArtifactSink(
        bucket="my-bucket",
        client_factory=lambda: stub_client,
    )
    with pytest.raises(FileNotFoundError):
        await sink.stat("missing.txt")


@pytest.mark.asyncio
async def test_delete_is_idempotent(stub_client: _StubS3Client) -> None:
    from bernstein.core.storage.sinks.s3 import S3ArtifactSink

    sink = S3ArtifactSink(
        bucket="my-bucket",
        client_factory=lambda: stub_client,
    )
    await sink.delete("nope.txt")
    await sink.write("k.txt", b"x")
    await sink.delete("k.txt")
    assert "k.txt" not in stub_client.store


@pytest.mark.asyncio
async def test_list_returns_sorted_keys(stub_client: _StubS3Client) -> None:
    from bernstein.core.storage.sinks.s3 import S3ArtifactSink

    sink = S3ArtifactSink(
        bucket="my-bucket",
        client_factory=lambda: stub_client,
    )
    await sink.write("b.txt", b"b")
    await sink.write("a.txt", b"a")
    keys = await sink.list("")
    assert keys == ["a.txt", "b.txt"]


@pytest.mark.asyncio
async def test_list_strips_prefix_in_results(stub_client: _StubS3Client) -> None:
    from bernstein.core.storage.sinks.s3 import S3ArtifactSink

    sink = S3ArtifactSink(
        bucket="my-bucket",
        prefix="runs/abc",
        client_factory=lambda: stub_client,
    )
    await sink.write("runtime/state.json", b"{}")
    keys = await sink.list("")
    # Result should not leak the sink-level prefix.
    assert keys == ["runtime/state.json"]


@pytest.mark.asyncio
async def test_missing_bucket_raises(stub_client: _StubS3Client) -> None:
    from bernstein.core.storage.sink import SinkError
    from bernstein.core.storage.sinks.s3 import S3ArtifactSink

    # When no bucket is configured the client_factory is still consulted,
    # but we want the explicit error path.
    sink = S3ArtifactSink(client_factory=None)
    with pytest.raises(SinkError, match="bucket"):
        await sink.write("k.txt", b"x")


@pytest.mark.asyncio
async def test_close_idempotent(stub_client: _StubS3Client) -> None:
    from bernstein.core.storage.sinks.s3 import S3ArtifactSink

    sink = S3ArtifactSink(
        bucket="my-bucket",
        client_factory=lambda: stub_client,
    )
    await sink.write("k.txt", b"x")
    await sink.close()
    await sink.close()


@pytest.mark.asyncio
async def test_r2_subclass_sets_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    from bernstein.core.storage.sinks.r2 import R2ArtifactSink

    # Clear potentially leaking AWS_ env vars
    for var in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_REGION",
    ):
        monkeypatch.delenv(var, raising=False)

    sink = R2ArtifactSink(
        bucket="b",
        account_id="acc-123",
        access_key_id="k",
        secret_access_key="s",
    )
    assert sink._endpoint_url == "https://acc-123.r2.cloudflarestorage.com"  # type: ignore[attr-defined]
    assert sink._region == "auto"  # type: ignore[attr-defined]
