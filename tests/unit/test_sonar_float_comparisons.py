"""Regression coverage for Sonar float-comparison findings."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_sonar_float_sentinel_comparisons_use_ordered_checks() -> None:
    """Avoid exact equality checks against float sentinel values."""
    files = (
        REPO_ROOT / "src/bernstein/core/autofix/review_router.py",
        REPO_ROOT / "src/bernstein/core/cost/cost_rollup_by_envelope.py",
    )
    disallowed = ("== 0.0", "!= 0.0")

    offenders: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        offenders.extend(
            f"{path.relative_to(REPO_ROOT)} contains {fragment}" for fragment in disallowed if fragment in text
        )

    assert offenders == []
