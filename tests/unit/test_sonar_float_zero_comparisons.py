"""Regression tests for Sonar python:S1244 float-zero comparisons."""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_TRACKED_PATHS = [
    "src/bernstein/core/autofix/review_router.py",
    "src/bernstein/core/cost/cost_rollup_by_envelope.py",
    "src/bernstein/core/orchestration/schedule_supervisor.py",
    "src/bernstein/core/quality/review_pipeline/verdict.py",
]


def _is_zero_float(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, float) and node.value == 0.0


def _direct_zero_float_comparisons(relative_path: str) -> list[str]:
    tree = ast.parse((PROJECT_ROOT / relative_path).read_text(encoding="utf-8"))
    findings: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(operator, ast.Eq | ast.NotEq | ast.LtE | ast.GtE) for operator in node.ops):
            continue
        compared = [node.left, *node.comparators]
        if any(_is_zero_float(value) for value in compared):
            findings.append(f"{relative_path}:{node.lineno}:{ast.unparse(node)}")

    return findings


def test_s1244_cluster_avoids_direct_zero_float_comparisons() -> None:
    """Use explicit closeness checks when zero is part of a float comparison."""
    findings = [finding for path in _TRACKED_PATHS for finding in _direct_zero_float_comparisons(path)]

    assert findings == []
