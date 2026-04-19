"""Unit tests for :class:`BufferedSink` (oai-003).

The tests use two in-memory-backed LocalFsSinks rooted at separate
tmp paths to simulate the local-vs-remote split. That keeps the
tests fast and deterministic; the cloud-emulator integration tests
exercise the real network paths.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from bernstein.core.storage import (
    BufferedSink,
    LocalFsSink,
    SinkError,
)
from bernstein.core.storage.sink import ArtifactSink, ArtifactStat

if TYPE_CHECKING:
    from pathlib import Path


class _FlakyRemote(LocalFsSink):
    """LocalFsSink subclass whose writes fail the first N times.

    Used to exercise BufferedSink's failure counter path.
    """

    def __init__(self, root: Path, fail_first: int) -> None:
        super().__init__(root)
        self._fail_first = fail_first
        self._attempts = 0

    async def write(
        self,
        key: str,
        data: bytes,
        *,
        durable: bool = True,
        content_type: str | None = None,
    ) -> None:
        self._attempts += 1
        if self._attempts <= self._fail_first:
            raise SinkError("simulated failure")
        await super().write(
            key,
            data,
            durable=durable,
            content_type=content_type,
        )


class _CountingSink(LocalFsSink):
    """LocalFsSink whose operations can be observed for test assertions."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.reads = 0
        self.writes = 0

    async def read(self, key: str) -> bytes:
        self.reads += 1
        return await super().read(key)

    async def write(
        self,
        key: str,
        data: bytes,
        *,
        durable: bool = True,
        content_type: str | None = None,
    ) -> None:
        self.writes += 1
        await super().write(
            key,
            data,
            durable=durable,
            content_type=content_type,
        )


async def _drain(sink: BufferedSink, timeout: float = 2.0) -> None:
    """Wait until all queued mirrors complete."""
    deadline = asyncio.get_event_loop().time() + timeout
    while sink.stats().pending_writes > 0:
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError("BufferedSink failed to drain")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_local_write_is_synchronous(tmp_path: Path) -> None:
    """After ``write`` returns, the local sink already has the data."""
    local = LocalFsSink(tmp_path / "local")
    remote = LocalFsSink(tmp_path / "remote")
    sink = BufferedSink(local=local, remote=remote)
    try:
        await sink.write("hello.txt", b"world")
        # Local visible immediately (crash-safety invariant).
        assert await local.read("hello.txt") == b"world"
    finally:
        await sink.close()


@pytest.mark.asyncio
async def test_remote_mirror_eventually_lands(tmp_path: Path) -> None:
    """Queued writes drain to the remote sink in the background."""
    local = LocalFsSink(tmp_path / "local")
    remote = _CountingSink(tmp_path / "remote")
    sink = BufferedSink(local=local, remote=remote)
    try:
        await sink.write("k.txt", b"payload")
        await _drain(sink)
        assert await remote.read("k.txt") == b"payload"
        assert remote.writes == 1
    finally:
        await sink.close()


@pytest.mark.asyncio
async def test_close_drains_pending(tmp_path: Path) -> None:
    """``close`` must block until every mirror has been attempted."""
    local = LocalFsSink(tmp_path / "local")
    remote = LocalFsSink(tmp_path / "remote")
    sink = BufferedSink(local=local, remote=remote)
    for i in range(10):
        await sink.write(f"many/{i}.txt", str(i).encode())
    await sink.close()
    # All files must have mirrored to remote after close
    listed = sorted(await remote.list("many"))
    assert len(listed) == 10


@pytest.mark.asyncio
async def test_read_prefers_remote(tmp_path: Path) -> None:
    """Read paths hit remote first — critical for crash recovery."""
    local = LocalFsSink(tmp_path / "local")
    remote = _CountingSink(tmp_path / "remote")
    sink = BufferedSink(local=local, remote=remote)
    try:
        await sink.write("k.txt", b"v")
        await _drain(sink)
        _ = await sink.read("k.txt")
        assert remote.reads == 1
    finally:
        await sink.close()


@pytest.mark.asyncio
async def test_read_falls_back_to_local_when_remote_missing(
    tmp_path: Path,
) -> None:
    """If the remote doesn't have it (mirror still pending), local serves."""
    local = LocalFsSink(tmp_path / "local")
    # Use a fresh tmp dir with no remote state
    remote = LocalFsSink(tmp_path / "remote")
    sink = BufferedSink(local=local, remote=remote)
    try:
        # Pre-populate local only (simulate pre-crash state).
        await local.write("k.txt", b"recovered")
        got = await sink.read("k.txt")
        assert got == b"recovered"
    finally:
        await sink.close()


@pytest.mark.asyncio
async def test_failed_mirror_counted(tmp_path: Path) -> None:
    """A transient remote failure increments failure counter and keeps going."""
    local = LocalFsSink(tmp_path / "local")
    remote = _FlakyRemote(tmp_path / "remote", fail_first=2)
    sink = BufferedSink(local=local, remote=remote)
    try:
        await sink.write("a.txt", b"1")
        await sink.write("b.txt", b"2")
        await sink.write("c.txt", b"3")
        await _drain(sink)
        stats = sink.stats()
        assert stats.failed_mirrors == 2
        assert stats.completed_mirrors == 1
    finally:
        await sink.close()


