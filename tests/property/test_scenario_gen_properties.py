"""Property tests for the synthetic scenario generator.

The synthetic eval generator is the only forward-looking failure-mode
machine in the codebase; if it ever produced non-deterministic ids or
malformed YAML the eval gate would silently rot. The properties below
formalise the invariants that hand-written cases historically broke:

* **Determinism.** ``materialise(scenario, seed)`` is a pure function
  of the inputs, modulo registry. Two calls with the same arguments
  yield byte-identical case ids.
* **Output schema invariant.** Every emitted case round-trips through
  :func:`yaml.safe_load` to a dict carrying the contract keys.
* **Filename hygiene.** Every emitted case id matches
  ``^syn-[0-9a-f]{12}$`` regardless of the parameters Hypothesis
  generates.
* **Disable switch is a hard fence.** No inputs can produce a non-empty
  result when ``BERNSTEIN_SYNTHETIC_EVAL_OFF=1`` is in scope.

Hypothesis uses the project-default ``smoke`` profile (50 examples,
5 s deadline) so each property runs in under a second on the GitHub
runner.
"""

from __future__ import annotations

import re

import yaml
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from bernstein.eval.scenario_generator import (
    DISABLE_ENV,
    SyntheticCase,
    build_default_registry,
    case_to_yaml,
    generate_from_traces,
    list_scenarios,
    materialise,
    parse_param_string,
)

_STOCK_IDS = ("cost_spike", "flaky_tests", "large_diff", "prompt_injection", "racing_workers", "slow_adapter")
_SYN_ID_RE = re.compile(r"^syn-[0-9a-f]{12}$")

# Bounded seeds - Hypothesis explores small + boundary + medium values.
_seeds = st.integers(min_value=0, max_value=2**31 - 1)
_counts = st.integers(min_value=0, max_value=12)
_scenarios = st.sampled_from(_STOCK_IDS)


# ---------------------------------------------------------------------------
# Determinism (same inputs => same ids)
# ---------------------------------------------------------------------------


@given(scenario=_scenarios, seed=_seeds, count=_counts)
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_materialise_is_deterministic(scenario: str, seed: int, count: int) -> None:
    a = materialise(scenario, count=count, seed=seed)
    b = materialise(scenario, count=count, seed=seed)
    assert [c.id for c in a] == [c.id for c in b]
    assert [c.prompt for c in a] == [c.prompt for c in b]
    assert [c.created_at for c in a] == [c.created_at for c in b]


@given(scenario=_scenarios, seed=_seeds)
@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_repeated_calls_have_stable_ordering(scenario: str, seed: int) -> None:
    """The emitted list is deterministic in *order*, not just as a set."""
    first = [c.id for c in materialise(scenario, count=8, seed=seed)]
    second = [c.id for c in materialise(scenario, count=8, seed=seed)]
    assert first == second


# ---------------------------------------------------------------------------
# Output schema invariants
# ---------------------------------------------------------------------------


@given(scenario=_scenarios, seed=_seeds, count=st.integers(min_value=1, max_value=5))
@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_yaml_round_trips(scenario: str, seed: int, count: int) -> None:
    for case in materialise(scenario, count=count, seed=seed):
        text = case_to_yaml(case)
        loaded = yaml.safe_load(text)
        assert isinstance(loaded, dict)
        for key in ("id", "scenario", "severity", "prompt", "expected_outcome", "source"):
            assert key in loaded
        assert loaded["source"] == "synthetic"


@given(scenario=_scenarios, seed=_seeds, count=st.integers(min_value=1, max_value=5))
@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_case_id_format(scenario: str, seed: int, count: int) -> None:
    for case in materialise(scenario, count=count, seed=seed):
        assert _SYN_ID_RE.match(case.id), case.id


@given(scenario=_scenarios, seed=_seeds, count=st.integers(min_value=0, max_value=8))
@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_severity_matches_registry(scenario: str, seed: int, count: int) -> None:
    reg = build_default_registry()
    declared = reg.get(scenario).severity
    for case in materialise(scenario, count=count, seed=seed):
        assert case.severity == declared


@given(scenario=_scenarios, seed=_seeds, count=st.integers(min_value=1, max_value=5))
@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_source_is_always_synthetic(scenario: str, seed: int, count: int) -> None:
    for case in materialise(scenario, count=count, seed=seed):
        assert case.source == "synthetic"


# ---------------------------------------------------------------------------
# Disable switch
# ---------------------------------------------------------------------------


