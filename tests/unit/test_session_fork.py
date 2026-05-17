"""Tests for ``bernstein.core.sessions.fork`` (#1222).

These cover the snapshot-based fork primitive end-to-end against a real
git repository created in ``tmp_path``.  We exercise:

* successful fork → sibling worktree on a derived branch, snapshot written
* fork-id and branch-name slugification with awkward labels
* error paths: missing parent session, unknown id, non-git ``repo_root``,
  and pre-existing fork worktree directory
* CLI ``bernstein session fork`` happy path + JSON output

The tests avoid mocking subprocess.  Initialising a small temp repo is
cheaper than maintaining a fake-git layer and exercises the real worktree
plumbing the production code relies on.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from bernstein.cli.commands.session_cmd import session_group
from bernstein.core.orchestration.run_session import RunSession, sessions_dir_for
from bernstein.core.sessions.fork import (
    SessionFork,
    SessionForkError,
    _build_fork_branch_name,
    _slugify_label,
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
    """Initialise an empty git repository with one commit."""
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
    """Create + persist a parent run session inside *repo*."""
    sdir = sessions_dir_for(repo)
    sdir.mkdir(parents=True, exist_ok=True)
    session = RunSession.create(goal="build a feature", run_seed=42)
    session.tasks = [
        {"id": "t-1", "role": "backend", "title": "implement", "status": "in_progress"},
        {"id": "t-2", "role": "qa", "title": "verify", "status": "pending"},
    ]
    session.routing_decisions = {"t-1": "sonnet"}
    session.save(sdir)
    return session


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_keeps_safe_chars(self) -> None:
        assert _slugify_label("use-yaml.v1_2") == "use-yaml.v1_2"

    def test_replaces_unsafe_with_dash(self) -> None:
        assert _slugify_label("try yaml not json!") == "try-yaml-not-json"

    def test_truncates_long_labels(self) -> None:
        out = _slugify_label("a" * 200)
        assert len(out) == 32
        assert set(out) == {"a"}

    def test_strips_leading_trailing_separators(self) -> None:
        assert _slugify_label("---foo---") == "foo"

    def test_empty_string(self) -> None:
        assert _slugify_label("") == ""


class TestBranchNameBuilder:
    def test_parent_branch_with_slash(self) -> None:
        name = _build_fork_branch_name("feature/foo", "fork-x-20260101-aaa")
        assert name == "fork/feature-foo/fork-x-20260101-aaa"

    def test_detached_parent_falls_back_to_session(self) -> None:
        name = _build_fork_branch_name("", "fork-20260101-bbb")
        assert name == "fork/session/fork-20260101-bbb"


# ---------------------------------------------------------------------------
# fork_session — happy path
# ---------------------------------------------------------------------------


class TestForkSessionHappyPath:
    def test_creates_sibling_worktree_and_snapshot(self, repo: Path, parent_session: RunSession) -> None:
        fork = fork_session(
            parent_session_id=parent_session.session_id,
            fork_label="use yaml",
            repo_root=repo,
        )
        # Structural checks on the returned descriptor.
        assert isinstance(fork, SessionFork)
        assert fork.parent_session_id == parent_session.session_id
        assert fork.fork_session_id.startswith("fork-use-yaml-")
        assert fork.fork_branch.startswith("fork/main/")
        assert fork.fork_worktree.is_dir()
        assert fork.fork_worktree.parent.name == "worktrees"
        # The fork worktree is on the new branch we asked for.
        head_branch = subprocess.run(
            ["git", "symbolic-ref", "--short", "HEAD"],
            cwd=fork.fork_worktree,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert head_branch == fork.fork_branch
        # And it branched from the parent's commit.
        head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=fork.fork_worktree,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert head_sha == fork.fork_commit
        # Parent session file is preserved verbatim, fork snapshot lives
        # inside the fork worktree's sessions directory.
        assert fork.snapshot_path.is_file()
        assert fork.snapshot_path.parent == sessions_dir_for(fork.fork_worktree)

    def test_snapshot_preserves_tasks_and_lineage_metadata(self, repo: Path, parent_session: RunSession) -> None:
        fork = fork_session(
            parent_session_id=parent_session.session_id,
            fork_label="alt",
            repo_root=repo,
        )
        payload = json.loads(fork.snapshot_path.read_text(encoding="utf-8"))
        assert payload["session_id"] == fork.fork_session_id
        assert payload["goal"] == parent_session.goal
        assert payload["run_seed"] == parent_session.run_seed
        assert payload["tasks"] == parent_session.tasks
        assert payload["routing_decisions"] == parent_session.routing_decisions
        lineage = payload["fork"]
        assert lineage["parent_session_id"] == parent_session.session_id
        assert lineage["label"] == "alt"
        assert lineage["branch"] == fork.fork_branch
        assert lineage["branched_from_commit"] == fork.fork_commit

    def test_to_dict_serialises_paths_as_strings(self, repo: Path, parent_session: RunSession) -> None:
        fork = fork_session(
            parent_session_id=parent_session.session_id,
            repo_root=repo,
        )
        as_dict = fork.to_dict()
        assert isinstance(as_dict["fork_worktree"], str)
        assert isinstance(as_dict["parent_worktree"], str)
        assert isinstance(as_dict["snapshot_path"], str)
        # Round-trips through JSON.
        json.dumps(as_dict)

    def test_empty_label_omitted_from_id(self, repo: Path, parent_session: RunSession) -> None:
        fork = fork_session(
            parent_session_id=parent_session.session_id,
            fork_label="",
            repo_root=repo,
        )
        assert fork.fork_session_id.startswith("fork-")
        assert "fork--" not in fork.fork_session_id

    def test_parent_session_file_untouched(self, repo: Path, parent_session: RunSession) -> None:
        parent_path = sessions_dir_for(repo) / f"{parent_session.session_id}.json"
        before = parent_path.read_text(encoding="utf-8")
        fork_session(
            parent_session_id=parent_session.session_id,
            repo_root=repo,
        )
        assert parent_path.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# fork_session — error paths
# ---------------------------------------------------------------------------


class TestForkSessionErrors:
    def test_empty_parent_id_rejected(self, repo: Path) -> None:
        with pytest.raises(SessionForkError, match="parent_session_id"):
            fork_session(parent_session_id="", repo_root=repo)

    def test_unknown_parent_id_rejected(self, repo: Path) -> None:
        with pytest.raises(SessionForkError, match="parent session not found"):
            fork_session(parent_session_id="does-not-exist", repo_root=repo)

    def test_non_git_repo_root_rejected(self, tmp_path: Path, parent_session: RunSession) -> None:
        # Point at a fresh non-git tmp_path so the HEAD lookup fails even
        # though the parent session JSON loads fine.
        bare = tmp_path / "not-a-repo"
        bare.mkdir()
        parent_session.save(sessions_dir_for(bare))
        with pytest.raises(SessionForkError, match="git HEAD"):
            fork_session(parent_session_id=parent_session.session_id, repo_root=bare)

    def test_missing_repo_root_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(SessionForkError, match="repo_root"):
            fork_session(
                parent_session_id="any",
                repo_root=tmp_path / "missing",
            )

    def test_existing_fork_worktree_rejected(
        self, repo: Path, parent_session: RunSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force the fork worktree path to land somewhere we've pre-created
        # by stubbing the id generator to a known value.
        from bernstein.core.sessions import fork as fork_mod

        monkeypatch.setattr(fork_mod, "_generate_fork_session_id", lambda _label: "fork-fixed")
        target = repo / ".sdd" / "worktrees" / "fork-fixed"
        target.mkdir(parents=True)
        with pytest.raises(SessionForkError, match="worktree path already exists"):
            fork_session(
                parent_session_id=parent_session.session_id,
                repo_root=repo,
            )

    def test_two_forks_get_distinct_branches(self, repo: Path, parent_session: RunSession) -> None:
        a = fork_session(parent_session_id=parent_session.session_id, repo_root=repo)
        b = fork_session(parent_session_id=parent_session.session_id, repo_root=repo)
        assert a.fork_session_id != b.fork_session_id
        assert a.fork_branch != b.fork_branch
        assert a.fork_worktree != b.fork_worktree


# ---------------------------------------------------------------------------
# CLI surface — ``bernstein session fork``
# ---------------------------------------------------------------------------


class TestSessionForkCLI:
    def test_cli_happy_path_human(
        self, repo: Path, parent_session: RunSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(repo)
        runner = CliRunner()
        result = runner.invoke(
            session_group,
            ["fork", parent_session.session_id, "--label", "alt-path"],
        )
        assert result.exit_code == 0, result.output
        assert "Forked session" in result.output
        assert parent_session.session_id in result.output

    def test_cli_json_output(self, repo: Path, parent_session: RunSession, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(repo)
        runner = CliRunner()
        result = runner.invoke(
            session_group,
            ["fork", parent_session.session_id, "--json"],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["parent_session_id"] == parent_session.session_id
        assert payload["fork_session_id"].startswith("fork-")
        assert payload["fork_branch"].startswith("fork/")

    def test_cli_unknown_parent_exits_nonzero(self, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(repo)
        runner = CliRunner()
        result = runner.invoke(session_group, ["fork", "does-not-exist"])
        assert result.exit_code == 1
        assert "Fork failed" in result.output
