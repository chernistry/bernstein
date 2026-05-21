"""Unit tests for scripts/pr_push_lock.sh - advisory parallel-agent PR lock.

Regression guard for the advisory push-lock that prevents two agents
from pushing to the same PR head ref concurrently.

Tests cover the cooperative contract:
  * free file -> status 'free'
  * acquire creates an active record; status -> 'held'
  * release appends a release record; status -> 'free'
  * second agent on held lock -> exit 1 with 'skipping' message
  * expired lock is reclaimable
  * different PRs are independent
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "pr_push_lock.sh"


def _run(args: list[str], lock_file: Path, **env_extra: str):
    """Run pr_push_lock.sh with isolated lock file + fast retry params."""
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "PR_PUSH_LOCK_FILE": str(lock_file),
        "PR_PUSH_LOCK_RETRY_COUNT": "2",
        "PR_PUSH_LOCK_RETRY_SLEEP_SEC": "0",
    } | env_extra
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_script_exists_and_is_executable() -> None:
    assert SCRIPT.exists(), "scripts/pr_push_lock.sh must exist"
    assert SCRIPT.stat().st_mode & 0o111, "scripts/pr_push_lock.sh must be executable"


def test_status_on_missing_lock_file_reports_free(tmp_path: Path) -> None:
    lock = tmp_path / "lock.jsonl"
    result = _run(["status", "1452"], lock)
    assert result.returncode == 0, result.stderr
    assert "lock=free" in result.stdout


def test_acquire_then_status_held(tmp_path: Path) -> None:
    lock = tmp_path / "lock.jsonl"
    acq = _run(["acquire", "1452", "agent-a", "600"], lock)
    assert acq.returncode == 0, acq.stderr
    assert "acquired" in acq.stdout

    status = _run(["status", "1452"], lock)
    assert status.returncode == 0, status.stderr
    assert "lock=held" in status.stdout
    assert "agent=agent-a" in status.stdout


def test_release_makes_lock_free(tmp_path: Path) -> None:
    lock = tmp_path / "lock.jsonl"
    _run(["acquire", "1452", "agent-a", "600"], lock)
    rel = _run(["release", "1452", "agent-a"], lock)
    assert rel.returncode == 0, rel.stderr
    assert "released" in rel.stdout

    status = _run(["status", "1452"], lock)
    assert "lock=free" in status.stdout


def test_second_agent_blocked_on_held_lock(tmp_path: Path) -> None:
    lock = tmp_path / "lock.jsonl"
    _run(["acquire", "1452", "agent-a", "600"], lock)
    result = _run(["acquire", "1452", "agent-b", "600"], lock)
    assert result.returncode == 1, (
        f"second agent must exit 1 when lock is held. stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "lock-held-by=agent-a" in result.stderr


def test_different_prs_independent(tmp_path: Path) -> None:
    lock = tmp_path / "lock.jsonl"
    a = _run(["acquire", "1452", "agent-a", "600"], lock)
    b = _run(["acquire", "1453", "agent-b", "600"], lock)
    assert a.returncode == 0 and b.returncode == 0, (a.stderr, b.stderr)
    assert "lock=held" in _run(["status", "1452"], lock).stdout
    assert "lock=held" in _run(["status", "1453"], lock).stdout


def test_expired_lock_can_be_reclaimed(tmp_path: Path) -> None:
    lock = tmp_path / "lock.jsonl"
    # Acquire with 1-second TTL, sleep past expiry, then re-acquire as
    # a different agent. The expired record must NOT block the new one.
    a = _run(["acquire", "1452", "agent-a", "1"], lock)
    assert a.returncode == 0, a.stderr
    time.sleep(2)
    b = _run(["acquire", "1452", "agent-b", "600"], lock)
    assert b.returncode == 0, f"expired lock should be reclaimable. stdout={b.stdout!r} stderr={b.stderr!r}"
    assert "acquired" in b.stdout
    status = _run(["status", "1452"], lock)
    assert "agent=agent-b" in status.stdout


def test_same_agent_reacquire_succeeds(tmp_path: Path) -> None:
    """An agent that already holds the lock can re-acquire (refresh)."""
    lock = tmp_path / "lock.jsonl"
    a1 = _run(["acquire", "1452", "agent-a", "600"], lock)
    a2 = _run(["acquire", "1452", "agent-a", "600"], lock)
    assert a1.returncode == 0 and a2.returncode == 0
