"""Smoke tests for the ``bernstein git`` CLI group.

These exercise the Click command runner against a real repo so the
shell semantics (exit codes, JSON shape, error messages) stay glued to
the underlying :class:`SnapshotStore` contract.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from bernstein.cli.commands.git_cmd import git_cmd
from bernstein.core.git.snapshot import SnapshotStore, stack_push


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Minimal initialised repo with one commit at HEAD."""
    _git("init", "-q", "-b", "main", cwd=tmp_path)
    _git("config", "user.email", "test@example.com", cwd=tmp_path)
    _git("config", "user.name", "Test", cwd=tmp_path)
    _git("config", "commit.gpgsign", "false", cwd=tmp_path)
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")
    _git("add", "README.md", cwd=tmp_path)
    _git("commit", "-q", "-m", "initial", cwd=tmp_path)
    return tmp_path


def test_snapshots_table_lists_taken_entries(repo: Path) -> None:
    """``bernstein git snapshots`` renders captured snapshots."""
    SnapshotStore(repo).take(task_id="T-cli", label="step-1")
    runner = CliRunner()
    result = runner.invoke(git_cmd, ["snapshots", "--workdir", str(repo)])
    assert result.exit_code == 0, result.output
    assert "T-cli" in result.output
    assert "step-1" in result.output


def test_snapshots_json_emits_array(repo: Path) -> None:
    """``--json`` returns a parseable array of snapshot dicts."""
    SnapshotStore(repo).take(task_id="T-json")
    runner = CliRunner()
    result = runner.invoke(git_cmd, ["snapshots", "--workdir", str(repo), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert isinstance(payload, list)
    assert payload and payload[0]["task_id"] == "T-json"


def test_undo_restores_tree(repo: Path) -> None:
    """``bernstein git undo`` restores the work tree."""
    store = SnapshotStore(repo)
    snap = store.take(task_id="T-undo")
    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", "change", cwd=repo)

    runner = CliRunner()
    result = runner.invoke(git_cmd, ["undo", snap.snapshot_id, "--workdir", str(repo)])

    assert result.exit_code == 0, result.output
    assert "restored" in result.output
    assert (repo / "README.md").read_text(encoding="utf-8") == "hello\n"


def test_undo_missing_id_returns_nonzero(repo: Path) -> None:
    """Unknown snapshot IDs surface as a Click error with a non-zero exit."""
    runner = CliRunner()
    result = runner.invoke(git_cmd, ["undo", "nope-id", "--workdir", str(repo)])
    assert result.exit_code != 0
    assert "not found" in result.output


def test_stack_renders_chain(repo: Path) -> None:
    """``bernstein git stack`` renders ordered stack entries."""
    _git("checkout", "-q", "-b", "agent/a", cwd=repo)
    stack_push(repo, task_id="T-s", branch="agent/a")
    _git("checkout", "-q", "-b", "agent/b", cwd=repo)
    stack_push(repo, task_id="T-s", branch="agent/b")

    runner = CliRunner()
    result = runner.invoke(git_cmd, ["stack", "--task", "T-s", "--workdir", str(repo)])
    assert result.exit_code == 0, result.output
    assert "agent/a" in result.output
    assert "agent/b" in result.output


def test_diff_between_snapshots(repo: Path) -> None:
    """``bernstein git diff`` prints the diff stat for the named pair."""
    store = SnapshotStore(repo)
    first = store.take(task_id="T-diff")
    (repo / "new.txt").write_text("hi\n", encoding="utf-8")
    second = store.take(task_id="T-diff")

    runner = CliRunner()
    result = runner.invoke(git_cmd, ["diff", first.snapshot_id, second.snapshot_id, "--workdir", str(repo)])
    assert result.exit_code == 0, result.output
    assert "new.txt" in result.output


def test_gc_reports_removed_count(repo: Path) -> None:
    """``bernstein git gc`` reports how many snapshots were removed."""
    store = SnapshotStore(repo)
    snap = store.take(task_id="T-gc")
    sidecar = repo / ".git" / "bernstein" / "snapshots" / f"{snap.snapshot_id}.json"
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload["ts_ns"] = 1
    sidecar.write_text(json.dumps(payload), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(git_cmd, ["gc", "--days", "1", "--workdir", str(repo)])
    assert result.exit_code == 0, result.output
    assert "1 snapshot" in result.output
