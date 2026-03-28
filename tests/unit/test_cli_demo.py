"""Tests for `bernstein demo` CLI command."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from bernstein.cli.main import (
    _DEMO_TASKS,
    _detect_available_adapter,
    _setup_demo_project,
    cli,
)


# ---------------------------------------------------------------------------
# _detect_available_adapter
# ---------------------------------------------------------------------------


def test_detect_available_adapter_returns_first_found(tmp_path):
    """Returns the name of the first adapter whose CLI is in PATH."""
    with patch("shutil.which", side_effect=lambda cmd: "/usr/bin/" + cmd if cmd == "claude" else None):
        result = _detect_available_adapter()
    assert result == "claude"


def test_detect_available_adapter_returns_none_when_nothing_found():
    """Returns None when no supported CLI tool is available."""
    with patch("shutil.which", return_value=None):
        result = _detect_available_adapter()
    assert result is None


def test_detect_available_adapter_prefers_claude_over_codex():
    """claude is checked before codex in the discovery order."""
    def _which(cmd: str) -> str | None:
        return "/usr/bin/" + cmd if cmd in {"claude", "codex"} else None

    with patch("shutil.which", side_effect=_which):
        result = _detect_available_adapter()
    # claude is first in _ADAPTER_COMMANDS so it should win
    assert result == "claude"


# ---------------------------------------------------------------------------
# _setup_demo_project
# ---------------------------------------------------------------------------


def test_setup_demo_project_creates_sdd_dirs(tmp_path):
    """_setup_demo_project must create the .sdd/ workspace directories."""
    _setup_demo_project(tmp_path, "claude")
    assert (tmp_path / ".sdd" / "backlog" / "open").is_dir()
    assert (tmp_path / ".sdd" / "runtime").is_dir()


def test_setup_demo_project_seeds_three_tasks(tmp_path):
    """Three backlog .md files must exist after project setup."""
    _setup_demo_project(tmp_path, "claude")
    backlog_files = list((tmp_path / ".sdd" / "backlog" / "open").glob("*.md"))
    assert len(backlog_files) == len(_DEMO_TASKS)


def test_setup_demo_project_task_filenames_match(tmp_path):
    """Backlog filenames must match _DEMO_TASKS definitions."""
    _setup_demo_project(tmp_path, "claude")
    backlog_dir = tmp_path / ".sdd" / "backlog" / "open"
    for task in _DEMO_TASKS:
        assert (backlog_dir / task["filename"]).exists()


def test_setup_demo_project_writes_config(tmp_path):
    """A .sdd/config.yaml with the correct adapter must be written."""
    _setup_demo_project(tmp_path, "gemini")
    config_text = (tmp_path / ".sdd" / "config.yaml").read_text()
    assert "gemini" in config_text


def test_setup_demo_project_creates_app_py(tmp_path):
    """app.py should exist in the project root after setup."""
    _setup_demo_project(tmp_path, "claude")
    assert (tmp_path / "app.py").exists()


# ---------------------------------------------------------------------------
# demo command — dry-run mode (no real agents spawned)
# ---------------------------------------------------------------------------


def test_demo_dry_run_exits_zero():
    """bernstein demo --dry-run must exit with code 0."""
    runner = CliRunner()
    with patch("bernstein.cli.main._detect_available_adapter", return_value="claude"):
        result = runner.invoke(cli, ["demo", "--dry-run"])
    assert result.exit_code == 0, result.output


def test_demo_dry_run_shows_cost_estimate():
    """bernstein demo --dry-run must print the cost estimate."""
    runner = CliRunner()
    with patch("bernstein.cli.main._detect_available_adapter", return_value="claude"):
        result = runner.invoke(cli, ["demo", "--dry-run"])
    assert "0.15" in result.output


def test_demo_dry_run_shows_dry_run_label():
    """bernstein demo --dry-run output must contain '[DRY RUN]'."""
    runner = CliRunner()
    with patch("bernstein.cli.main._detect_available_adapter", return_value="claude"):
        result = runner.invoke(cli, ["demo", "--dry-run"])
    assert "DRY RUN" in result.output


def test_demo_no_adapter_exits_nonzero():
    """bernstein demo must exit non-zero when no adapter is available."""
    runner = CliRunner()
    with patch("bernstein.cli.main._detect_available_adapter", return_value=None):
        result = runner.invoke(cli, ["demo", "--dry-run"])
    assert result.exit_code != 0


def test_demo_explicit_adapter_bypasses_detection():
    """--adapter flag must skip auto-detection."""
    runner = CliRunner()
    # No need to patch _detect_available_adapter — explicit flag skips it
    with patch("bernstein.cli.main._detect_available_adapter") as mock_detect:
        result = runner.invoke(cli, ["demo", "--dry-run", "--adapter", "claude"])
    mock_detect.assert_not_called()
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# _DEMO_TASKS sanity checks
# ---------------------------------------------------------------------------


def test_demo_tasks_have_required_fields():
    """Every entry in _DEMO_TASKS must have 'filename' and 'content'."""
    for task in _DEMO_TASKS:
        assert "filename" in task
        assert "content" in task


def test_demo_tasks_filenames_end_with_md():
    """Every demo task filename must end with '.md'."""
    for task in _DEMO_TASKS:
        assert task["filename"].endswith(".md"), task["filename"]


def test_demo_tasks_content_includes_role():
    """Every demo task must specify a **Role:** field."""
    for task in _DEMO_TASKS:
        assert "**Role:**" in task["content"], task["filename"]
