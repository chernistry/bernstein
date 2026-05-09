"""Tests for the JSONL memory log auto-injection into the spawner pipeline.

Acceptance criteria (matches the ticket):

* The default behaviour is unchanged: when ``BERNSTEIN_MEMORY_AUTO_INJECT``
  is unset, no ``<lessons>`` block appears in the rendered prompt.
* When the env var is enabled and a ``.bernstein/memory/lessons.jsonl``
  file exists, the most recent ``N=10`` entries appear in a stable
  ``<lessons>...</lessons>`` block AFTER the role/git_safety header
  but BEFORE the ``## Assigned tasks`` body (KV-cache locality).
* A missing log is a no-op.
* The block is capped at 10 entries — older ones are dropped, recent
  ones survive.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from bernstein.core.spawn_prompt import (
    _MEMORY_AUTO_INJECT_ENV_VAR,
    _MEMORY_LESSONS_CLOSE,
    _MEMORY_LESSONS_KEY,
    _MEMORY_LESSONS_MAX,
    _MEMORY_LESSONS_OPEN,
    _format_memory_lesson,
    _memory_auto_inject_enabled,
    _render_memory_lessons_block,
    _render_prompt,
)

from bernstein.core.memory.jsonl_log import JSONLMemoryLog

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Env-gating helpers
# ---------------------------------------------------------------------------


class TestEnvGating:
    """The auto-inject path is opt-in via ``BERNSTEIN_MEMORY_AUTO_INJECT``."""

    def test_disabled_when_env_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(_MEMORY_AUTO_INJECT_ENV_VAR, raising=False)
        assert _memory_auto_inject_enabled() is False

    @pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "FALSE"])
    def test_disabled_for_falsey_values(
        self,
        monkeypatch: pytest.MonkeyPatch,
        value: str,
    ) -> None:
        monkeypatch.setenv(_MEMORY_AUTO_INJECT_ENV_VAR, value)
        assert _memory_auto_inject_enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE"])
    def test_enabled_for_truthy_values(
        self,
        monkeypatch: pytest.MonkeyPatch,
        value: str,
    ) -> None:
        monkeypatch.setenv(_MEMORY_AUTO_INJECT_ENV_VAR, value)
        assert _memory_auto_inject_enabled() is True


# ---------------------------------------------------------------------------
# Lesson rendering
# ---------------------------------------------------------------------------


class TestFormatMemoryLesson:
    """``_format_memory_lesson`` collapses dict entries to a one-line bullet."""

    def test_lesson_field_used(self) -> None:
        bullet = _format_memory_lesson({"task": "T-1", "lesson": "guard imports"})
        assert bullet == "- (T-1) guard imports"

    def test_text_field_fallback(self) -> None:
        bullet = _format_memory_lesson({"text": "deflake the parser"})
        assert bullet == "- deflake the parser"

    def test_message_field_fallback(self) -> None:
        bullet = _format_memory_lesson({"message": "watch GIL contention"})
        assert bullet == "- watch GIL contention"

    def test_no_text_falls_back_to_json_dump(self) -> None:
        bullet = _format_memory_lesson({"score": 3, "stage": "review"})
        # Falls back to compact JSON dump so callers always get something.
        assert bullet.startswith("- ")
        assert "score" in bullet
        assert "stage" in bullet


# ---------------------------------------------------------------------------
# _render_memory_lessons_block — disk-bound behaviour
# ---------------------------------------------------------------------------


class TestRenderMemoryLessonsBlock:
    """Block renders only when the JSONL file is present and non-empty."""

    def test_missing_log_returns_empty(self, tmp_path: Path) -> None:
        # No `.bernstein/memory/lessons.jsonl` on disk.
        assert _render_memory_lessons_block(tmp_path) == ""

    def test_empty_log_returns_empty(self, tmp_path: Path) -> None:
        # File exists but contains no records.
        log = JSONLMemoryLog(root=tmp_path / ".bernstein" / "memory")
        log.write(_MEMORY_LESSONS_KEY, {"lesson": "tmp"})
        log.clear(_MEMORY_LESSONS_KEY)
        assert _render_memory_lessons_block(tmp_path) == ""

    def test_block_wraps_entries_with_stable_separator(self, tmp_path: Path) -> None:
        log = JSONLMemoryLog(root=tmp_path / ".bernstein" / "memory")
        log.write(_MEMORY_LESSONS_KEY, {"task": "T-1", "lesson": "guard imports"})
        block = _render_memory_lessons_block(tmp_path)
        assert _MEMORY_LESSONS_OPEN in block
        assert _MEMORY_LESSONS_CLOSE in block
        assert "guard imports" in block

    def test_block_caps_at_max_recent_entries(self, tmp_path: Path) -> None:
        """N=10 cap — only the most recent entries survive."""
        log = JSONLMemoryLog(root=tmp_path / ".bernstein" / "memory")
        for i in range(_MEMORY_LESSONS_MAX + 5):
            log.write(_MEMORY_LESSONS_KEY, {"lesson": f"L{i}"})
        block = _render_memory_lessons_block(tmp_path)
        # Oldest entries dropped.
        assert "L0" not in block
        assert "L4" not in block
        # Most recent kept.
        assert "L14" in block
        assert "L5" in block
        # Exactly N bullet lines between the markers.
        body = block.split(_MEMORY_LESSONS_OPEN, 1)[1].split(_MEMORY_LESSONS_CLOSE, 1)[0]
        bullets = [ln for ln in body.splitlines() if ln.startswith("- ")]
        assert len(bullets) == _MEMORY_LESSONS_MAX


# ---------------------------------------------------------------------------
# End-to-end: _render_prompt picks up the block when the env var is set
# ---------------------------------------------------------------------------


class TestRenderPromptInjection:
    """The full leaf-agent prompt embeds the block at the correct anchor."""

    def test_default_off_no_block_in_rendered_prompt(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        make_task: Any,
    ) -> None:
        monkeypatch.delenv(_MEMORY_AUTO_INJECT_ENV_VAR, raising=False)
        # Even with a populated log, a default boot does not inject.
        log = JSONLMemoryLog(root=tmp_path / ".bernstein" / "memory")
        log.write(_MEMORY_LESSONS_KEY, {"lesson": "should not appear"})
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()

        rendered = _render_prompt(
            tasks=[make_task(role="backend", title="Implement T-1")],
            templates_dir=templates_dir,
            workdir=tmp_path,
        )
        assert _MEMORY_LESSONS_OPEN not in rendered
        assert "should not appear" not in rendered

    def test_enabled_injects_block_before_assigned_tasks(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        make_task: Any,
    ) -> None:
        monkeypatch.setenv(_MEMORY_AUTO_INJECT_ENV_VAR, "1")
        log = JSONLMemoryLog(root=tmp_path / ".bernstein" / "memory")
        log.write(
            _MEMORY_LESSONS_KEY,
            {"task": "T-prev", "lesson": "always validate inputs"},
        )
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()

        rendered = _render_prompt(
            tasks=[make_task(role="backend", title="Implement T-1")],
            templates_dir=templates_dir,
            workdir=tmp_path,
        )
        assert _MEMORY_LESSONS_OPEN in rendered
        assert "always validate inputs" in rendered
        # KV-cache locality: lessons block precedes the variable goal
        # block ("## Assigned tasks") — the spec is explicit on the order.
        assert rendered.index(_MEMORY_LESSONS_OPEN) < rendered.index("## Assigned tasks")

    def test_enabled_with_missing_log_is_noop(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        make_task: Any,
    ) -> None:
        monkeypatch.setenv(_MEMORY_AUTO_INJECT_ENV_VAR, "1")
        # No `.bernstein/memory/lessons.jsonl` written — must not raise.
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        rendered = _render_prompt(
            tasks=[make_task(role="backend", title="Implement T-1")],
            templates_dir=templates_dir,
            workdir=tmp_path,
        )
        assert _MEMORY_LESSONS_OPEN not in rendered
