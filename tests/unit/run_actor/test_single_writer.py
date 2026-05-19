"""Single-writer invariance for :class:`RunActor`.

Concurrent submitters MUST observe a total order: events come out with
strictly monotonic sequence numbers, and each event is applied exactly
once.
"""

from __future__ import annotations

import asyncio

import pytest

from bernstein.core.orchestration.run_actor import Event, RunActor


@pytest.mark.asyncio
async def test_concurrent_writers_observe_total_order() -> None:
    """N concurrent writers produce a strictly monotonic seq log."""
    actor = RunActor("sess-1")
    await actor.start()
    try:
        writers = 16
        per_writer = 25
        total = writers * per_writer

        async def writer(writer_id: int) -> list[int]:
            seqs: list[int] = []
            for i in range(per_writer):
                seq = await actor.submit_and_wait(
                    Event(
                        kind="task_progress",
                        payload={"task_id": f"w{writer_id}-t{i}", "step": i},
                        source=f"writer-{writer_id}",
                    )
                )
                seqs.append(seq)
            return seqs

        results = await asyncio.gather(*(writer(w) for w in range(writers)))
        assigned = sorted(seq for run in results for seq in run)
        assert assigned == list(range(1, total + 1)), "seqs must be 1..N exactly once"

        # No event lost: state should reflect exactly `total` applied events
        # (each writer writes distinct task_ids).
        snap = actor.snapshot()
        assert snap.last_seq == total
        assert len(snap.tasks) == total
    finally:
        await actor.stop()


@pytest.mark.asyncio
async def test_sequence_is_monotonic_under_load() -> None:
    """Even with fire-and-forget submit(), seq is strictly monotonic."""
    actor = RunActor("sess-monotonic")
    await actor.start()
    try:
        for i in range(200):
            await actor.submit(Event(kind="watchdog_tick", payload={"i": i}, source="tick"))

        # Quiesce the queue.
        await actor._queue.join()
        snap = actor.snapshot()
        assert snap.last_seq == 200
    finally:
        await actor.stop()
