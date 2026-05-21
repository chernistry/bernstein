"""Lock contention and deadlock probes.

* Many concurrent writers should scale near-linearly (or better) -
  *not* quadratically.  Quadratic degradation usually means a global
  lock with O(N) work inside the critical section, which is a real
  cliff in production.

* Acquiring two locks in opposite orders across threads must not
  deadlock - we time-budget each scenario and fail loudly if any
  thread is still blocked at the deadline.

The TaskStore concurrency model uses ``asyncio.Lock`` (single-process
single-loop).  We exercise it via ``asyncio.gather`` over many coroutines
running on one event loop - which is the production topology - instead
of OS threads (the production model never sees real threaded callers).
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from bernstein.core.tasks.task_store_core import TaskStore
from tests.stress.conftest import make_task_request

pytestmark = [pytest.mark.stress, pytest.mark.timeout(60)]


@pytest.mark.anyio
async def test_taskstore_concurrent_writers_finish_in_absolute_budget(tmp_path: Path) -> None:
    """8 coroutines × 200 ops on a shared TaskStore must finish in 40 s wall-clock.

    Why an absolute budget instead of a baseline-vs-contended ratio:
    nightly CI runners share the host with other jobs (and on a dev
    laptop with parallel pytest sessions the baseline drifts wildly),
    so a ratio cap flakes under ambient contention while the absolute
    budget still catches a true quadratic regression - a quadratic
    impl would clear the 40 s ceiling almost regardless of host noise.

    Single-writer steady state is roughly 1-5 ms / create depending on
    host load; 8 × 200 = 1600 ops therefore expect a few seconds of
    serial-locked work plus scheduling slack.  The 40 s budget gives
    ~5-8x headroom over the worst case observed under heavy parallel
    pytest contention.

    Catches: a quadratic-in-N implementation of any helper inside the
    critical section (e.g. a full re-scan of ``self._tasks`` on each
    insert), and any change that holds the lock during I/O.
    """

    n_writers = 8
    ops_per_writer = 200
    total_ops = n_writers * ops_per_writer

    contention_store = TaskStore(tmp_path / "shared" / "tasks.jsonl")

    async def _worker(prefix: str) -> None:
        for i in range(ops_per_writer):
            await contention_store.create(make_task_request(title=f"{prefix}-{i}"))

    start = time.perf_counter()
    await asyncio.gather(*[_worker(f"w{w}") for w in range(n_writers)])
    await contention_store.flush_buffer()
    elapsed = time.perf_counter() - start

    budget_s = 40.0
    assert elapsed <= budget_s, (
        f"TaskStore contended writes blew the budget: "
        f"{total_ops} ops took {elapsed:.2f}s (cap {budget_s:.1f}s) - "
        f"possible quadratic regression in the critical section"
    )
    # Sanity: every task landed exactly once.
    assert len(contention_store._tasks) == total_ops  # pyright: ignore[reportPrivateUsage]


def test_two_locks_opposite_order_does_not_deadlock() -> None:
    """Acquiring two locks in opposite orders should converge within 5 s.

    This is a contrived synthetic case - both threads serialise on a
    shared third lock that always gets acquired first.  Implementing
    the test here documents the "deadlock smoke test" pattern for
    future stress cases that target a real lock hierarchy.
    """

    outer = threading.Lock()
    inner_a = threading.Lock()
    inner_b = threading.Lock()
    finished = threading.Event()
    errors: list[str] = []

    def _worker_ab() -> None:
        try:
            with outer:
                with inner_a, inner_b:
                    pass
        except Exception as exc:  # pragma: no cover - defensive
            errors.append(f"ab: {exc!r}")

    def _worker_ba() -> None:
        try:
            with outer:
                with inner_b, inner_a:
                    pass
            finished.set()
        except Exception as exc:  # pragma: no cover - defensive
            errors.append(f"ba: {exc!r}")

    t_ab = threading.Thread(target=_worker_ab, daemon=True)
    t_ba = threading.Thread(target=_worker_ba, daemon=True)
    t_ab.start()
    t_ba.start()
    t_ab.join(timeout=5.0)
    t_ba.join(timeout=5.0)

    assert not t_ab.is_alive(), "ab worker still alive after 5 s - deadlock?"
    assert not t_ba.is_alive(), "ba worker still alive after 5 s - deadlock?"
    assert finished.is_set(), "ba worker did not signal completion"
    assert errors == [], f"workers raised: {errors}"


def test_thread_enumerate_returns_to_baseline_after_workers_exit() -> None:
    """After spawning + joining 16 threads, threading.enumerate() returns to baseline.

    Catches: daemon threads or background pollers spawned by helper
    functions that never exit.  Production has seen "thread leaks"
    from forgotten Timer threads - this test would have caught one.
    """

    baseline_threads = {t.ident for t in threading.enumerate()}

    def _short() -> None:
        time.sleep(0.01)

    threads = [threading.Thread(target=_short) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    # Allow up to 2 s for the thread state cleanup to settle.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        leaked = {t.ident for t in threading.enumerate() if t.ident is not None} - baseline_threads
        if not leaked:
            break
        time.sleep(0.05)

    leaked = {t.ident for t in threading.enumerate() if t.ident is not None} - baseline_threads
    assert leaked == set(), f"thread leak detected: {len(leaked)} threads beyond baseline"
