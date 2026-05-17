"""Integration tests for the atomic ``claim_next`` primitive under contention.

These tests exercise the *integration* surface of the file-backed backlog
claim primitive — multiple OS processes and multiple in-process threads
racing for the same backlog file. They complement the unit tests in
``tests/unit/test_claim_next.py``, which cover happy-path filter logic
under no contention.

Failure modes covered (would have caught issue #1261 class of race
conditions):

| Mode                                     | Test |
|------------------------------------------|------|
| Same-process thread race -> double-claim | ``test_threads_never_double_claim`` |
| Cross-process race -> double-claim       | ``test_subprocesses_never_double_claim`` |
| Worker crash after claim, before run     | ``test_orphan_claim_visible_for_reclaim`` |
| Lease churn under load (10 workers, 50 tasks) | ``test_high_contention_each_task_claimed_once`` |
| Role filter interleaved with race        | ``test_role_filtered_race_respects_role`` |
| Backlog mutated mid-flight (append)      | ``test_appended_task_visible_to_next_claim`` |

No ``time.sleep`` for synchronization — barriers, queues, and process
joins only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from bernstein.core.tasks.claim import (
    Backlog,
    BacklogEntry,
    ClaimFilter,
    claim_next,
    claim_next_entry,
)

pytestmark = [pytest.mark.integration]


def _rows(path: Path) -> list[dict[str, object]]:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Same-process thread races
# ---------------------------------------------------------------------------


def test_threads_never_double_claim(tmp_path: Path) -> None:
    """N threads racing for M tasks: every task claimed exactly once."""
    backlog_path = tmp_path / "backlog.json"
    n_tasks = 20
    n_threads = 8

    Backlog.write(
        backlog_path,
        [BacklogEntry(id=f"task-{i:03d}", role="backend") for i in range(n_tasks)],
    )

    barrier = threading.Barrier(n_threads)
    claimed_by_worker: list[list[str]] = [[] for _ in range(n_threads)]

    def _worker(worker_idx: int) -> None:
        barrier.wait()  # release all threads simultaneously
        while True:
            claimed = claim_next(
                backlog_path,
                claimer_id=f"worker-{worker_idx}",
                filter=ClaimFilter(role="backend"),
            )
            if claimed is None:
                return
            claimed_by_worker[worker_idx].append(claimed)

    with ThreadPoolExecutor(max_workers=n_threads) as pool:
        futures = [pool.submit(_worker, i) for i in range(n_threads)]
        for fut in as_completed(futures):
            fut.result()  # surface any thread exceptions

    flat = [tid for lst in claimed_by_worker for tid in lst]
    assert sorted(flat) == [f"task-{i:03d}" for i in range(n_tasks)], (
        f"expected {n_tasks} unique claims; got {len(flat)} (duplicates={len(flat) - len(set(flat))})"
    )
    assert len(flat) == len(set(flat)), "double-claim detected"


def test_threads_with_at_least_one_idle_returns_none(tmp_path: Path) -> None:
    """When threads outnumber tasks the excess threads see None and exit cleanly."""
    backlog_path = tmp_path / "backlog.json"
    Backlog.write(
        backlog_path,
        [BacklogEntry(id="only-task", role="backend")],
    )

    n_threads = 5
    barrier = threading.Barrier(n_threads)
    outcomes: list[str | None] = [None] * n_threads

    def _worker(idx: int) -> None:
        barrier.wait()
        outcomes[idx] = claim_next(
            backlog_path,
            claimer_id=f"worker-{idx}",
            filter=ClaimFilter(role="backend"),
        )

    with ThreadPoolExecutor(max_workers=n_threads) as pool:
        list(pool.map(_worker, range(n_threads)))

    successes = [o for o in outcomes if o is not None]
    assert successes == ["only-task"], outcomes
    assert outcomes.count(None) == n_threads - 1


def test_role_filtered_race_respects_role(tmp_path: Path) -> None:
    """Two role pools racing: each pool only ever sees its own role's tasks."""
    backlog_path = tmp_path / "backlog.json"
    Backlog.write(
        backlog_path,
        [
            *[BacklogEntry(id=f"b-{i:02d}", role="backend") for i in range(6)],
            *[BacklogEntry(id=f"q-{i:02d}", role="qa") for i in range(6)],
        ],
    )

    n_per_role = 4
    n_total = n_per_role * 2
    barrier = threading.Barrier(n_total)
    claimed_backend: list[str] = []
    claimed_qa: list[str] = []
    lock = threading.Lock()

    def _claim_role(role: str, sink: list[str], idx: int) -> None:
        barrier.wait()
        while True:
            claimed = claim_next(
                backlog_path,
                claimer_id=f"{role}-{idx}",
                filter=ClaimFilter(role=role),
            )
            if claimed is None:
                return
            with lock:
                sink.append(claimed)

    with ThreadPoolExecutor(max_workers=n_total) as pool:
        futures = []
        for i in range(n_per_role):
            futures.append(pool.submit(_claim_role, "backend", claimed_backend, i))
            futures.append(pool.submit(_claim_role, "qa", claimed_qa, i))
        for fut in as_completed(futures):
            fut.result()

    assert all(tid.startswith("b-") for tid in claimed_backend), claimed_backend
    assert all(tid.startswith("q-") for tid in claimed_qa), claimed_qa
    assert sorted(claimed_backend) == [f"b-{i:02d}" for i in range(6)]
    assert sorted(claimed_qa) == [f"q-{i:02d}" for i in range(6)]