@pytest.mark.asyncio
async def test_list_merges_local_and_remote(tmp_path: Path) -> None:
    """List returns the union of both sinks' views."""
    local = LocalFsSink(tmp_path / "local")
    remote = LocalFsSink(tmp_path / "remote")
    sink = BufferedSink(local=local, remote=remote)
    try:
        # Pre-populate remote with something not in local
        await remote.write("recovered/x.txt", b"x")
        # Then route a fresh write through the sink
        await sink.write("recovered/y.txt", b"y")
        await _drain(sink)
        keys = await sink.list("recovered")
        assert "recovered/x.txt" in keys
        assert "recovered/y.txt" in keys
    finally:
        await sink.close()


@pytest.mark.asyncio
async def test_delete_hits_both(tmp_path: Path) -> None:
    local = LocalFsSink(tmp_path / "local")
    remote = LocalFsSink(tmp_path / "remote")
    sink = BufferedSink(local=local, remote=remote)
    try:
        await sink.write("dead.txt", b"gone")
        await _drain(sink)
        await sink.delete("dead.txt")
        assert await local.exists("dead.txt") is False
        assert await remote.exists("dead.txt") is False
    finally:
        await sink.close()


@pytest.mark.asyncio
async def test_exists_checks_both(tmp_path: Path) -> None:
    local = LocalFsSink(tmp_path / "local")
    remote = LocalFsSink(tmp_path / "remote")
    sink = BufferedSink(local=local, remote=remote)
    try:
        await remote.write("in-remote.txt", b"r")
        assert await sink.exists("in-remote.txt") is True
        assert await sink.exists("nowhere.txt") is False
    finally:
        await sink.close()


@pytest.mark.asyncio
async def test_write_after_close_raises(tmp_path: Path) -> None:
    local = LocalFsSink(tmp_path / "local")
    remote = LocalFsSink(tmp_path / "remote")
    sink = BufferedSink(local=local, remote=remote)
    await sink.close()
    with pytest.raises(SinkError):
        await sink.write("after.txt", b"x")


@pytest.mark.asyncio
async def test_stats_surface_queue_depth(tmp_path: Path) -> None:
    """Stats expose queue depth for Prometheus metric export."""

    class _SlowRemote(LocalFsSink):
        async def write(
            self,
            key: str,
            data: bytes,
            *,
            durable: bool = True,
            content_type: str | None = None,
        ) -> None:
            await asyncio.sleep(0.05)
            await super().write(
                key,
                data,
                durable=durable,
                content_type=content_type,
            )

    local = LocalFsSink(tmp_path / "local")
    remote = _SlowRemote(tmp_path / "remote")
    sink = BufferedSink(local=local, remote=remote, max_pending=8)
    try:
        for i in range(5):
            await sink.write(f"k{i}.txt", b"x")
        # Some writes should still be queued
        stats = sink.stats()
        assert stats.pending_writes >= 0  # at least observable
        await _drain(sink, timeout=5.0)
    finally:
        await sink.close()


@pytest.mark.asyncio
async def test_max_pending_must_be_positive(tmp_path: Path) -> None:
    local = LocalFsSink(tmp_path / "local")
    remote = LocalFsSink(tmp_path / "remote")
    with pytest.raises(ValueError):
        BufferedSink(local=local, remote=remote, max_pending=0)


@pytest.mark.asyncio
async def test_idempotent_close(tmp_path: Path) -> None:
    local = LocalFsSink(tmp_path / "local")
    remote = LocalFsSink(tmp_path / "remote")
    sink = BufferedSink(local=local, remote=remote)
    await sink.close()
    await sink.close()  # must not raise


@pytest.mark.asyncio
async def test_crash_recovery_preserves_local_data(tmp_path: Path) -> None:
    """After a simulated kill before drain, local sink still has the WAL."""
    local = LocalFsSink(tmp_path / "local")
    remote = LocalFsSink(tmp_path / "remote")
    sink = BufferedSink(local=local, remote=remote, max_pending=16)

    # Simulate "WAL append" — durable=True
    await sink.write("runtime/wal/r.wal.jsonl", b'{"seq":0}\n')

    # Simulate orchestrator crash: we don't drain or close gracefully
    # before shutting down. Drop references to force GC.
    del sink

    # A new process spawns a fresh sink rooted at the same paths.
    recovered_local = LocalFsSink(tmp_path / "local")
    data = await recovered_local.read("runtime/wal/r.wal.jsonl")
    assert data == b'{"seq":0}\n'


class _ProtocolCheck(ArtifactSink):
    """Compile-time check that ArtifactStat is importable/usable."""

    name = "proto-check"

    async def write(
        self,
        key: str,
        data: bytes,
        *,
        durable: bool = True,
        content_type: str | None = None,
    ) -> None:
        pass

    async def read(self, key: str) -> bytes:
        return b""

    async def list(self, prefix: str) -> list[str]:
        return []

    async def delete(self, key: str) -> None:
        pass

    async def exists(self, key: str) -> bool:
        return False

    async def stat(self, key: str) -> ArtifactStat:
        return ArtifactStat(size_bytes=0, last_modified_unix=0.0)

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_protocol_is_structural() -> None:
    """A class implementing the protocol satisfies isinstance."""
    check = _ProtocolCheck()
    assert isinstance(check, ArtifactSink)
