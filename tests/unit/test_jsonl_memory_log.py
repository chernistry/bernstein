"""Unit tests for :class:`JSONLMemoryLog` - append-only memory primitive."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from bernstein.core.memory.jsonl_log import JSONLMemoryLog

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def log(tmp_path: Path) -> JSONLMemoryLog:
    """Return a JSONL log rooted in a clean temp dir."""
    return JSONLMemoryLog(root=tmp_path / "memory")


class TestRoundTrip:
    """write() then read() returns what was stored."""

    def test_single_entry_roundtrip(self, log: JSONLMemoryLog) -> None:
        log.write("manager.lessons", {"task": "T-1", "lesson": "guard imports"})
        entries = log.read("manager.lessons")
        assert entries == [{"task": "T-1", "lesson": "guard imports"}]

    def test_unicode_preserved(self, log: JSONLMemoryLog) -> None:
        log.write("notes", {"text": "réfactor - Łódź"})
        assert log.read("notes")[0]["text"] == "réfactor - Łódź"

    def test_nested_structures_preserved(self, log: JSONLMemoryLog) -> None:
        payload = {"ints": [1, 2, 3], "obj": {"k": "v"}, "bool": True, "null": None}
        log.write("nested", payload)
        assert log.read("nested") == [payload]


class TestMultiEntryAppend:
    """Append semantics: each write adds a line, read preserves order."""

    def test_three_entries_in_order(self, log: JSONLMemoryLog) -> None:
        log.write("events", {"i": 1})
        log.write("events", {"i": 2})
        log.write("events", {"i": 3})
        assert log.read("events") == [{"i": 1}, {"i": 2}, {"i": 3}]

    def test_separate_keys_isolated(self, log: JSONLMemoryLog) -> None:
        log.write("a", {"v": "alpha"})
        log.write("b", {"v": "beta"})
        assert log.read("a") == [{"v": "alpha"}]
        assert log.read("b") == [{"v": "beta"}]

    def test_cross_instance_append_visible(self, tmp_path: Path) -> None:
        """Writer instance commits to disk; a fresh reader instance sees it."""
        root = tmp_path / "shared"
        writer = JSONLMemoryLog(root=root)
        writer.write("k", {"step": 1})

        reader = JSONLMemoryLog(root=root)
        assert reader.read("k") == [{"step": 1}]

        # Further appends from a third instance accumulate.
        appender = JSONLMemoryLog(root=root)
        appender.write("k", {"step": 2})
        assert reader.read("k") == [{"step": 1}, {"step": 2}]


class TestEmptyAndMissing:
    """Reading a missing key is a clean empty list, not an error."""

    def test_read_missing_returns_empty(self, log: JSONLMemoryLog) -> None:
        assert log.read("never-written") == []

    def test_list_keys_empty_when_no_writes(self, log: JSONLMemoryLog) -> None:
        assert log.list_keys() == []

    def test_list_keys_after_writes(self, log: JSONLMemoryLog) -> None:
        log.write("zeta", {})
        log.write("alpha", {})
        log.write("mid", {})
        assert log.list_keys() == ["alpha", "mid", "zeta"]


class TestClear:
    """clear() removes a key file, leaves others untouched."""

    def test_clear_removes_file(self, log: JSONLMemoryLog) -> None:
        log.write("k", {"v": 1})
        assert log.clear("k") is True
        assert log.read("k") == []

    def test_clear_missing_returns_false(self, log: JSONLMemoryLog) -> None:
        assert log.clear("ghost") is False

    def test_clear_does_not_touch_siblings(self, log: JSONLMemoryLog) -> None:
        log.write("keep", {"v": 1})
        log.write("drop", {"v": 2})
        log.clear("drop")
        assert log.list_keys() == ["keep"]
        assert log.read("keep") == [{"v": 1}]


class TestKeyValidation:
    """Validation guards against path traversal and OS-reserved names."""

    @pytest.mark.parametrize(
        "bad_key",
        [
            "",
            "../escape",
            "with/slash",
            "with space",
            ".leading-dot",
            "with\\backslash",
            "with:colon",
            "a" * 200,
        ],
    )
    def test_invalid_keys_rejected(self, log: JSONLMemoryLog, bad_key: str) -> None:
        with pytest.raises(ValueError):
            log.write(bad_key, {"v": 1})
        with pytest.raises(ValueError):
            log.read(bad_key)
        with pytest.raises(ValueError):
            log.clear(bad_key)

    @pytest.mark.parametrize(
        "ok_key",
        ["a", "manager.lessons", "with-dash", "with_underscore", "Mixed.Case-1"],
    )
    def test_valid_keys_accepted(self, log: JSONLMemoryLog, ok_key: str) -> None:
        log.write(ok_key, {"v": 1})
        assert log.read(ok_key) == [{"v": 1}]


class TestEntryValidation:
    """Non-dict entries and unserialisable payloads fail loudly."""

    def test_non_dict_entry_rejected(self, log: JSONLMemoryLog) -> None:
        with pytest.raises(TypeError):
            log.write("k", [1, 2, 3])  # type: ignore[arg-type]

    def test_non_serialisable_entry_raises(self, log: JSONLMemoryLog) -> None:
        with pytest.raises(TypeError):
            log.write("k", {"obj": object()})


class TestCorruptionTolerance:
    """A malformed tail line should not poison previously-good entries."""

    def test_malformed_line_skipped(self, tmp_path: Path) -> None:
        root = tmp_path / "memory"
        log = JSONLMemoryLog(root=root)
        log.write("k", {"v": 1})
        log.write("k", {"v": 2})

        # Hand-corrupt the file by appending garbage + a valid third entry.
        path = root / "k.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write("not-valid-json{\n")
            fh.write('{"v":3}\n')

        assert log.read("k") == [{"v": 1}, {"v": 2}, {"v": 3}]

    def test_non_dict_json_line_skipped(self, tmp_path: Path) -> None:
        root = tmp_path / "memory"
        log = JSONLMemoryLog(root=root)
        log.write("k", {"v": 1})
        path = root / "k.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write("[1,2,3]\n")  # valid JSON, wrong shape
            fh.write('{"v":2}\n')
        assert log.read("k") == [{"v": 1}, {"v": 2}]
