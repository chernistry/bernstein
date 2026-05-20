"""Tests for the task DAG walker with explicit parallel-safety batches."""

from __future__ import annotations

import pytest

from bernstein.core.orchestration.task_dag import (
    TaskDag,
    TaskDagCycleError,
    TaskDagError,
    TaskNode,
    format_plan,
    topological_iter_with_parallel,
)


def _ids(batch: frozenset[TaskNode]) -> list[str]:
    return sorted(n.task_id for n in batch)


# ──────────────────────────────────────────────────────────────────────────
# Topological walk: scenarios from the issue acceptance plan
# ──────────────────────────────────────────────────────────────────────────


def test_single_task_dag_yields_one_batch() -> None:
    dag = TaskDag.from_nodes([TaskNode("T001", "only", parallel_safe=True)])
    batches = list(topological_iter_with_parallel(dag))
    assert [_ids(b) for b in batches] == [["T001"]]


def test_sequential_chain_yields_serial_batches() -> None:
    """A → B → C with no [P] flags walks as three serial batches."""
    dag = TaskDag.from_nodes(
        [
            TaskNode("T001", "first"),
            TaskNode("T002", "second", depends_on=("T001",)),
            TaskNode("T003", "third", depends_on=("T002",)),
        ]
    )
    batches = list(topological_iter_with_parallel(dag))
    assert [_ids(b) for b in batches] == [["T001"], ["T002"], ["T003"]]
    assert all(len(b) == 1 for b in batches)


def test_parallel_batch_emits_single_concurrent_set() -> None:
    """Two independent [P] tasks merge into one parallel batch."""
    dag = TaskDag.from_nodes(
        [
            TaskNode("T001", "a", parallel_safe=True),
            TaskNode("T002", "b", parallel_safe=True),
        ]
    )
    batches = list(topological_iter_with_parallel(dag))
    assert len(batches) == 1
    assert _ids(batches[0]) == ["T001", "T002"]


def test_mixed_parallel_then_sequential() -> None:
    """T1[P] + T2[P] run together; T3 (serial) follows, then T4[P]."""
    dag = TaskDag.from_nodes(
        [
            TaskNode("T001", "a", parallel_safe=True),
            TaskNode("T002", "b", parallel_safe=True),
            TaskNode("T003", "c", depends_on=("T001", "T002")),
            TaskNode("T004", "d", parallel_safe=True, depends_on=("T003",)),
        ]
    )
    batches = [_ids(b) for b in topological_iter_with_parallel(dag)]
    assert batches == [["T001", "T002"], ["T003"], ["T004"]]


def test_mixed_serial_and_parallel_in_same_ready_set_serialises_first() -> None:
    """When a serial task is ready alongside [P] siblings, serial wins
    the batch and the [P] tasks form a pure parallel batch on the next
    tick."""
    dag = TaskDag.from_nodes(
        [
            TaskNode("T001", "serial"),
            TaskNode("T002", "p1", parallel_safe=True),
            TaskNode("T003", "p2", parallel_safe=True),
        ]
    )
    batches = [_ids(b) for b in topological_iter_with_parallel(dag)]
    # Serial T001 runs alone first; T002+T003 then form a parallel batch.
    assert batches == [["T001"], ["T002", "T003"]]


def test_cycle_detection_raises() -> None:
    """A → B → A is rejected with TaskDagCycleError."""
    dag = TaskDag.from_nodes(
        [
            TaskNode("T001", "a", depends_on=("T002",)),
            TaskNode("T002", "b", depends_on=("T001",)),
        ]
    )
    with pytest.raises(TaskDagCycleError) as excinfo:
        list(topological_iter_with_parallel(dag))
    assert set(excinfo.value.remaining) == {"T001", "T002"}


def test_unknown_dependency_rejected_at_construction() -> None:
    with pytest.raises(TaskDagError):
        TaskDag.from_nodes([TaskNode("T001", "a", depends_on=("T999",))])


def test_duplicate_task_id_rejected_at_construction() -> None:
    with pytest.raises(TaskDagError):
        TaskDag.from_nodes(
            [
                TaskNode("T001", "a"),
                TaskNode("T001", "b"),
            ]
        )


# ──────────────────────────────────────────────────────────────────────────
# Loading from markdown / YAML
# ──────────────────────────────────────────────────────────────────────────


def test_from_markdown_loads_dag_with_markers() -> None:
    md = """# Plan

- [ ] [T001] [P] [US1] Load YAML
- [ ] [T002] [P] [US1] Load markdown
- [ ] [T003] [US1] Wire orchestrator -> T001, T002
"""
    dag = TaskDag.from_markdown(md)
    assert len(dag) == 3
    t1 = dag.get("T001")
    t3 = dag.get("T003")
    assert t1 is not None
    assert t1.parallel_safe is True
    assert t1.story_id == "US1"
    assert t3 is not None
    assert t3.depends_on == ("T001", "T002")

    batches = [_ids(b) for b in topological_iter_with_parallel(dag)]
    assert batches == [["T001", "T002"], ["T003"]]


def test_from_yaml_loads_dag() -> None:
    pytest.importorskip("yaml")
    content = """
tasks:
  - id: T001
    description: First
    parallel_safe: true
    story_id: US1
  - id: T002
    description: Second
    depends_on: [T001]
    story_id: US1
"""
    dag = TaskDag.from_yaml(content)
    assert len(dag) == 2
    assert dag.get("T001").parallel_safe is True  # type: ignore[union-attr]
    assert dag.get("T002").depends_on == ("T001",)  # type: ignore[union-attr]


def test_stories_groups_nodes_by_story_id() -> None:
    dag = TaskDag.from_nodes(
        [
            TaskNode("T001", "a", story_id="US1"),
            TaskNode("T002", "b", story_id="US1", depends_on=("T001",)),
            TaskNode("T003", "c", story_id="US2"),
            TaskNode("T004", "d"),  # no story
        ]
    )
    stories = dag.stories()
    assert sorted(stories) == ["US1", "US2"]
    assert sorted(n.task_id for n in stories["US1"]) == ["T001", "T002"]
    assert sorted(n.task_id for n in stories["US2"]) == ["T003"]


def test_format_plan_marks_parallel_batches() -> None:
    dag = TaskDag.from_nodes(
        [
            TaskNode("T001", "a", parallel_safe=True),
            TaskNode("T002", "b", parallel_safe=True),
            TaskNode("T003", "c", depends_on=("T001", "T002")),
        ]
    )
    rendered = format_plan(dag)
    assert "PARALLEL" in rendered
    assert "SERIAL" in rendered
    assert "T001" in rendered
    assert "T003" in rendered
