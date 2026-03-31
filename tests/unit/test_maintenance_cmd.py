"""Tests for Track B maintenance CLI commands."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from bernstein.cli.main import cli


def _write_task_record(path: Path, *, task_id: str, status: str, assigned_agent: str | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": task_id,
        "title": "Fix auth flow",
        "description": "Repair auth flow",
        "role": "backend",
        "priority": 2,
        "scope": "medium",
        "complexity": "medium",
        "estimated_minutes": 30,
        "status": status,
        "task_type": "standard",
        "upgrade_details": None,
        "depends_on": [],
        "owned_files": ["src/auth.py"],
        "assigned_agent": assigned_agent,
        "result_summary": None,
        "cell_id": None,
        "batch_eligible": False,
        "slack_context": None,
        "version": 1,
    }
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_history_filters_archive_by_owned_file(tmp_path: Path) -> None:
    archive_path = tmp_path / ".sdd" / "archive" / "tasks.jsonl"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "task_id": "task-auth",
                        "title": "Fix auth flow",
                        "role": "backend",
                        "status": "done",
                        "created_at": 1.0,
                        "completed_at": 2.0,
                        "duration_seconds": 1.0,
                        "result_summary": "done",
                        "cost_usd": None,
                        "assigned_agent": "sess-auth",
                        "owned_files": ["src/auth.py", "src/auth_helpers.py"],
                    }
                ),
                json.dumps(
                    {
                        "task_id": "task-other",
                        "title": "Fix docs",
                        "role": "docs",
                        "status": "done",
                        "created_at": 1.0,
                        "completed_at": 2.0,
                        "duration_seconds": 1.0,
                        "result_summary": "done",
                        "cost_usd": None,
                        "assigned_agent": "sess-docs",
                        "owned_files": ["docs/guide.md"],
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["history", "src/auth.py", "--json", "--workdir", str(tmp_path)])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["file"] == "src/auth.py"
    assert len(payload["tasks"]) == 1
    assert payload["tasks"][0]["task_id"] == "task-auth"


def test_cleanup_removes_only_inactive_worktrees(tmp_path: Path) -> None:
    tasks_path = tmp_path / ".sdd" / "runtime" / "tasks.jsonl"
    _write_task_record(tasks_path, task_id="task-live", status="claimed", assigned_agent="sess-live")

    runner = CliRunner()
    with (
        patch(
            "bernstein.cli.maintenance_cmd.WorktreeManager.list_active",
            return_value=["sess-live", "sess-done"],
        ),
        patch("bernstein.cli.maintenance_cmd.WorktreeManager.cleanup") as cleanup,
        patch(
            "bernstein.cli.maintenance_cmd.run_hygiene",
            return_value={"worktrees_cleaned": 1, "branches_deleted": 2, "stash_dropped": 0},
        ),
    ):
        result = runner.invoke(cli, ["cleanup", "--yes", "--workdir", str(tmp_path)])

    assert result.exit_code == 0
    cleanup.assert_called_once_with("sess-done")
    assert "Cleanup complete" in result.output
