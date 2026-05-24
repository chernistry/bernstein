"""Data-loss guards for the worktree garbage collector.

These tests build *real* git worktrees under a throw-away repo's
``.sdd/runtime/worktrees/`` directory (not the fake ``.git`` stubs used by
:mod:`tests.unit.test_worktrees_cmd`) so the classifier can probe genuine
git state - uncommitted changes and unmerged commits.

Invariant under test: ``bernstein worktrees gc`` must never ``rmtree`` a
worktree that still holds the only copy of unsaved work. A worktree drops
to ``ORPHAN`` precisely when its PID record is missing - exactly the
crash-recovery situation where unmerged commits are most likely stranded -
so "no PID record" must not be treated as "safe to delete".
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from bernstein.cli.commands.worktrees_cmd import run_gc, worktrees_group
from bernstein.core.worktrees.classifier import (
    WorktreeState,
    classify_worktrees,
)

# ---------------------------------------------------------------------------
# Real-git fixture builders
# ---------------------------------------------------------------------------


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a git command in *repo_root* with a deterministic identity."""
    return subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "-c",
            "user.email=test@bernstein",
            "-c",
            "user.name=test",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(repo_root: Path) -> None:
    """Initialise a git repo with one commit on ``main`` at *repo_root*."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo_root)], check=True)
    (repo_root / "seed.txt").write_text("seed")
    _git(repo_root, "add", ".")
    _git(repo_root, "commit", "-q", "-m", "seed")


def _add_worktree(repo_root: Path, session_id: str) -> Path:
    """Create a real git worktree on a fresh ``agent/<session>`` branch.

    The worktree lives under ``.sdd/runtime/worktrees/<session>`` so the
    classifier discovers it. With no PID record it classifies as ORPHAN.
    """
    base = repo_root / ".sdd" / "runtime" / "worktrees"
    base.mkdir(parents=True, exist_ok=True)
    wt = base / session_id
    _git(repo_root, "worktree", "add", "-q", "-b", f"agent/{session_id}", str(wt), "main")
    return wt


def _commit_in_worktree(wt: Path, filename: str, content: str) -> None:
    """Create and commit a file inside the worktree (an unmerged commit)."""
    (wt / filename).write_text(content)
    _git(wt, "add", ".")
    _git(wt, "commit", "-q", "-m", f"work on {filename}")


@pytest.fixture()
def repo_root(tmp_path: Path) -> Path:
    """Initialised throw-away repo root with real git."""
    _init_repo(tmp_path)
    return tmp_path


def _row(repo_root: Path, session_id: str):  # type: ignore[no-untyped-def]
    rows = classify_worktrees(repo_root)
    matching = [r for r in rows if r.session_id == session_id]
    assert matching, f"no classifier row for {session_id}"
    return matching[0]


# ---------------------------------------------------------------------------
# Classifier safety: dirty / unmerged worktrees are not reapable
# ---------------------------------------------------------------------------


def test_orphan_with_untracked_file_is_not_reapable(repo_root: Path) -> None:
    """A dirty working tree (untracked file) blocks reaping.

    Acceptance: ``git status --porcelain`` is non-empty -> not reapable.
    """
    wt = _add_worktree(repo_root, "dirty-untracked")
    (wt / "scratch.txt").write_text("uncommitted agent work")

    row = _row(repo_root, "dirty-untracked")
    assert row.state is WorktreeState.ORPHAN
    assert row.has_unsaved_work is True
    assert row.is_reapable is False


def test_orphan_with_staged_change_is_not_reapable(repo_root: Path) -> None:
    """A staged-but-uncommitted change blocks reaping."""
    wt = _add_worktree(repo_root, "dirty-staged")
    (wt / "seed.txt").write_text("modified and staged")
    _git(wt, "add", "seed.txt")

    row = _row(repo_root, "dirty-staged")
    assert row.has_unsaved_work is True
    assert row.is_reapable is False


def test_orphan_with_unmerged_commit_is_not_reapable(repo_root: Path) -> None:
    """A branch with commits absent from main blocks reaping.

    Acceptance: commit on the worktree branch, run classify -> not reapable.
    """
    wt = _add_worktree(repo_root, "unmerged")
    _commit_in_worktree(wt, "feature.py", "print('new')\n")

    row = _row(repo_root, "unmerged")
    assert row.state is WorktreeState.ORPHAN
    assert row.has_unsaved_work is True
    assert row.is_reapable is False


def test_clean_orphan_still_reapable(repo_root: Path) -> None:
    """A clean worktree whose branch is merged into main still reaps.

    Regression guard: the common case the GC was built for is unaffected.
    """
    _add_worktree(repo_root, "clean")
    # agent/clean was branched from main and has no extra commits, so it is
    # an ancestor of main (fully merged) and the tree is clean.

    row = _row(repo_root, "clean")
    assert row.state is WorktreeState.ORPHAN
    assert row.has_unsaved_work is False
    assert row.is_reapable is True


def test_corrupt_empty_dir_is_reapable(repo_root: Path) -> None:
    """A CORRUPT worktree with no tracked content reaps (cannot be probed)."""
    base = repo_root / ".sdd" / "runtime" / "worktrees"
    base.mkdir(parents=True, exist_ok=True)
    empty = base / "corrupt-empty"
    empty.mkdir()

    row = _row(repo_root, "corrupt-empty")
    assert row.state is WorktreeState.CORRUPT
    assert row.has_unsaved_work is False
    assert row.is_reapable is True


def test_corrupt_empty_dir_does_not_need_git_worktree_paths(repo_root: Path) -> None:
    """A corrupt directory is classified from local filesystem state only."""
    base = repo_root / ".sdd" / "runtime" / "worktrees"
    base.mkdir(parents=True, exist_ok=True)
    empty = base / "corrupt-empty"
    empty.mkdir()

    with patch(
        "bernstein.core.worktrees.classifier.subprocess.run",
        side_effect=AssertionError("git should not be queried for corrupt directory classification"),
    ):
        row = _row(repo_root, "corrupt-empty")
    assert row.state is WorktreeState.CORRUPT
    assert row.is_reapable is True


def test_corrupt_dir_with_content_is_not_reapable(repo_root: Path) -> None:
    """A CORRUPT worktree (no .git) holding files is surfaced, not reaped."""
    base = repo_root / ".sdd" / "runtime" / "worktrees"
    base.mkdir(parents=True, exist_ok=True)
    corrupt = base / "corrupt-content"
    corrupt.mkdir()
    (corrupt / "rescue-me.txt").write_text("the only copy")

    row = _row(repo_root, "corrupt-content")
    assert row.state is WorktreeState.CORRUPT
    assert row.has_unsaved_work is True
    assert row.is_reapable is False


# ---------------------------------------------------------------------------
# run_gc end-to-end: dirty/unmerged survive, clean is removed
# ---------------------------------------------------------------------------


def test_run_gc_preserves_unsaved_and_reaps_clean(repo_root: Path) -> None:
    """A batch ``gc`` over mixed worktrees keeps unsaved work, reaps clean."""
    clean = _add_worktree(repo_root, "gc-clean")
    dirty = _add_worktree(repo_root, "gc-dirty")
    (dirty / "scratch.txt").write_text("unsaved")
    unmerged = _add_worktree(repo_root, "gc-unmerged")
    _commit_in_worktree(unmerged, "x.py", "x=1\n")

    rows = classify_worktrees(repo_root)
    reapable = [r for r in rows if r.is_reapable]
    run_gc(repo_root, reapable, dry_run=False)

    assert not clean.exists(), "clean worktree should have been reaped"
    assert dirty.exists(), "dirty worktree must survive gc"
    assert unmerged.exists(), "unmerged worktree must survive gc"


def test_force_unsaved_reaps_dirty_worktree(repo_root: Path) -> None:
    """``--force-unsaved`` deletes a dirty worktree after confirmation."""
    dirty = _add_worktree(repo_root, "force-dirty")
    (dirty / "scratch.txt").write_text("unsaved")

    runner = CliRunner()
    result = runner.invoke(
        worktrees_group,
        ["gc", "--workdir", str(repo_root), "--yes", "--force-unsaved"],
    )
    assert result.exit_code == 0, result.output
    assert not dirty.exists(), "--force-unsaved should reap the dirty worktree"


def test_gc_without_force_reports_safety_skip(repo_root: Path) -> None:
    """Plain ``gc`` reports the unsaved-work skip and leaves the dir."""
    dirty = _add_worktree(repo_root, "skip-dirty")
    (dirty / "scratch.txt").write_text("unsaved")

    runner = CliRunner()
    result = runner.invoke(
        worktrees_group,
        ["gc", "--workdir", str(repo_root), "--yes"],
    )
    assert result.exit_code == 0, result.output
    assert dirty.exists(), "dirty worktree must survive a plain gc"
    assert "unsaved" in result.output.lower() or "skip" in result.output.lower()
