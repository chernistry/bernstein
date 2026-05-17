"""Hypothesis property tests for auto-heal modules.

Properties exercised here:

* Categorizer: classifications are total (every string yields a class).
* Bandit: posteriors after a success/loss sequence are commutative in
  count but order-sensitive only in the sample draw (the Beta is
  fully determined by alpha and beta).
* Audit log: round-trip serialise -> parse is the identity.
* Bayesian: confidence stays inside ``[0, 1]`` after any update mix.
* Idempotency: ``patch_sha_for`` is deterministic and collision-resistant
  for distinct inputs in small-sample tests.
"""

from __future__ import annotations

import json
import time

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from bernstein.core.autoheal.audit_log import HealRecord, _coerce_outcome, iter_records
from bernstein.core.autoheal.bandit import BanditState
from bernstein.core.autoheal.bayesian import ConfidenceState
from bernstein.core.autoheal.categorizer import classify
from bernstein.core.autoheal.idempotency import patch_sha_for

_OUTCOMES: tuple[str, ...] = (
    "applied",
    "skipped_no_jobs",
    "skipped_kill_switch",
    "skipped_idempotent",
    "skipped_budget",
    "shadow",
    "failed_validation",
    "failed_push",
    "escalated",
)


@given(st.text())
def test_classify_always_returns_a_class(job: str) -> None:
    """Categorizer is total - never raises, always returns a valid class."""
    c = classify(job)
    assert c.cls in ("safe", "heuristic", "risky", "unknown")
    assert isinstance(c.rule, str) and c.rule != ""


@given(
    st.lists(st.booleans(), min_size=1, max_size=50),
)
def test_bandit_alpha_plus_beta_grows_linearly(seq: list[bool]) -> None:
    """Each observation moves alpha+beta by exactly one."""
    s = BanditState()
    for outcome in seq:
        s.record("x", success=outcome)
    arm = s.arms["x"]
    assert arm.alpha + arm.beta == 2.0 + len(seq)
    assert arm.alpha == 1.0 + sum(1 for o in seq if o)
    assert arm.beta == 1.0 + sum(1 for o in seq if not o)


@given(
    st.lists(
        st.tuples(
            st.sampled_from(["safe", "heuristic", "risky", "unknown"]),
            st.text(min_size=1, max_size=20),
            st.booleans(),
        ),
        min_size=0,
        max_size=20,
    ),
)
def test_bayesian_confidence_within_unit_interval(
    seq: list[tuple[str, str, bool]],
) -> None:
    """Posterior mean is always a proper probability."""
    s = ConfidenceState()
    for cls, job, ok in seq:
        s.update(cls, job, success=ok)  # type: ignore[arg-type]
        c = s.confidence(cls, job)  # type: ignore[arg-type]
        assert 0.0 <= c <= 1.0


@given(st.binary(min_size=0, max_size=200))
def test_patch_sha_deterministic(payload: bytes) -> None:
    assert patch_sha_for(payload) == patch_sha_for(payload)


@given(
    st.binary(min_size=1, max_size=50),
    st.binary(min_size=1, max_size=50),
)
def test_patch_sha_distinct_for_distinct_input(a: bytes, b: bytes) -> None:
    if a == b:
        return
    # 16-hex shortened sha256 collisions are improbable in this size space.
    assert patch_sha_for(a) != patch_sha_for(b)


@given(
    st.sampled_from(_OUTCOMES),
)
def test_coerce_outcome_is_identity_on_known(value: str) -> None:
    assert _coerce_outcome(value) == value


@given(st.text())
def test_coerce_outcome_total(value: str) -> None:
    out = _coerce_outcome(value)
    assert out in _OUTCOMES


@given(
    st.lists(
        st.fixed_dictionaries(
            {
                "run_id": st.text(alphabet="abcdef0123456789", min_size=1, max_size=8),
                "strategy": st.text(alphabet="abc", min_size=1, max_size=5),
                "cls": st.sampled_from(["safe", "heuristic", "risky", "unknown"]),
                "outcome": st.sampled_from(_OUTCOMES),
                "confidence": st.floats(0.0, 1.0),
                "cost_usd": st.floats(0.0, 5.0, allow_nan=False, allow_infinity=False),
                "llm_calls": st.integers(0, 20),
            },
        ),
        min_size=0,
        max_size=10,
    ),
)
@settings(max_examples=25, suppress_health_check=[HealthCheck.too_slow])
def test_audit_log_round_trip(rows: list[dict[str, object]]) -> None:
    """Round-trip via JSONL is identity-preserving on key fields."""
    records: list[HealRecord] = []
    lines: list[str] = []
    for body in rows:
        rec = HealRecord(
            ts=time.time(),
            run_id=str(body["run_id"]),
            head_sha="h",
            strategy=str(body["strategy"]),
            cls=str(body["cls"]),
            confidence=float(body["confidence"]),  # type: ignore[arg-type]
            outcome=_coerce_outcome(body["outcome"]),
            cost_usd=float(body["cost_usd"]),  # type: ignore[arg-type]
            llm_calls=int(body["llm_calls"]),  # type: ignore[arg-type]
        )
        records.append(rec)
        lines.append(rec.to_jsonl())
    # Stitch + parse the same way ``iter_records`` does.
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "h.jsonl"
        p.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        parsed = list(iter_records(p))
    assert len(parsed) == len(records)
    for a, b in zip(parsed, records, strict=True):
        assert a.run_id == b.run_id
        assert a.strategy == b.strategy
        assert a.cls == b.cls


@given(
    st.lists(st.text(min_size=1, max_size=10), min_size=1, max_size=5),
)
def test_bandit_select_returns_a_candidate(candidates: list[str]) -> None:
    """Selection always returns a member of the input set."""
    import random

    s = BanditState()
    chosen = s.select(candidates, rng=random.Random(0))
    assert chosen in candidates


@given(st.text(min_size=0, max_size=20))
def test_jsonl_emit_is_one_line(rationale: str) -> None:
    """``HealRecord.to_jsonl`` never produces multiple lines."""
    rec = HealRecord(
        ts=0.0,
        run_id="r",
        head_sha="s",
        strategy="x",
        cls="safe",
        confidence=0.5,
        outcome="applied",
        rationale=rationale,
    )
    line = rec.to_jsonl()
    assert "\n" not in line
    json.loads(line)  # also valid JSON


@given(
    st.text(min_size=1, max_size=20),
    st.text(min_size=1, max_size=20),
    st.lists(st.booleans(), min_size=0, max_size=15),
)
def test_bayesian_order_independence_of_count(cls_raw: str, job: str, outcomes: list[bool]) -> None:
    """The Beta posterior only sees counts, not order."""
    cls: str = cls_raw if cls_raw in ("safe", "heuristic", "risky", "unknown") else "safe"
    s1 = ConfidenceState()
    s2 = ConfidenceState()
    for o in outcomes:
        s1.update(cls, job, success=o)  # type: ignore[arg-type]
    for o in reversed(outcomes):
        s2.update(cls, job, success=o)  # type: ignore[arg-type]
    assert abs(s1.confidence(cls, job) - s2.confidence(cls, job)) < 1e-9  # type: ignore[arg-type]


@given(
    st.text(min_size=0, max_size=30),
)
def test_classify_idempotent(job: str) -> None:
    c1 = classify(job)
    c2 = classify(job)
    assert c1.cls == c2.cls
    assert c1.rule == c2.rule
