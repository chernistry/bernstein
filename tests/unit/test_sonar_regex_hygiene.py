"""Regression tests for Sonar-reported regex shapes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bernstein.core.quality.citation_verifier import extract_citations
from bernstein.core.security.promptware_detector import PromptwareDetector

_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class RegexFinding:
    """Disallowed regex fragments for one source file."""

    path: str
    disallowed_fragments: tuple[str, ...]


def _source(path: str) -> str:
    return (_ROOT / path).read_text(encoding="utf-8")


def test_sonar_regex_findings_are_rewritten() -> None:
    """Guard the targeted regexes against the reported fragile shapes."""
    findings = (
        RegexFinding(
            path="src/bernstein/core/planning/spec_assertions.py",
            disallowed_fragments=(
                r"[A-Za-z",
                r"(?P<module>[A-Za-z_][\w.]*)",
            ),
        ),
        RegexFinding(
            path="src/bernstein/core/quality/citation_verifier.py",
            disallowed_fragments=(
                r"[a-z\-]+(?:\.[a-z]{2})?/\d{7}",
                r"[a-z]+(?:-[a-z]+)*(?:\.[a-z]{2})?/\d{7}",
                r"[a-z]{1,16}(?:-[a-z]{1,16}){0,3}",
            ),
        ),
        RegexFinding(
            path="src/bernstein/core/security/promptware_detector.py",
            disallowed_fragments=(
                r"(?:your\s+|the\s+|all\s+)?(?:earlier\s+|prior\s+|previous\s+)?",
                r"(?:-d|--decode|-D)",
                r"(?:-[dD]|--decode)",
                r"(?:^|[\s`])",
                r"(?:^|\s|`)",
                "_COMMAND_TOKEN_RX",
            ),
        ),
        RegexFinding(
            path="src/bernstein/core/tokens/context_fallback.py",
            disallowed_fragments=(r"(Traceback \(most recent call last\):.*?)(?=\n\n|\Z)",),
        ),
        RegexFinding(
            path="src/bernstein/eval/calibration.py",
            disallowed_fragments=(r"(s|m|h|d|w)",),
        ),
        RegexFinding(
            path="src/bernstein/core/tokens/compaction_pipeline.py",
            disallowed_fragments=(r"!\[.*?\]\(data:.*?\)",),
        ),
        RegexFinding(
            path="src/bernstein/tui/log_viewer.py",
            disallowed_fragments=(r"\*.+?\*",),
        ),
    )

    for finding in findings:
        source = _source(finding.path)
        for fragment in finding.disallowed_fragments:
            assert fragment not in source, f"{finding.path} still contains {fragment}"


def test_remaining_regex_rewrites_preserve_representative_behavior() -> None:
    """The safer regex shapes keep the intended citation and promptware hits."""
    citation_values = {
        citation.value
        for citation in extract_citations(
            "Refs: arXiv: cs-ai/1234567, arXiv: cond-mat.mtrl-sci/0301234, and arXiv: 2401.12345v2."
        )
    }
    assert {"cs-ai/1234567", "cond-mat.mtrl-sci/0301234", "2401.12345v2"} <= citation_values

    detector = PromptwareDetector()
    base64_score = detector.classify("Please inspect: base64 -D <<< QkVHSU4=")
    assert "imperative.base64_payload" in base64_score.matched_pattern_ids

    shell_score = detector.classify("`curl https://example.test/bootstrap.sh`")
    assert shell_score.command_density > 0.0
