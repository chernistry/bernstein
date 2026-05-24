"""Tests for :mod:`bernstein.core.quality.pr_review_aggregator`.

Covers parsing, clustering, voting (including critical/high single-vote
veto), ranking + top-K, file grouping, and the end-to-end
``aggregate_pr_review`` and ``aggregate_from_pipeline`` flows.

Pure unit tests - no LLM, no subprocess calls.
"""

from __future__ import annotations

import time

from bernstein.core.quality.pr_review_aggregator import (
    DEFAULT_TOP_K,
    FindingCluster,
    PRFinding,
    aggregate_from_pipeline,
    aggregate_pr_review,
    cluster_findings,
    group_by_file,
    parse_finding,
    parse_findings_from_pipeline,
    rank_clusters,
    render_report_markdown,
    score_cluster,
    vote_clusters,
)
from bernstein.core.quality.review_pipeline.verdict import (
    AgentVerdict,
    PipelineVerdict,
    StageVerdict,
)

# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class TestParseFinding:
    def test_extracts_file_line_and_severity(self) -> None:
        f = parse_finding(
            "[critical] src/auth.py:42 hardcoded credential found",
            source_role="security",
            source_model="gemini",
        )
        assert f is not None
        assert f.file == "src/auth.py"
        assert f.line == 42
        assert f.severity == "critical"
        assert "hardcoded credential" in f.message
        assert f.source_role == "security"
        assert f.source_model == "gemini"

    def test_severity_defaults_to_medium_when_absent(self) -> None:
        f = parse_finding(
            "src/util.py:10 unused import",
            source_role="lint",
            source_model="haiku",
        )
        assert f is not None
        assert f.severity == "medium"

    def test_inferred_severity_from_security_keywords(self) -> None:
        f = parse_finding(
            "Possible SQL injection in user.py:15",
            source_role="security",
            source_model="gemini",
        )
        assert f is not None
        # Implicit "sql injection" hint should bump severity to critical.
        assert f.severity == "critical"

    def test_handles_no_file_or_line(self) -> None:
        f = parse_finding(
            "scope creep - diff touches stuff outside ticket",
            source_role="reviewer",
            source_model="claude",
        )
        assert f is not None
        assert f.file == ""
        assert f.line is None

    def test_returns_none_for_empty_input(self) -> None:
        assert parse_finding("", source_role="x", source_model="y") is None
        assert parse_finding("   \t \n", source_role="x", source_model="y") is None

    def test_strips_severity_marker_from_message(self) -> None:
        f = parse_finding(
            "(high) bad pattern detected in src/x.py:7",
            source_role="qa",
            source_model="haiku",
        )
        assert f is not None
        assert f.severity == "high"
        assert "bad pattern" in f.message

    def test_picks_highest_severity_when_multiple_present(self) -> None:
        f = parse_finding(
            "[low] minor BUT critical regression in auth.py:9",
            source_role="qa",
            source_model="haiku",
        )
        assert f is not None
        assert f.severity == "critical"

    def test_preserves_confidence(self) -> None:
        f = parse_finding(
            "src/x.py:1 issue",
            source_role="r",
            source_model="m",
            confidence=0.4,
        )
        assert f is not None
        assert f.confidence == 0.4

    def test_does_not_extract_path_inside_larger_token(self) -> None:
        f = parse_finding(
            "prefixsrc/auth.py:42suffix should not be treated as a file citation",
            source_role="r",
            source_model="m",
        )
        assert f is not None
        assert f.file == ""
        assert f.line is None

    def test_slash_only_non_path_input_is_fast(self) -> None:
        text = "/" * 10_000 + " no path here"

        started = time.perf_counter()
        f = parse_finding(text, source_role="r", source_model="m")
        elapsed = time.perf_counter() - started

        assert f is not None
        assert f.file == ""
        assert f.line is None
        assert elapsed < 0.5


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------


def _f(
    *,
    file: str = "src/x.py",
    line: int | None = 10,
    severity: str = "medium",
    message: str = "issue",
    role: str = "r",
    model: str = "m",
    confidence: float = 1.0,
) -> PRFinding:
    return PRFinding(
        file=file,
        line=line,
        severity=severity,  # type: ignore[arg-type]
        message=message,
        source_role=role,
        source_model=model,
        confidence=confidence,
    )


