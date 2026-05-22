"""Regression tests for Sonar python:S5886 return-type mismatch findings."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

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
            is_replace_name = isinstance(func, ast.Name) and func.id == "replace"
            is_replace_attribute = isinstance(func, ast.Attribute) and func.attr == "replace"
            if is_replace_name or is_replace_attribute:
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


def test_direct_replace_scanner_detects_attribute_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The scanner should catch module-qualified replace calls too."""
    sample = tmp_path / "sample.py"
    sample.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "import dataclasses",
                "",
                "def update(value: object) -> object:",
                "    return dataclasses.replace(value)",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("tests.unit.test_sonar_return_type_mismatches.PROJECT_ROOT", tmp_path)
    findings = _direct_replace_returns("sample.py")

    assert findings == ["sample.py:update:6"]