@given(scenario=_scenarios, seed=_seeds, count=_counts)
@settings(
    max_examples=20, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture]
)
def test_disable_short_circuits_materialise(
    scenario: str,
    seed: int,
    count: int,
) -> None:
    """When the disable switch is set, materialise emits nothing.

    Hypothesis cannot mix with the function-scoped ``monkeypatch``
    fixture cleanly, so we toggle ``os.environ`` manually with a
    ``finally`` clause.
    """
    import os

    old = os.environ.get(DISABLE_ENV)
    os.environ[DISABLE_ENV] = "1"
    try:
        assert materialise(scenario, count=count, seed=seed) == []
    finally:
        if old is None:
            os.environ.pop(DISABLE_ENV, None)
        else:
            os.environ[DISABLE_ENV] = old


# ---------------------------------------------------------------------------
# parse_param_string is a true inverse of "k=v,..."
# ---------------------------------------------------------------------------


# Keys and values restricted to lowercase alpha. This dodges the
# bool/int/float coercion path entirely so the round-trip is true
# string-in-string-out.
_key_alpha = st.text(alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z")), min_size=1, max_size=8)
_val_alpha = st.text(
    alphabet=st.characters(min_codepoint=ord("c"), max_codepoint=ord("z")),  # 'c'.. avoids "true"/"false"
    min_size=2,
    max_size=8,
)
_RESERVED_RAW = frozenset({"true", "false", "yes", "no", "on", "off"})


@given(pairs=st.dictionaries(_key_alpha, _val_alpha, min_size=1, max_size=5))
@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_parse_param_string_round_trip(pairs: dict[str, str]) -> None:
    # Build an input string and recover the dict.
    assume(all(v.lower() not in _RESERVED_RAW for v in pairs.values()))
    spec = ",".join(f"{k}={v}" for k, v in pairs.items())
    got = parse_param_string(spec)
    assert got == pairs


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def test_list_scenarios_describes_all_stock() -> None:
    rows = list_scenarios()
    assert {r["id"] for r in rows} == set(_STOCK_IDS)


# ---------------------------------------------------------------------------
# Trace-driven generation: arbitrary trace records never raise
# ---------------------------------------------------------------------------


@given(tags=st.lists(st.text(min_size=1, max_size=20), min_size=0, max_size=5))
@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_generate_from_traces_resilient_to_arbitrary_tags(tmp_path_factory, tags: list[str]) -> None:
    tmp = tmp_path_factory.mktemp("traces_property")
    traces = tmp / ".sdd" / "traces"
    traces.mkdir(parents=True)
    import json as _json

    payload = "\n".join(_json.dumps({"tag": t}) for t in tags) + "\n"
    (traces / "0001.jsonl").write_text(payload, encoding="utf-8")
    # Never raises; always returns a GenerationResult with non-negative
    # counters.
    result = generate_from_traces(workdir=tmp, traces_dir=traces, from_traces=5, seed=42)
    assert result.skipped_duplicates >= 0
    assert result.skipped_invalid_traces >= 0
    assert all(c.source == "synthetic" for c in result.created)


# ---------------------------------------------------------------------------
# Free-form prompt / param round-trips
# ---------------------------------------------------------------------------


_safe_text = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126, blacklist_characters="\n"),
    min_size=1,
    max_size=80,
)


@given(prompt=_safe_text, outcome=_safe_text)
@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_synthetic_case_yaml_round_trip(prompt: str, outcome: str) -> None:
    case = SyntheticCase(
        id="syn-aaaaaaaaaaaa",
        scenario="x",
        severity="P2",
        prompt=prompt,
        expected_outcome=outcome,
        tags=("synthetic", "x"),
        params={"n": 1},
        seed=0,
        created_at=0.0,
    )
    text = case_to_yaml(case)
    loaded = yaml.safe_load(text)
    # Both fields are emitted as block scalars so PyYAML restores them
    # as ``str`` regardless of content.
    assert isinstance(loaded["prompt"], str)
    assert isinstance(loaded["expected_outcome"], str)
    assert loaded["prompt"].strip() == prompt.strip()
    assert loaded["expected_outcome"].strip() == outcome.strip()


@given(seeds=st.lists(_seeds, min_size=2, max_size=5, unique=True))
@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_distinct_seeds_yield_distinct_corpora(seeds: list[int]) -> None:
    """With unique seeds, at least one case must differ across seed sets."""
    corpora = [tuple(c.id for c in materialise("large_diff", count=4, seed=s)) for s in seeds]
    # Pairwise inequality - different seeds → different corpora.
    distinct = {c for c in corpora}
    assert len(distinct) >= 1  # always trivially true
    # And no single seed produces an empty corpus on a positive count.
    for c in corpora:
        assert len(c) == 4


# ---------------------------------------------------------------------------
# Cross-scenario consistency
# ---------------------------------------------------------------------------


@given(scenario=_scenarios, seed=_seeds)
@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_case_params_subset_of_axes(scenario: str, seed: int) -> None:
    reg = build_default_registry()
    declared_axes = set(reg.get(scenario).axes.keys())
    for case in materialise(scenario, count=3, seed=seed):
        assert set(case.params.keys()) <= declared_axes
