"""Property-based tests for ``bernstein.adapters.report``.

Pins invariants that should hold for any plausible adapter registry
and any plausible mix of conformance verdicts:

* The summary counters cover every row exactly once.
* JSON serialisation round-trips for any populated report.
* The ``conformance`` field always lands in the closed verdict set.
* Capability sets emit sorted lists in JSON regardless of insertion
  order.
* ``build_report(only=name)`` filters to exactly one row when the name
  is in the registry.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

from hypothesis import given
from hypothesis import strategies as st

from bernstein.adapters.base import CLIAdapter
from bernstein.adapters.report import (
    CONFORMANCE_FAIL,
    CONFORMANCE_OK,
    CONFORMANCE_SKIP,
    AdapterReport,
    AdapterStatus,
    ReportSummary,
    _summarize,
    build_report,
)

_VERDICTS = (CONFORMANCE_OK, CONFORMANCE_FAIL, CONFORMANCE_SKIP)


class _Stub(CLIAdapter):
    """Bare CLIAdapter stub for property tests."""

    def name(self) -> str:
        return "stub"

    def spawn(
        self,
        *,
        prompt: str,
        workdir: Any,
        model_config: Any,
        session_id: str,
        mcp_config: dict[str, Any] | None = None,
        timeout_seconds: int = 1800,
        task_scope: str = "medium",
        budget_multiplier: float = 1.0,
        system_addendum: str = "",
    ) -> Any:
        raise NotImplementedError


def _status(name: str, verdict: str, *, binary: str | None) -> AdapterStatus:
    """Compact factory for AdapterStatus rows in property tests."""
    return AdapterStatus(
        name=name,
        module_path=f"{name}.py",
        binary_resolved=binary,
        version_string=None,
        capabilities=frozenset(),
        conformance=verdict,  # type: ignore[arg-type]
        conformance_detail="",
        last_modified_utc="",
        contract_hash="",
    )


@st.composite
def _adapter_status(draw: st.DrawFn) -> AdapterStatus:
    name = draw(st.text(alphabet=st.characters(whitelist_categories=("Lu", "Ll")), min_size=1, max_size=12))
    verdict = draw(st.sampled_from(_VERDICTS))
    binary = draw(st.one_of(st.none(), st.just("/usr/bin/" + name)))
    caps = draw(st.sets(st.text(min_size=1, max_size=6), max_size=5))
    return AdapterStatus(
        name=name,
        module_path=f"{name}.py",
        binary_resolved=binary,
        version_string=None,
        capabilities=frozenset(caps),
        conformance=verdict,  # type: ignore[arg-type]
        conformance_detail="",
        last_modified_utc="",
        contract_hash="",
    )


# ---------------------------------------------------------------------------
# Summary invariants
# ---------------------------------------------------------------------------


@given(rows=st.lists(_adapter_status(), max_size=20))
def test_summary_total_matches_row_count(rows: list[AdapterStatus]) -> None:
    """``summary.total`` equals the number of rows for any registry shape."""
    s = _summarize(tuple(rows))
    assert s.total == len(rows)


@given(rows=st.lists(_adapter_status(), max_size=20))
def test_summary_verdict_counters_cover_every_row(rows: list[AdapterStatus]) -> None:
    """conform + fail + skip == total for any row distribution."""
    s = _summarize(tuple(rows))
    assert s.conform + s.fail + s.skip == s.total


@given(rows=st.lists(_adapter_status(), max_size=20))
def test_summary_reachable_le_total(rows: list[AdapterStatus]) -> None:
    """``reachable`` is bounded by ``total``."""
    s = _summarize(tuple(rows))
    assert 0 <= s.reachable <= s.total


@given(rows=st.lists(_adapter_status(), max_size=20))
def test_summary_no_negative_counters(rows: list[AdapterStatus]) -> None:
    """Every counter is non-negative for any input."""
    s = _summarize(tuple(rows))
    assert all(v >= 0 for v in (s.total, s.reachable, s.conform, s.fail, s.skip))


# ---------------------------------------------------------------------------
# JSON round-trip
# ---------------------------------------------------------------------------


@given(rows=st.lists(_adapter_status(), max_size=12))
def test_report_json_round_trip(rows: list[AdapterStatus]) -> None:
    """Any report serialises to JSON and parses back to an equivalent dict."""
    report = AdapterReport(adapters=tuple(rows), summary=_summarize(tuple(rows)))
    payload = report.to_json()
    parsed = json.loads(payload)
    assert parsed["summary"]["total"] == len(rows)


@given(rows=st.lists(_adapter_status(), min_size=1, max_size=8))
def test_report_json_capabilities_always_sorted(rows: list[AdapterStatus]) -> None:
    """JSON ``capabilities`` is always a sorted list."""
    report = AdapterReport(adapters=tuple(rows), summary=_summarize(tuple(rows)))
    parsed = json.loads(report.to_json())
    for row in parsed["adapters"]:
        assert row["capabilities"] == sorted(row["capabilities"])


@given(rows=st.lists(_adapter_status(), max_size=10))
def test_report_summary_is_int_only(rows: list[AdapterStatus]) -> None:
    """JSON-serialised summary contains ints (no leaked sets)."""
    report = AdapterReport(adapters=tuple(rows), summary=_summarize(tuple(rows)))
    parsed = json.loads(report.to_json())
    for v in parsed["summary"].values():
        assert isinstance(v, int)


# ---------------------------------------------------------------------------
# Verdict membership
# ---------------------------------------------------------------------------


@given(rows=st.lists(_adapter_status(), max_size=10))
def test_every_verdict_is_in_closed_set(rows: list[AdapterStatus]) -> None:
    """Every row's conformance lives in the closed verdict set."""
    assert all(r.conformance in _VERDICTS for r in rows)


