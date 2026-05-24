"""Regression tests for tracker transition exception handlers."""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TRACKER_FILES = (
    Path("src/bernstein/core/trackers/builtin/github_projects_adapter.py"),
    Path("src/bernstein/core/trackers/builtin/jira_cloud_adapter.py"),
    Path("src/bernstein/core/trackers/linear.py"),
)


def test_tracker_transition_methods_do_not_catch_only_to_rethrow() -> None:
    """Optimistic concurrency errors should propagate without a redundant handler."""
    redundant_handlers: list[tuple[Path, int]] = []
    for path in TRACKER_FILES:
        tree = ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"), filename=str(path))
        for handler in ast.walk(tree):
            if (
                isinstance(handler, ast.ExceptHandler)
                and isinstance(handler.type, ast.Name)
                and handler.type.id == "OptimisticConcurrencyError"
                and len(handler.body) == 1
                and isinstance(handler.body[0], ast.Raise)
                and handler.body[0].exc is None
            ):
                redundant_handlers.append((path, handler.lineno))

    assert redundant_handlers == []
