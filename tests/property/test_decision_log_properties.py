"""Hypothesis property tests for :mod:`decision_log`.

The properties here are written so any *legal* sequence of writer
calls produces a ledger that:

* round-trips back through ``replay`` to the same records,
* has non-decreasing ``ts`` (single-process invariant),
* is unaffected in semantic content by interleaved blank lines,
* truncates oversize ``alternatives`` and ``rationale`` strings,
* tolerates concurrent append commutativity (set-equality of records).

Hypothesis budgets are kept small (max_examples ≤ 60) so the whole
file finishes well under 30 s on a GitHub-hosted runner.
"""

from __future__ import annotations

import json
import string

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from bernstein.core.observability import decision_log as dl

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_ID_ALPHABET = string.ascii_letters + string.digits + "-_"

_kind_strategy = st.sampled_from(sorted(dl.VALID_KINDS))

_chosen_strategy = st.text(alphabet=_ID_ALPHABET, min_size=1, max_size=24)

_rationale_strategy = st.text(min_size=0, max_size=200)

_confidence_strategy = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)

_alt_strategy = st.builds(
    dl.Alternative,
    id=st.text(alphabet=_ID_ALPHABET, min_size=1, max_size=16),
    score=st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
    reason=st.text(max_size=40),
)

_alts_list_strategy = st.lists(_alt_strategy, min_size=0, max_size=8)

_policy_strategy = st.lists(
    st.text(alphabet=_ID_ALPHABET, min_size=1, max_size=12),
    min_size=0,
    max_size=5,
)