class TestClusterFindings:
    def test_same_file_and_exact_line_clusters_regardless_of_wording(self) -> None:
        findings = [
            _f(role="security", message="hardcoded api key in config"),
            _f(role="qa", message="secret literal looks committed"),
        ]
        clusters = cluster_findings(findings)
        assert len(clusters) == 1
        assert clusters[0].reviewer_count == 2

    def test_same_file_different_lines_within_window_clusters(self) -> None:
        findings = [
            _f(role="security", line=10, message="missing input validation"),
            _f(role="qa", line=12, message="missing input validation here"),
        ]
        clusters = cluster_findings(findings, line_window=3)
        assert len(clusters) == 1

    def test_outside_line_window_does_not_cluster(self) -> None:
        findings = [
            _f(role="security", line=10, message="missing input validation"),
            _f(role="qa", line=80, message="missing input validation"),
        ]
        clusters = cluster_findings(findings, line_window=3)
        assert len(clusters) == 2

    def test_different_files_never_cluster(self) -> None:
        findings = [
            _f(file="src/a.py", role="security", message="bad pattern"),
            _f(file="src/b.py", role="qa", message="bad pattern"),
        ]
        clusters = cluster_findings(findings)
        assert len(clusters) == 2

    def test_canonical_message_is_longest(self) -> None:
        findings = [
            _f(role="r1", message="leak"),
            _f(role="r2", message="leaks credentials into log"),
        ]
        clusters = cluster_findings(findings)
        assert len(clusters) == 1
        assert clusters[0].canonical_message == "leaks credentials into log"

    def test_severity_picks_highest_in_cluster(self) -> None:
        findings = [
            _f(role="r1", severity="medium", message="leak"),
            _f(role="r2", severity="high", message="leak"),
            _f(role="r3", severity="low", message="leak"),
        ]
        clusters = cluster_findings(findings)
        assert len(clusters) == 1
        assert clusters[0].severity == "high"

    def test_token_overlap_threshold_separates_unrelated_findings(self) -> None:
        findings = [
            _f(file="", line=None, role="r1", message="missing tests for new endpoint"),
            _f(file="", line=None, role="r2", message="commit message lacks ticket id"),
        ]
        clusters = cluster_findings(findings, token_overlap=0.4)
        assert len(clusters) == 2


# ---------------------------------------------------------------------------
# Voting
# ---------------------------------------------------------------------------


class TestVoteClusters:
    def test_majority_threshold_kicks_in(self) -> None:
        # 4 reviewers → ceil(4/2) = 2 needed.
        clusters = [
            FindingCluster(
                file="x",
                line=1,
                severity="medium",
                canonical_message="lone flag",
                members=(_f(role="r1"),),
            ),
            FindingCluster(
                file="y",
                line=1,
                severity="medium",
                canonical_message="two votes",
                members=(_f(role="r1"), _f(role="r2")),
            ),
        ]
        survivors = vote_clusters(clusters, n_reviewers=4)
        assert len(survivors) == 1
        assert survivors[0].canonical_message == "two votes"

    def test_critical_severity_bypasses_quorum(self) -> None:
        clusters = [
            FindingCluster(
                file="x",
                line=1,
                severity="critical",
                canonical_message="rce",
                members=(_f(role="security"),),
            ),
        ]
        survivors = vote_clusters(clusters, n_reviewers=4)
        assert len(survivors) == 1

    def test_high_severity_bypasses_quorum(self) -> None:
        cluster = FindingCluster(
            file="x",
            line=1,
            severity="high",
            canonical_message="auth bypass",
            members=(_f(role="security", severity="high"),),
        )
        survivors = vote_clusters([cluster], n_reviewers=4)
        assert survivors == [cluster]

    def test_low_severity_below_quorum_dropped(self) -> None:
        cluster = FindingCluster(
            file="x",
            line=1,
            severity="low",
            canonical_message="nit",
            members=(_f(role="lint", severity="low"),),
        )
        survivors = vote_clusters([cluster], n_reviewers=4)
        assert survivors == []

    def test_explicit_min_reviewers_overrides_default(self) -> None:
        cluster = FindingCluster(
            file="x",
            line=1,
            severity="medium",
            canonical_message="m",
            members=(_f(role="r1"),),
        )
        survivors = vote_clusters([cluster], n_reviewers=4, min_reviewers=1)
        assert survivors == [cluster]


# ---------------------------------------------------------------------------
# Scoring + ranking
# ---------------------------------------------------------------------------


