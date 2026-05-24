"""Regression coverage for Sonar regex readability findings."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

DISALLOWED_REGEX_FRAGMENTS: dict[str, tuple[str, ...]] = {
    "src/bernstein/core/communication/direct.py": (
        r"[A-Za-z0-9_\-]",
        r"[A-Za-z0-9][A-Za-z0-9_\-]*",
    ),
    "src/bernstein/core/devops/trend_scan.py": (r"[A-Za-z0-9_+-]",),
    "src/bernstein/core/lifecycle/hook_filter.py": (r"[A-Za-z0-9_]",),
    "src/bernstein/core/persistence/action_cache.py": (
        r"[A-Za-z0-9_]",
        r"[A-Za-z0-9._\-]",
    ),
    "src/bernstein/core/planning/spec_quality.py": (r"[\w./_-]",),
    "src/bernstein/core/quality/pr_review_aggregator.py": (r"[A-Za-z_0-9]",),
    "src/bernstein/core/quality/review_consensus.py": (r"[A-Za-z_0-9]",),
    "src/bernstein/core/tasks/backlog_parser.py": (
        r"[A-Za-z0-9_,\s\-]",
        r"[A-Za-z0-9_\-]",
    ),
    "src/bernstein/core/tasks/task_lifecycle.py": (r"[-:\-]",),
    "src/bernstein/eval/incident_synthesizer.py": (r"[^A-Za-z0-9_]",),
    "src/bernstein/evolution/detector.py": (r"[-\-]",),
    "src/bernstein/sdd/validator.py": (r"[0-9]",),
}


def test_sonar_regex_character_classes_stay_concise() -> None:
    """Regexes should avoid duplicate elements and redundant ASCII classes."""
    failures: list[str] = []

    for relative_path, fragments in DISALLOWED_REGEX_FRAGMENTS.items():
        source = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        for fragment in fragments:
            if fragment in source:
                failures.append(f"{relative_path}: {fragment}")

    assert not failures
