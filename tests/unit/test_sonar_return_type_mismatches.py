"""Regression tests for Sonar python:S5886 return-type mismatch findings."""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _direct_replace_returns(relative_path: str) -> list[str]:
    tree = ast.parse((PROJECT_ROOT / relative_path).read_text(encoding="utf-8"))
    findings: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Return):
                continue
            value = child.value
            if not isinstance(value, ast.Call):
                continue
            func = value.func
            if isinstance(func, ast.Name) and func.id == "replace":
                findings.append(f"{relative_path}:{node.name}:{child.lineno}")

    return findings


def test_s5886_cluster_avoids_direct_replace_returns() -> None:
    """Typed functions should not directly return dataclasses.replace calls."""
    paths = [
        "src/bernstein/core/orchestration/run_actor.py",
        "src/bernstein/core/orchestration/consensus_relay.py",
        "src/bernstein/core/cost/retry_budget.py",
    ]

    findings = [finding for path in paths for finding in _direct_replace_returns(path)]

    assert findings == []
