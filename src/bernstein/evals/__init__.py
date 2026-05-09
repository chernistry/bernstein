"""Contamination-resistant evaluation harness.

This package hosts the nightly contamination-resistant eval driver that runs
Bernstein against three benchmark suites that publish *after* training-data
cut-offs for the major frontier models:

* ``swe-bench-pro`` — 1,865 tasks across 41 repos under GPL/proprietary licence
* ``terminal-bench-2`` — Stanford / Laude Institute, 89 tasks; harness-model
  pair eval contract
* ``swe-rebench`` — monthly contamination-free updates for trend tracking

The :class:`bernstein.benchmark.swe_bench.SWEBenchRunner` targets the now-
deprecated ``SWE-bench Verified`` set (gold-patch leakage confirmed across all
frontier models — see ``agentic_systems_v2.md`` lines 30-31). This package is
its replacement for any leaderboard-grade claims.

The first slice (this module) ships:

* A :class:`LeaderboardEntry` with the four-field harness-model pair record
  required by Terminal-Bench 2.0 submissions.
* A minimal :func:`run_single_task` driver that produces one JSON entry from
  a synthetic or real task descriptor — enough to wire into a nightly job
  before the full HuggingFace loaders land.

Full suite loaders, contamination-resistance audit, and the
``/eval/leaderboard`` route are intentionally deferred — see the original
ticket at ``.sdd/backlog/closed/2026-05-07-feat-swe-bench-pro-terminal-bench-nightly.md``.
"""

from __future__ import annotations

from bernstein.evals.driver import (
    EvalSuite,
    LeaderboardEntry,
    run_single_task,
    write_leaderboard_entry,
)

__all__ = [
    "EvalSuite",
    "LeaderboardEntry",
    "run_single_task",
    "write_leaderboard_entry",
]
