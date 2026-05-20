"""Tests for :mod:`bernstein.core.quality.review_consensus`.

Covers the consensus engine end to end: single-bot findings, multi-bot
agreement, multi-bot disagreement (distinct loci stay separate),
detected-by provenance, bucket boundaries, the must-address promotion
threshold, and the rendered sticky-comment provenance line.

Pure unit tests - no LLM, no subprocess, no network.
"""

from __future__ import annotations

from bernstein.core.quality.review_consensus import (
    CONFIRMED_THRESHOLD,
    NEEDS_VERIFICATION_THRESHOLD,
    ConsensusLevel,
    Evidence,
    NormalizedFinding,
    bucket_for_score,
    compute_consensus,
    must_address,
    render_consensus_markdown,
    render_provenance,
)


def _finding(
    bot: str,
    *,
    file: str = "src/auth.py",
    line: int | None = 42,
    category: str = "security",
    title: str = "hardcoded credential in auth handler",
    severity: str = "high",
    confidence: float = 1.0,
    finding_id: str = "f1",
) -> NormalizedFinding:
    return NormalizedFinding(
        bot=bot,
        finding_id=finding_id,
        severity=severity,  # type: ignore[arg-type]
        category=category,
        title=title,
        evidence=Evidence(file=file, line=line),
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Single bot
# ---------------------------------------------------------------------------


class TestNormalizedFinding:
    def test_locus_key_is_file_line_category(self) -> None:
        f = _finding("coderabbit", file="src/a.py", line=7, category="security")
        assert f.locus_key == ("src/a.py", 7, "security")


class TestSingleBot:
    def test_single_bot_single_finding(self) -> None:
        out = compute_consensus([_finding("coderabbit")], bots_ran=3)
        assert len(out) == 1
        c = out[0]
        assert c.detected_by == ("coderabbit",)
        assert c.detected_by_count == 1
        assert c.bots_ran == 3
        # 1 of 3 bots -> 0.333, x max_confidence 1.0.
        assert abs(c.agreement_ratio - 1 / 3) < 1e-9
        assert abs(c.consensus_score - 1 / 3) < 1e-9
        assert c.level is ConsensusLevel.NEEDS_VERIFICATION

    def test_single_bot_low_confidence_drops_to_unverified(self) -> None:
        out = compute_consensus(
            [_finding("sourcery", confidence=0.5)],
            bots_ran=3,
        )
        # agreement 0.333 * 0.5 confidence = 0.166 -> unverified.
        assert out[0].consensus_score < NEEDS_VERIFICATION_THRESHOLD
        assert out[0].level is ConsensusLevel.UNVERIFIED

    def test_lone_bot_run_is_fully_confirmed(self) -> None:
        # When only one bot ran, its finding has 100% agreement.
        out = compute_consensus([_finding("coderabbit")], bots_ran=1)
        assert out[0].agreement_ratio == 1.0
        assert out[0].consensus_score == 1.0
        assert out[0].level is ConsensusLevel.CONFIRMED


# ---------------------------------------------------------------------------
# Multi-bot agreement
# ---------------------------------------------------------------------------


class TestMultiBotAgree:
    def test_three_bots_same_locus_merge_and_confirm(self) -> None:
        out = compute_consensus(
            [
                _finding("coderabbit"),
                _finding("sourcery"),
                _finding("gh-advanced-security"),
            ],
            bots_ran=3,
        )
        assert len(out) == 1
        c = out[0]
        assert c.detected_by == ("coderabbit", "gh-advanced-security", "sourcery")
        assert c.agreement_ratio == 1.0
        assert c.consensus_score == 1.0
        assert c.level is ConsensusLevel.CONFIRMED

    def test_merges_within_line_window(self) -> None:
        out = compute_consensus(
            [
                _finding("coderabbit", line=42),
                _finding("sourcery", line=44),  # within DEFAULT_LINE_WINDOW=3
            ],
            bots_ran=2,
        )
        assert len(out) == 1
        assert out[0].detected_by_count == 2
        # Anchoring line is the smallest member line.
        assert out[0].line == 42

    def test_fuzzy_title_match_without_line(self) -> None:
        out = compute_consensus(
            [
                _finding("coderabbit", line=None, title="possible SQL injection in query builder"),
                _finding("sourcery", line=None, title="SQL injection risk in the query builder path"),
            ],
            bots_ran=2,
        )
        assert len(out) == 1
        assert out[0].detected_by_count == 2

    def test_max_confidence_used_not_mean(self) -> None:
        out = compute_consensus(
            [
                _finding("coderabbit", confidence=0.4),
                _finding("sourcery", confidence=0.9),
            ],
            bots_ran=2,
        )
        assert out[0].max_confidence == 0.9
        # agreement 1.0 * 0.9 = 0.9.
        assert abs(out[0].consensus_score - 0.9) < 1e-9

    def test_out_of_range_confidence_is_clamped(self) -> None:
        # An adapter reporting confidence outside [0.0, 1.0] must not push the
        # consensus score past its band; clamping keeps bucketing stable.
        out = compute_consensus(
            [
                _finding("coderabbit", confidence=5.0),
                _finding("sourcery", confidence=-2.0),
            ],
            bots_ran=2,
        )
        assert out[0].max_confidence == 1.0
        # agreement 1.0 * clamped 1.0 = 1.0.
        assert out[0].consensus_score == 1.0
        assert out[0].level is ConsensusLevel.CONFIRMED


# ---------------------------------------------------------------------------
# Multi-bot disagreement
# ---------------------------------------------------------------------------


class TestMultiBotDisagree:
    def test_different_categories_stay_separate(self) -> None:
        out = compute_consensus(
            [
                _finding("coderabbit", category="security"),
                _finding("sourcery", category="style"),
            ],
            bots_ran=2,
        )
        assert len(out) == 2
        for c in out:
            assert c.detected_by_count == 1

    def test_distant_lines_stay_separate(self) -> None:
        out = compute_consensus(
            [
                _finding("coderabbit", line=10, title="unused variable foo"),
                _finding("sourcery", line=200, title="unused variable bar"),
            ],
            bots_ran=2,
        )
        assert len(out) == 2

    def test_different_files_stay_separate(self) -> None:
        out = compute_consensus(
            [
                _finding("coderabbit", file="src/a.py"),
                _finding("sourcery", file="src/b.py"),
            ],
            bots_ran=2,
        )
        assert len(out) == 2


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


class TestProvenance:
    def test_detected_by_is_sorted_and_distinct(self) -> None:
        out = compute_consensus(
            [
                _finding("sourcery"),
                _finding("coderabbit"),
                _finding("sourcery", finding_id="f2"),  # same bot, second hit
            ],
            bots_ran=2,
        )
        assert out[0].detected_by == ("coderabbit", "sourcery")
        assert out[0].detected_by_count == 2

    def test_render_provenance_format(self) -> None:
        c = compute_consensus(
            [_finding("coderabbit"), _finding("sourcery")],
            bots_ran=4,
        )[0]
        # 2 of 4 bots agreed -> 50%.
        assert render_provenance(c) == "[detected by 2/4 bots, agreement 50%]"

    def test_markdown_carries_provenance_and_bot_names(self) -> None:
        out = compute_consensus(
            [_finding("coderabbit"), _finding("sourcery")],
            bots_ran=2,
        )
        md = render_consensus_markdown(out)
        assert "[detected by 2/2 bots, agreement 100%]" in md
        assert "by coderabbit, sourcery" in md
        assert "### confirmed" in md

    def test_markdown_empty(self) -> None:
        assert "_No findings to report._" in render_consensus_markdown([])


# ---------------------------------------------------------------------------
# Bucket boundaries
# ---------------------------------------------------------------------------


class TestBucketBoundaries:
    def test_confirmed_lower_edge_inclusive(self) -> None:
        assert bucket_for_score(CONFIRMED_THRESHOLD) is ConsensusLevel.CONFIRMED
        assert bucket_for_score(1.0) is ConsensusLevel.CONFIRMED

    def test_needs_verification_band(self) -> None:
        assert bucket_for_score(NEEDS_VERIFICATION_THRESHOLD) is ConsensusLevel.NEEDS_VERIFICATION
        assert bucket_for_score(CONFIRMED_THRESHOLD - 1e-9) is ConsensusLevel.NEEDS_VERIFICATION

    def test_unverified_band(self) -> None:
        assert bucket_for_score(0.0) is ConsensusLevel.UNVERIFIED
        assert bucket_for_score(NEEDS_VERIFICATION_THRESHOLD - 1e-9) is ConsensusLevel.UNVERIFIED


# ---------------------------------------------------------------------------
# Must-address promotion threshold
# ---------------------------------------------------------------------------


class TestMustAddress:
    def test_promotes_only_confirmed_by_default(self) -> None:
        out = compute_consensus(
            [
                # Confirmed: both bots agree (2/2).
                _finding("coderabbit", file="src/a.py", line=1, category="security"),
                _finding("sourcery", file="src/a.py", line=1, category="security"),
                # Needs-verification: lone bot of two (1/2 = 0.5).
                _finding("coderabbit", file="src/b.py", line=9, category="style", title="long line"),
            ],
            bots_ran=2,
        )
        blocking = must_address(out)
        assert len(blocking) == 1
        assert blocking[0].file == "src/a.py"
        assert blocking[0].level is ConsensusLevel.CONFIRMED

    def test_min_level_can_lower_the_bar(self) -> None:
        out = compute_consensus(
            [_finding("coderabbit", file="src/b.py", line=9, category="style", title="long line")],
            bots_ran=2,
        )
        # 1/2 = 0.5 -> needs-verification, excluded by default.
        assert must_address(out) == []
        # Lower the gate to needs-verification and it surfaces.
        promoted = must_address(out, min_level=ConsensusLevel.NEEDS_VERIFICATION)
        assert len(promoted) == 1


# ---------------------------------------------------------------------------
# Denominator handling
# ---------------------------------------------------------------------------


class TestDenominator:
    def test_bots_ran_derived_when_omitted(self) -> None:
        # No explicit bots_ran -> derived from distinct input bots (2).
        out = compute_consensus([_finding("coderabbit"), _finding("sourcery")])
        assert out[0].bots_ran == 2
        assert out[0].agreement_ratio == 1.0

    def test_agreement_clamped_to_one(self) -> None:
        # bots_ran under-reported relative to distinct detectors must clamp.
        out = compute_consensus(
            [_finding("coderabbit"), _finding("sourcery")],
            bots_ran=1,
        )
        assert out[0].agreement_ratio == 1.0
        assert out[0].consensus_score == 1.0
