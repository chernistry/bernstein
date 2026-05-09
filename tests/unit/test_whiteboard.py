"""Unit tests for the whiteboard primitive (smallest-viable slice).

Covers:

* round-trip append + read
* multiple writers, file-order preservation
* per-agent visibility filter at read time
* malformed-line resilience
* schema validation on ``from_dict``
* run_id sanitisation
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from bernstein.core.communication.whiteboard import (
    WILDCARD_ROLE,
    Whiteboard,
    WhiteboardEntry,
)


def _make_entry(**overrides: object) -> WhiteboardEntry:
    """Build an entry with sensible defaults for each test."""
    base: dict[str, object] = {
        "key": "api_contract",
        "scope": "backend",
        "value": {"version": 1},
        "owner_agent_id": "agent-backend-1",
        "visibility": ("backend",),
    }
    base.update(overrides)
    return WhiteboardEntry(**base)  # type: ignore[arg-type]


def test_append_and_read_round_trip(tmp_path: Path) -> None:
    """A single writer's entry round-trips through the file."""
    wb = Whiteboard(tmp_path, run_id="run-1")
    entry = _make_entry()

    wb.append(entry)
    rows = wb.read(reader_role="backend")

    assert len(rows) == 1
    assert rows[0].key == "api_contract"
    assert rows[0].scope == "backend"
    assert rows[0].value == {"version": 1}
    assert rows[0].owner_agent_id == "agent-backend-1"
    assert rows[0].visibility == ("backend",)
    assert rows[0].ts_ns > 0


def test_append_creates_run_dir_lazily(tmp_path: Path) -> None:
    """Run directory is created on first append, not on construction."""
    wb = Whiteboard(tmp_path, run_id="run-deferred")
    assert not (tmp_path / "run-deferred").exists()

    wb.append(_make_entry())

    assert (tmp_path / "run-deferred" / "whiteboard.jsonl").exists()


def test_read_before_any_append_returns_empty(tmp_path: Path) -> None:
    """Reading from a fresh whiteboard returns an empty list, not an error."""
    wb = Whiteboard(tmp_path, run_id="run-empty")

    assert wb.read(reader_role="backend") == []


def test_visibility_filter_hides_unallowed_roles(tmp_path: Path) -> None:
    """A role outside ``visibility`` cannot read the entry."""
    wb = Whiteboard(tmp_path, run_id="run-vis")
    wb.append(_make_entry(visibility=("backend",)))

    qa_view = wb.read(reader_role="qa")
    backend_view = wb.read(reader_role="backend")

    assert qa_view == []
    assert len(backend_view) == 1


def test_visibility_empty_means_public(tmp_path: Path) -> None:
    """Empty ``visibility`` makes the entry public to every reader."""
    wb = Whiteboard(tmp_path, run_id="run-pub")
    wb.append(_make_entry(visibility=()))

    assert len(wb.read(reader_role="qa")) == 1
    assert len(wb.read(reader_role="backend")) == 1
    assert len(wb.read(reader_role="anything-goes")) == 1


def test_wildcard_role_bypasses_filter(tmp_path: Path) -> None:
    """Reader role ``"*"`` sees every entry (orchestrator/debug use)."""
    wb = Whiteboard(tmp_path, run_id="run-star")
    wb.append(_make_entry(visibility=("backend",)))
    wb.append(_make_entry(key="other", visibility=("qa",)))

    assert len(wb.read(reader_role=WILDCARD_ROLE)) == 2


def test_filter_by_scope_and_key(tmp_path: Path) -> None:
    """``scope`` and ``key`` arguments narrow the result set."""
    wb = Whiteboard(tmp_path, run_id="run-filter")
    wb.append(_make_entry(scope="backend", key="api"))
    wb.append(_make_entry(scope="backend", key="schema"))
    wb.append(_make_entry(scope="frontend", key="api"))

    backend_only = wb.read(reader_role=WILDCARD_ROLE, scope="backend")
    api_only = wb.read(reader_role=WILDCARD_ROLE, key="api")

    assert {e.key for e in backend_only} == {"api", "schema"}
    assert {e.scope for e in api_only} == {"backend", "frontend"}


def test_two_agents_writing_same_run_preserves_all_entries(tmp_path: Path) -> None:
    """Concurrent in-process writers all land; file order matches lock order."""
    wb = Whiteboard(tmp_path, run_id="run-multi")
    n_writers = 8
    entries_per_writer = 5
    barrier = threading.Barrier(n_writers)

    def _worker(agent_id: str) -> None:
        barrier.wait()
        for i in range(entries_per_writer):
            wb.append(
                _make_entry(
                    key=f"key-{i}",
                    owner_agent_id=agent_id,
                    visibility=(),
                ),
            )

    with ThreadPoolExecutor(max_workers=n_writers) as pool:
        for idx in range(n_writers):
            pool.submit(_worker, f"agent-{idx}")

    rows = wb.read(reader_role=WILDCARD_ROLE)
    assert len(rows) == n_writers * entries_per_writer

    owners = {row.owner_agent_id for row in rows}
    assert owners == {f"agent-{idx}" for idx in range(n_writers)}


def test_malformed_line_is_skipped_with_warning(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A bad JSONL line is logged + skipped rather than raising."""
    wb = Whiteboard(tmp_path, run_id="run-bad")
    wb.append(_make_entry())
    # Inject a junk line directly so we know the reader is resilient
    # against out-of-band edits or partial writes that slipped past
    # ``LOCK_EX`` (e.g. a crashed cross-process writer).
    with wb.path.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
        fh.write(json.dumps({"key": "k", "scope": "s"}) + "\n")

    caplog.set_level("WARNING")
    rows = wb.read(reader_role=WILDCARD_ROLE)

    assert len(rows) == 1
    warnings = [rec for rec in caplog.records if rec.levelname == "WARNING"]
    assert len(warnings) == 2  # one per malformed line


def test_iter_visible_streams_entries(tmp_path: Path) -> None:
    """``iter_visible`` yields the same entries as ``read``."""
    wb = Whiteboard(tmp_path, run_id="run-iter")
    wb.append(_make_entry(key="a"))
    wb.append(_make_entry(key="b"))
    wb.append(_make_entry(key="c", visibility=("qa",)))

    streamed = list(wb.iter_visible(reader_role="backend"))

    assert {e.key for e in streamed} == {"a", "b"}


def test_from_dict_rejects_missing_fields() -> None:
    """Schema validation rejects entries missing required keys."""
    with pytest.raises(ValueError, match="missing fields"):
        WhiteboardEntry.from_dict({"key": "k", "scope": "s"})


def test_from_dict_rejects_wrong_visibility_type() -> None:
    """Visibility must be a list of strings or absent."""
    with pytest.raises(ValueError, match="visibility"):
        WhiteboardEntry.from_dict(
            {
                "key": "k",
                "scope": "s",
                "value": 1,
                "owner_agent_id": "a",
                "ts_ns": 1,
                "visibility": "backend",
            },
        )


def test_invalid_run_id_rejected(tmp_path: Path) -> None:
    """Path-traversal flavoured ``run_id`` values are rejected up-front."""
    for bad in ("..", "a/b", "a\\b", ""):
        with pytest.raises(ValueError, match="run_id"):
            Whiteboard(tmp_path, run_id=bad)


def test_to_json_line_round_trip() -> None:
    """to_json_line / from_dict is loss-less for the schema fields."""
    entry = _make_entry(visibility=("backend", "qa"))

    parsed = WhiteboardEntry.from_dict(json.loads(entry.to_json_line()))

    assert parsed == entry
