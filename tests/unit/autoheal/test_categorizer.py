"""Unit tests for ``bernstein.core.autoheal.categorizer``."""

from __future__ import annotations

import pytest

from bernstein.core.autoheal.categorizer import (
    BucketedJobs,
    Classification,
    bucketize,
    classify,
)


@pytest.mark.parametrize(
    ("job", "expected_cls", "expected_rule"),
    [
        ("Lint", "safe", "safe_exact"),
        ("Repo hygiene", "safe", "safe_exact"),
        ("Dead code (Vulture)", "safe", "safe_exact"),
        ("Snapshot tests (syrupy)", "safe", "safe_exact"),
        ("Workflow lint", "safe", "safe_exact"),
    ],
)
def test_safe_exact_matches(job: str, expected_cls: str, expected_rule: str) -> None:
    c = classify(job)
    assert c.cls == expected_cls
    assert c.rule == expected_rule


def test_heuristic_spelling() -> None:
    c = classify("Spelling (typos)")
    assert c.cls == "heuristic"
    assert c.rule == "heuristic_exact"


@pytest.mark.parametrize(
    "job",
    [
        "Type check",
        "Pyright strict (security + cluster)",
        "CodeQL",
        "CodeQL (python)",
        "Bandit (security)",
        "Semgrep (custom rules)",
        "Mutation (diff-only)",
        "Schemathesis smoke",
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
def test_risky_exact(job: str) -> None:
    c = classify(job)
    assert c.cls == "risky"
    assert c.rule == "risky_exact"


@pytest.mark.parametrize(
    "job",
    [
        "Test (ubuntu-latest, Python 3.13)",
        "Test (macos-latest, Python 3.13)",
        "Test (windows-latest, Python 3.12)",
        "Test (ubuntu-latest, Python 3.12)",
    ],
)
def test_test_matrix_is_risky_prefix(job: str) -> None:
    c = classify(job)
    assert c.cls == "risky"
    assert c.rule == "risky_prefix"


@pytest.mark.parametrize("job", ["", "   ", "\t"])
def test_empty_or_whitespace_is_unknown(job: str) -> None:
    c = classify(job)
    assert c.cls == "unknown"
    assert c.rule == "unknown_default"


@pytest.mark.parametrize(
    "job",
    [
        "Some new unseen job",
        "Random Bot Action",
        "garbage-12345",
    ],
)
def test_unrecognised_jobs_are_unknown(job: str) -> None:
    c = classify(job)
    assert c.cls == "unknown"


def test_classify_returns_dataclass_with_original_name() -> None:
    c = classify("Lint")
    assert isinstance(c, Classification)
    assert c.name == "Lint"


def test_classify_strips_surrounding_whitespace() -> None:
    c = classify("  Lint  ")
    assert c.cls == "safe"
    assert c.name == "Lint"


def test_bucketize_groups_by_class() -> None:
    bucketed = bucketize(
        [
            "Lint",
            "Spelling (typos)",
            "Type check",
            "Random",
            "Repo hygiene",
            "Test (ubuntu-latest, Python 3.13)",
        ]
    )
    assert isinstance(bucketed, BucketedJobs)
    assert bucketed.safe == ("Lint", "Repo hygiene")
    assert bucketed.heuristic == ("Spelling (typos)",)
    assert bucketed.risky == ("Type check", "Test (ubuntu-latest, Python 3.13)")
    assert bucketed.unknown == ("Random",)


def test_bucketize_empty_input() -> None:
    bucketed = bucketize([])
    assert bucketed.safe == ()
    assert bucketed.heuristic == ()
    assert bucketed.risky == ()
    assert bucketed.unknown == ()
    assert bucketed.should_heal() is False


def test_should_heal_true_if_any_safe() -> None:
    bucketed = bucketize(["Lint"])
    assert bucketed.should_heal() is True


def test_should_heal_true_if_any_heuristic() -> None:
    bucketed = bucketize(["Spelling (typos)"])
    assert bucketed.should_heal() is True


def test_should_heal_false_if_only_risky_or_unknown() -> None:
    bucketed = bucketize(["Type check", "Random"])
    assert bucketed.should_heal() is False


def test_bucketize_preserves_order_within_bucket() -> None:
    bucketed = bucketize(["Repo hygiene", "Lint", "Snapshot tests (syrupy)"])
    assert bucketed.safe == ("Repo hygiene", "Lint", "Snapshot tests (syrupy)")


def test_bucketize_handles_duplicates() -> None:
    bucketed = bucketize(["Lint", "Lint", "Lint"])
    assert bucketed.safe == ("Lint", "Lint", "Lint")
