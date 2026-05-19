"""Evicted-reconnect contract.

A client that disconnects, then reconnects past the buffer window, must
observe:

1. Exactly one :class:`Gap` marker.
2. Followed by the current snapshot (i.e. every event still held).

After consuming the gap, the client uses :meth:`RunActor.snapshot` to
realign and then resumes incremental reads from the latest seen seq.
"""

from __future__ import annotations

import pytest

from bernstein.core.orchestration.run_actor import (
    Event,
    Gap,
    RunActor,
)


@pytest.mark.asyncio
async def test_evicted_reconnect_observes_gap_then_realigns_via_snapshot() -> None:
    actor = RunActor("sess-reconnect", replay_capacity=8)
    await actor.start()
    try:
        # Initial run: 5 task_started events.
        for i in range(5):
            await actor.submit_and_wait(Event(kind="task_started", payload={"task_id": f"T{i}"}))
        # Client snapshots and disconnects.
        first_snap = actor.snapshot()
        client_last_seen = first_snap.last_seq
        assert client_last_seen == 5

        # While disconnected: 20 more events. The buffer (capacity 8)
        # has evicted everything from seq 1..17.
        for i in range(5, 25):
            await actor.submit_and_wait(Event(kind="task_progress", payload={"task_id": f"T{i}"}))

        # Client reconnects with its stale last_seen_seq.
        items = await actor.since(client_last_seen)
        assert isinstance(items[0], Gap)
        assert items[0].up_to_seq >= client_last_seen
        # Exactly one Gap.
        assert sum(1 for i in items if isinstance(i, Gap)) == 1

        # All non-Gap items are inside the current buffer window.
        events_after_gap = [i for i in items[1:] if not isinstance(i, Gap)]
        seqs = [e.seq for e in events_after_gap]
        assert seqs == sorted(seqs)
        assert all(s > items[0].up_to_seq for s in seqs)

        # Client realigns via snapshot rather than trying to re-derive
        # missed state from the truncated stream.
        snap = actor.snapshot()
        assert snap.last_seq == 25
        # Resume from snapshot: subsequent reads return only new items.
        items_resume = await actor.since(snap.last_seq)
        assert items_resume == []
    finally:
        await actor.stop()


@pytest.mark.asyncio
async def test_fresh_subscriber_with_seq_zero_gets_no_gap_when_buffer_fits() -> None:
    """Edge case: brand-new subscriber, capacity holds entire history."""
    actor = RunActor("sess-fresh", replay_capacity=64)
    await actor.start()
    try:
        for _ in range(5):
            await actor.submit_and_wait(Event(kind="watchdog_tick"))
        items = await actor.since(0)
        assert all(not isinstance(i, Gap) for i in items)
        assert [i.seq for i in items] == [1, 2, 3, 4, 5]  # type: ignore[union-attr]
    finally:
        await actor.stop()


@pytest.mark.asyncio
async def test_fresh_subscriber_with_seq_zero_after_eviction_gets_gap() -> None:
    """Edge case: brand-new subscriber arrives after eviction."""
    actor = RunActor("sess-fresh-late", replay_capacity=4)
    await actor.start()
    try:
        for _ in range(20):
            await actor.submit_and_wait(Event(kind="watchdog_tick"))
        items = await actor.since(0)
        assert isinstance(items[0], Gap)
        # Subsequent items are the current window only.
        events = items[1:]
        assert len(events) == 4
    finally:
        await actor.stop()
