"""Unit tests for the diary module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bernstein.core.knowledge.diary import (
    DIARY_SCHEMA_VERSION,
    DiaryEntry,
    DiaryError,
    build_entry,
    compute_redaction_hash,
    extract_sections,
    extract_tags,
    load_diaries,
    load_diary,
    redact,
    verify_diary,
    write_diary,
    write_diary_from_transcript,
)

# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


class TestRedaction:
    """Cover redaction patterns and helper invariants."""

    def test_redact_openai_key(self) -> None:
        text = "key=sk-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa stuff"
        assert "sk-aaaa" not in redact(text)
        assert "[REDACTED:openai-key]" in redact(text)

    def test_redact_github_token(self) -> None:
        text = "auth: ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        assert "[REDACTED:github-token]" in redact(text)

    def test_redact_aws_access_key(self) -> None:
        text = "AWS=AKIAABCDEFGHIJKLMNOP suffix"
        assert "AKIAABCDEFGHIJKLMNOP" not in redact(text)

    def test_redact_private_key_banner(self) -> None:
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----"
        assert "[REDACTED:private-key]" in redact(text)

    def test_redact_hex_token(self) -> None:
        text = "token=" + "a" * 64
        assert "[REDACTED:token]" in redact(text)

    def test_redact_empty_string(self) -> None:
        assert redact("") == ""

    def test_redact_idempotent(self) -> None:
        text = "sk-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        once = redact(text)
        twice = redact(once)
        assert once == twice

    def test_redact_preserves_structure(self) -> None:
        text = "tried:\n- step one\nworked:\n- step two"
        out = redact(text)
        assert "tried:" in out
        assert "worked:" in out

    def test_compute_redaction_hash_stable(self) -> None:
        a = compute_redaction_hash("hello world")
        b = compute_redaction_hash("hello world")
        assert a == b

    def test_compute_redaction_hash_redacts_first(self) -> None:
        clean = compute_redaction_hash("hello sk-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        # Different raw input but same redacted form yields a different hash
        # from a totally unrelated input. The point is determinism plus
        # masking before hashing.
        assert clean != compute_redaction_hash("hello world")


# ---------------------------------------------------------------------------
# Section extraction
# ---------------------------------------------------------------------------


class TestExtractSections:
    """Cover header parsing across the diary section markers."""

    def test_extract_basic_sections(self) -> None:
        text = "tried:\n- thing a\n- thing b\nworked:\n- thing a\nfailed:\n- thing b\nrationale:\n- because reasons\n"
        sections = extract_sections(text)
        assert sections["tried"] == ["thing a", "thing b"]
        assert sections["worked"] == ["thing a"]
        assert sections["failed"] == ["thing b"]
        assert sections["rationale"] == ["because reasons"]

    def test_extract_case_insensitive(self) -> None:
        text = "TRIED:\n- one\nWORKED:\n- two"
        sections = extract_sections(text)
        assert sections["tried"] == ["one"]
        assert sections["worked"] == ["two"]

    def test_extract_inline_after_colon(self) -> None:
        text = "tried: quick attempt"
        sections = extract_sections(text)
        assert sections["tried"] == ["quick attempt"]

    def test_extract_alternate_marker_attempted(self) -> None:
        text = "attempted:\n- alpha"
        sections = extract_sections(text)
        assert sections["tried"] == ["alpha"]

    def test_extract_succeeded_maps_to_worked(self) -> None:
        text = "succeeded:\n- one"
        sections = extract_sections(text)
        assert sections["worked"] == ["one"]

    def test_extract_did_not_work_maps_to_failed(self) -> None:
        text = "did not work:\n- failed thing"
        sections = extract_sections(text)
        assert sections["failed"] == ["failed thing"]

    def test_extract_lesson_maps_to_rationale(self) -> None:
        text = "lesson:\n- always wash hands"
        sections = extract_sections(text)
        assert sections["rationale"] == ["always wash hands"]

    def test_extract_empty(self) -> None:
        sections = extract_sections("")
        assert sections == {"tried": [], "worked": [], "failed": [], "rationale": []}

    def test_extract_ignores_pre_heading_noise(self) -> None:
        text = "garbage banter\ntried:\n- alpha"
        sections = extract_sections(text)
        assert sections["tried"] == ["alpha"]

    def test_extract_handles_star_bullets(self) -> None:
        text = "worked:\n* alpha\n* beta"
        sections = extract_sections(text)
        assert sections["worked"] == ["alpha", "beta"]


# ---------------------------------------------------------------------------
# Tag extraction
# ---------------------------------------------------------------------------


class TestExtractTags:
    """Cover deterministic, deduplicated tag extraction."""

    def test_basic_tags(self) -> None:
        tags = extract_tags("backend retry retry network")
        assert "backend" in tags
        assert "retry" in tags
        assert "network" in tags

    def test_tags_dedup(self) -> None:
        tags = extract_tags("alpha alpha alpha")
        assert tags.count("alpha") == 1

    def test_tags_stoplist(self) -> None:
        tags = extract_tags("the and for backend")
        assert "the" not in tags
        assert "backend" in tags

    def test_tags_lowercase(self) -> None:
        tags = extract_tags("Backend NetWORK")
        assert "backend" in tags
        assert "network" in tags

    def test_tags_min_length(self) -> None:
        tags = extract_tags("a ab abc longer")
        assert "a" not in tags
        assert "ab" not in tags
        assert "abc" in tags

    def test_tags_limit_respected(self) -> None:
        text = " ".join(f"word{i}" for i in range(50))
        tags = extract_tags(text, limit=5)
        assert len(tags) <= 5

    def test_tags_empty_input(self) -> None:
        assert extract_tags("") == ()

    def test_tags_order_first_seen(self) -> None:
        tags = extract_tags("zulu yankee xray whiskey")
        assert list(tags[:4]) == ["zulu", "yankee", "xray", "whiskey"]


# ---------------------------------------------------------------------------
# Build entry
# ---------------------------------------------------------------------------


class TestBuildEntry:
    """Cover the high-level diary builder."""

    def test_build_minimal(self) -> None:
        entry = build_entry("task-1", "tried: a\nworked: b")
        assert entry.task_id == "task-1"
        assert entry.tried == ("a",)
        assert entry.worked == ("b",)
        assert entry.schema_version == DIARY_SCHEMA_VERSION
        assert isinstance(entry.redaction_hash, str)
        assert len(entry.redaction_hash) == 64

    def test_build_empty_transcript_has_rationale_empty(self) -> None:
        entry = build_entry("task-1", "")
        assert entry.rationale == ""
        assert entry.tried == ()
        assert entry.worked == ()
        assert entry.failed == ()

    def test_build_blank_task_raises(self) -> None:
        with pytest.raises(DiaryError):
            build_entry("", "tried: a")

    def test_build_whitespace_task_raises(self) -> None:
        with pytest.raises(DiaryError):
            build_entry("   ", "tried: a")

    def test_build_strips_task_id(self) -> None:
        entry = build_entry("  task-7  ", "tried: a")
        assert entry.task_id == "task-7"

    def test_build_falls_back_to_first_line_rationale(self) -> None:
        entry = build_entry("task-2", "no headings here\njust prose")
        assert entry.rationale == "no headings here"

    def test_build_redacts_before_extraction(self) -> None:
        text = "tried:\n- use sk-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        entry = build_entry("task-3", text)
        # The tried bullet contains the redaction marker, not the raw key
        assert any("[REDACTED:openai-key]" in t for t in entry.tried)
        assert not any("sk-aaaa" in t for t in entry.tried)

    def test_build_tags_present(self) -> None:
        entry = build_entry("task-4", "tried: backend retry retry network")
        assert len(entry.tags) >= 1

    def test_build_to_dict_serialisable(self) -> None:
        entry = build_entry("task-5", "tried: x")
        payload = entry.to_dict()
        encoded = json.dumps(payload)
        assert json.loads(encoded)["task_id"] == "task-5"
        assert isinstance(payload["tried"], list)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    """Cover diary disk read/write roundtrip."""

    def test_write_diary_creates_file(self, tmp_path: Path) -> None:
        entry = build_entry("task-x", "tried: alpha")
        path = write_diary(entry, tmp_path)
        assert path.exists()
        payload = json.loads(path.read_text())
        assert payload["task_id"] == "task-x"

    def test_write_then_load_round_trip(self, tmp_path: Path) -> None:
        original = build_entry("task-y", "tried: alpha\nworked: beta")
        write_diary(original, tmp_path)
        loaded = load_diary(tmp_path / "runtime" / "diaries" / "task-y.json")
        assert loaded.task_id == original.task_id
        assert loaded.tried == original.tried
        assert loaded.worked == original.worked
        assert loaded.redaction_hash == original.redaction_hash

    def test_write_diary_from_transcript_writes(self, tmp_path: Path) -> None:
        path = write_diary_from_transcript("task-z", "tried: alpha", tmp_path)
        assert path.exists()
        loaded = load_diary(path)
        assert loaded.task_id == "task-z"
        assert "alpha" in loaded.tried

    def test_load_diaries_empty_dir(self, tmp_path: Path) -> None:
        assert load_diaries(tmp_path) == []

    def test_load_diaries_sorted_by_created_at(self, tmp_path: Path) -> None:
        diaries_dir = tmp_path / "runtime" / "diaries"
        diaries_dir.mkdir(parents=True)
        for task_id, ts in (("task-b", "2026-01-02T00:00:00+00:00"), ("task-a", "2026-01-01T00:00:00+00:00")):
            payload = {
                "task_id": task_id,
                "tried": [],
                "worked": [],
                "failed": [],
                "rationale": "",
                "tags": [],
                "redaction_hash": "x" * 64,
                "created_at": ts,
                "schema_version": 1,
            }
            (diaries_dir / f"{task_id}.json").write_text(json.dumps(payload))
        entries = load_diaries(tmp_path)
        assert [e.task_id for e in entries] == ["task-a", "task-b"]

    def test_load_diaries_skips_corrupt(self, tmp_path: Path) -> None:
        diaries_dir = tmp_path / "runtime" / "diaries"
        diaries_dir.mkdir(parents=True)
        (diaries_dir / "good.json").write_text(
            json.dumps(
                {
                    "task_id": "good",
                    "tried": [],
                    "worked": [],
                    "failed": [],
                    "rationale": "",
                    "tags": [],
                    "redaction_hash": "x" * 64,
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "schema_version": 1,
                }
            )
        )
        (diaries_dir / "bad.json").write_text("{ not valid json")
        entries = load_diaries(tmp_path)
        assert len(entries) == 1
        assert entries[0].task_id == "good"

    def test_load_diary_missing_keys_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.json"
        path.write_text(json.dumps({"task_id": "x"}))
        with pytest.raises(DiaryError):
            load_diary(path)

    def test_load_diary_unparseable_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.json"
        path.write_text("[ not json")
        with pytest.raises(DiaryError):
            load_diary(path)

    def test_write_diary_rejects_path_traversal(self, tmp_path: Path) -> None:
        # Build a payload with a hostile task id; the writer must sanitise
        # the filename so the file never lands outside the diary directory.
        entry = DiaryEntry(
            task_id="../escape",
            tried=(),
            worked=(),
            failed=(),
            rationale="",
            tags=(),
            redaction_hash="x" * 64,
        )
        path = write_diary(entry, tmp_path)
        assert tmp_path in path.parents
        assert "escape" in path.name

    def test_write_diary_atomic(self, tmp_path: Path) -> None:
        entry = build_entry("atomic-task", "tried: alpha")
        path = write_diary(entry, tmp_path)
        # Atomic writer must not leave temp siblings behind on success.
        siblings = list(path.parent.glob("*.tmp.*"))
        assert siblings == []


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


class TestVerify:
    """Cover the redaction-hash verification helper."""

    def test_verify_matching(self) -> None:
        entry = build_entry("task-v", "tried: alpha")
        assert verify_diary(entry, "tried: alpha") is True

    def test_verify_mismatched(self) -> None:
        entry = build_entry("task-v", "tried: alpha")
        assert verify_diary(entry, "different content") is False

    def test_verify_after_redaction_equivalent(self) -> None:
        # Two transcripts that redact to the same form must verify
        # against the same entry.
        key_a = "sk-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        key_b = "sk-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        entry = build_entry("task-v", f"tried: use {key_a}")
        # Different raw secret, same redaction shape -> same hash.
        assert verify_diary(entry, f"tried: use {key_b}") is True
