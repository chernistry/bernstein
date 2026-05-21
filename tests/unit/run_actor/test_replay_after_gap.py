"""Replay after the bounded buffer has evicted events.

A subscriber that requests a range below the buffer's oldest stored
sequence MUST receive exactly one :class:`Gap` marker followed by every
event the buffer still holds - never a silently truncated list.
"""

from __future__ import annotations

import pytest

from bernstein.core.orchestration.run_actor import (
    Event,
    Gap,
    ReplayBuffer,
    RunActor,
)


def _stamp(events: list[Event]) -> list[Event]:
    """Assign monotonic seq=1..N for buffer-only tests."""
    out = []
    for i, ev in enumerate(events, start=1):
        out.append(Event(kind=ev.kind, payload=ev.payload, seq=i, source=ev.source))
    return out


def test_buffer_within_capacity_returns_no_gap() -> None:
    buf = ReplayBuffer(capacity=8)
    for e in _stamp([Event(kind="watchdog_tick") for _ in range(5)]):
        buf.append(e)

    items = buf.since(2)
    assert all(not isinstance(i, Gap) for i in items)
    assert [i.seq for i in items] == [3, 4, 5]  # type: ignore[union-attr]


def test_buffer_evicted_returns_gap_then_remaining_events() -> None:
    buf = ReplayBuffer(capacity=4)
    # Append 10; buffer keeps last 4 (seqs 7..10).
    for e in _stamp([Event(kind="watchdog_tick") for _ in range(10)]):
        buf.append(e)

    # Subscriber says "I've seen seq=3"; the range 4..6 is evicted.
    items = buf.since(3)
    assert len(items) >= 1
    assert isinstance(items[0], Gap)
    gap = items[0]
    # gap.up_to_seq == 6 because oldest held is 7.
    assert gap.up_to_seq == 6
    # Remaining items are exactly the events still in the buffer.
    tail = items[1:]
    assert [i.seq for i in tail] == [7, 8, 9, 10]  # type: ignore[union-attr]


def test_buffer_request_at_or_above_latest_returns_empty() -> None:
    buf = ReplayBuffer(capacity=4)
    for e in _stamp([Event(kind="watchdog_tick") for _ in range(4)]):
        buf.append(e)
    assert buf.since(4) == []
    assert buf.since(99) == []


def test_buffer_exactly_one_gap_even_with_huge_gap() -> None:
    buf = ReplayBuffer(capacity=4)
    for e in _stamp([Event(kind="watchdog_tick") for _ in range(100)]):
        buf.append(e)
    items = buf.since(1)
    gaps = [i for i in items if isinstance(i, Gap)]
    assert len(gaps) == 1, "exactly one Gap marker even when far behind"


@pytest.mark.asyncio
async def test_actor_replay_after_gap_end_to_end() -> None:
    actor = RunActor("sess-gap", replay_capacity=4)
    await actor.start()
    try:
        # Submit 10 events; buffer keeps last 4 (seqs 7..10).
        for i in range(10):
            await actor.submit_and_wait(Event(kind="watchdog_tick", payload={"i": i}))

        # A subscriber comes back claiming last-seen = 2.
        items = await actor.since(2)
        assert isinstance(items[0], Gap)
        assert items[0].up_to_seq == 6
        tail = items[1:]
        assert [i.seq for i in tail] == [7, 8, 9, 10]  # type: ignore[union-attr]

        # And a fresh subscriber (last_seen=8) still inside the window:
        items_ok = await actor.since(8)
        assert all(not isinstance(i, Gap) for i in items_ok)
        assert [i.seq for i in items_ok] == [9, 10]  # type: ignore[union-attr]
    finally:
        await actor.stop()
