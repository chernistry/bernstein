"""Snapshot reads on :class:`RunActor`.

A reader gets a stable, frozen view of state. Mutations submitted after
the snapshot was taken do not retroactively change the snapshot.
"""

from __future__ import annotations

import asyncio
import dataclasses

import pytest

from bernstein.core.orchestration.run_actor import Event, RunActor


@pytest.mark.asyncio
async def test_snapshot_is_stable_across_subsequent_writes() -> None:
    actor = RunActor("sess-snap")
    await actor.start()
    try:
        await actor.submit_and_wait(Event(kind="task_started", payload={"task_id": "A"}, source="t"))
        snap_a = actor.snapshot()

        await actor.submit_and_wait(Event(kind="task_completed", payload={"task_id": "A"}, source="t"))
        snap_b = actor.snapshot()

        # snap_a is the previous state object and is frozen.
        assert dataclasses.is_dataclass(snap_a)
        with pytest.raises(dataclasses.FrozenInstanceError):
            snap_a.last_seq = 99  # type: ignore[misc]

        assert snap_a.tasks["A"]["status"] == "running"
        assert snap_b.tasks["A"]["status"] == "done"
        assert snap_a is not snap_b
    finally:
        await actor.stop()


@pytest.mark.asyncio
async def test_snapshot_after_no_writes() -> None:
    actor = RunActor("sess-empty")
    await actor.start()
    try:
        snap = actor.snapshot()
        assert snap.session_id == "sess-empty"
        assert snap.last_seq == 0
        assert snap.status == "pending"
        assert snap.tasks == {}
    finally:
        await actor.stop()


@pytest.mark.asyncio
async def test_concurrent_readers_see_consistent_state() -> None:
    actor = RunActor("sess-readers")
    await actor.start()
    try:
        for i in range(50):
            await actor.submit_and_wait(
                Event(
                    kind="task_progress",
                    payload={"task_id": f"T{i}", "step": i},
                )
            )

        async def reader() -> int:
            snap = actor.snapshot()
            await asyncio.sleep(0)
            return snap.last_seq

        seqs = await asyncio.gather(*(reader() for _ in range(10)))
        # All readers see a coherent (post-writes) state.
        assert all(s == 50 for s in seqs)
    finally:
        await actor.stop()
