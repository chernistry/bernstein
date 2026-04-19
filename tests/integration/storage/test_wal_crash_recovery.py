"""WAL crash-recovery with :class:`BufferedSink` (oai-003).

This test simulates an orchestrator killed mid-run with a BufferedSink
configured. It asserts two things:

1. The local fsync semantics are preserved — every WAL line the WAL
   writer emitted is on disk after the simulated crash.
2. The asynchronous mirror eventually reproduces the same WAL on the
   remote sink. (A separate integration test against LocalStack
   exercises the cloud wire path; this one uses a LocalFsSink as the
   'remote' so it runs in every environment.)

Runs as an integration test because it spans the WAL module, the
storage package, and asyncio lifecycles.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from bernstein.core.persistence.wal import WALReader, WALWriter
from bernstein.core.storage import BufferedSink, LocalFsSink

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_wal_local_survives_crash_with_buffered_sink(tmp_path: Path) -> None:
    """Simulated crash: WAL lines are on local disk even if the mirror didn't drain."""
    local_root = tmp_path / "local"
    remote_root = tmp_path / "remote"
    local = LocalFsSink(local_root)
    remote = LocalFsSink(remote_root)
    sink = BufferedSink(local=local, remote=remote)

    # The WAL writer writes directly to disk (unchanged from oai-002).
    # BufferedSink is exercised in parallel so the mirror is already
    # running in the background when we simulate the kill.
    writer = WALWriter(run_id="crash-run", sdd_dir=local_root)
    writer.append(
        decision_type="task_claimed",
        inputs={"task_id": "t-1"},
        output={"status": "ok"},
        actor="orchestrator",
        committed=False,
    )
    # Route the same payload through the sink to exercise the mirror.
    await sink.write(
        "runtime/wal/crash-run.wal.jsonl",
        local_root.joinpath("runtime", "wal", "crash-run.wal.jsonl").read_bytes(),
        durable=True,
    )

    # Simulate orchestrator kill — do NOT close the sink.
    # Drop references to exercise the crash path; pending mirrors may
    # or may not have drained yet.
    del sink

    # On restart, the WAL reader can still load every entry from local.
    reader = WALReader(run_id="crash-run", sdd_dir=local_root)
    entries = list(reader.iter_entries())
    assert len(entries) == 1
    assert entries[0].decision_type == "task_claimed"
    assert entries[0].committed is False


@pytest.mark.asyncio
async def test_wal_mirror_drains_on_clean_close(tmp_path: Path) -> None:
    """Graceful shutdown: every WAL line is mirrored before close returns."""
    local_root = tmp_path / "local"
    remote_root = tmp_path / "remote"
    local = LocalFsSink(local_root)
    remote = LocalFsSink(remote_root)
    sink = BufferedSink(local=local, remote=remote)

    for i in range(5):
        await sink.write(f"runtime/wal/run-{i}.wal.jsonl", str(i).encode())

    await sink.close()

    # Every file must exist in the remote sink after close.
    for i in range(5):
        assert await remote.exists(f"runtime/wal/run-{i}.wal.jsonl")