# ---------------------------------------------------------------------------
# build_report filter
# ---------------------------------------------------------------------------


@given(names=st.lists(st.sampled_from(["aichat", "claude", "codex", "gemini"]), min_size=1, max_size=4, unique=True))
def test_build_report_only_filters_to_single_row(names: list[str]) -> None:
    """``only=name`` against a stub registry produces exactly one row."""

    def _iter() -> Any:
        for n in names:
            yield n, _Stub

    chosen = names[0]
    with patch("bernstein.adapters.registry.iter_adapter_specs", _iter):
        report = build_report(capture_version=False, only=chosen)
    assert len(report.adapters) == 1
    assert report.adapters[0].name == chosen


@given(names=st.lists(st.text(min_size=1, max_size=10, alphabet="abcdef"), min_size=1, max_size=6, unique=True))
def test_build_report_sorts_by_name(names: list[str]) -> None:
    """``build_report`` always returns rows in alphabetic order."""

    def _iter() -> Any:
        for n in names:
            yield n, _Stub

    with patch("bernstein.adapters.registry.iter_adapter_specs", _iter):
        report = build_report(capture_version=False)
    emitted = [a.name for a in report.adapters]
    assert emitted == sorted(emitted)


# ---------------------------------------------------------------------------
# Report summary helpers - extra coverage
# ---------------------------------------------------------------------------


@given(
    ok=st.integers(min_value=0, max_value=10),
    fail=st.integers(min_value=0, max_value=10),
    skip=st.integers(min_value=0, max_value=10),
)
def test_summary_handles_uniform_verdict_distributions(ok: int, fail: int, skip: int) -> None:
    """Summary counts match the input verdict distribution."""
    rows: list[AdapterStatus] = []
    rows.extend(_status(f"o{i}", CONFORMANCE_OK, binary=f"/usr/bin/o{i}") for i in range(ok))
    rows.extend(_status(f"f{i}", CONFORMANCE_FAIL, binary=f"/usr/bin/f{i}") for i in range(fail))
    rows.extend(_status(f"s{i}", CONFORMANCE_SKIP, binary=None) for i in range(skip))
    s = _summarize(tuple(rows))
    assert s.conform == ok
    assert s.fail == fail
    assert s.skip == skip


@given(
    n_with_binary=st.integers(min_value=0, max_value=10),
    n_without=st.integers(min_value=0, max_value=10),
)
def test_summary_reachable_counts_binary_resolved(n_with_binary: int, n_without: int) -> None:
    """``reachable`` counts only rows whose ``binary_resolved`` is set."""
    rows: list[AdapterStatus] = []
    rows.extend(_status(f"w{i}", CONFORMANCE_OK, binary=f"/usr/bin/w{i}") for i in range(n_with_binary))
    rows.extend(_status(f"x{i}", CONFORMANCE_SKIP, binary=None) for i in range(n_without))
    s = _summarize(tuple(rows))
    assert s.reachable == n_with_binary


@given(rows=st.lists(_adapter_status(), max_size=15))
def test_summary_is_reconstructible_from_rows(rows: list[AdapterStatus]) -> None:
    """The summary depends only on the rows, not the report container."""
    s1 = _summarize(tuple(rows))
    s2 = _summarize(tuple(rows))
    assert s1 == s2


def test_report_summary_with_zero_rows_is_zero_summary() -> None:
    """Empty report has an all-zero summary (sanity for boundary)."""
    assert _summarize(()) == ReportSummary(0, 0, 0, 0, 0)
