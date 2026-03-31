"""Unit tests for dependency validation."""

from __future__ import annotations

from typing import Any

from bernstein.core.dep_validator import DependencyValidator
from bernstein.core.models import TaskStatus


def test_valid_dag(make_task: Any) -> None:
    a = make_task(id="A")
    b = make_task(id="B")
    c = make_task(id="C")
    b.depends_on = ["A"]
    c.depends_on = ["B"]

    result = DependencyValidator().validate([a, b, c])

    assert result.valid is True
    assert result.cycles == []


def test_simple_cycle(make_task: Any) -> None:
    a = make_task(id="A")
    b = make_task(id="B")
    a.depends_on = ["B"]
    b.depends_on = ["A"]

    result = DependencyValidator().validate([a, b])

    assert result.valid is False
    assert result.cycles == [["A", "B", "A"]]


def test_diamond_dependency(make_task: Any) -> None:
    a = make_task(id="A")
    b = make_task(id="B")
    c = make_task(id="C")
    d = make_task(id="D")
    b.depends_on = ["A"]
    c.depends_on = ["A"]
    d.depends_on = ["B", "C"]

    result = DependencyValidator().validate([a, b, c, d])

    assert result.valid is True
    assert result.cycles == []


def test_missing_dependency(make_task: Any) -> None:
    a = make_task(id="A")
    a.depends_on = ["missing"]

    result = DependencyValidator().validate([a])

    assert result.missing_deps == [("A", "missing")]


def test_stuck_dependency(make_task: Any) -> None:
    a = make_task(id="A")
    b = make_task(id="B", status=TaskStatus.FAILED)
    a.depends_on = ["B"]

    result = DependencyValidator().validate([a, b])

    assert result.stuck_deps == [("A", "B", "failed")]


def test_topological_order(make_task: Any) -> None:
    a = make_task(id="A")
    b = make_task(id="B")
    c = make_task(id="C")
    b.depends_on = ["A"]
    c.depends_on = ["B"]

    assert DependencyValidator().topological_order([a, b, c]) == ["A", "B", "C"]


def test_critical_path(make_task: Any) -> None:
    a = make_task(id="A")
    b = make_task(id="B")
    c = make_task(id="C")
    d = make_task(id="D")
    a.estimated_minutes = 30
    b.estimated_minutes = 30
    c.estimated_minutes = 30
    d.estimated_minutes = 10
    b.depends_on = ["A"]
    c.depends_on = ["B", "D"]
    d.depends_on = []

    assert DependencyValidator().critical_path([a, b, c, d]) == ["A", "B", "C"]


def test_deep_chain_warning(make_task: Any) -> None:
    tasks = [make_task(id=f"T-{idx}") for idx in range(7)]
    for idx in range(1, len(tasks)):
        tasks[idx].depends_on = [tasks[idx - 1].id]

    result = DependencyValidator().validate(tasks)

    assert any("deep dependency chain" in warning for warning in result.warnings)


def test_high_fan_in_warning(make_task: Any) -> None:
    root = make_task(id="root")
    deps = [make_task(id=f"D-{idx}") for idx in range(5)]
    root.depends_on = [task.id for task in deps]

    result = DependencyValidator().validate([root, *deps])

    assert any("fan-in" in warning for warning in result.warnings)


def test_ready_tasks(make_task: Any) -> None:
    done = make_task(id="done", status=TaskStatus.DONE)
    ready = make_task(id="ready")
    blocked = make_task(id="blocked")
    independent = make_task(id="independent")
    ready.depends_on = ["done"]
    blocked.depends_on = ["ready"]

    tasks = DependencyValidator().ready_tasks([done, ready, blocked, independent])

    assert {task.id for task in tasks} == {"ready", "independent"}
