"""Unit tests for the deterministic schedule projection (#1798).

The projection is the load-bearing property of the recurring-goals
feature: two operators with identical
``(schedule_id, fire_time, last_state)`` MUST land on the byte-identical
task graph and the byte-identical projection_hash.
"""

from __future__ import annotations

import json

import pytest

from bernstein.core.orchestration.schedule_projection import (
    SCHEDULE_PROJECTION_REV,
    project_schedule_fire,
)


class TestProjectionDeterminism:
    def test_same_inputs_byte_identical(self) -> None:
        a = project_schedule_fire(
            schedule_id="sched_alpha",
            fire_time=1_700_000_000,
            last_state=None,
            goal="Send daily digest",
        )
        b = project_schedule_fire(
            schedule_id="sched_alpha",
            fire_time=1_700_000_000,
            last_state=None,
            goal="Send daily digest",
        )
        assert a.canonical_bytes == b.canonical_bytes
        assert a.projection_hash == b.projection_hash

    def test_different_schedule_id_differs(self) -> None:
        a = project_schedule_fire(
            schedule_id="sched_alpha",
            fire_time=1_700_000_000,
            last_state=None,
            goal="g",
        )
        b = project_schedule_fire(
            schedule_id="sched_beta",
            fire_time=1_700_000_000,
            last_state=None,
            goal="g",
        )
        assert a.projection_hash != b.projection_hash

    def test_different_fire_time_differs(self) -> None:
        a = project_schedule_fire(schedule_id="sched_alpha", fire_time=1, last_state=None, goal="g")
        b = project_schedule_fire(schedule_id="sched_alpha", fire_time=2, last_state=None, goal="g")
        assert a.projection_hash != b.projection_hash

    def test_different_last_state_differs(self) -> None:
        a = project_schedule_fire(
            schedule_id="sched_alpha",
            fire_time=1_700_000_000,
            last_state={"key": "A"},
            goal="g",
        )
        b = project_schedule_fire(
            schedule_id="sched_alpha",
            fire_time=1_700_000_000,
            last_state={"key": "B"},
            goal="g",
        )
        assert a.projection_hash != b.projection_hash

    def test_last_state_key_order_independent(self) -> None:
        """Two callers that pass an equal mapping in different insertion
        order MUST still land on the same projection. Python dicts preserve
        insertion order so this is a real failure mode for naive callers.
        """
        a = project_schedule_fire(
            schedule_id="sched_alpha",
            fire_time=1_700_000_000,
            last_state={"a": 1, "b": 2},
            goal="g",
        )
        b = project_schedule_fire(
            schedule_id="sched_alpha",
            fire_time=1_700_000_000,
            last_state={"b": 2, "a": 1},
            goal="g",
        )
        assert a.canonical_bytes == b.canonical_bytes

    def test_rev_baked_into_payload(self) -> None:
        result = project_schedule_fire(
            schedule_id="sched_alpha",
            fire_time=1_700_000_000,
            last_state=None,
            goal="g",
        )
        decoded = json.loads(result.canonical_bytes.decode())
        assert decoded["rev"] == SCHEDULE_PROJECTION_REV


class TestProjectionInputContract:
    def test_float_fire_time_rejected(self) -> None:
        """``fire_time`` MUST be an int.

        Allowing float values lets sub-second jitter fork two operators'
        projections; the AC mandates byte-identical output, so we reject
        the float at the type contract layer.
        """
        with pytest.raises(TypeError):
            project_schedule_fire(
                schedule_id="sched_alpha",
                fire_time=1_700_000_000.5,  # type: ignore[arg-type]
                last_state=None,
                goal="g",
            )

    def test_root_node_present(self) -> None:
        result = project_schedule_fire(
            schedule_id="sched_alpha",
            fire_time=1_700_000_000,
            last_state=None,
            goal="g",
        )
        assert len(result.nodes) == 1
        node = result.nodes[0]
        assert node.task_id.startswith("sched-task-")
        # task_id is deterministic in the schedule id, fire_time, and rev.
        result2 = project_schedule_fire(
            schedule_id="sched_alpha",
            fire_time=1_700_000_000,
            last_state=None,
            goal="g",
        )
        assert result.nodes[0].task_id == result2.nodes[0].task_id

    def test_genesis_digest_when_no_state(self) -> None:
        result = project_schedule_fire(
            schedule_id="sched_alpha",
            fire_time=1_700_000_000,
            last_state=None,
            goal="g",
        )
        assert result.last_state_digest == "genesis"

    def test_scenario_id_baked_into_metadata(self) -> None:
        result = project_schedule_fire(
            schedule_id="sched_alpha",
            fire_time=1_700_000_000,
            last_state=None,
            scenario_id="security-pentest",
        )
        node = result.nodes[0]
        meta = dict(node.metadata)
        assert meta.get("scenario_id") == "security-pentest"

    def test_node_metadata_sorted(self) -> None:
        """metadata sort must be stable so the canonical bytes do not flip
        when the caller emits the same tags in a different order.
        """
        result = project_schedule_fire(
            schedule_id="sched_alpha",
            fire_time=1_700_000_000,
            last_state=None,
            goal="g",
        )
        decoded = json.loads(result.canonical_bytes.decode())
        meta = decoded["nodes"][0]["metadata"]
        # the encoder sorts metadata tuples → list-of-lists; ensure it's sorted.
        sorted_meta = sorted(meta)
        assert meta == sorted_meta


class TestProjectionPurity:
    """The projection function must be pure.

    We assert purity through observable contracts (no environment reads,
    no random output, no time.time() drift). These tests run the same
    inputs many times and check we get the same byte-output.
    """

    def test_repeated_calls_byte_stable(self) -> None:
        previous = None
        for _ in range(8):
            result = project_schedule_fire(
                schedule_id="sched_alpha",
                fire_time=1_700_000_000,
                last_state={"k": "v"},
                goal="Daily digest",
                scenario_id="security-pentest",
            )
            if previous is None:
                previous = result.canonical_bytes
            assert result.canonical_bytes == previous
