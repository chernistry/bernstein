"""Snapshot tests for the TOPSIS ranking renderer.

These tests pin the JSON shape returned by
:func:`render_ranking_json` so future refactors of the ranker cannot
silently change the operator-facing audit trail (the artefact that
``bernstein best-of-n show`` reads to explain the winner).
"""

from __future__ import annotations

from syrupy.assertion import SnapshotAssertion

from bernstein.core.orchestration.multi_criteria_rank import (
    Candidate,
    Criterion,
    CriterionProfile,
    rank_candidates,
    render_ranking_json,
)


def _profile() -> CriterionProfile:
    return CriterionProfile(
        criteria=(
            Criterion("correctness", direction="benefit", weight=2.0),
            Criterion("cost", direction="cost", weight=1.0),
        )
    )


def test_ranking_winner_three_candidate_snapshot(snapshot: SnapshotAssertion) -> None:
    profile = _profile()
    candidates = [
        Candidate("alpha", {"correctness": 0.90, "cost": 10.0}),
        Candidate("beta", {"correctness": 0.50, "cost": 1.0}),
        Candidate("gamma", {"correctness": 0.75, "cost": 5.0}),
    ]
    ranked = rank_candidates(candidates, profile)
    assert render_ranking_json(ranked, profile) == snapshot


def test_ranking_winner_speed_first_snapshot(snapshot: SnapshotAssertion) -> None:
    profile = CriterionProfile(
        criteria=(
            Criterion("correctness", weight=1.0),
            Criterion("latency", direction="cost", weight=10.0),
        )
    )
    candidates = [
        Candidate("slow-safe", {"correctness": 1.00, "latency": 600.0}),
        Candidate("mid", {"correctness": 0.80, "latency": 60.0}),
        Candidate("fast-risky", {"correctness": 0.60, "latency": 5.0}),
    ]
    ranked = rank_candidates(candidates, profile)
    assert render_ranking_json(ranked, profile) == snapshot


def test_ranking_single_candidate_snapshot(snapshot: SnapshotAssertion) -> None:
    profile = CriterionProfile(criteria=(Criterion("x"),))
    ranked = rank_candidates([Candidate("only", {"x": 1.0})], profile)
    assert render_ranking_json(ranked, profile) == snapshot
