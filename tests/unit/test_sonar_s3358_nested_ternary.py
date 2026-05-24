"""Regression coverage for Sonar python:S3358 nested ternary findings."""

from __future__ import annotations

import ast
from pathlib import Path

S3358_TARGETS: tuple[Path, ...] = (
    Path("src/bernstein/cli/commands/analyze_cmd.py"),
    Path("src/bernstein/cli/commands/doctor/backends.py"),
    Path("src/bernstein/cli/commands/recipes_cmd.py"),
    Path("src/bernstein/cli/commands/schedule_cmd.py"),
    Path("src/bernstein/cli/commands/workflow_cmd.py"),
    Path("src/bernstein/cli/commands/worktrees_cmd.py"),
    Path("src/bernstein/core/security/network_policy.py"),
)


def _nested_ifexp_lines(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.IfExp):
            continue
        if (
            any(isinstance(child, ast.IfExp) for child in ast.walk(node.test))
            or any(isinstance(child, ast.IfExp) for child in ast.walk(node.body))
            or any(isinstance(child, ast.IfExp) for child in ast.walk(node.orelse))
        ):
            lines.append(node.lineno)
    return lines


def test_nested_ternary_detector_checks_conditions(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    source.write_text("x = 1 if (2 if cond else 3) else 4\n", encoding="utf-8")
    assert _nested_ifexp_lines(source) == [1]


def test_sonar_s3358_targets_do_not_use_nested_ternaries() -> None:
    offenders = {str(path): _nested_ifexp_lines(path) for path in S3358_TARGETS}
    offenders = {path: lines for path, lines in offenders.items() if lines}
    assert offenders == {}