# ---------------------------------------------------------------------------
# Cross-process races
# ---------------------------------------------------------------------------


_SUBPROC_CLAIMER = textwrap.dedent(
    """
    import json
    import os
    import sys
    from pathlib import Path
    from bernstein.core.tasks.claim import ClaimFilter, claim_next

    backlog_path = Path(os.environ["BACKLOG_PATH"])
    claimer_id = os.environ["CLAIMER_ID"]
    role = os.environ.get("CLAIM_ROLE") or None

    claimed = []
    while True:
        cid = claim_next(
            backlog_path,
            claimer_id=claimer_id,
            filter=ClaimFilter(role=role) if role else ClaimFilter(),
        )
        if cid is None:
            break
        claimed.append(cid)

    sys.stdout.write(json.dumps(claimed))
    """
).strip()


def _run_claimer_process(
    backlog_path: Path,
    claimer_id: str,
    *,
    role: str | None = None,
) -> subprocess.Popen[str]:
    env = dict(os.environ)
    env["BACKLOG_PATH"] = str(backlog_path)
    env["CLAIMER_ID"] = claimer_id
    if role is not None:
        env["CLAIM_ROLE"] = role
    return subprocess.Popen(
        [sys.executable, "-c", _SUBPROC_CLAIMER],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_subprocesses_never_double_claim(tmp_path: Path) -> None:
    """5 OS processes racing for 10 tasks — each task claimed exactly once.

    Reproduces the multi-worker production layout. The file-level advisory
    lock (``flock`` / ``msvcrt``) must serialise claims across PIDs, not
    just across threads in the same PID.
    """
    backlog_path = tmp_path / "backlog.json"
    n_tasks = 10
    n_workers = 5
    Backlog.write(
        backlog_path,
        [BacklogEntry(id=f"task-{i:03d}", role="backend") for i in range(n_tasks)],
    )

    procs = [
        _run_claimer_process(backlog_path, f"worker-{i}", role="backend")
        for i in range(n_workers)
    ]

    all_claimed: list[str] = []
    for proc in procs:
        stdout, stderr = proc.communicate(timeout=20)
        assert proc.returncode == 0, f"worker failed: rc={proc.returncode}\nstderr={stderr}"
        worker_claimed = json.loads(stdout)
        all_claimed.extend(worker_claimed)

    assert sorted(all_claimed) == [f"task-{i:03d}" for i in range(n_tasks)], (
        f"missing/extra task ids; got {sorted(all_claimed)}"
    )
    assert len(all_claimed) == len(set(all_claimed)), "double-claim across processes"


def test_high_contention_each_task_claimed_once(tmp_path: Path) -> None:
    """10 workers competing for 50 tasks via mixed thread+process pool.

    Mixes threads (4) and subprocesses (3) to stress *both* the OS-level
    file lock AND the in-process thread lock at once.
    """
    backlog_path = tmp_path / "backlog.json"
    n_tasks = 50
    Backlog.write(
        backlog_path,
        [BacklogEntry(id=f"t-{i:03d}", role="backend") for i in range(n_tasks)],
    )

    # 3 subprocesses
    procs = [_run_claimer_process(backlog_path, f"proc-{i}", role="backend") for i in range(3)]

    # 4 in-process threads
    thread_claims: list[str] = []
    thread_lock = threading.Lock()
    barrier = threading.Barrier(4)

    def _thread_claimer(idx: int) -> None:
        barrier.wait()
        while True:
            cid = claim_next(
                backlog_path,
                claimer_id=f"thr-{idx}",
                filter=ClaimFilter(role="backend"),
            )
            if cid is None:
                return
            with thread_lock:
                thread_claims.append(cid)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_thread_claimer, i) for i in range(4)]
        for fut in as_completed(futures):
            fut.result()

    proc_claims: list[str] = []
    for proc in procs:
        stdout, stderr = proc.communicate(timeout=20)
        assert proc.returncode == 0, stderr
        proc_claims.extend(json.loads(stdout))

    all_claims = thread_claims + proc_claims
    assert len(all_claims) == n_tasks, f"got {len(all_claims)}/{n_tasks}"
    assert len(set(all_claims)) == n_tasks, "duplicate claims"
    assert sorted(all_claims) == [f"t-{i:03d}" for i in range(n_tasks)]