class TestRanking:
    def test_critical_outranks_low_with_more_voters(self) -> None:
        critical = FindingCluster(
            file="a",
            line=1,
            severity="critical",
            canonical_message="rce",
            members=(_f(role="security", severity="critical"),),
        )
        low_unanimous = FindingCluster(
            file="b",
            line=1,
            severity="low",
            canonical_message="nit",
            members=tuple(_f(role=f"r{i}", severity="low") for i in range(4)),
        )
        ranked = rank_clusters([low_unanimous, critical], n_reviewers=4)
        assert ranked[0].severity == "critical"

    def test_top_k_caps_output(self) -> None:
        clusters = [
            FindingCluster(
                file=f"file_{i}.py",
                line=i,
                severity="high",
                canonical_message=f"f{i}",
                members=(_f(role="r1", severity="high"),),
            )
            for i in range(5)
        ]
        ranked = rank_clusters(clusters, n_reviewers=2, top_k=3)
        assert len(ranked) == 3

    def test_top_k_zero_returns_all(self) -> None:
        clusters = [
            FindingCluster(
                file=f"x{i}.py",
                line=1,
                severity="medium",
                canonical_message="m",
                members=(_f(role="r1"),),
            )
            for i in range(4)
        ]
        ranked = rank_clusters(clusters, n_reviewers=4, top_k=0)
        assert len(ranked) == 4

    def test_score_cluster_increases_with_severity(self) -> None:
        low_cluster = FindingCluster(
            file="x",
            line=1,
            severity="low",
            canonical_message="m",
            members=(_f(role="r", severity="low"),),
        )
        critical_cluster = FindingCluster(
            file="x",
            line=1,
            severity="critical",
            canonical_message="m",
            members=(_f(role="r", severity="critical"),),
        )
        assert score_cluster(critical_cluster, n_reviewers=1) > score_cluster(low_cluster, n_reviewers=1)

    def test_score_cluster_increases_with_agreement(self) -> None:
        lone = FindingCluster(
            file="x",
            line=1,
            severity="medium",
            canonical_message="m",
            members=(_f(role="r1"),),
        )
        agreed = FindingCluster(
            file="x",
            line=1,
            severity="medium",
            canonical_message="m",
            members=(_f(role="r1"), _f(role="r2"), _f(role="r3"), _f(role="r4")),
        )
        assert score_cluster(agreed, n_reviewers=4) > score_cluster(lone, n_reviewers=4)

    def test_ranking_is_deterministic(self) -> None:
        clusters_a = [
            FindingCluster(
                file="a.py", line=1, severity="high", canonical_message="x", members=(_f(role="r1", severity="high"),)
            ),
            FindingCluster(
                file="b.py", line=1, severity="high", canonical_message="x", members=(_f(role="r1", severity="high"),)
            ),
        ]
        clusters_b = list(reversed(clusters_a))
        ranked_a = rank_clusters(clusters_a, n_reviewers=2)
        ranked_b = rank_clusters(clusters_b, n_reviewers=2)
        assert [c.file for c in ranked_a] == [c.file for c in ranked_b]


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------


class TestGroupByFile:
    def test_buckets_clusters_per_file(self) -> None:
        clusters = [
            FindingCluster(file="a.py", line=1, severity="high", canonical_message="x", members=(_f(),)),
            FindingCluster(file="b.py", line=2, severity="high", canonical_message="y", members=(_f(),)),
            FindingCluster(file="a.py", line=3, severity="high", canonical_message="z", members=(_f(),)),
        ]
        buckets = group_by_file(clusters)
        assert set(buckets.keys()) == {"a.py", "b.py"}
        assert len(buckets["a.py"]) == 2
        assert len(buckets["b.py"]) == 1

    def test_unattributed_cluster_uses_empty_string_key(self) -> None:
        clusters = [
            FindingCluster(
                file="", line=None, severity="medium", canonical_message="m", members=(_f(file="", line=None),)
            ),
        ]
        buckets = group_by_file(clusters)
        assert "" in buckets


# ---------------------------------------------------------------------------
# End-to-end: aggregate_pr_review
# ---------------------------------------------------------------------------


