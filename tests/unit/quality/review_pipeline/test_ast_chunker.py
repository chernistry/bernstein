"""Tests for the AST-aware reviewer chunker.

Critical contract: on a 5000-line Python fixture, no emitted chunk may
split a top-level function or class. Each chunk must AST-parse cleanly.
"""

from __future__ import annotations

import ast
import itertools
import logging
from pathlib import Path

import pytest

from bernstein.core.quality.review_pipeline import (
    ReviewChunk,
    chunk_for_review,
)
from bernstein.core.quality.review_pipeline.ast_chunker import (
    _CHARS_PER_TOKEN,
)


def _synthesize_python_file(path: Path, target_lines: int = 5000) -> int:
    """Write a deterministic Python module of roughly *target_lines* lines."""
    lines: list[str] = [
        '"""Synthetic fixture for AST chunker tests."""',
        "",
        "from __future__ import annotations",
        "",
        "import math",
        "import os",
        "",
        "MODULE_CONST = 42",
        "",
    ]
    func_idx = 0
    cls_idx = 0
    while len(lines) < target_lines:
        if func_idx % 5 == 4:
            cls_idx += 1
            lines.append(f"class Widget{cls_idx}:")
            lines.append(f'    """Widget number {cls_idx}."""')
            lines.append("")
            for m in range(3):
                lines.append(f"    def method_{m}(self, x: int) -> int:")
                lines.append(f'        """Method {m} of widget {cls_idx}."""')
                lines.append("        total = 0")
                for k in range(8):
                    lines.append(f"        total += x * {k} + {m}")
                lines.append("        return total")
                lines.append("")
        else:
            lines.append(f"def helper_{func_idx}(x: int, y: int = 1) -> int:")
            lines.append(f'    """Helper function {func_idx}."""')
            lines.append("    acc = x + y")
            for k in range(6):
                lines.append(f"    acc += {k} * x - y")
            lines.append("    return acc")
            lines.append("")
        func_idx += 1

    text = "\n".join(lines) + "\n"
    path.write_text(text, encoding="utf-8")
    return text.count("\n")


@pytest.fixture
def big_python_file(tmp_path: Path) -> Path:
    fp = tmp_path / "big_module.py"
    actual_lines = _synthesize_python_file(fp, target_lines=5000)
    assert actual_lines >= 5000
    return fp


def _assert_no_split_definitions(chunks: list[ReviewChunk]) -> None:
    """Each chunk must parse as valid Python (no half-functions inside)."""
    for ch in chunks:
        assert ch.language == "python", f"unexpected language for {ch.path}"
        try:
            tree = ast.parse(ch.text)
        except SyntaxError as exc:  # pragma: no cover - failure path
            pytest.fail(f"chunk L{ch.start_line}-{ch.end_line} failed to parse: {exc}\n{ch.text[:300]}")
        for node in tree.body:
            assert node.end_lineno is not None
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                # The node body must be non-empty when the def line is in range.
                assert node.body, f"empty body for {node.name} in chunk L{ch.start_line}-{ch.end_line}"


def test_5000_line_fixture_never_splits_definitions(big_python_file: Path) -> None:
    chunks = chunk_for_review(big_python_file, budget_tokens=2000)
    assert len(chunks) > 1, "fixture should produce multiple chunks at this budget"
    _assert_no_split_definitions(chunks)


def test_chunks_cover_full_file_in_order(big_python_file: Path) -> None:
    chunks = chunk_for_review(big_python_file, budget_tokens=2000)
    source = big_python_file.read_text(encoding="utf-8")
    total_lines = source.count("\n")

    assert chunks[0].start_line == 1
    # Last chunk may stop one line short — we deliberately drop trailing
    # whitespace-only runs that have no AST node.
    assert chunks[-1].end_line >= total_lines - 1
    for prev, nxt in itertools.pairwise(chunks):
        assert nxt.start_line == prev.end_line + 1
        assert prev.end_line >= prev.start_line


def test_each_chunk_under_budget_when_units_fit(tmp_path: Path) -> None:
    fp = tmp_path / "small.py"
    _synthesize_python_file(fp, target_lines=400)
    budget = 1500
    chunks = chunk_for_review(fp, budget_tokens=budget)
    for ch in chunks[:-1]:
        assert ch.estimated_tokens() <= budget * 1.5


def test_oversized_function_emitted_whole(tmp_path: Path) -> None:
    """A function larger than the budget must still be one intact chunk."""
    fp = tmp_path / "huge_fn.py"
    body = "\n".join(f"    x += {i}" for i in range(400))
    src = f"def huge():\n    x = 0\n{body}\n    return x\n"
    fp.write_text(src, encoding="utf-8")

    chunks = chunk_for_review(fp, budget_tokens=10)
    assert len(chunks) == 1
    parsed = ast.parse(chunks[0].text)
    assert isinstance(parsed.body[0], ast.FunctionDef)
    assert parsed.body[0].name == "huge"


def test_header_lists_top_level_symbols(tmp_path: Path) -> None:
    fp = tmp_path / "two.py"
    fp.write_text("def alpha():\n    return 1\n\ndef beta():\n    return 2\n", encoding="utf-8")
    chunks = chunk_for_review(fp, budget_tokens=10_000)
    assert len(chunks) == 1
    assert "alpha" in chunks[0].header
    assert "beta" in chunks[0].header
    assert chunks[0].symbols == ("alpha", "beta")


def test_non_python_falls_back_to_line_based(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    fp = tmp_path / "notes.md"
    fp.write_text("\n".join(f"line {i}" for i in range(600)) + "\n", encoding="utf-8")
    with caplog.at_level(logging.INFO, logger="bernstein.core.quality.review_pipeline.ast_chunker"):
        chunks = chunk_for_review(fp, budget_tokens=200)
    assert chunks, "should still produce some chunks"
    assert all(c.language == "text" for c in chunks)
    assert any("line-based fallback" in r.getMessage() for r in caplog.records)


def test_unparseable_python_falls_back_with_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    fp = tmp_path / "broken.py"
    fp.write_text("def oops(:\n    pass\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="bernstein.core.quality.review_pipeline.ast_chunker"):
        chunks = chunk_for_review(fp, budget_tokens=200)
    assert chunks
    assert all(c.language == "text" for c in chunks)
    assert any("falling back to line-based" in r.getMessage() for r in caplog.records)


def test_missing_file_returns_empty(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    fp = tmp_path / "does_not_exist.py"
    with caplog.at_level(logging.WARNING, logger="bernstein.core.quality.review_pipeline.ast_chunker"):
        chunks = chunk_for_review(fp)
    assert chunks == []
    assert any("cannot read" in r.getMessage() for r in caplog.records)


def test_chars_per_token_constant_matches_optimizer() -> None:
    # Sanity: keep this aligned with the prompt-cache optimizer's heuristic.
    assert _CHARS_PER_TOKEN == 4
