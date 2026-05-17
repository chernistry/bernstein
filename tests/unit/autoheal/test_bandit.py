"""Unit tests for ``bernstein.core.autoheal.bandit``."""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from bernstein.core.autoheal.bandit import (
    ENV_SEED,
    ArmState,
    BanditState,
    load_state,
    save_state,
)


def test_arm_starts_at_uniform_prior() -> None:
    a = ArmState()
    assert a.alpha == 1.0
    assert a.beta == 1.0
    assert a.pulls == 0
    assert a.mean == pytest.approx(0.5)


def test_arm_mean_after_observations() -> None:
    a = ArmState(alpha=4.0, beta=2.0)
    # 3 wins, 1 loss -> mean 4/6
    assert a.mean == pytest.approx(4.0 / 6.0)
    assert a.pulls == 4


def test_arm_sample_in_unit_interval() -> None:
    rng = random.Random(7)
    a = ArmState(alpha=2.0, beta=2.0)
    for _ in range(20):
        v = a.sample(rng)
        assert 0.0 <= v <= 1.0


def test_bandit_ensure_creates_new_arm() -> None:
    s = BanditState()
    arm = s.ensure("ruff-format")
    assert arm.alpha == 1.0
    assert arm.beta == 1.0
    assert "ruff-format" in s.arms


def test_bandit_record_success_increments_alpha() -> None:
    s = BanditState()
    s.record("ruff-format", success=True)
    assert s.arms["ruff-format"].alpha == 2.0
    assert s.arms["ruff-format"].beta == 1.0


def test_bandit_record_failure_increments_beta() -> None:
    s = BanditState()
    s.record("ruff-format", success=False)
    assert s.arms["ruff-format"].alpha == 1.0
    assert s.arms["ruff-format"].beta == 2.0


def test_bandit_record_idempotent_in_arm_set() -> None:
    s = BanditState()
    s.record("a", success=True)
    s.record("a", success=True)
    s.record("b", success=False)
    assert set(s.arms.keys()) == {"a", "b"}


def test_select_prefers_arm_with_strong_winning_record() -> None:
    rng = random.Random(42)
    s = BanditState()
    # Arm "good" has 100 wins, 1 loss -> almost surely picked.
    s.arms["good"] = ArmState(alpha=101.0, beta=2.0)
    s.arms["bad"] = ArmState(alpha=1.0, beta=20.0)
    picks = [s.select(["good", "bad"], rng=rng) for _ in range(50)]
    assert picks.count("good") >= 40


def test_select_with_single_candidate_returns_it() -> None:
    s = BanditState()
    assert s.select(["only-one"], rng=random.Random(0)) == "only-one"


def test_select_empty_raises() -> None:
    s = BanditState()
    with pytest.raises(ValueError):
        s.select([], rng=random.Random(0))


def test_select_creates_arm_for_unknown_strategy() -> None:
    s = BanditState()
    chosen = s.select(["new-strategy"], rng=random.Random(0))
    assert chosen == "new-strategy"
    assert "new-strategy" in s.arms


def test_to_dict_round_trip() -> None:
    s1 = BanditState()
    s1.record("a", success=True)
    s1.record("a", success=False)
    s1.record("b", success=True)
    data = s1.to_dict()
    s2 = BanditState.from_dict(data)
    assert s2.arms["a"].alpha == s1.arms["a"].alpha
    assert s2.arms["a"].beta == s1.arms["a"].beta
    assert s2.arms["b"].alpha == s1.arms["b"].alpha


def test_from_dict_rejects_malformed_entries() -> None:
    s = BanditState.from_dict(
        {
            "arms": {
                "good": {"alpha": 2.0, "beta": 3.0},
                "bad_negative": {"alpha": -1.0, "beta": 1.0},
                "bad_type": {"alpha": "x", "beta": 1.0},
                "bad_shape": "not-a-dict",
            }
        }
    )
    assert "good" in s.arms
    assert "bad_negative" not in s.arms
    assert "bad_type" not in s.arms
    assert "bad_shape" not in s.arms


def test_from_dict_empty_payload() -> None:
    s = BanditState.from_dict({})
    assert s.arms == {}


def test_save_then_load_round_trip(tmp_path: Path) -> None:
    p = tmp_path / "bandit.json"
    s1 = BanditState()
    s1.record("ruff", success=True)
    s1.record("ruff", success=True)
    save_state(s1, p)
    s2 = load_state(p)
    assert s2.arms["ruff"].alpha == 3.0
    assert s2.arms["ruff"].beta == 1.0


def test_load_missing_file_returns_fresh_state(tmp_path: Path) -> None:
    p = tmp_path / "absent.json"
    s = load_state(p)
    assert s.arms == {}


def test_load_corrupt_file_returns_fresh_state(tmp_path: Path) -> None:
    p = tmp_path / "bandit.json"
    p.write_text("not json", encoding="utf-8")
    s = load_state(p)
    assert s.arms == {}


def test_save_state_is_atomic(tmp_path: Path) -> None:
    p = tmp_path / "bandit.json"
    s = BanditState()
    s.record("a", success=True)
    save_state(s, p)
    # No temp file leftover.
    assert not (tmp_path / "bandit.json.tmp").exists()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["v"] == 1
    assert "arms" in data


def test_env_seed_makes_select_reproducible(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting ``BERNSTEIN_AUTOHEAL_BANDIT_SEED`` pins the picked arm."""
    monkeypatch.setenv(ENV_SEED, "42")
    s1 = BanditState()
    pick1 = s1.select(["a", "b", "c", "d"])
    s2 = BanditState()
    pick2 = s2.select(["a", "b", "c", "d"])
    assert pick1 == pick2


def test_env_seed_invalid_falls_back_to_fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-integer seed must not break select."""
    monkeypatch.setenv(ENV_SEED, "not-an-int")
    s = BanditState()
    pick = s.select(["a", "b"])
    assert pick in {"a", "b"}


def test_explicit_rng_overrides_env_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the caller passes ``rng=`` the env seed is ignored."""
    monkeypatch.setenv(ENV_SEED, "1")
    s = BanditState()
    pick = s.select(["a", "b", "c"], rng=random.Random(99))
    # Compute the expected pick deterministically.
    s2 = BanditState()
    expected = s2.select(["a", "b", "c"], rng=random.Random(99))
    assert pick == expected
