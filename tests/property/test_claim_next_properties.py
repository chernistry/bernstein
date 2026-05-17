"""Contention properties for the atomic ``claim_next`` primitive."""

from __future__ import annotations

import random
import threading
from collections import Counter
from pathlib import Path

from bernstein.core.tasks.claim import Backlog, BacklogEntry, ClaimFilter, claim_next


def _spawn_claimers(backlog_path: Path, *, workers: int, total_calls: int) -> list[str | None]:
    """Start workers at the same time and collect exactly ``total_calls`` results."""
    barrier = threading.Barrier(workers)
    results: list[str | None] = []
    results_lock = threading.Lock()

    calls_by_worker = [total_calls // workers] * workers
    for idx in range(total_calls % workers):
        calls_by_worker[idx] += 1

    def _runner(worker_idx: int) -> None:
        local: list[str | None] = []
        barrier.wait()
        for _ in range(calls_by_worker[worker_idx]):
            local.append(
                claim_next(
                    backlog_path,
                    claimer_id=f"worker-{worker_idx}",
                    filter=ClaimFilter(role="reviewer"),
                )
            )
        with results_lock:
            results.extend(local)

    threads = [threading.Thread(target=_runner, args=(idx,), daemon=True) for idx in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()
    return results


def test_no_double_claim_with_32_threads_1000_calls_across_10_fuzz_seeds(tmp_path: Path) -> None:
    """A 100-item backlog under hard contention is drained once, then returns None."""
    for seed in range(10):
        backlog_path = tmp_path / f"backlog-{seed}.json"
        entries = [BacklogEntry(id=f"task-{i}", role="reviewer") for i in range(100)]
        random.Random(seed).shuffle(entries)
        Backlog.write(backlog_path, entries)

        results = _spawn_claimers(backlog_path, workers=32, total_calls=1000)

        claimed = [result for result in results if result is not None]
        empty = [result for result in results if result is None]
        counts = Counter(claimed)
        final = Backlog.load(backlog_path)

        assert len(results) == 1000
        assert len(claimed) == 100
        assert len(empty) == 900
        assert all(count == 1 for count in counts.values())
        assert all(entry.status == "in_progress" for entry in final.entries)
        assert all(entry.claimer is not None for entry in final.entries)
