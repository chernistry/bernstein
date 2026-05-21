"""Unit tests for the ``bernstein worktrees`` subcommand and classifier.

Every fixture builds an isolated ``.sdd/`` tree under ``tmp_path`` - the
suite NEVER touches the real repo's 30+ worktrees.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from bernstein.cli.commands.worktrees_cmd import (
    GcLockError,
    format_age,
    lock_gc,
    render_worktrees_table,
    run_gc,
    worktrees_group,
)
from bernstein.core.worktrees.classifier import (
    GC_LOCK_RELPATH,
    STALE_TRACE_AGE_S,
    ClassifiedWorktree,
    WorktreeState,
    classify_worktrees,
    format_size,
    iter_worktree_dirs,
    reap_worktree,
    worktrees_root,
)

# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _init_repo(repo_root: Path) -> None:
    """Initialise a bare git repo with one main commit at ``repo_root``."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo_root)], check=True)
    (repo_root / "seed.txt").write_text("seed")
    subprocess.run(["git", "-C", str(repo_root), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "-c",
            "user.email=test@bernstein",
            "-c",
            "user.name=test",
            "commit",
            "-q",
            "-m",
            "seed",
        ],
        check=True,
    )


def _make_worktree_dir(repo_root: Path, session_id: str, *, with_git: bool = True) -> Path:
    """Create a fake worktree directory under ``.sdd/runtime/worktrees``.

    We do not actually call ``git worktree add`` - the classifier only
    needs the directory layout (`.git` anchor + size on disk).
    """
    base = repo_root / ".sdd" / "runtime" / "worktrees"
    base.mkdir(parents=True, exist_ok=True)
    wt = base / session_id
    wt.mkdir()
    (wt / "file.txt").write_text("hello")
    if with_git:
        (wt / ".git").write_text("gitdir: /fake")
    return wt


def _write_pid_record(repo_root: Path, session_id: str, *, pid: int, task_id: str | None = None) -> None:
    pid_dir = repo_root / ".sdd" / "runtime" / "pids"
    pid_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {"worker_pid": pid}
    if task_id is not None:
        payload["task_id"] = task_id
    (pid_dir / f"{session_id}.json").write_text(json.dumps(payload))


def _write_trace(repo_root: Path, session_id: str, *, mtime: float | None = None) -> Path:
    trace_dir = repo_root / ".sdd" / "traces"
    trace_dir.mkdir(parents=True, exist_ok=True)
    path = trace_dir / f"{session_id}.jsonl"
    path.write_text("{}\n")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


