"""Tests for the intent-summary + pseudocode abstraction layer."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable
from typing import Any

import pytest

from bernstein.core.quality.review_pipeline.abstract_diff import (
    IntentSummary,
    TaskContext,
    pseudo_for_function,
    render_pr_body,
    summarize_diff,
)
from bernstein.core.review_responder.pr_gen import build_pr_body

_TWO_FILE_DIFF = """\
diff --git a/src/foo.py b/src/foo.py
--- a/src/foo.py
+++ b/src/foo.py
@@ -1,3 +1,7 @@
-def slow(xs):
-    return sorted(xs)
+def slow(xs):
+    if len(xs) > 32:
+        return quicksort(xs)
+    return sorted(xs)
diff --git a/src/bar.py b/src/bar.py
--- a/src/bar.py
+++ b/src/bar.py
@@ -1,1 +1,2 @@
+import os
 x = 1
"""


def _stub_llm(result_by_path: dict[str, dict[str, Any]]) -> Any:
    """Return an async LLM stub that picks responses by file path in the prompt."""
    calls: list[dict[str, Any]] = []

    async def caller(*, prompt: str, model: str, **_: Any) -> str:
        calls.append({"prompt": prompt, "model": model})
        for path, payload in result_by_path.items():
            if path in prompt:
                return json.dumps(payload)
        return json.dumps({"bullets": ["fallback"], "confidence": 0.5})

    caller.calls = calls  # type: ignore[attr-defined]
    return caller


def _run(awaitable: Awaitable[Any]) -> Any:
    return asyncio.run(awaitable)


def test_summarize_diff_returns_one_summary_per_file() -> None:
    stub = _stub_llm(
        {
            "src/foo.py": {"bullets": ["adds quicksort branch for large lists"], "confidence": 0.9},
            "src/bar.py": {"bullets": ["imports os"], "confidence": 0.7},
        }
    )
    summaries = _run(
        summarize_diff(
            _TWO_FILE_DIFF,
            TaskContext(title="Speed up sort", description="prefer quicksort", writer_model="claude"),
            llm_caller=stub,
        )
    )
    assert [s.path for s in summaries] == ["src/foo.py", "src/bar.py"]
    assert summaries[0].bullet_points == ("adds quicksort branch for large lists",)
    assert summaries[0].confidence == pytest.approx(0.9)
    assert summaries[1].bullet_points == ("imports os",)


def test_summarize_diff_disabled_returns_empty() -> None:
    summaries = _run(
        summarize_diff(
            _TWO_FILE_DIFF,
            TaskContext(title="x"),
            llm_caller=_stub_llm({}),
            enabled=False,
        )
    )
    assert summaries == []


def test_summarize_diff_empty_diff_returns_empty() -> None:
    summaries = _run(summarize_diff("", TaskContext(title="x"), llm_caller=_stub_llm({})))
    assert summaries == []


def test_summarize_diff_caps_bullets_at_three() -> None:
    stub = _stub_llm(
        {
            "src/foo.py": {
                "bullets": ["a", "b", "c", "d", "e"],
                "confidence": 0.5,
            },
            "src/bar.py": {"bullets": [], "confidence": 0.0},
        }
    )
    summaries = _run(summarize_diff(_TWO_FILE_DIFF, TaskContext(title="x"), llm_caller=stub))
    assert len(summaries[0].bullet_points) == 3


def test_summarize_diff_handles_unparseable_llm_output() -> None:
    async def junk(*, prompt: str, model: str, **_: Any) -> str:
        return "not json at all"

    summaries = _run(summarize_diff(_TWO_FILE_DIFF, TaskContext(title="x"), llm_caller=junk))
    assert all(s.bullet_points == () for s in summaries)
    assert all(s.confidence == 0.0 for s in summaries)


def test_summarize_diff_handles_llm_exception() -> None:
    async def boom(*, prompt: str, model: str, **_: Any) -> str:
        raise RuntimeError("rate-limited")

    summaries = _run(summarize_diff(_TWO_FILE_DIFF, TaskContext(title="x"), llm_caller=boom))
    assert len(summaries) == 2
    for s in summaries:
        assert s.bullet_points == ()
        assert s.confidence == 0.0


def test_summarize_diff_degrades_when_too_many_files() -> None:
    big = "\n".join(
        f"diff --git a/f{i}.py b/f{i}.py\n--- a/f{i}.py\n+++ b/f{i}.py\n@@ -1 +1 @@\n-x\n+y\n" for i in range(60)
    )
    stub = _stub_llm({})
    summaries = _run(summarize_diff(big, TaskContext(title="huge"), llm_caller=stub, max_files=50))
    assert len(summaries) == 1
    assert summaries[0].path == "<aggregate>"
    assert "exceeds abstract-diff cap" in summaries[0].bullet_points[0]
    assert stub.calls == []  # no per-file calls — graceful degradation


def test_summarize_diff_disallows_opus_tier() -> None:
    stub = _stub_llm({"src/foo.py": {"bullets": ["x"]}, "src/bar.py": {"bullets": ["y"]}})
    _ = _run(
        summarize_diff(
            _TWO_FILE_DIFF,
            TaskContext(title="t", writer_model="opus"),
            llm_caller=stub,
        )
    )
    for call in stub.calls:
        assert "opus" not in call["model"].lower()


def test_pseudo_for_function_renders_control_flow() -> None:
    src = """\
