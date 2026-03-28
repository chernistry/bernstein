"""Unit tests for the Bernstein TUI session manager."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from bernstein.tui.app import BernsteinApp
from bernstein.tui.widgets import (
    STATUS_COLORS,
    AgentLogWidget,
    StatusBar,
    TaskListWidget,
    TaskRow,
    status_color,
)


class TestStatusColors:
    """Tests for status-to-colour mapping."""

    def test_open_is_white(self) -> None:
        assert status_color("open") == "white"

    def test_claimed_is_cyan(self) -> None:
        assert status_color("claimed") == "cyan"

    def test_in_progress_is_yellow(self) -> None:
        assert status_color("in_progress") == "yellow"

    def test_done_is_green(self) -> None:
        assert status_color("done") == "green"

    def test_failed_is_red(self) -> None:
        assert status_color("failed") == "red"

    def test_blocked_is_dim(self) -> None:
        assert status_color("blocked") == "dim"

    def test_cancelled_is_dim(self) -> None:
        assert status_color("cancelled") == "dim"

    def test_unknown_defaults_to_white(self) -> None:
        assert status_color("some_unknown_status") == "white"

    def test_all_task_statuses_covered(self) -> None:
        expected = {"open", "claimed", "in_progress", "done", "failed", "blocked", "cancelled"}
        assert set(STATUS_COLORS.keys()) == expected


class TestTaskRow:
    """Tests for TaskRow.from_api parsing."""

    def test_full_dict(self) -> None:
        raw: dict[str, Any] = {
            "id": "t-001",
            "status": "in_progress",
            "role": "backend",
            "title": "Implement login",
        }
        row = TaskRow.from_api(raw)
        assert row.task_id == "t-001"
        assert row.status == "in_progress"
        assert row.role == "backend"
        assert row.title == "Implement login"

    def test_missing_fields_use_defaults(self) -> None:
        row = TaskRow.from_api({})
        assert row.task_id == ""
        assert row.status == "open"
        assert row.role == ""
        assert row.title == ""

    def test_numeric_id_coerced_to_str(self) -> None:
        raw: dict[str, Any] = {"id": 42, "status": "done", "role": "qa", "title": "Test things"}
        row = TaskRow.from_api(raw)
        assert row.task_id == "42"

    def test_extra_fields_ignored(self) -> None:
        raw: dict[str, Any] = {
            "id": "t-002",
            "status": "open",
            "role": "frontend",
            "title": "Build UI",
            "priority": 1,
            "depends_on": ["t-001"],
        }
        row = TaskRow.from_api(raw)
        assert row.task_id == "t-002"
        assert row.title == "Build UI"


class TestWidgetCreation:
    """Tests that widgets can be instantiated without crashing."""

    def test_task_list_widget(self) -> None:
        widget = TaskListWidget()
        assert widget is not None

    def test_agent_log_widget(self) -> None:
        widget = AgentLogWidget()
        assert widget is not None

    def test_status_bar(self) -> None:
        widget = StatusBar("initial")
        assert widget is not None


class TestAppInstantiation:
    """Tests that the Textual app can be created."""

    def test_app_can_be_created(self) -> None:
        app = BernsteinApp()
        assert app is not None
        assert app.TITLE == "Bernstein"

    def test_app_custom_interval(self) -> None:
        app = BernsteinApp(poll_interval=5.0)
        assert app._poll_interval == 5.0  # noqa: SLF001

    def test_app_has_bindings(self) -> None:
        app = BernsteinApp()
        binding_keys = {b.key for b in app.BINDINGS if hasattr(b, "key")}
        assert "q" in binding_keys
        assert "tab" in binding_keys
        assert "r" in binding_keys

    def test_count_active_agents_no_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Returns 0 when agents.json does not exist."""
        monkeypatch.chdir(tmp_path)
        count = BernsteinApp._count_active_agents()  # noqa: SLF001
        assert count == 0