@pytest.fixture()
def repo_root(tmp_path: Path) -> Path:
    """Initialised throw-away repo root."""
    _init_repo(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Classifier - 4-state matrix
# ---------------------------------------------------------------------------


def test_classifier_active(repo_root: Path) -> None:
    """Live PID + task record + recent trace => active."""
    sid = "alive"
    _make_worktree_dir(repo_root, sid)
    _write_pid_record(repo_root, sid, pid=os.getpid(), task_id="task-1")
    _write_trace(repo_root, sid)

    rows = classify_worktrees(repo_root)
    assert len(rows) == 1
    assert rows[0].state is WorktreeState.ACTIVE
    assert rows[0].task_id == "task-1"
    assert rows[0].pid_alive is True


def test_classifier_orphan(repo_root: Path) -> None:
    """Directory exists but no PID record => orphan."""
    sid = "orph"
    _make_worktree_dir(repo_root, sid)

    rows = classify_worktrees(repo_root)
    assert rows[0].state is WorktreeState.ORPHAN
    assert rows[0].is_reapable is True


def test_classifier_stale(repo_root: Path) -> None:
    """Dead PID + trace older than 24h => stale."""
    sid = "stale"
    _make_worktree_dir(repo_root, sid)
    # Use a PID very unlikely to be in use.
    dead_pid = 999_999_998
    _write_pid_record(repo_root, sid, pid=dead_pid, task_id="task-stale")
    long_ago = time.time() - (STALE_TRACE_AGE_S + 3600)
    _write_trace(repo_root, sid, mtime=long_ago)

    rows = classify_worktrees(repo_root)
    assert rows[0].state is WorktreeState.STALE
    assert rows[0].is_reapable is True


def test_classifier_corrupt(repo_root: Path) -> None:
    """Directory has no .git anchor => corrupt."""
    sid = "corrupt"
    _make_worktree_dir(repo_root, sid, with_git=False)

    rows = classify_worktrees(repo_root)
    assert rows[0].state is WorktreeState.CORRUPT
    assert rows[0].is_reapable is True


def test_classifier_dead_pid_recent_trace_stays_active(repo_root: Path) -> None:
    """Dead PID but recent trace => still active (avoid racing).

    We refuse to reap a worktree whose trace is fresh, even if its PID
    has gone away - the agent may simply be restarting.
    """
    sid = "race"
    _make_worktree_dir(repo_root, sid)
    _write_pid_record(repo_root, sid, pid=999_999_997, task_id="task-race")
    _write_trace(repo_root, sid)  # mtime = now

    rows = classify_worktrees(repo_root)
    assert rows[0].state is WorktreeState.ACTIVE
    assert rows[0].is_reapable is False


def test_iter_worktree_dirs_skips_locks(repo_root: Path) -> None:
    """The bookkeeping ``.locks`` directory is excluded from iteration."""
    _make_worktree_dir(repo_root, "real")
    locks = worktrees_root(repo_root) / ".locks"
    locks.mkdir()
    (locks / "foo.lock").write_text("")
    assert [p.name for p in iter_worktree_dirs(repo_root)] == ["real"]


# ---------------------------------------------------------------------------
# Lock - concurrency safety
# ---------------------------------------------------------------------------


def test_lock_prevents_concurrent_gc(repo_root: Path) -> None:
    """A second ``lock_gc`` while the first is held raises ``GcLockError``."""
    with lock_gc(repo_root):
        with pytest.raises(GcLockError):
            with lock_gc(repo_root):
                pass


def test_lock_released_on_exception(repo_root: Path) -> None:
    """The lock file is unlinked even when the body raises."""

    class Boom(RuntimeError):
        pass

    with pytest.raises(Boom):
        with lock_gc(repo_root):
            raise Boom

    # Lock should be gone - next acquisition succeeds.
    with lock_gc(repo_root):
        pass


def test_lock_held_by_thread_blocks_second(repo_root: Path) -> None:
    """Concurrent threads must serialise through the lock file."""
    started = threading.Event()
    release = threading.Event()
    errors: list[BaseException] = []

    def hold() -> None:
        try:
            with lock_gc(repo_root):
                started.set()
                release.wait(timeout=5)
        except BaseException as exc:  # pragma: no cover - propagated below
            errors.append(exc)

    holder = threading.Thread(target=hold)
    holder.start()
    started.wait(timeout=5)
    try:
        with pytest.raises(GcLockError):
            with lock_gc(repo_root):
                pass
    finally:
        release.set()
        holder.join(timeout=5)
    assert not errors


# ---------------------------------------------------------------------------
# Reap behaviour - --dry and real deletion
# ---------------------------------------------------------------------------


def _orphan_row(repo_root: Path, sid: str) -> ClassifiedWorktree:
    """Convenience: classify a single orphan worktree."""
    rows = classify_worktrees(repo_root)
    matching = [r for r in rows if r.session_id == sid]
    assert matching, f"no row for {sid}"
    return matching[0]


def test_reap_worktree_dry_run_does_not_touch_disk(repo_root: Path) -> None:
    """``dry_run=True`` leaves the directory in place."""
    sid = "dry"
    wt = _make_worktree_dir(repo_root, sid)
    row = _orphan_row(repo_root, sid)
    assert row.state is WorktreeState.ORPHAN

    assert reap_worktree(repo_root, row, dry_run=True) is True
    assert wt.exists()
    assert (wt / "file.txt").read_text() == "hello"


def test_reap_worktree_real_removes_directory(repo_root: Path) -> None:
    """Without ``dry_run`` the directory is gone after the call."""
    sid = "real"
    wt = _make_worktree_dir(repo_root, sid)
    row = _orphan_row(repo_root, sid)

    assert reap_worktree(repo_root, row, dry_run=False) is True
    assert not wt.exists()


def test_run_gc_invokes_git_prune(repo_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``run_gc`` runs ``git worktree prune`` after each reap."""
    sid = "pruned"
    _make_worktree_dir(repo_root, sid)
    row = _orphan_row(repo_root, sid)

    captured: list[list[str]] = []
    real_run = subprocess.run

    def fake_run(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
        if isinstance(cmd, list) and cmd[:3] == ["git", "worktree", "prune"]:
            captured.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)

    run_gc(repo_root, [row], dry_run=False)
    assert any(c[:3] == ["git", "worktree", "prune"] for c in captured)


def test_run_gc_dry_run_skips_prune(repo_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``--dry`` must not invoke ``git worktree prune`` either."""
    sid = "dry-prune"
    _make_worktree_dir(repo_root, sid)
    row = _orphan_row(repo_root, sid)

    captured: list[list[str]] = []
    real_run = subprocess.run

    def fake_run(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
        if isinstance(cmd, list) and cmd[:3] == ["git", "worktree", "prune"]:
            captured.append(list(cmd))
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)

    run_gc(repo_root, [row], dry_run=True)
    assert captured == []
    assert row.path.exists()  # filesystem untouched


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def test_format_age_buckets() -> None:
    assert format_age(0) == "0s"
    assert format_age(59) == "59s"
    assert format_age(60) == "1m"
    assert format_age(3600) == "1h 00m"
    assert format_age(86_400) == "1d 00h"
    assert format_age(86_400 + 7200) == "1d 02h"


def test_format_size_units() -> None:
    assert format_size(0) == "0 B"
    assert format_size(1023) == "1023 B"
    assert format_size(1024) == "1.0 KB"
    assert format_size(1024 * 1024) == "1.0 MB"


def test_render_table_has_columns() -> None:
    """Smoke test that the table builder accepts classifier output."""
    row = ClassifiedWorktree(
        path=Path("/tmp/x"),
        session_id="x",
        task_id=None,
        state=WorktreeState.ORPHAN,
        age_seconds=300,
        size_bytes=2048,
        pid=None,
        pid_alive=False,
        last_trace_mtime=None,
    )
    table = render_worktrees_table([row])
    headers = [col.header for col in table.columns]
    assert headers == ["Path", "Task", "State", "Age", "Size", "PID"]


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


def test_cli_list_renders_table(repo_root: Path) -> None:
    """``worktrees list`` prints a table with one row per worktree."""
    _make_worktree_dir(repo_root, "alpha")
    _make_worktree_dir(repo_root, "beta")
    runner = CliRunner()
    result = runner.invoke(worktrees_group, ["list", "--workdir", str(repo_root)])
    assert result.exit_code == 0, result.output
    assert "alpha" in result.output
    assert "beta" in result.output
    assert "orphan" in result.output


def test_cli_list_json_output(repo_root: Path) -> None:
    """``--json`` returns a parseable JSON array."""
    _make_worktree_dir(repo_root, "alpha")
    runner = CliRunner()
    result = runner.invoke(worktrees_group, ["list", "--workdir", str(repo_root), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert len(payload) == 1
    assert payload[0]["state"] == "orphan"
    assert payload[0]["reapable"] is True


def test_cli_gc_dry_run_keeps_disk(repo_root: Path) -> None:
    """``gc --dry --yes`` reports work but never deletes."""
    wt = _make_worktree_dir(repo_root, "gc-dry")
    runner = CliRunner()
    result = runner.invoke(
        worktrees_group,
        ["gc", "--workdir", str(repo_root), "--yes", "--dry"],
    )
    assert result.exit_code == 0, result.output
    assert wt.exists()
    assert "Would remove" in result.output


def test_cli_gc_yes_deletes(repo_root: Path) -> None:
    """``gc --yes`` skips the prompt and removes orphans."""
    wt = _make_worktree_dir(repo_root, "gc-real")
    runner = CliRunner()
    result = runner.invoke(worktrees_group, ["gc", "--workdir", str(repo_root), "--yes"])
    assert result.exit_code == 0, result.output
    assert not wt.exists()


def test_cli_gc_no_reapable(repo_root: Path) -> None:
    """When everything is active, ``gc`` is a no-op with friendly output."""
    sid = "live"
    _make_worktree_dir(repo_root, sid)
    _write_pid_record(repo_root, sid, pid=os.getpid(), task_id="t")
    _write_trace(repo_root, sid)
    runner = CliRunner()
    result = runner.invoke(worktrees_group, ["gc", "--workdir", str(repo_root), "--yes"])
    assert result.exit_code == 0
    assert "nothing to do" in result.output.lower()


def test_cli_gc_concurrent_lock_collision(repo_root: Path) -> None:
    """Holding the lock externally makes ``gc`` exit with code 2."""
    lock_path = repo_root / GC_LOCK_RELPATH
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("{}")
    _make_worktree_dir(repo_root, "blocked")

    runner = CliRunner()
    result = runner.invoke(worktrees_group, ["gc", "--workdir", str(repo_root), "--yes"])
    assert result.exit_code == 2
    assert "already running" in result.output.lower()
