"""Unit tests for ``bernstein.core.autoheal.bayesian``."""

from __future__ import annotations

from pathlib import Path

import pytest

from bernstein.core.autoheal.bayesian import ConfidenceState, load, save


def test_default_safe_prior_is_05() -> None:
    s = ConfidenceState()
    assert s.confidence("safe", "Lint") == pytest.approx(0.5)


def test_default_heuristic_prior_is_below_05() -> None:
    s = ConfidenceState()
    assert s.confidence("heuristic", "Spelling (typos)") < 0.5


def test_default_risky_prior_is_pessimistic() -> None:
    s = ConfidenceState()
    # Beta(1, 5) mean = 1/6 ~= 0.166
    c = s.confidence("risky", "Type check")
    assert c < 0.25


def test_update_success_raises_confidence() -> None:
    s = ConfidenceState()
    before = s.confidence("safe", "Lint")
    s.update("safe", "Lint", success=True)
    after = s.confidence("safe", "Lint")
    assert after > before


def test_update_failure_lowers_confidence() -> None:
    s = ConfidenceState()
    before = s.confidence("safe", "Lint")
    s.update("safe", "Lint", success=False)
    after = s.confidence("safe", "Lint")
    assert after < before


def test_repeated_successes_converge_to_one() -> None:
    s = ConfidenceState()
    for _ in range(100):
        s.update("safe", "Lint", success=True)
    assert s.confidence("safe", "Lint") > 0.95


def test_repeated_failures_converge_to_zero() -> None:
    s = ConfidenceState()
    for _ in range(100):
        s.update("risky", "Type check", success=False)
    assert s.confidence("risky", "Type check") < 0.05


def test_keys_are_class_scoped() -> None:
    s = ConfidenceState()
    s.update("safe", "Lint", success=True)
    # Same job name under a different class -> fresh prior.
    assert s.confidence("risky", "Lint") < 0.25


def test_roundtrip_through_dict() -> None:
    s1 = ConfidenceState()
    s1.update("safe", "Lint", success=True)
    s1.update("heuristic", "Spelling (typos)", success=False)
    s2 = ConfidenceState.from_dict(s1.to_dict())
    assert s2.confidence("safe", "Lint") == pytest.approx(s1.confidence("safe", "Lint"))
    assert s2.confidence("heuristic", "Spelling (typos)") == pytest.approx(
        s1.confidence("heuristic", "Spelling (typos)")
    )


def test_from_dict_rejects_negative_pairs() -> None:
    s = ConfidenceState.from_dict({"posteriors": {"safe:Lint": [-1.0, 1.0]}})
    assert s.confidence("safe", "Lint") == pytest.approx(0.5)  # default prior


def test_from_dict_rejects_wrong_arity() -> None:
    s = ConfidenceState.from_dict({"posteriors": {"safe:Lint": [1.0]}})
    assert s.confidence("safe", "Lint") == pytest.approx(0.5)


def test_save_then_load_round_trip(tmp_path: Path) -> None:
    p = tmp_path / "bayes.json"
    s = ConfidenceState()
    s.update("safe", "Lint", success=True)
    save(s, p)
    s2 = load(p)
    assert s2.confidence("safe", "Lint") == pytest.approx(s.confidence("safe", "Lint"))


def test_load_missing_file_is_fresh(tmp_path: Path) -> None:
    s = load(tmp_path / "absent.json")
    assert s.posteriors == {}


def test_load_corrupt_file_is_fresh(tmp_path: Path) -> None:
    p = tmp_path / "bayes.json"
    p.write_text("not-json", encoding="utf-8")
    assert load(p).posteriors == {}
