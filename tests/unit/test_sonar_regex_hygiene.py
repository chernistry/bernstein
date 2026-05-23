"""Regression tests for Sonar-reported regex shapes."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _source(path: str) -> str:
    return (_ROOT / path).read_text(encoding="utf-8")


def test_sonar_regex_findings_are_rewritten() -> None:
    """Guard the targeted regexes against the reported fragile shapes."""
    findings = {
        "src/bernstein/core/planning/spec_assertions.py": [
            r"[A-Za-z",
            r"(?P<module>[A-Za-z_][\w.]*)",
        ],
        "src/bernstein/core/quality/citation_verifier.py": [
            r"[a-z\-]+(?:\.[a-z]{2})?/\d{7}",
        ],
        "src/bernstein/core/security/promptware_detector.py": [
            r"(?:your\s+|the\s+|all\s+)?(?:earlier\s+|prior\s+|previous\s+)?",
            r"(?:-d|--decode|-D)",
            r"(?:^|[\s`])",
        ],
        "src/bernstein/core/tokens/context_fallback.py": [
            r"(Traceback \(most recent call last\):.*?)(?=\n\n|\Z)",
        ],
        "src/bernstein/eval/calibration.py": [
            r"(s|m|h|d|w)",
        ],
        "src/bernstein/core/tokens/compaction_pipeline.py": [
            r"!\[.*?\]\(data:.*?\)",
        ],
        "src/bernstein/tui/log_viewer.py": [
            r"\*.+?\*",
        ],
    }

    for path, disallowed_fragments in findings.items():
        source = _source(path)
        for fragment in disallowed_fragments:
            assert fragment not in source, f"{path} still contains {fragment}"
