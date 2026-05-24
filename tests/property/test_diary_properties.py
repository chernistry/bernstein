"""Hypothesis property tests for diary + synthesizer modules."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import hypothesis.strategies as st
import pytest
from hypothesis import HealthCheck, given, settings

from bernstein.core.knowledge.diary import (
    build_entry,
    compute_redaction_hash,
    extract_tags,
    redact,
    verify_diary,
    write_diary,
    write_diary_from_transcript,
)
from bernstein.core.knowledge.synthesizer import (
    cluster_entries,
    filter_recent,
    jaccard,
    parse_duration,
    render_report,
    synthesize,
)

# Reasonable upper bounds keep the suite fast under CI.
_TEXT = st.text(min_size=0, max_size=200)
_TASK = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="-_."),
    min_size=1,
    max_size=32,
)
_TAGSET = st.lists(
    st.text(
        alphabet=st.characters(whitelist_categories=("Ll",)),
        min_size=3,
        max_size=12,
    ),
    min_size=0,
    max_size=12,
    unique=True,
).map(tuple)


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


class TestRedactionProperties:
    @given(_TEXT)
    def test_redact_idempotent(self, text: str) -> None:
        once = redact(text)
        twice = redact(once)
        assert once == twice

    @given(_TEXT)
    def test_redact_never_lengthens_unbounded(self, text: str) -> None:
        # Output may be shorter or longer (replacements differ in size),
        # but cannot grow without bound.
        assert len(redact(text)) <= len(text) + 1024

    @given(_TEXT)
    def test_compute_hash_is_64_hex(self, text: str) -> None:
        digest = compute_redaction_hash(text)
        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)

    @given(_TEXT)
    def test_hash_stable(self, text: str) -> None:
        assert compute_redaction_hash(text) == compute_redaction_hash(text)


# ---------------------------------------------------------------------------
# Build entry
# ---------------------------------------------------------------------------


class TestBuildEntryProperties:
    @given(_TASK, _TEXT)
    def test_build_entry_total(self, task_id: str, transcript: str) -> None:
        if not task_id.strip():
            return
        entry = build_entry(task_id, transcript)
        assert entry.task_id == task_id.strip()
        assert isinstance(entry.tried, tuple)
        assert isinstance(entry.worked, tuple)
        assert isinstance(entry.failed, tuple)
        assert isinstance(entry.tags, tuple)

    @given(_TASK, _TEXT)
    def test_verify_matches_self(self, task_id: str, transcript: str) -> None:
        if not task_id.strip():
            return
        entry = build_entry(task_id, transcript)
        assert verify_diary(entry, transcript) is True

    @given(_TEXT)
    def test_extract_tags_are_lowercase_unique(self, text: str) -> None:
        tags = extract_tags(text)
        assert len(set(tags)) == len(tags)
        assert all(t == t.lower() for t in tags)


# ---------------------------------------------------------------------------
# Persistence round-trip
# ---------------------------------------------------------------------------


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None)
@given(task_id=_TASK, transcript=_TEXT)
def test_write_load_round_trip(tmp_path_factory: pytest.TempPathFactory, task_id: str, transcript: str) -> None:
    if not task_id.strip():
        return
    sdd_dir = tmp_path_factory.mktemp("sdd")
    from bernstein.core.knowledge.diary import load_diary

    path = write_diary_from_transcript(task_id, transcript, sdd_dir)
    loaded = load_diary(path)
    assert loaded.task_id == task_id.strip()


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None)
@given(task_id=_TASK, transcript=_TEXT)
def test_write_diary_does_not_escape_tree(
    tmp_path_factory: pytest.TempPathFactory, task_id: str, transcript: str
) -> None:
    if not task_id.strip():
        return
    sdd_dir = tmp_path_factory.mktemp("sdd")
    path = write_diary_from_transcript(task_id, transcript, sdd_dir)
    assert sdd_dir in path.parents


def test_write_diary_sanitizes_dot_task_id(tmp_path: Path) -> None:
    path = write_diary_from_transcript(".", "worked\n", tmp_path)
    assert tmp_path in path.parents
    assert path.name == "_.json"

    parent_path = write_diary_from_transcript("..", "worked\n", tmp_path)
    assert tmp_path in parent_path.parents
    assert parent_path.name == "__.json"


# ---------------------------------------------------------------------------
# Jaccard + clustering
# ---------------------------------------------------------------------------


class TestJaccardProperties:
    @given(_TAGSET, _TAGSET)
    def test_jaccard_bounded(self, a: tuple[str, ...], b: tuple[str, ...]) -> None:
        score = jaccard(a, b)
        assert 0.0 <= score <= 1.0

    @given(_TAGSET, _TAGSET)
    def test_jaccard_symmetric(self, a: tuple[str, ...], b: tuple[str, ...]) -> None:
        assert jaccard(a, b) == jaccard(b, a)

    @given(_TAGSET)
    def test_jaccard_identity(self, a: tuple[str, ...]) -> None:
        if a:
            assert jaccard(a, a) == 1.0
        else:
            # Empty/empty is defined as 0.0 in our implementation.
            assert jaccard(a, a) == 0.0


# ---------------------------------------------------------------------------
# Clustering invariants
# ---------------------------------------------------------------------------


class TestClusterProperties:
    @given(
        st.lists(_TAGSET, max_size=20),
        st.floats(
            min_value=0.0,
            max_value=1.0,
            allow_nan=False,
            allow_infinity=False,
        ),
    )
    def test_cluster_preserves_count(self, tagsets: list[tuple[str, ...]], threshold: float) -> None:
        from bernstein.core.knowledge.diary import DiaryEntry

        entries = [
            DiaryEntry(
                task_id=f"t-{i}",
                tried=(),
                worked=(),
                failed=(),
                rationale="",
                tags=tags,
                redaction_hash="x" * 64,
            )
            for i, tags in enumerate(tagsets)
        ]
        clusters = cluster_entries(entries, threshold=threshold)
        total = sum(len(c) for c in clusters)
        assert total == len(entries)

    @given(
        st.lists(_TAGSET, max_size=10),
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    )
    def test_cluster_sorted_size_desc(self, tagsets: list[tuple[str, ...]], threshold: float) -> None:
        from bernstein.core.knowledge.diary import DiaryEntry

        entries = [
            DiaryEntry(
                task_id=f"t-{i}",
                tried=(),
                worked=(),
                failed=(),
                rationale="",
                tags=tags,
                redaction_hash="x" * 64,
            )
            for i, tags in enumerate(tagsets)
        ]
        clusters = cluster_entries(entries, threshold=threshold)
        sizes = [len(c) for c in clusters]
        assert sizes == sorted(sizes, reverse=True)


# ---------------------------------------------------------------------------
# Filter recent
# ---------------------------------------------------------------------------


class TestFilterRecentProperties:
    @given(st.integers(min_value=-10, max_value=365))
    def test_filter_recent_no_entries(self, window_days: int) -> None:
        assert filter_recent([], window_days) == []


# ---------------------------------------------------------------------------
# Synthesize total
# ---------------------------------------------------------------------------


class TestSynthesizeProperties:
    @given(st.lists(_TAGSET, max_size=10))
    def test_synthesize_total(self, tagsets: list[tuple[str, ...]]) -> None:
        from bernstein.core.knowledge.diary import DiaryEntry

        entries = [
            DiaryEntry(
                task_id=f"t-{i}",
                tried=(),
                worked=(),
                failed=(),
                rationale="",
                tags=tags,
                redaction_hash="x" * 64,
            )
            for i, tags in enumerate(tagsets)
        ]
        report = synthesize(entries, window_days=0)
        # render always returns a non-empty markdown document
        assert "---" in render_report(report)

    @given(st.lists(_TAGSET, max_size=10))
    def test_synthesize_theme_count_bounded(self, tagsets: list[tuple[str, ...]]) -> None:
        from bernstein.core.knowledge.diary import DiaryEntry

        entries = [
            DiaryEntry(
                task_id=f"t-{i}",
                tried=(),
                worked=(),
                failed=(),
                rationale="",
                tags=tags,
                redaction_hash="x" * 64,
            )
            for i, tags in enumerate(tagsets)
        ]
        report = synthesize(entries, window_days=0)
        assert report.theme_count <= len(entries)


# ---------------------------------------------------------------------------
# Parse duration
# ---------------------------------------------------------------------------


class TestParseDurationProperties:
    @given(st.integers(min_value=1, max_value=10**6))
    def test_parse_days_integer(self, n: int) -> None:
        delta = parse_duration(f"{n}d")
        assert delta.days == n

    @given(st.integers(min_value=1, max_value=10**6))
    def test_parse_bare_int_as_days(self, n: int) -> None:
        delta = parse_duration(str(n))
        assert delta.days == n


# ---------------------------------------------------------------------------
# Bonus invariant: write_diary always produces a JSON-decodable file
# ---------------------------------------------------------------------------


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None)
@given(task_id=_TASK, transcript=_TEXT)
def test_payload_is_json_decodable(tmp_path_factory: pytest.TempPathFactory, task_id: str, transcript: str) -> None:
    if not task_id.strip():
        return
    sdd_dir = tmp_path_factory.mktemp("sdd")
    entry = build_entry(task_id, transcript)
    path = write_diary(entry, sdd_dir)
    import json as _json

    _json.loads(path.read_text())
    # generated_at is parseable
    datetime.fromisoformat(entry.created_at).astimezone(UTC)
