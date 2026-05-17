"""Unit tests for ``scripts/auto_heal_categorize.py``.

The categorizer is the safety gate of ``.github/workflows/auto-heal.yml``.
A miscategorised job name means either:

* a known-mechanical fix is dropped on the floor (``safe`` -> ``unknown``,
  no harm but the heal never fires), or
* a real test / security signal is silently auto-fixed
  (``risky`` -> ``safe``, business-logic touch).

The second class is the dangerous one, so we test every job name from the
CI workflow plus a generous set of negative cases.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
_SPEC = importlib.util.spec_from_file_location("auto_heal_categorize", _SCRIPTS / "auto_heal_categorize.py")
assert _SPEC is not None and _SPEC.loader is not None
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)

classify = _MOD.classify
categorize = _MOD.categorize
main = _MOD.main


# ---- classify(): safe class --------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "Lint",
        "Repo hygiene",
        "Dead code (Vulture)",
        "Snapshot tests (syrupy)",
        "Workflow lint",
    ],
)
def test_classify_safe_known(name: str) -> None:
    assert classify(name) == "safe"


# ---- classify(): heuristic class --------------------------------------------


def test_classify_spelling_is_heuristic() -> None:
    assert classify("Spelling (typos)") == "heuristic"


# ---- classify(): risky class -------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "Type check",
        "Pyright strict (security + cluster)",
        "CodeQL",
        "Bandit (security)",
        "Semgrep (custom rules)",
        "Schemathesis smoke",
        "Mutation (diff-only)",
        "Property tests (Hypothesis smoke)",
        "Beartype (type contracts)",
        "Adapter integration (fake-CLI)",
        "Diff coverage gate",
        "pip-audit (deps)",
        "Package size check",
        "Lineage Gate",
        "Determine changes",
        "CI gate",
        "Auto-fix lint",
        "PR CI summary",
        "Close resolved CI issues",
    ],
)
def test_classify_risky_known(name: str) -> None:
    assert classify(name) == "risky"


@pytest.mark.parametrize(
    "name",
    [
        "Test (ubuntu-latest, Python 3.12)",
        "Test (ubuntu-latest, Python 3.13)",
        "Test (macos-latest, Python 3.13)",
        "Test (${{ matrix.os }}, Python ${{ matrix.python-version }})",
    ],
)
def test_classify_test_matrix_legs_are_risky(name: str) -> None:
    assert classify(name) == "risky"


# ---- classify(): unknown defaults to unknown (not safe) ---------------------


@pytest.mark.parametrize(
    "name",
    [
        "Random new gate",
        "Build wheel",
        "Publish to PyPI",
        "ZZZ",
    ],
)
def test_classify_unknown_defaults_to_unknown(name: str) -> None:
    # Critical safety property: unknown -> unknown, never safe.
    assert classify(name) == "unknown"


# ---- classify(): edge cases --------------------------------------------------


def test_classify_empty_string_is_unknown() -> None:
    assert classify("") == "unknown"


def test_classify_whitespace_only_is_unknown() -> None:
    assert classify("   \t") == "unknown"


def test_classify_strips_surrounding_whitespace() -> None:
    assert classify("  Lint  ") == "safe"


def test_classify_case_sensitive() -> None:
    # CI job names are exact -- "lint" lowercase is NOT the "Lint" job.
    assert classify("lint") == "unknown"


def test_classify_test_prefix_only_with_paren() -> None:
    # "Test" alone (no bracket) should not match -- defensive.
    assert classify("Test") == "unknown"


# ---- categorize(): batch behaviour ------------------------------------------


def test_categorize_groups_jobs_by_class() -> None:
    result = categorize(
        [
            "Lint",
            "Type check",
            "Spelling (typos)",
            "Test (ubuntu-latest, Python 3.12)",
            "Mystery",
        ]
    )
    assert result == {
        "safe": ["Lint"],
        "heuristic": ["Spelling (typos)"],
        "risky": ["Type check", "Test (ubuntu-latest, Python 3.12)"],
        "unknown": ["Mystery"],
    }


def test_categorize_preserves_input_order_within_bucket() -> None:
    result = categorize(["Repo hygiene", "Lint", "Dead code (Vulture)"])
    assert result["safe"] == ["Repo hygiene", "Lint", "Dead code (Vulture)"]


def test_categorize_drops_empty_lines() -> None:
    result = categorize(["", "Lint", "   "])
    assert result == {"safe": ["Lint"], "heuristic": [], "risky": [], "unknown": []}


def test_categorize_empty_input() -> None:
    assert categorize([]) == {"safe": [], "heuristic": [], "risky": [], "unknown": []}


def test_categorize_all_buckets_always_present() -> None:
    # Even when no job lands in a bucket, the key MUST be present so the
    # workflow's `jq` query never sees a missing field.
    result = categorize(["Lint"])
    assert set(result.keys()) == {"safe", "heuristic", "risky", "unknown"}


def test_categorize_duplicates_preserved() -> None:
    # Duplicate failing jobs can legitimately occur on matrix legs; the
    # categorizer must not silently drop them.
    result = categorize(
        [
            "Test (ubuntu-latest, Python 3.12)",
            "Test (ubuntu-latest, Python 3.13)",
            "Test (macos-latest, Python 3.13)",
        ]
    )
    assert len(result["risky"]) == 3


# ---- main(): stdin / stdout contract ----------------------------------------


def _run_main(stdin_text: str) -> tuple[int, dict[str, list[str]]]:
    stdin = io.StringIO(stdin_text)
    stdout = io.StringIO()
    with mock.patch.object(sys, "stdin", stdin), mock.patch.object(sys, "stdout", stdout):
        rc = main()
    return rc, json.loads(stdout.getvalue())


def test_main_emits_json_with_all_buckets() -> None:
    rc, payload = _run_main("Lint\nType check\nSpelling (typos)\n")
    assert rc == 0
    assert payload == {
        "safe": ["Lint"],
        "heuristic": ["Spelling (typos)"],
        "risky": ["Type check"],
        "unknown": [],
    }


def test_main_handles_empty_stdin() -> None:
    rc, payload = _run_main("")
    assert rc == 0
    assert payload == {"safe": [], "heuristic": [], "risky": [], "unknown": []}


def test_main_handles_trailing_newlines() -> None:
    rc, payload = _run_main("Lint\n\n\n")
    assert rc == 0
    assert payload["safe"] == ["Lint"]


def test_main_strips_carriage_returns() -> None:
    # Workflow heredocs can leak CRLF on Windows-checked-out files;
    # `strip()` inside classify makes us resilient.
    rc, payload = _run_main("Lint\r\nType check\r\n")
    assert rc == 0
    assert payload["safe"] == ["Lint"]
    assert payload["risky"] == ["Type check"]


def test_main_output_is_deterministic() -> None:
    # Stable order matters because the workflow uses the JSON in
    # conditional steps -- a flaky order would re-classify the same run.
    rc1, p1 = _run_main("Lint\nSpelling (typos)\nType check\n")
    rc2, p2 = _run_main("Lint\nSpelling (typos)\nType check\n")
    assert rc1 == rc2 == 0
    assert p1 == p2


# ---- safety properties ------------------------------------------------------


def test_no_test_job_is_ever_safe() -> None:
    # The hardest invariant to break: a real test failure must never be
    # auto-fixed by the safe-class pipeline.
    for os_name in ("ubuntu-latest", "macos-latest", "windows-latest"):
        for py in ("3.11", "3.12", "3.13"):
            name = f"Test ({os_name}, Python {py})"
            assert classify(name) == "risky"


def test_no_security_job_is_ever_safe_or_heuristic() -> None:
    # Security findings must never be auto-fixed.
    risky = {
        "CodeQL",
        "Bandit (security)",
        "Semgrep (custom rules)",
        "pip-audit (deps)",
    }
    for name in risky:
        cls = classify(name)
        assert cls == "risky", f"{name!r} -> {cls!r} (expected risky)"


def test_unknown_never_promotes_to_safe() -> None:
    # Robustness: a job whose name was renamed upstream must default to a
    # non-fixing bucket. We model "promotion" as a literal class swap.
    for name in (
        "New unrecognised job",
        "Lint!",
        "lint",  # case differs
        "Repo hygiene (renamed)",
    ):
        assert classify(name) != "safe"