@pytest.fixture(autouse=True)
def _enable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make sure no globally-exported BERNSTEIN_DECISION_LOG=0 stubs us out."""
    monkeypatch.delenv(dl.ENV_DISABLE, raising=False)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    kind=_kind_strategy,
    chosen=_chosen_strategy,
    rationale=_rationale_strategy,
    confidence=_confidence_strategy,
    alts=_alts_list_strategy,
    policy_path=_policy_strategy,
)
def test_property_round_trip_through_jsonl(
    tmp_path_factory: pytest.TempPathFactory,
    kind: str,
    chosen: str,
    rationale: str,
    confidence: float,
    alts: list[dl.Alternative],
    policy_path: list[str],
) -> None:
    """Any legal call to ``record_decision`` round-trips through ``replay``.

    The record we read back is byte-identical (after rationale/alts caps)
    to the record the writer returned in-memory.
    """
    path = tmp_path_factory.mktemp("dl") / "decisions.jsonl"
    written = dl.record_decision(
        kind=kind,
        chosen=chosen,
        rationale=rationale,
        confidence=confidence,
        alternatives=alts,
        policy_path=policy_path,
        path=path,
    )
    assert written is not None
    [back] = dl.replay(path)
    assert back == written


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    pairs=st.lists(
        st.tuples(_kind_strategy, _chosen_strategy),
        min_size=1,
        max_size=30,
    )
)
def test_property_ts_non_decreasing(tmp_path_factory: pytest.TempPathFactory, pairs: list[tuple[str, str]]) -> None:
    """Single-process writes always produce monotonically non-decreasing ts."""
    path = tmp_path_factory.mktemp("dl") / "decisions.jsonl"
    for kind, chosen in pairs:
        dl.record_decision(kind=kind, chosen=chosen, path=path)
    timestamps = [r.ts for r in dl.replay(path)]
    assert timestamps == sorted(timestamps)


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(records=st.lists(st.tuples(_kind_strategy, _chosen_strategy), min_size=0, max_size=10))
def test_property_record_count_matches_calls(
    tmp_path_factory: pytest.TempPathFactory, records: list[tuple[str, str]]
) -> None:
    """N successful writes ⇒ exactly N records on replay."""
    path = tmp_path_factory.mktemp("dl") / "decisions.jsonl"
    for kind, chosen in records:
        dl.record_decision(kind=kind, chosen=chosen, path=path)
    assert len(dl.replay(path)) == len(records)


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(rationale=st.text(min_size=0, max_size=dl.MAX_RATIONALE_LEN + 1000))
def test_property_rationale_never_exceeds_cap(tmp_path_factory: pytest.TempPathFactory, rationale: str) -> None:
    """Rationale length on disk never exceeds :data:`MAX_RATIONALE_LEN`."""
    path = tmp_path_factory.mktemp("dl") / "decisions.jsonl"
    rec = dl.record_decision(kind="model_route", chosen="m", rationale=rationale, path=path)
    assert rec is not None
    assert len(rec.rationale) <= dl.MAX_RATIONALE_LEN


@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    extra=st.integers(min_value=0, max_value=200),
)
def test_property_alternatives_truncated_to_cap(tmp_path_factory: pytest.TempPathFactory, extra: int) -> None:
    """No matter how many alternatives are passed, persisted count is capped."""
    path = tmp_path_factory.mktemp("dl") / "decisions.jsonl"
    alts = [dl.Alternative(id=f"a-{i}") for i in range(dl.MAX_ALTERNATIVES + extra)]
    rec = dl.record_decision(kind="model_route", chosen="m", alternatives=alts, path=path)
    assert rec is not None
    assert len(rec.alternatives) <= dl.MAX_ALTERNATIVES


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    pairs=st.lists(
        st.tuples(_kind_strategy, _chosen_strategy),
        min_size=0,
        max_size=20,
    )
)
def test_property_append_commutativity_set_eq(
    tmp_path_factory: pytest.TempPathFactory, pairs: list[tuple[str, str]]
) -> None:
    """Sequential append preserves set equality of records (no losses)."""
    path = tmp_path_factory.mktemp("dl") / "decisions.jsonl"
    ids: set[str] = set()
    for kind, chosen in pairs:
        rec = dl.record_decision(kind=kind, chosen=chosen, path=path)
        assert rec is not None
        ids.add(rec.decision_id)
    replayed = {r.decision_id for r in dl.replay(path)}
    assert replayed == ids


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(records=st.lists(st.tuples(_kind_strategy, _chosen_strategy), min_size=1, max_size=10))
def test_property_blank_lines_do_not_affect_replay(
    tmp_path_factory: pytest.TempPathFactory, records: list[tuple[str, str]]
) -> None:
    """Injecting blank lines between records does not change the replay result."""
    path = tmp_path_factory.mktemp("dl") / "decisions.jsonl"
    for kind, chosen in records:
        dl.record_decision(kind=kind, chosen=chosen, path=path)
        with path.open("a", encoding="utf-8") as fh:
            fh.write("\n   \n")
    assert len(dl.replay(path)) == len(records)


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(records=st.lists(st.tuples(_kind_strategy, _chosen_strategy), min_size=0, max_size=15))
def test_property_jsonl_each_line_parses(
    tmp_path_factory: pytest.TempPathFactory, records: list[tuple[str, str]]
) -> None:
    """Every persisted line is independently a valid JSON object."""
    path = tmp_path_factory.mktemp("dl") / "decisions.jsonl"
    for kind, chosen in records:
        dl.record_decision(kind=kind, chosen=chosen, path=path)
    if not records:
        return
    for line in path.read_text().splitlines():
        data = json.loads(line)
        assert isinstance(data, dict)
        assert "decision_id" in data


@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    kind=_kind_strategy,
    chosen=_chosen_strategy,
    confidence=_confidence_strategy,
    alts=_alts_list_strategy,
)
def test_property_to_dict_from_dict_idempotent(
    kind: str, chosen: str, confidence: float, alts: list[dl.Alternative]
) -> None:
    """to_dict ∘ from_dict ∘ to_dict produces the same dict as a single to_dict."""
    rec = dl.DecisionRecord(
        ts=1.0,
        decision_id="dec-x",
        kind=kind,
        chosen=chosen,
        alternatives=tuple(alts),
        confidence=confidence,
        rationale="",
    )
    once = rec.to_dict()
    twice = dl.DecisionRecord.from_dict(once).to_dict()
    assert once == twice


@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    pairs=st.lists(st.tuples(_kind_strategy, _chosen_strategy), min_size=1, max_size=10),
    cutoff=st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False),
)
def test_property_filter_since_subset(
    tmp_path_factory: pytest.TempPathFactory, pairs: list[tuple[str, str]], cutoff: float
) -> None:
    """filter_since returns a subset of the input list."""
    path = tmp_path_factory.mktemp("dl") / "decisions.jsonl"
    for kind, chosen in pairs:
        dl.record_decision(kind=kind, chosen=chosen, path=path)
    records = dl.replay(path)
    filtered = dl.filter_since(records, cutoff)
    assert all(r in records for r in filtered)
    assert all(r.ts >= cutoff for r in filtered)


@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(spec=st.from_regex(r"^[1-9][0-9]?[smhd]$", fullmatch=True))
def test_property_parse_duration_positive(spec: str) -> None:
    """Any well-formed duration spec parses to a positive number of seconds."""
    seconds = dl.parse_duration(spec)
    assert seconds > 0.0
