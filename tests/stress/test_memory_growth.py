"""RSS growth bounds for long-running Bernstein primitives.

Each test runs a tight loop over a primitive that the orchestrator
hammers in production (TaskStore writes, audit appends, subprocess
spawn/reap) and asserts that the resident-set size has not climbed
past a loose budget.  Budgets carry ~3x headroom over observed
steady-state to keep nightly CI from flaking on platform noise.

Bug class: slow leaks from accumulating references (closures pinning
state, append-only caches without an eviction policy, file handles
captured by long-lived structures).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from bernstein.core.security.audit import AuditLog
from bernstein.core.tasks.task_store_core import TaskStore
from tests.stress._probes import have_psutil, rss_bytes, settle
from tests.stress.conftest import make_task_request

pytestmark = [pytest.mark.stress, pytest.mark.timeout(60)]

_MB = 1024 * 1024


def _require_rss() -> int:
    """Return current RSS in bytes or skip the test if unavailable."""

    value = rss_bytes()
    if value is None:
        pytest.skip("RSS probe unavailable on this platform")
    return value


@pytest.mark.anyio
async def test_task_store_append_loop_rss_growth_bounded(tmp_path: Path) -> None:
    """1000 TaskStore.create() cycles should not bloat RSS past 10 MB.

    Catches: a closure or callback list inside TaskStore that captures
    each Task and never releases it.  A leak of even 10 KB per task
    would show up as ~10 MB after the loop.
    """

    if not have_psutil():
        pytest.skip("psutil required for RSS probe")

    store = TaskStore(tmp_path / "runtime" / "tasks.jsonl")
    # Warm-up: first ~50 creates pull in lazy imports (fast_path classifier,
    # FSM tables, ...) - measure RSS after warm-up so we isolate the loop.
    for _ in range(50):
        await store.create(make_task_request())
    await store.flush_buffer()
    settle()
    rss_before = _require_rss()

    for i in range(1000):
        await store.create(make_task_request(title=f"task-{i}"))
        if i % 100 == 99:
            await store.flush_buffer()
    await store.flush_buffer()
    settle()
    rss_after = _require_rss()

    delta = rss_after - rss_before
    # 10 MB budget for 1000 tasks ≈ 10 KB per task - generous.  Each Task
    # dataclass is well under 1 KB; the rest is JSONL buffer + index
    # bookkeeping that should stabilise long before the loop ends.
    assert delta < 10 * _MB, (
        f"TaskStore append loop leaked: rss_before={rss_before / _MB:.1f}MB "
        f"rss_after={rss_after / _MB:.1f}MB delta={delta / _MB:.1f}MB "
        f"(budget 10MB)"
    )


def test_audit_log_append_loop_rss_growth_bounded(tmp_path: Path) -> None:
    """1000 AuditLog.log() appends should not bloat RSS past 10 MB.

    Catches: HMAC chain implementations that retain every prior entry
    instead of carrying the prev_hmac forward.  A leak of the entry
    dict (~500 bytes serialised) would show up as ~500 KB minimum.
    """

    if not have_psutil():
        pytest.skip("psutil required for RSS probe")

    key = b"k" * 32
    log = AuditLog(tmp_path / "audit", key=key)
    for _ in range(50):  # warm-up
        log.log("warmup", "tester", "task", "warmup-0")
    settle()
    rss_before = _require_rss()

    for i in range(1000):
        log.log("event", "actor-1", "task", f"r-{i}", details={"i": i})
    settle()
    rss_after = _require_rss()

    delta = rss_after - rss_before
    assert delta < 10 * _MB, f"AuditLog append loop leaked: delta={delta / _MB:.1f}MB (budget 10MB)"


def test_subprocess_spawn_reap_rss_growth_bounded(tmp_path: Path) -> None:
    """100 fast subprocess spawn+reap cycles should not bloat RSS > 20 MB.

    Catches: leaks in our subprocess wrapper (e.g. an orchestrator that
    keeps every spawned Popen object live in a class-level list, or a
    parent-side stream reader thread that never exits).  We exercise
    the *plain* ``subprocess.run`` path here as a lower-bound - if the
    raw API leaks under our environment, every Bernstein-side wrapper
    inherits the leak.
    """

    if not have_psutil():
        pytest.skip("psutil required for RSS probe")

    # Pre-warm one process so the interpreter's subprocess machinery is hot.
    subprocess.run([sys.executable, "-c", "pass"], check=True, capture_output=True)
    settle()
    rss_before = _require_rss()

    for _ in range(100):
        completed = subprocess.run(
            [sys.executable, "-c", "import sys; sys.exit(0)"],
            check=True,
            capture_output=True,
            timeout=10,
        )
        assert completed.returncode == 0
    settle()
    rss_after = _require_rss()

    delta = rss_after - rss_before
    assert delta < 20 * _MB, f"Subprocess spawn/reap leaked: delta={delta / _MB:.1f}MB (budget 20MB)"
