"""Tests for the Lifecycle Governance Kernel.

Covers:
- Every allowed task transition
- Every allowed agent transition
- Disallowed task transitions (at least 5)
- Disallowed agent transitions
- LifecycleEvent emission
- Listener registration / removal
- Property-based: random transition sequences never reach an illegal state
"""

from __future__ import annotations

import random
import time

import pytest

from bernstein.core.lifecycle import (
    AGENT_TRANSITIONS,
    TASK_TRANSITIONS,
    IllegalTransitionError,
    add_listener,
    remove_listener,
    transition_agent,
    transition_task,
)
from bernstein.core.models import AgentSession, LifecycleEvent, Task, TaskStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(status: TaskStatus = TaskStatus.OPEN, task_id: str = "t-001") -> Task:
    return Task(id=task_id, title="test", description="test task", role="backend", status=status)


def _make_agent(status: str = "starting", agent_id: str = "a-001") -> AgentSession:
    return AgentSession(id=agent_id, role="backend", status=status)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Allowed task transitions — one test per edge
# ---------------------------------------------------------------------------


class TestAllowedTaskTransitions:
    """Every entry in TASK_TRANSITIONS must succeed and emit an event."""

    @pytest.mark.parametrize(
        "from_status,to_status",
        list(TASK_TRANSITIONS.keys()),
        ids=[f"{f.value}->{t.value}" for f, t in TASK_TRANSITIONS],
    )
    def test_allowed_transition(self, from_status: TaskStatus, to_status: TaskStatus) -> None:
        task = _make_task(status=from_status)
        events: list[LifecycleEvent] = []
        add_listener(events.append)
        try:
            event = transition_task(task, to_status, actor="test", reason="unit test")
            assert task.status == to_status
            assert event.entity_type == "task"
            assert event.from_status == from_status.value
            assert event.to_status == to_status.value
            assert event.entity_id == "t-001"
            assert event.actor == "test"
            assert event.reason == "unit test"
            assert len(events) == 1
            assert events[0] is event
        finally:
            remove_listener(events.append)


# ---------------------------------------------------------------------------
# Disallowed task transitions
# ---------------------------------------------------------------------------


class TestDisallowedTaskTransitions:
    """Transitions NOT in the table must raise IllegalTransitionError."""

    @pytest.mark.parametrize(
        "from_status,to_status",
        [
            # DONE is terminal
            (TaskStatus.DONE, TaskStatus.OPEN),
            (TaskStatus.DONE, TaskStatus.FAILED),
            (TaskStatus.DONE, TaskStatus.CLAIMED),
            # CANCELLED is terminal
            (TaskStatus.CANCELLED, TaskStatus.OPEN),
            (TaskStatus.CANCELLED, TaskStatus.DONE),
            # OPEN cannot skip to DONE
            (TaskStatus.OPEN, TaskStatus.DONE),
            # OPEN cannot skip to IN_PROGRESS
            (TaskStatus.OPEN, TaskStatus.IN_PROGRESS),
            # OPEN cannot go to FAILED directly
            (TaskStatus.OPEN, TaskStatus.FAILED),
            # PLANNED cannot go to CLAIMED
            (TaskStatus.PLANNED, TaskStatus.CLAIMED),
            # FAILED cannot go to DONE directly
            (TaskStatus.FAILED, TaskStatus.DONE),
        ],
        ids=[
            "DONE->OPEN",
            "DONE->FAILED",
            "DONE->CLAIMED",
            "CANCELLED->OPEN",
            "CANCELLED->DONE",
            "OPEN->DONE",
            "OPEN->IN_PROGRESS",
            "OPEN->FAILED",
            "PLANNED->CLAIMED",
            "FAILED->DONE",
        ],
    )
    def test_disallowed_transition(self, from_status: TaskStatus, to_status: TaskStatus) -> None:
        task = _make_task(status=from_status)
        with pytest.raises(IllegalTransitionError) as exc_info:
            transition_task(task, to_status)
        assert exc_info.value.entity_type == "task"
        assert exc_info.value.from_status == from_status.value
        assert exc_info.value.to_status == to_status.value
        # Status must NOT have changed
        assert task.status == from_status


# ---------------------------------------------------------------------------
# Allowed agent transitions
# ---------------------------------------------------------------------------


class TestAllowedAgentTransitions:
    """Every entry in AGENT_TRANSITIONS must succeed and emit an event."""

    @pytest.mark.parametrize(
        "from_status,to_status",
        list(AGENT_TRANSITIONS.keys()),
        ids=[f"{f}->{t}" for f, t in AGENT_TRANSITIONS],
    )
    def test_allowed_transition(self, from_status: str, to_status: str) -> None:
        agent = _make_agent(status=from_status)
        events: list[LifecycleEvent] = []
        add_listener(events.append)
        try:
            event = transition_agent(agent, to_status, actor="test", reason="unit test")  # type: ignore[arg-type]
            assert agent.status == to_status
            assert event.entity_type == "agent"
            assert event.from_status == from_status
            assert event.to_status == to_status
            assert len(events) == 1
        finally:
            remove_listener(events.append)


# ---------------------------------------------------------------------------
# Disallowed agent transitions
# ---------------------------------------------------------------------------


