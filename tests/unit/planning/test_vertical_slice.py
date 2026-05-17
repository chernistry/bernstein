"""Unit tests for the vertical-slice shape checker (issue #1321)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bernstein.core.planning.vertical_slice import (
    DEFAULT_MAX_FILES,
    ShapeConfig,
    ShapeViolation,
    check_plan,
    format_violations_for_reprompt,
    load_shape_config,
    summarise_slice,
)
from bernstein.core.tasks.models import Complexity, Scope, Task


def _make_task(
    title: str,
    *,
    owned_files: list[str] | None = None,
    scope: Scope = Scope.MEDIUM,
    role: str = "backend",
) -> Task:
    return Task(
        id=f"t-{title}",
        title=title,
        description=f"impl {title}",
        role=role,
        scope=scope,
        complexity=Complexity.MEDIUM,
        owned_files=owned_files or [],
    )


# ---------------------------------------------------------------------------
# Defaults & disabled mode
# ---------------------------------------------------------------------------


def test_shape_config_defaults_match_issue_spec() -> None:
    cfg = ShapeConfig()
    assert cfg.enforce_vertical is True
    assert cfg.max_loc_hard == 400
    assert cfg.max_loc_ideal == 200
    assert cfg.max_files == 10
    assert cfg.max_modules == 2


def test_disabled_config_returns_no_violations_even_for_terrible_plan() -> None:
    tasks = [
        _make_task("db schema", owned_files=["src/app/db/schema.py"]),
        _make_task("db migration", owned_files=["src/app/db/migration.py"]),
        _make_task(
            "huge task",
            owned_files=[f"src/app/core/x{i}.py" for i in range(50)],
            scope=Scope.LARGE,
        ),
    ]
    cfg = ShapeConfig(enforce_vertical=False)
    assert check_plan(tasks, cfg) == []


# ---------------------------------------------------------------------------
# Oversized slice detection
# ---------------------------------------------------------------------------


def test_oversized_task_is_rejected_by_loc_cap() -> None:
    task = _make_task(
        "huge slice",
        # 20 files * 40 LOC = 800 LOC, well above the 400 hard cap.
        owned_files=[f"src/pkg/a{i}.py" for i in range(20)],
        scope=Scope.LARGE,
    )
    violations = check_plan([task])
    rules = {v.rule for v in violations}
    assert "max_loc_hard" in rules
    assert any(v.severity == "error" for v in violations)


def test_too_many_files_is_rejected() -> None:
    task = _make_task(
        "too many files",
        owned_files=[f"src/pkg/file{i}.py" for i in range(DEFAULT_MAX_FILES + 5)],
        scope=Scope.SMALL,
    )
    violations = check_plan([task])
    assert any(v.rule == "max_files" for v in violations)


def test_too_many_modules_is_rejected() -> None:
    task = _make_task(
        "spread across modules",
        owned_files=[
            "src/pkgA/x.py",
            "src/pkgB/y.py",
            "src/pkgC/z.py",
        ],
    )
    violations = check_plan([task])
    assert any(v.rule == "max_modules" for v in violations)


def test_ideal_loc_warning_only() -> None:
    task = _make_task(
        "slightly large slice",
        # 7 files * 40 = 280 LOC: above ideal 200 but under hard 400.
        owned_files=[f"src/pkg/a{i}.py" for i in range(7)],
        scope=Scope.SMALL,
    )
    violations = check_plan([task])
    rule_severities = {(v.rule, v.severity) for v in violations}
    assert ("max_loc_ideal", "warn") in rule_severities
    assert not any(v.severity == "error" and v.rule == "max_loc_hard" for v in violations)


# ---------------------------------------------------------------------------
# Horizontally-phased detection
# ---------------------------------------------------------------------------


def test_horizontally_phased_pair_is_flagged() -> None:
    tasks = [
        _make_task("db schema", owned_files=["src/app/db/schema.py"]),
        _make_task("db migration", owned_files=["src/app/db/migration.py"]),
    ]
    violations = check_plan(tasks)
    assert any(v.rule == "horizontally_phased" for v in violations)


def test_vertical_pair_with_tests_is_accepted() -> None:
    tasks = [
        _make_task(
            "feature A",
            owned_files=[
                "src/app/api/handler.py",
                "tests/test_handler.py",
            ],
        ),
        _make_task(
            "feature B",
            owned_files=[
                "src/app/api/other.py",
                "tests/test_other.py",
            ],
        ),
    ]
    errors = [v for v in check_plan(tasks) if v.severity == "error"]
    assert errors == []


def test_pair_in_different_modules_is_not_horizontally_phased() -> None:
    tasks = [
        _make_task("db work", owned_files=["src/pkgA/db/schema.py"]),
        _make_task("api work", owned_files=["src/pkgB/api/handler.py"]),
    ]
    errors = [v for v in check_plan(tasks) if v.severity == "error"]
    assert not any(v.rule == "horizontally_phased" for v in errors)


# ---------------------------------------------------------------------------
# Re-prompt body formatting
# ---------------------------------------------------------------------------


def test_format_violations_for_reprompt_empty_returns_empty() -> None:
    assert format_violations_for_reprompt([]) == ""
    only_warn = [ShapeViolation(rule="x", message="m", severity="warn")]
    assert format_violations_for_reprompt(only_warn) == ""


def test_format_violations_for_reprompt_includes_errors() -> None:
    violations = [
        ShapeViolation(rule="max_loc_hard", message="too big", severity="error"),
        ShapeViolation(rule="ignored", message="warn only", severity="warn"),
    ]
    text = format_violations_for_reprompt(violations)
    assert "too big" in text
    assert "warn only" not in text
    assert "Re-emit" in text
    assert "vertical slices" in text


# ---------------------------------------------------------------------------
# Summary line
# ---------------------------------------------------------------------------


def test_summarise_slice_includes_layers_and_loc() -> None:
    task = _make_task(
        "feature",
        owned_files=["src/app/api/handler.py", "tests/test_handler.py"],
    )
    summary = summarise_slice(task)
    assert "api" in summary
    assert "tests" in summary
    assert "LOC" in summary


def test_summarise_slice_handles_no_owned_files() -> None:
    task = _make_task("free-form")
    summary = summarise_slice(task)
    assert "no-files" in summary
    assert "LOC" in summary


# ---------------------------------------------------------------------------
# bernstein.yaml [plan] overrides
# ---------------------------------------------------------------------------


def test_load_shape_config_no_file_returns_defaults(tmp_path: Path) -> None:
    cfg = load_shape_config(tmp_path)
    assert cfg == ShapeConfig()


def test_load_shape_config_with_overrides(tmp_path: Path) -> None:
    (tmp_path / "bernstein.yaml").write_text(
        "plan:\n  enforce_vertical: false\n  max_loc: 250\n  max_loc_ideal: 150\n  max_files: 5\n  max_modules: 1\n",
        encoding="utf-8",
    )
    cfg = load_shape_config(tmp_path)
    assert cfg.enforce_vertical is False
    assert cfg.max_loc_hard == 250
    assert cfg.max_loc_ideal == 150
    assert cfg.max_files == 5
    assert cfg.max_modules == 1


def test_load_shape_config_missing_plan_block_returns_defaults(tmp_path: Path) -> None:
    (tmp_path / "bernstein.yaml").write_text(
        "goal: 'do things'\nmax_agents: 4\n",
        encoding="utf-8",
    )
    cfg = load_shape_config(tmp_path)
    assert cfg == ShapeConfig()


def test_load_shape_config_string_bool_accepted(tmp_path: Path) -> None:
    (tmp_path / "bernstein.yaml").write_text(
        "plan:\n  enforce_vertical: 'no'\n",
        encoding="utf-8",
    )
    cfg = load_shape_config(tmp_path)
    assert cfg.enforce_vertical is False


# ---------------------------------------------------------------------------
# Integration-ish scenarios
# ---------------------------------------------------------------------------


def test_horizontally_phased_full_plan_known_oversized_split_target() -> None:
    """Known horizontally-phased plan should be rejected and the
    re-prompt body should mention how to split.
    """
    tasks = [
        _make_task(
            "design db schema",
            owned_files=["src/svc/db/users.py", "src/svc/db/posts.py"],
        ),
        _make_task(
            "write db migrations",
            owned_files=["src/svc/db/migrations/001.py"],
        ),
        _make_task(
            "implement api endpoints",
            owned_files=[
                "src/svc/api/users.py",
                "src/svc/api/posts.py",
                "src/svc/api/comments.py",
            ],
        ),
    ]
    violations = check_plan(tasks)
    errors = [v for v in violations if v.severity == "error"]
    assert errors, "expected at least one error on a phased plan"
    body = format_violations_for_reprompt(violations)
    assert "Re-emit" in body
    assert "vertical slices" in body


@pytest.mark.parametrize(
    "value, expected",
    [
        ("true", True),
        ("True", True),
        ("yes", True),
        ("1", True),
        ("false", False),
        ("0", False),
        ("off", False),
    ],
)
def test_load_shape_config_string_bool_variants(tmp_path: Path, value: str, expected: bool) -> None:
    (tmp_path / "bernstein.yaml").write_text(
        f"plan:\n  enforce_vertical: '{value}'\n",
        encoding="utf-8",
    )
    cfg = load_shape_config(tmp_path)
    assert cfg.enforce_vertical is expected
