"""Tests for the declarative parallel-safety flag and story-link grouping.

Covers:

* Task schema round-trip for ``parallel_safe`` and ``story_id``.
* Backlog parser handling of the ``[T###] [P] [USn]`` checkbox format.
* Scheduler consumption via ``tasks_safe_to_run_in_parallel`` (declarative
  flag wins; legacy file-overlap fallback applies only without the flag).
"""

from __future__ import annotations

from bernstein.core.orchestration.adaptive_parallelism import (
    tasks_safe_to_run_in_parallel,
)
from bernstein.core.tasks.backlog_parser import (
    ParsedTaskLine,
    parse_backlog_text,
    parse_task_line,
    parse_task_lines,
)
from bernstein.core.tasks.models import Task

# ──────────────────────────────────────────────────────────────────────────
# Schema
# ──────────────────────────────────────────────────────────────────────────


def test_task_defaults_to_serial_and_no_story() -> None:
    """A freshly constructed Task is serial-only with no story link."""
    task = Task(id="T001", title="t", description="d", role="backend")
    assert task.parallel_safe is False
    assert task.story_id is None


def test_task_from_dict_round_trips_parallel_safe_and_story_id() -> None:
    raw = {
        "id": "T002",
        "title": "title",
        "description": "desc",
        "role": "backend",
        "parallel_safe": True,
        "story_id": "US7",
    }
    task = Task.from_dict(raw)
    assert task.parallel_safe is True
    assert task.story_id == "US7"


def test_task_from_dict_absence_defaults_to_serial() -> None:
    raw = {"id": "T003", "title": "t", "description": "d", "role": "backend"}
    task = Task.from_dict(raw)
    assert task.parallel_safe is False
    assert task.story_id is None


# ──────────────────────────────────────────────────────────────────────────
# Backlog parser: YAML frontmatter
# ──────────────────────────────────────────────────────────────────────────


def test_yaml_frontmatter_carries_parallel_safe_and_story_id() -> None:
    content = """---
id: T010
title: Wire DAG loader
role: backend
parallel_safe: true
story_id: US1
---

# Wire DAG loader
"""
    parsed = parse_backlog_text("T010.md", content)
    assert parsed is not None
    assert parsed.parallel_safe is True
    assert parsed.story_id == "US1"


def test_yaml_frontmatter_absence_defaults_to_serial() -> None:
    content = """---
id: T011
title: Wire serial step
role: backend
---

# Wire serial step
"""
    parsed = parse_backlog_text("T011.md", content)
    assert parsed is not None
    assert parsed.parallel_safe is False
    assert parsed.story_id is None


# ──────────────────────────────────────────────────────────────────────────
# Backlog parser: [T###] [P] [USn] markdown checkbox format
# ──────────────────────────────────────────────────────────────────────────


def test_parse_task_line_full_markers() -> None:
    parsed = parse_task_line("- [ ] [T001] [P] [US1] Add YAML loader")
    assert parsed == ParsedTaskLine(
        task_id="T001",
        description="Add YAML loader",
        parallel_safe=True,
        story_id="US1",
        depends_on=(),
    )


def test_parse_task_line_serial_without_p_marker() -> None:
    parsed = parse_task_line("- [ ] [T002] [US1] Wire loader -> T001")
    assert parsed is not None
    assert parsed.task_id == "T002"
    assert parsed.parallel_safe is False
    assert parsed.story_id == "US1"
    assert parsed.depends_on == ("T001",)
    assert parsed.description == "Wire loader"


def test_parse_task_line_marker_order_independent() -> None:
    a = parse_task_line("- [ ] [T010] [P] [US2] Run gate")
    b = parse_task_line("- [ ] [T010] [US2] [P] Run gate")
    assert a is not None and b is not None
    assert a.parallel_safe is True and b.parallel_safe is True
    assert a.story_id == b.story_id == "US2"


def test_parse_task_line_rejects_non_task_rows() -> None:
    assert parse_task_line("# Heading") is None
    assert parse_task_line("Just prose, no checkbox") is None
    assert parse_task_line("- [ ] No task id marker") is None
    assert parse_task_line("") is None


def test_parse_task_lines_skips_non_task_rows() -> None:
    md = """# Task DAG

Some intro prose.

- [ ] [T001] [P] [US1] First
- [ ] [T002] [US1] Second -> T001
- not a checkbox
- [ ] [T003] [P] [US2] Third
"""
    parsed = parse_task_lines(md)
    assert [p.task_id for p in parsed] == ["T001", "T002", "T003"]
    assert parsed[0].parallel_safe is True
    assert parsed[1].depends_on == ("T001",)


# ──────────────────────────────────────────────────────────────────────────
# Scheduler consumption: tasks_safe_to_run_in_parallel
# ──────────────────────────────────────────────────────────────────────────


def _task(
    task_id: str,
    *,
    parallel_safe: bool | None = None,
    owned: list[str] | None = None,
) -> Task:
    kwargs: dict[str, object] = dict(
        id=task_id,
        title=task_id,
        description="d",
        role="backend",
        owned_files=owned or [],
    )
    if parallel_safe is not None:
        kwargs["parallel_safe"] = parallel_safe
    return Task(**kwargs)  # type: ignore[arg-type]


def test_declarative_flag_permits_parallel() -> None:
    a = _task("T1", parallel_safe=True, owned=["src/foo.py"])
    b = _task("T2", parallel_safe=True, owned=["src/foo.py"])  # same file!
    # Declarative flag wins even though files overlap.
    assert tasks_safe_to_run_in_parallel(a, b) is True


def test_declarative_flag_forces_serial() -> None:
    a = _task("T1", parallel_safe=False, owned=["src/foo.py"])
    b = _task("T2", parallel_safe=True, owned=["src/bar.py"])  # disjoint!
    # Either-False forces serial despite disjoint files.
    assert tasks_safe_to_run_in_parallel(a, b) is False


def test_legacy_fallback_uses_file_overlap_when_attr_missing() -> None:
    # Plain objects without the parallel_safe attribute hit the legacy path.
    class Legacy:
        def __init__(self, files: list[str]) -> None:
            self.owned_files = files

    a = Legacy(["src/foo.py"])
    b = Legacy(["src/foo.py"])
    c = Legacy(["src/bar.py"])
    assert tasks_safe_to_run_in_parallel(a, b) is False
    assert tasks_safe_to_run_in_parallel(a, c) is True