def slow(xs):
    if len(xs) > 32:
        return quicksort(xs)
    total = 0
    for x in xs:
        total = total + x
    return sorted(xs)
"""
    pseudo = pseudo_for_function(src)
    assert pseudo.startswith("function slow(xs):")
    assert "if len(xs) > 32:" in pseudo
    assert "return quicksort(xs)" in pseudo
    assert "for x in xs:" in pseudo
    assert "return sorted(xs)" in pseudo


def test_pseudo_for_function_empty_input() -> None:
    assert pseudo_for_function("") == ""
    assert pseudo_for_function("not a function") == ""
    assert pseudo_for_function("def broken(:") == ""


def test_pseudo_for_function_pass_body() -> None:
    pseudo = pseudo_for_function("def empty(): ...")
    assert pseudo.startswith("function empty():")


def test_render_pr_body_includes_intent_and_details() -> None:
    summaries = [
        IntentSummary(
            path="src/foo.py",
            bullet_points=("switches to quicksort",),
            pseudocode_blocks=("function slow(xs):\n    return sorted(xs)",),
            confidence=0.8,
        ),
        IntentSummary(
            path="src/bar.py",
            bullet_points=("imports os",),
            confidence=0.6,
        ),
    ]
    body = render_pr_body(summaries, raw_diff=_TWO_FILE_DIFF)
    assert "## Intent" in body
    assert "### `src/foo.py`" in body
    assert "- switches to quicksort" in body
    assert "<details><summary>Pseudocode</summary>" in body
    assert "<details><summary>Raw diff</summary>" in body
    assert "<details><summary>Full raw diff</summary>" in body
    assert "_confidence: 0.80_" in body


def test_render_pr_body_empty_summaries_returns_empty_string() -> None:
    assert render_pr_body([]) == ""


def test_render_pr_body_falls_back_to_link_when_no_raw_diff() -> None:
    body = render_pr_body([IntentSummary(path="src/x.py", bullet_points=("a",), raw_diff_link="https://gh/diff")])
    assert "[Raw diff](https://gh/diff)" in body
    assert "<details><summary>Raw diff>" not in body


def test_build_pr_body_concatenates_description_and_intent() -> None:
    stub = _stub_llm(
        {
            "src/foo.py": {"bullets": ["adds quicksort"], "confidence": 0.9},
            "src/bar.py": {"bullets": ["imports os"], "confidence": 0.4},
        }
    )
    body = _run(
        build_pr_body(
            description="Closes #123 — speed up sort.",
            diff=_TWO_FILE_DIFF,
            task_context=TaskContext(title="Speed up sort", writer_model="claude"),
            llm_caller=stub,
        )
    )
    assert body.startswith("Closes #123 — speed up sort.")
    assert "## Intent" in body
    assert "- adds quicksort" in body


def test_build_pr_body_disabled_returns_description_only() -> None:
    body = _run(
        build_pr_body(
            description="Closes #1.",
            diff=_TWO_FILE_DIFF,
            task_context=TaskContext(title="x"),
            llm_caller=_stub_llm({}),
            enabled=False,
        )
    )
    assert "## Intent" not in body
    assert body.rstrip() == "Closes #1."


def test_build_pr_body_large_diff_degrades_gracefully() -> None:
    big = "\n".join(
        f"diff --git a/f{i}.py b/f{i}.py\n--- a/f{i}.py\n+++ b/f{i}.py\n@@ -1 +1 @@\n-x\n+y\n" for i in range(100)
    )
    stub = _stub_llm({})
    body = _run(
        build_pr_body(
            description="Big change",
            diff=big,
            task_context=TaskContext(title="huge"),
            llm_caller=stub,
            max_files=50,
        )
    )
    assert "exceeds abstract-diff cap" in body
    assert stub.calls == []