# ---------------------------------------------------------------------------
# Crash-after-claim / orphan reclaim
# ---------------------------------------------------------------------------


def test_orphan_claim_visible_for_reclaim(tmp_path: Path) -> None:
    """Worker crashes after claim; the row stays ``in_progress`` and is
    observable on disk so a watchdog can reset it to ``open``.

    This is the orphan-task contract: ``claim_next`` is *not* responsible
    for lease expiry — the watchdog is — but the artefact ``claim_next``
    leaves behind (claimer + claimed_at + in_progress) must be sufficient
    for a watchdog to recognise and revert. This test pins that contract.
    """
    backlog_path = tmp_path / "backlog.json"
    Backlog.write(
        backlog_path,
        [BacklogEntry(id="will-orphan", role="backend")],
    )

    # Phase 1: claim
    claimed = claim_next_entry(
        backlog_path,
        claimer_id="crashed-worker",
        filter=ClaimFilter(role="backend"),
    )
    assert claimed is not None
    assert claimed.id == "will-orphan"

    # Phase 2: simulate crash — no further action. The row is now stranded.
    rows = _rows(backlog_path)
    assert rows[0]["status"] == "in_progress"
    assert rows[0]["claimer"] == "crashed-worker"
    assert isinstance(rows[0]["claimed_at"], float)
    crashed_at = rows[0]["claimed_at"]

    # Phase 3: a watchdog reverts the row by rewriting the file.
    revived = Backlog(path=backlog_path)
    revived.entries = [BacklogEntry.from_dict(rows[0])]
    revived.entries[0].status = "open"
    revived.entries[0].claimer = None
    revived.entries[0].claimed_at = None
    revived.save()

    # Phase 4: a fresh worker can claim it. attempts increments to 2
    # (the original claim left attempts=1).
    new_claim = claim_next_entry(
        backlog_path,
        claimer_id="recovery-worker",
        filter=ClaimFilter(role="backend"),
    )
    assert new_claim is not None
    assert new_claim.id == "will-orphan"
    assert new_claim.claimer == "recovery-worker"
    assert new_claim.attempts == 2
    assert new_claim.claimed_at is not None and new_claim.claimed_at >= crashed_at


def test_attempts_ceiling_blocks_reclaim_when_max_reached(tmp_path: Path) -> None:
    """A row that has hit ``max_attempts`` is invisible to ``claim_next``.

    Prevents perpetual reclaim loops on a poison task. The orphan task
    must be visible only to an out-of-band tool (e.g. dead-letter queue),
    not to a normal worker poll.
    """
    backlog_path = tmp_path / "backlog.json"
    Backlog.write(
        backlog_path,
        [BacklogEntry(id="poison", role="backend", attempts=3, max_attempts=3)],
    )

    # No worker should ever claim a row at or above the per-row ceiling.
    assert claim_next(backlog_path, claimer_id="w-1", filter=ClaimFilter(role="backend")) is None
    # And the row state is unchanged on disk.
    rows = _rows(backlog_path)
    assert rows[0]["status"] == "open"
    assert rows[0]["claimer"] is None
    assert rows[0]["attempts"] == 3


# ---------------------------------------------------------------------------
# Backlog mutated mid-flight
# ---------------------------------------------------------------------------


def test_appended_task_visible_to_next_claim(tmp_path: Path) -> None:
    """A task appended to the backlog file between claims is picked up.

    Models the manager spawn pattern: manager appends a row to the
    backlog, then the next worker poll must see it. If ``claim_next``
    cached the file contents it would miss the new row.
    """
    backlog_path = tmp_path / "backlog.json"
    Backlog.write(backlog_path, [BacklogEntry(id="t1", role="backend")])

    # First claim consumes t1.
    first = claim_next(backlog_path, claimer_id="w-1", filter=ClaimFilter(role="backend"))
    assert first == "t1"

    # Manager appends t2 + t3 by rewriting the backlog (the production
    # path uses ``Backlog.write`` for atomicity).
    rows = _rows(backlog_path)
    rows.append({"id": "t2", "role": "backend", "status": "open", "claimer": None})
    rows.append({"id": "t3", "role": "backend", "status": "open", "claimer": None})
    backlog_path.write_text(json.dumps(rows))

    # Next claim must see t2.
    second = claim_next(backlog_path, claimer_id="w-2", filter=ClaimFilter(role="backend"))
    assert second == "t2"

    # And the third.
    third = claim_next(backlog_path, claimer_id="w-3", filter=ClaimFilter(role="backend"))
    assert third == "t3"
