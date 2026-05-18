"""Unit tests for the synthesizer module."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bernstein.core.knowledge.diary import DiaryEntry
from bernstein.core.knowledge.synthesizer import (
    DEFAULT_JACCARD_THRESHOLD,
    SynthesisReport,
    SynthesizerError,
    Theme,
    approve,
    build_themes,
    cluster_entries,
    filter_recent,
    jaccard,
    parse_duration,
    render_report,
    synthesize,
    write_report,
)


def _make_entry(
    task_id: str,
    tags: tuple[str, ...],
    *,
    tried: tuple[str, ...] = (),
    worked: tuple[str, ...] = (),
    failed: tuple[str, ...] = (),
    rationale: str = "",
    created_at: str | None = None,
) -> DiaryEntry:
    return DiaryEntry(
        task_id=task_id,
        tried=tried,
        worked=worked,
        failed=failed,
        rationale=rationale,
        tags=tags,
        redaction_hash="x" * 64,
        created_at=created_at or "2026-05-01T12:00:00+00:00",
    )


# ---------------------------------------------------------------------------
# Jaccard
# ---------------------------------------------------------------------------


class TestJaccard:
    """Cover Jaccard similarity invariants."""

    def test_identical_sets(self) -> None:
        assert jaccard(("a", "b"), ("a", "b")) == 1.0

    def test_disjoint_sets(self) -> None:
        assert jaccard(("a",), ("b",)) == 0.0

    def test_empty_sets(self) -> None:
        assert jaccard((), ()) == 0.0

    def test_one_empty(self) -> None:
        assert jaccard((), ("a",)) == 0.0

    def test_partial_overlap(self) -> None:
        score = jaccard(("a", "b"), ("b", "c"))
        assert 0.3 < score < 0.4  # 1/3

    def test_symmetric(self) -> None:
        assert jaccard(("a", "b", "c"), ("b", "c")) == jaccard(("b", "c"), ("a", "b", "c"))


# ---------------------------------------------------------------------------
# Filter recent
# ---------------------------------------------------------------------------


class TestFilterRecent:
    """Cover time-window filtering."""

    def test_window_zero_returns_all(self) -> None:
        entries = [_make_entry("t-1", ("a",))]
        result = filter_recent(entries, 0)
        assert result == entries

    def test_window_excludes_old(self) -> None:
        old = _make_entry("old", ("a",), created_at="2026-01-01T00:00:00+00:00")
        new = _make_entry("new", ("a",), created_at="2026-05-01T00:00:00+00:00")
        result = filter_recent([old, new], 14, now=datetime(2026, 5, 10, tzinfo=UTC))
        assert [e.task_id for e in result] == ["new"]

    def test_window_keeps_recent(self) -> None:
        recent = _make_entry("recent", ("a",), created_at="2026-05-08T00:00:00+00:00")
        result = filter_recent([recent], 7, now=datetime(2026, 5, 10, tzinfo=UTC))
        assert result == [recent]

    def test_unparseable_created_at_kept(self) -> None:
        bogus = _make_entry("bogus", ("a",), created_at="not-a-date")
        result = filter_recent([bogus], 1, now=datetime(2026, 5, 10, tzinfo=UTC))
        assert result == [bogus]

    def test_negative_window_returns_all(self) -> None:
        entries = [_make_entry("x", ("a",))]
        assert filter_recent(entries, -3) == entries


# ---------------------------------------------------------------------------
# Cluster entries
# ---------------------------------------------------------------------------


class TestClusterEntries:
    """Cover the single-linkage clustering pass."""

    def test_cluster_empty(self) -> None:
        assert cluster_entries([]) == []

    def test_cluster_singletons(self) -> None:
        entries = [
            _make_entry("t-1", ("a",)),
            _make_entry("t-2", ("b",)),
        ]
        clusters = cluster_entries(entries, threshold=DEFAULT_JACCARD_THRESHOLD)
        assert len(clusters) == 2

    def test_cluster_merges_similar(self) -> None:
        entries = [
            _make_entry("t-1", ("a", "b")),
            _make_entry("t-2", ("a", "b")),
        ]
        clusters = cluster_entries(entries, threshold=0.5)
        assert len(clusters) == 1
        assert len(clusters[0]) == 2

    def test_cluster_threshold_respected(self) -> None:
        entries = [
            _make_entry("t-1", ("a", "b", "c", "d")),
            _make_entry("t-2", ("a", "e", "f", "g")),
        ]
        # Jaccard = 1/7, well below 0.5
        clusters = cluster_entries(entries, threshold=0.5)
        assert len(clusters) == 2

    def test_cluster_invalid_threshold(self) -> None:
        with pytest.raises(SynthesizerError):
            cluster_entries([], threshold=1.5)

    def test_cluster_invalid_min_size(self) -> None:
        with pytest.raises(SynthesizerError):
            cluster_entries([], min_cluster_size=0)

    def test_cluster_min_size_drops_small(self) -> None:
        entries = [_make_entry("t-1", ("a",))]
        clusters = cluster_entries(entries, min_cluster_size=2)
        assert clusters == []

    def test_cluster_sorted_by_size_desc(self) -> None:
        entries = [
            _make_entry("t-1", ("z",)),
            _make_entry("t-2", ("a", "b")),
            _make_entry("t-3", ("a", "b")),
        ]
        clusters = cluster_entries(entries, threshold=0.5)
        assert clusters[0] == [entries[1], entries[2]] or len(clusters[0]) >= len(clusters[-1])


# ---------------------------------------------------------------------------
# Build themes
# ---------------------------------------------------------------------------


class TestBuildThemes:
    """Cover theme construction from cluster lists."""

    def test_theme_label_uses_shared_tags(self) -> None:
        entries = [
            _make_entry("t-1", ("backend", "retry")),
            _make_entry("t-2", ("backend", "retry")),
        ]
        themes = build_themes([entries])
        assert "backend" in themes[0].label

    def test_theme_label_fallback_first_tag(self) -> None:
        entries = [_make_entry("t-1", ("solo",))]
        themes = build_themes([entries])
        assert themes[0].label == "solo"

    def test_theme_label_misc_when_no_tags(self) -> None:
        entries = [_make_entry("t-1", ())]
        themes = build_themes([entries])
        assert themes[0].label == "misc"

    def test_theme_id_is_stable(self) -> None:
        entries = [_make_entry("t-1", ("aa",))]
        themes_1 = build_themes([entries])
        themes_2 = build_themes([entries])
        assert themes_1[0].theme_id == themes_2[0].theme_id

    def test_proposed_diff_lists_failures(self) -> None:
        entries = [
            _make_entry("t-1", ("a",), failed=("dropped connection",)),
            _make_entry("t-2", ("a",), failed=("dropped connection",)),
        ]
        themes = build_themes([entries])
        assert "dropped connection" in themes[0].proposed_diff
        assert "failure" in themes[0].proposed_diff.lower()

    def test_proposed_diff_lists_successes(self) -> None:
        entries = [_make_entry("t-1", ("a",), worked=("retry on 503",))]
        themes = build_themes([entries])
        assert "retry on 503" in themes[0].proposed_diff
        assert "success" in themes[0].proposed_diff.lower()

    def test_proposed_diff_fallback_to_rationale(self) -> None:
        entries = [_make_entry("t-1", ("a",), rationale="some thought")]
        themes = build_themes([entries])
        assert "some thought" in themes[0].proposed_diff


# ---------------------------------------------------------------------------
# Synthesize
# ---------------------------------------------------------------------------


class TestSynthesize:
    """End-to-end pipeline pure-function tests."""

    def test_empty_entries_yields_note(self) -> None:
        report = synthesize([])
        assert report.themes == ()
        assert any("no diary entries" in n.lower() for n in report.notes)

    def test_filters_then_clusters(self) -> None:
        now = datetime(2026, 5, 10, tzinfo=UTC)
        entries = [
            _make_entry("old", ("a",), created_at="2026-01-01T00:00:00+00:00"),
            _make_entry("new-1", ("a", "b"), created_at="2026-05-09T00:00:00+00:00"),
            _make_entry("new-2", ("a", "b"), created_at="2026-05-09T00:00:00+00:00"),
        ]
        report = synthesize(entries, window_days=7, now=now)
        # Old entry filtered out -> single cluster of size 2
        assert report.theme_count == 1
        assert report.themes[0].size == 2

    def test_singleton_clusters_allowed(self) -> None:
        report = synthesize(
            [_make_entry("solo", ("uniqq",))],
            window_days=0,
        )
        assert report.theme_count == 1

    def test_window_zero_filters_nothing(self) -> None:
        e1 = _make_entry("t-1", ("a",), created_at="2026-05-09T00:00:00+00:00")
        e2 = _make_entry("t-2", ("a",), created_at="2026-05-09T00:00:00+00:00")
        report = synthesize([e1, e2], window_days=0)
        assert report.theme_count >= 1

    def test_window_zero_yields_no_filter_note(self) -> None:
        report = synthesize([_make_entry("t-1", ("a",))], window_days=0)
        # No "empty" note when entries exist and pass through.
        assert not any("no diary entries" in n.lower() for n in report.notes)

    def test_default_approved_is_false(self) -> None:
        report = synthesize([])
        assert report.approved is False


# ---------------------------------------------------------------------------
# Approve
# ---------------------------------------------------------------------------


class TestApprove:
    """HITL approval gate."""

    def test_approve_sets_flag(self) -> None:
        report = synthesize([_make_entry("t-1", ("a",))])
        approved = approve(report)
        assert approved.approved is True

    def test_approve_preserves_themes(self) -> None:
        report = synthesize([_make_entry("t-1", ("a",))])
        approved = approve(report)
        assert approved.themes == report.themes

    def test_approve_returns_new_instance(self) -> None:
        report = synthesize([_make_entry("t-1", ("a",))])
        approved = approve(report)
        assert approved is not report
        assert approved.approved != report.approved


# ---------------------------------------------------------------------------
# Render report
# ---------------------------------------------------------------------------


class TestRenderReport:
    """Markdown rendering of synthesis reports."""

    def test_render_contains_frontmatter(self) -> None:
        report = synthesize([])
        out = render_report(report)
        assert out.startswith("---\n")
        assert "approved: false" in out

    def test_render_marks_approved(self) -> None:
        report = approve(synthesize([_make_entry("t-1", ("a",))]))
        out = render_report(report)
        assert "approved: true" in out

    def test_render_no_themes_message(self) -> None:
        out = render_report(synthesize([]))
        assert "No themes detected" in out

    def test_render_includes_theme_section(self) -> None:
        entries = [
            _make_entry("t-1", ("backend",)),
            _make_entry("t-2", ("backend",)),
        ]
        out = render_report(synthesize(entries, window_days=0))
        assert "backend" in out
        assert "Cluster size" in out


# ---------------------------------------------------------------------------
# Write report
# ---------------------------------------------------------------------------


class TestWriteReport:
    """Disk persistence for synthesis reports."""

    def test_write_creates_file(self, tmp_path: Path) -> None:
        report = synthesize([])
        path = write_report(report, tmp_path)
        assert path.exists()
        assert path.name.endswith(".md")

    def test_write_idempotent_per_day(self, tmp_path: Path) -> None:
        report = synthesize([])
        path_1 = write_report(report, tmp_path)
        path_2 = write_report(report, tmp_path)
        assert path_1 == path_2

    def test_write_atomic_no_temp_left(self, tmp_path: Path) -> None:
        report = synthesize([])
        path = write_report(report, tmp_path)
        siblings = list(path.parent.glob("*.tmp.*"))
        assert siblings == []

    def test_write_under_invalid_generated_at_falls_back(self, tmp_path: Path) -> None:
        report = SynthesisReport(
            generated_at="not-a-date",
            window_days=7,
            themes=(),
        )
        path = write_report(report, tmp_path)
        assert path.exists()


# ---------------------------------------------------------------------------
# Parse duration
# ---------------------------------------------------------------------------


class TestParseDuration:
    """CLI duration parsing."""

    def test_parse_bare_int_days(self) -> None:
        assert parse_duration("14") == timedelta(days=14)

    def test_parse_days(self) -> None:
        assert parse_duration("7d") == timedelta(days=7)

    def test_parse_hours(self) -> None:
        assert parse_duration("24h") == timedelta(hours=24)

    def test_parse_minutes(self) -> None:
        assert parse_duration("30m") == timedelta(minutes=30)

    def test_parse_seconds(self) -> None:
        assert parse_duration("45s") == timedelta(seconds=45)

    def test_parse_uppercase(self) -> None:
        assert parse_duration("7D") == timedelta(days=7)

    def test_parse_empty_raises(self) -> None:
        with pytest.raises(SynthesizerError):
            parse_duration("")

    def test_parse_garbage_raises(self) -> None:
        with pytest.raises(SynthesizerError):
            parse_duration("forever")

    def test_parse_negative_not_supported(self) -> None:
        with pytest.raises(SynthesizerError):
            parse_duration("-1d")


# ---------------------------------------------------------------------------
# Theme dataclass
# ---------------------------------------------------------------------------


class TestTheme:
    """Theme dataclass invariants."""

    def test_theme_size_matches_entries(self) -> None:
        entry = _make_entry("t-1", ("a",))
        theme = Theme(
            theme_id="theme-001-x",
            label="x",
            entries=(entry, entry),
            shared_tags=("a",),
            proposed_diff="",
        )
        assert theme.size == 2

    def test_theme_immutable(self) -> None:
        from dataclasses import FrozenInstanceError

        theme = Theme(
            theme_id="theme-001-x",
            label="x",
            entries=(),
            shared_tags=(),
            proposed_diff="",
        )
        with pytest.raises(FrozenInstanceError):
            theme.label = "y"  # type: ignore[misc]