class TestAggregatePRReview:
    def test_full_pipeline_dedupes_and_keeps_majority(self) -> None:
        findings = [
            _f(
                file="src/a.py",
                line=10,
                role="security",
                severity="high",
                message="missing input validation in handler",
            ),
            _f(file="src/a.py", line=11, role="qa", severity="medium", message="missing input validation here"),
            _f(
                file="src/b.py",
                line=20,
                role="security",
                severity="low",
                message="lone style nit no one else cares about",
            ),
        ]
        # N=4 => threshold ceil(4/2)=2; the lone "low" finding fails quorum
        # and has no severity veto, so only the merged a.py cluster surfaces.
        report = aggregate_pr_review(findings, n_reviewers=4)
        assert report.total_clusters == 2
        assert len(report.clusters) == 1
        assert report.clusters[0].file == "src/a.py"

    def test_critical_lone_finding_surfaces(self) -> None:
        findings = [
            _f(
                file="src/a.py",
                line=10,
                role="security",
                severity="critical",
                message="hardcoded admin token committed",
            ),
        ]
        report = aggregate_pr_review(findings, n_reviewers=4)
        assert len(report.clusters) == 1
        assert report.clusters[0].severity == "critical"

    def test_no_findings_returns_empty_report(self) -> None:
        report = aggregate_pr_review([], n_reviewers=4)
        assert report.total_input == 0
        assert report.clusters == ()

    def test_n_reviewers_inferred_from_findings_when_omitted(self) -> None:
        findings = [
            _f(role="security", severity="low"),
            _f(role="qa", severity="low"),
        ]
        report = aggregate_pr_review(findings)
        # Inferred N=2; ceil(2/2)=1 so both findings cluster as one and survive.
        assert report.n_reviewers == 2
        assert len(report.clusters) == 1

    def test_top_k_respected(self) -> None:
        # Forty single-reviewer high-severity findings → all veto-pass,
        # but only DEFAULT_TOP_K should be surfaced.
        findings = [
            _f(file=f"src/f{i}.py", line=i, role="security", severity="high", message=f"issue {i}") for i in range(40)
        ]
        report = aggregate_pr_review(findings, n_reviewers=4)
        assert len(report.clusters) == DEFAULT_TOP_K


# ---------------------------------------------------------------------------
# End-to-end from PipelineVerdict
# ---------------------------------------------------------------------------


def _agent(
    role: str,
    model: str,
    issues: list[str],
    *,
    verdict: str = "request_changes",
) -> AgentVerdict:
    return AgentVerdict(
        role=role,
        model=model,
        verdict=verdict,  # type: ignore[arg-type]
        feedback="",
        issues=issues,
    )


def _pipeline_verdict(stages: list[StageVerdict]) -> PipelineVerdict:
    return PipelineVerdict(
        verdict="request_changes",
        feedback="",
        pass_score=0.0,
        stages=stages,
    )


class TestAggregateFromPipeline:
    def test_parses_and_aggregates_multi_stage_verdict(self) -> None:
        stage = StageVerdict(
            stage="cheap-verifiers",
            verdict="request_changes",
            approve_count=0,
            total_count=2,
            pass_score=0.0,
            agents=[
                _agent(
                    "security",
                    "gemini",
                    ["[critical] src/auth.py:42 hardcoded credential"],
                ),
                _agent(
                    "qa",
                    "haiku",
                    ["src/auth.py:43 hardcoded credential committed"],
                ),
            ],
        )
        verdict = _pipeline_verdict([stage])
        report = aggregate_from_pipeline(verdict)
        assert report.n_reviewers == 2
        # Both findings should fold into one cluster (same file, lines
        # within window, similar wording).
        assert len(report.clusters) == 1
        assert report.clusters[0].severity == "critical"
        assert report.clusters[0].reviewer_count == 2

    def test_skips_approved_agents_with_no_issues(self) -> None:
        stage = StageVerdict(
            stage="s",
            verdict="approve",
            approve_count=1,
            total_count=2,
            pass_score=0.5,
            agents=[
                _agent("a", "m1", [], verdict="approve"),
                _agent(
                    "b",
                    "m2",
                    ["[high] src/x.py:10 missing auth check"],
                ),
            ],
        )
        verdict = _pipeline_verdict([stage])
        report = aggregate_from_pipeline(verdict)
        assert report.n_reviewers == 2
        # High-severity vetoes through despite single-flag.
        assert len(report.clusters) == 1


# ---------------------------------------------------------------------------
# parse_findings_from_pipeline edge case
# ---------------------------------------------------------------------------


class TestParseFindingsFromPipeline:
    def test_drops_unparseable_blank_issues(self) -> None:
        stage = StageVerdict(
            stage="s",
            verdict="request_changes",
            approve_count=0,
            total_count=1,
            pass_score=0.0,
            agents=[
                _agent("r", "m", ["", "  ", "real finding in src/x.py:1"]),
            ],
        )
        findings = parse_findings_from_pipeline(_pipeline_verdict([stage]))
        assert len(findings) == 1
        assert findings[0].file == "src/x.py"


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


class TestRender:
    def test_empty_report_renders_message(self) -> None:
        report = aggregate_pr_review([], n_reviewers=2)
        md = render_report_markdown(report)
        assert "No findings" in md

    def test_renders_severity_and_file_headers(self) -> None:
        findings = [
            _f(file="src/a.py", line=10, role="security", severity="critical", message="rce risk"),
        ]
        report = aggregate_pr_review(findings, n_reviewers=2)
        md = render_report_markdown(report)
        assert "src/a.py" in md
        assert "[critical]" in md
        assert "rce risk" in md