class TestDisallowedAgentTransitions:
    """Agent transitions NOT in the table must raise IllegalTransitionError."""

    @pytest.mark.parametrize(
        "from_status,to_status",
        [
            ("dead", "working"),
            ("dead", "starting"),
            ("dead", "idle"),
            ("working", "starting"),
            ("idle", "starting"),
            ("starting", "idle"),
        ],
        ids=[
            "dead->working",
            "dead->starting",
            "dead->idle",
            "working->starting",
            "idle->starting",
            "starting->idle",
        ],
    )
    def test_disallowed_transition(self, from_status: str, to_status: str) -> None:
        agent = _make_agent(status=from_status)
        with pytest.raises(IllegalTransitionError):
            transition_agent(agent, to_status)  # type: ignore[arg-type]
        assert agent.status == from_status


# ---------------------------------------------------------------------------
# Event emission and listeners
# ---------------------------------------------------------------------------


class TestEventEmission:
    """Verify LifecycleEvent correctness and listener management."""

    def test_event_timestamp_is_recent(self) -> None:
        before = time.time()
        task = _make_task(TaskStatus.OPEN)
        event = transition_task(task, TaskStatus.CLAIMED, actor="ts")
        after = time.time()
        assert before <= event.timestamp <= after

    def test_listener_removal(self) -> None:
        events: list[LifecycleEvent] = []
        add_listener(events.append)
        remove_listener(events.append)
        task = _make_task(TaskStatus.OPEN)
        transition_task(task, TaskStatus.CLAIMED)
        assert len(events) == 0

    def test_remove_unregistered_listener_is_noop(self) -> None:
        remove_listener(lambda _e: None)  # should not raise

    def test_listener_exception_does_not_break_transition(self) -> None:
        def _bad_listener(_event: LifecycleEvent) -> None:
            raise RuntimeError("boom")

        add_listener(_bad_listener)
        try:
            task = _make_task(TaskStatus.OPEN)
            transition_task(task, TaskStatus.CLAIMED)
            assert task.status == TaskStatus.CLAIMED
        finally:
            remove_listener(_bad_listener)


# ---------------------------------------------------------------------------
# IllegalTransitionError attributes
# ---------------------------------------------------------------------------


class TestIllegalTransitionError:
    """Verify error attributes for downstream handling."""

    def test_error_attributes(self) -> None:
        task = _make_task(TaskStatus.DONE)
        with pytest.raises(IllegalTransitionError) as exc_info:
            transition_task(task, TaskStatus.OPEN)
        err = exc_info.value
        assert err.entity_type == "task"
        assert err.entity_id == "t-001"
        assert err.from_status == "done"
        assert err.to_status == "open"
        assert "done" in str(err) and "open" in str(err)


# ---------------------------------------------------------------------------
# Property test: random walks never reach illegal state
# ---------------------------------------------------------------------------


class TestRandomWalkInvariant:
    """Random transition sequences stay within the allowed FSM."""

    def test_random_task_walk(self) -> None:
        """Perform 500 random attempts; only allowed transitions mutate state."""
        all_statuses = list(TaskStatus)
        task = _make_task(TaskStatus.OPEN)
        rng = random.Random(42)

        for _ in range(500):
            target = rng.choice(all_statuses)
            old = task.status
            key = (old, target)
            if key in TASK_TRANSITIONS:
                event = transition_task(task, target, actor="random_walk")
                assert task.status == target
                assert event.from_status == old.value
            else:
                with pytest.raises(IllegalTransitionError):
                    transition_task(task, target)
                assert task.status == old  # unchanged

    def test_random_agent_walk(self) -> None:
        """Perform 500 random attempts on agent status FSM."""
        all_statuses = ["starting", "working", "idle", "dead"]
        agent = _make_agent("starting")
        rng = random.Random(99)

        for _ in range(500):
            target = rng.choice(all_statuses)
            old = agent.status
            key = (old, target)
            if key in AGENT_TRANSITIONS:
                event = transition_agent(agent, target, actor="random_walk")  # type: ignore[arg-type]
                assert agent.status == target
                assert event.from_status == old
            else:
                with pytest.raises(IllegalTransitionError):
                    transition_agent(agent, target)  # type: ignore[arg-type]
                assert agent.status == old


# ---------------------------------------------------------------------------
# Exhaustive disallowed enumeration
# ---------------------------------------------------------------------------


class TestExhaustiveDisallowed:
    """Every (from, to) pair NOT in the table must raise."""

    def test_all_disallowed_task_pairs(self) -> None:
        all_statuses = list(TaskStatus)
        for from_s in all_statuses:
            for to_s in all_statuses:
                if (from_s, to_s) in TASK_TRANSITIONS:
                    continue
                task = _make_task(from_s)
                with pytest.raises(IllegalTransitionError):
                    transition_task(task, to_s)
                assert task.status == from_s

    def test_all_disallowed_agent_pairs(self) -> None:
        all_statuses = ["starting", "working", "idle", "dead"]
        for from_s in all_statuses:
            for to_s in all_statuses:
                if (from_s, to_s) in AGENT_TRANSITIONS:
                    continue
                agent = _make_agent(from_s)
                with pytest.raises(IllegalTransitionError):
                    transition_agent(agent, to_s)  # type: ignore[arg-type]
                assert agent.status == from_s
