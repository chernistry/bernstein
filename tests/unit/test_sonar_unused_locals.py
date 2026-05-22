"""Regression tests for tracked Sonar unused-local findings."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _assigned_names(path: str) -> set[str]:
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"), filename=path)
    names: set[str] = set()
    for node in ast.walk(tree):
        match node:
            case ast.Assign(targets=targets):
                for target in targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
            case ast.AnnAssign(target=ast.Name(id=name)):
                names.add(name)
            case _:
                pass
    return names


def test_rag_chunker_does_not_keep_dead_embedder_local() -> None:
    """The RAG chunker no longer carries a dead local only for cache salting."""
    assert "embedder_id" not in _assigned_names("src/bernstein/core/knowledge/rag.py")


def test_repo_analyzer_does_not_count_unused_other_files() -> None:
    """The repo analyzer should not maintain an unused non-source counter."""
    assert "other_files" not in _assigned_names("src/bernstein/core/knowledge/repo_analyzer.py")
