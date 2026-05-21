"""Integration tests for ``bernstein session fork --from-step`` (#1799).

These tests exercise the full fork-from-step plumbing against a real
git repository under ``tmp_path``:

* The fork worktree branches from the *parent's* commit at the time of
  step N (today: parent's current HEAD since we do not yet pin per-step
  commits; the chain hash is what pins per-step identity).
* The fork session metadata records ``from_step`` and the parent step
  hash so the chain becomes a tree, not just a list.
* Calling ``session fork`` *without* ``--from-step`` continues to work
  exactly as it did pre-#1799 (backward-compat regression net).
* Reconstructed context: the fork worktree's journal directory is
  pre-seeded with steps 0..N from the parent so an agent starting from
  the fork sees the same chain prefix.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from bernstein.cli.commands.session_cmd import session_group
from bernstein.core.orchestration.run_session import RunSession, sessions_dir_for
from bernstein.core.persistence.journal import Journal, JournalReader
from bernstein.core.sessions.fork import (
    SessionForkError,
    fork_session,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "README.md").write_text("# repo\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "initial")
    return root


@pytest.fixture
def parent_session(repo: Path) -> RunSession:
    sdir = sessions_dir_for(repo)
    sdir.mkdir(parents=True, exist_ok=True)
    session = RunSession.create(goal="build a feature", run_seed=42)
    session.tasks = [
        {"id": "t-1", "role": "backend", "title": "implement", "status": "in_progress"},
    ]
    session.save(sdir)
    return session


@pytest.fixture
def parent_journal(repo: Path, parent_session: RunSession) -> tuple[Path, list[str]]:
    """Populate a 4-step parent journal under ``.sdd/runtime/journal/<sid>``."""
    journal_dir = repo / ".sdd" / "runtime" / "journal" / parent_session.session_id
    journal = Journal.open(journal_dir)
    head_hashes: list[str] = []
    for i in range(4):
        entry = journal.append(
            input_hash=f"a{i}",
            model="m1",
            prompt=f"step {i}",
            tool_call={"name": "noop"},
            tool_result={"ok": True},
        )
        head_hashes.append(entry.step_hash)
    journal.close()
    return journal_dir, head_hashes


# ---------------------------------------------------------------------------
# Happy path: fork at step N
# ---------------------------------------------------------------------------


class TestForkFromStep:
    def test_fork_from_step_records_parent_step_hash(
        self,
        repo: Path,
        parent_session: RunSession,
        parent_journal: tuple[Path, list[str]],
    ) -> None:
        _journal_dir, head_hashes = parent_journal

        fork = fork_session(
            parent_session_id=parent_session.session_id,
            fork_label="branch-at-2",
            repo_root=repo,
            from_step=2,
        )

        # The snapshot records the parent step hash + step index.
        snapshot = json.loads(fork.snapshot_path.read_text(encoding="utf-8"))
        assert snapshot["fork"]["from_step"] == 2
        assert snapshot["fork"]["parent_step_hash"] == head_hashes[2]

    def test_fork_from_step_seeds_journal_prefix(
        self,
        repo: Path,
        parent_session: RunSession,
        parent_journal: tuple[Path, list[str]],
    ) -> None:
        _journal_dir, _ = parent_journal

        fork = fork_session(
            parent_session_id=parent_session.session_id,
            fork_label="branch-at-2",
            repo_root=repo,
            from_step=2,
        )

        # Fork journal directory exists and contains exactly steps 0..2.
        fork_journal_dir = fork.fork_worktree / ".sdd" / "runtime" / "journal" / fork.fork_session_id
        reader = JournalReader(fork_journal_dir)
        entries = list(reader.entries())
        assert [e.seq for e in entries] == [0, 1, 2]
        # Chain still verifies.
        result = reader.verify(expected_head=entries[-1].step_hash)
        assert result.ok, result.errors

    def test_fork_from_step_rejects_out_of_range_index(
        self,
        repo: Path,
        parent_session: RunSession,
        parent_journal: tuple[Path, list[str]],
    ) -> None:
        with pytest.raises(SessionForkError):
            fork_session(
                parent_session_id=parent_session.session_id,
                fork_label="too-far",
                repo_root=repo,
                from_step=99,
            )


# ---------------------------------------------------------------------------
# Backward compatibility: no --from-step
# ---------------------------------------------------------------------------


class TestForkBackwardCompat:
    def test_fork_without_from_step_works_unchanged(
        self,
        repo: Path,
        parent_session: RunSession,
    ) -> None:
        """The plain ``session fork`` path must keep working: no
        journal precondition, no fork.from_step in the snapshot."""
        fork = fork_session(
            parent_session_id=parent_session.session_id,
            fork_label="plain",
            repo_root=repo,
        )
        snapshot = json.loads(fork.snapshot_path.read_text(encoding="utf-8"))
        assert "from_step" not in snapshot["fork"]
        assert "parent_step_hash" not in snapshot["fork"]

    def test_cli_session_fork_without_from_step(
        self,
        repo: Path,
        parent_session: RunSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from click.testing import CliRunner

        monkeypatch.chdir(repo)
        runner = CliRunner()
        result = runner.invoke(
            session_group,
            ["fork", parent_session.session_id, "--json"],
        )
        assert result.exit_code == 0, result.output
        descriptor = json.loads(result.output)
        assert descriptor["parent_session_id"] == parent_session.session_id


class TestForkCli:
    def test_cli_session_fork_from_step(
        self,
        repo: Path,
        parent_session: RunSession,
        parent_journal: tuple[Path, list[str]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from click.testing import CliRunner

        monkeypatch.chdir(repo)
        runner = CliRunner()
        result = runner.invoke(
            session_group,
            [
                "fork",
                parent_session.session_id,
                "--from-step",
                "1",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        descriptor = json.loads(result.output)
        assert descriptor["from_step"] == 1
