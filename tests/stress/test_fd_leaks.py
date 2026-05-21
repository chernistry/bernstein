"""File-descriptor leak detection.

Each test does work that opens transient handles and then asserts the
fd count returned to (near) baseline.  Tolerances allow a few fds of
slack - pytest's capture machinery and lazy logger handlers can churn
1-2 fds on first touch, but those churns settle quickly.

Bug class: forgotten ``with``s, exception paths that bypass
``close()``, subprocess streams that never get drained, JSONL append
helpers that open per-write but never explicitly close.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from bernstein.core.security.audit import AuditLog
from bernstein.core.tasks.task_store_core import TaskStore
from tests.stress._probes import fd_count, settle

pytestmark = [pytest.mark.stress, pytest.mark.timeout(60)]


def _require_fd_count() -> int:
    """Return current fd count or skip when unavailable."""

    count = fd_count()
    if count is None:
        pytest.skip("fd-count probe unavailable on this platform")
    return count


@pytest.mark.anyio
async def test_task_store_construct_replay_no_fd_leak(tmp_path: Path) -> None:
    """100 TaskStore construct + replay cycles must leak ≤ 2 fds.

    Catches: ``replay_jsonl`` (or any helper it calls) leaving a file
    handle behind.  TaskStore opens its JSONL file repeatedly across
    its lifetime; if any read path skips the ``with`` block we'd see
    fd count climb monotonically.
    """

    jsonl = tmp_path / "runtime" / "tasks.jsonl"
    jsonl.parent.mkdir(parents=True, exist_ok=True)
    jsonl.write_text("")  # ensure file exists for replay path

    # Warm-up - first construction touches lazy imports + opens the
    # archive directory.  Measure after that settles.
    warm = TaskStore(jsonl)
    warm.replay_jsonl()
    del warm
    settle()
    fds_before = _require_fd_count()

    for _ in range(100):
        store = TaskStore(jsonl)
        store.replay_jsonl()
        del store
    settle()
    fds_after = _require_fd_count()

    delta = fds_after - fds_before
    assert delta <= 2, (
        f"TaskStore construct/replay leaked fds: before={fds_before} after={fds_after} delta={delta} (budget 2)"
    )


def test_subprocess_spawn_reap_no_fd_leak() -> None:
    """50 subprocess spawn/wait cycles must leak ≤ 5 fds.

    Catches: pipe descriptors that survive ``communicate()`` or
    Popen objects that retain their stdin/stdout references after the
    child exits.  We use ``capture_output=True`` so each spawn opens
    two pipes - a leak path would compound quickly.
    """

    # Warm-up to settle one-time fd allocations.
    subprocess.run([sys.executable, "-c", "pass"], check=True, capture_output=True)
    settle()
    fds_before = _require_fd_count()

    for _ in range(50):
        completed = subprocess.run(
            [sys.executable, "-c", "pass"],
            check=True,
            capture_output=True,
            timeout=10,
        )
        assert completed.returncode == 0
    settle()
    fds_after = _require_fd_count()

    delta = fds_after - fds_before
    assert delta <= 5, (
        f"Subprocess spawn/reap leaked fds: before={fds_before} after={fds_after} delta={delta} (budget 5)"
    )


def test_audit_log_append_then_query_no_fd_leak(tmp_path: Path) -> None:
    """1000 audit appends + 100 reads must leak ≤ 2 fds.

    Catches: AuditLog.log() opening but never closing the daily file
    (it uses a ``with`` block today, but stress catches future
    regressions where someone replaces ``with`` with a long-lived
    cached handle).
    """

    key = b"k" * 32
    log = AuditLog(tmp_path / "audit", key=key)
    for _ in range(50):  # warm-up
        log.log("warm", "tester", "task", "warm-0")
    settle()
    fds_before = _require_fd_count()

    for i in range(1000):
        log.log("event", "actor", "task", f"r-{i}")

    # Read the daily log files 100 times to also exercise the read
    # paths (verify + manual file scans).
    for _ in range(100):
        ok, errors = log.verify()
        assert ok, f"chain broke during stress: {errors[:3]}"
    settle()
    fds_after = _require_fd_count()

    delta = fds_after - fds_before
    assert delta <= 2, (
        f"AuditLog append/read leaked fds: before={fds_before} after={fds_after} delta={delta} (budget 2)"
    )
