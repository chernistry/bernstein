"""Purity of :func:`apply_event`.

The reducer must be deterministic and must not mutate inputs.
"""

from __future__ import annotations

from dataclasses import replace

from bernstein.core.orchestration.run_actor import (
    Event,
    RunState,
    apply_event,
    fold,
)


def test_apply_event_does_not_mutate_inputs() -> None:
    state = RunState(session_id="s")
    event = Event(kind="session_started", seq=1, source="t")
    snapshot_before = replace(state)
    new = apply_event(state, event)
    assert state == snapshot_before, "input state was mutated"
    assert new is not state
    assert new.status == "running"
    assert new.last_seq == 1


def test_apply_event_is_deterministic() -> None:
    a = RunState(session_id="s")
    b = RunState(session_id="s")
    e1 = Event(kind="task_started", payload={"task_id": "T1"}, seq=1)
    e2 = Event(kind="task_completed", payload={"task_id": "T1"}, seq=2)
    final_a = apply_event(apply_event(a, e1), e2)
    final_b = apply_event(apply_event(b, e1), e2)
    assert final_a == final_b
    assert final_a.tasks["T1"]["status"] == "done"


def test_out_of_order_event_is_rejected() -> None:
    state = RunState(session_id="s")
    # seq=2 with last_seq=0 is out of order.
    e = Event(kind="task_started", payload={"task_id": "T1"}, seq=2)
    out = apply_event(state, e)
    assert out == state, "out-of-order event must return state unchanged"


def test_fold_reconstructs_from_event_log() -> None:
    events = [
        Event(kind="session_started", seq=1),
        Event(kind="task_started", payload={"task_id": "A"}, seq=2),
        Event(kind="task_completed", payload={"task_id": "A"}, seq=3),
        Event(kind="approval_requested", payload={"approval_id": "AP1"}, seq=4),
        Event(kind="approval_granted", payload={"approval_id": "AP1"}, seq=5),
        Event(kind="session_ended", payload={"status": "done"}, seq=6),
    ]
    final = fold(events, RunState(session_id="s"))
    assert final.status == "done"
    assert final.last_seq == 6
    assert final.tasks["A"]["status"] == "done"
    assert final.approvals["AP1"] == "granted"
