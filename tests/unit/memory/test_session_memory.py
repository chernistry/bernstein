"""Unit tests for :mod:`bernstein.core.memory.session_memory`."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

import pytest

from bernstein.core.memory.session_memory import (
    RecallHit,
    SessionMemory,
    Turn,
    load_recent_turns,
)

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mem(tmp_path: Path) -> SessionMemory:
    """Return a fresh session memory bound to a clean tmp root."""
    return SessionMemory(
        root=tmp_path / "memory",
        task_id="task-a",
        session_id="session-1",
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_paths_are_derived_from_root_task_session(self, tmp_path: Path) -> None:
        m = SessionMemory(
            root=tmp_path / "memory",
            task_id="task-a",
            session_id="session-1",
        )
        expected_episodic = tmp_path / "memory" / "episodic" / "task-a" / "session-1.jsonl"
        assert m.episodic_path == expected_episodic
        assert m.semantic_db_path == tmp_path / "memory" / "semantic.sqlite"
        assert m.task_id == "task-a"
        assert m.session_id == "session-1"

    @pytest.mark.parametrize("bad", ["", "../escape", "a b", "a,b", "a/b"])
    def test_rejects_bad_task_id(self, tmp_path: Path, bad: str) -> None:
        with pytest.raises(ValueError, match="task_id"):
            SessionMemory(
                root=tmp_path / "memory",
                task_id=bad,
                session_id="s",
            )

    @pytest.mark.parametrize("bad", ["", "../escape", "a b", "a,b"])
    def test_rejects_bad_session_id(self, tmp_path: Path, bad: str) -> None:
        with pytest.raises(ValueError, match="session_id"):
            SessionMemory(
                root=tmp_path / "memory",
                task_id="t",
                session_id=bad,
            )

    def test_construction_does_not_touch_disk(self, tmp_path: Path) -> None:
        # Cheap construction: no directories or databases are created
        # until the first append or read.
        SessionMemory(
            root=tmp_path / "memory",
            task_id="t",
            session_id="s",
        )
        assert not (tmp_path / "memory").exists()


# ---------------------------------------------------------------------------
# append_turn
# ---------------------------------------------------------------------------


class TestAppend:
    def test_append_returns_content_hash(self, mem: SessionMemory) -> None:
        chash = mem.append_turn(Turn(role="user", content="hello world", tags=["greeting"]))
        assert chash.startswith("sha256:")
        assert len(chash) == len("sha256:") + 64

    def test_append_writes_episodic_jsonl(self, mem: SessionMemory) -> None:
        mem.append_turn(Turn(role="user", content="hello", tags=["greet"]))
        mem.append_turn(Turn(role="assistant", content="hi back", tags=["greet"]))
        lines = mem.episodic_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        first = json.loads(lines[0])
        assert first["role"] == "user"
        assert first["content"] == "hello"
        assert first["tags"] == ["greet"]
        assert first["task_id"] == "task-a"
        assert first["session_id"] == "session-1"
        assert first["content_hash"].startswith("sha256:")
        assert isinstance(first["ts_ns"], int)
        assert first["ts_ns"] > 0

    def test_append_mirrors_into_semantic_index(self, mem: SessionMemory) -> None:
        mem.append_turn(Turn(role="user", content="design the API", tags=["api"]))
        with sqlite3.connect(str(mem.semantic_db_path)) as conn:
            rows = conn.execute("SELECT role, content, tags, task_id, session_id FROM turns_fts").fetchall()
        assert rows == [
            ("user", "design the API", "api", "task-a", "session-1"),
        ]

    def test_append_preserves_explicit_ts_ns(self, mem: SessionMemory) -> None:
        mem.append_turn(Turn(role="user", content="x", ts_ns=12345))
        line = json.loads(mem.episodic_path.read_text(encoding="utf-8"))
        assert line["ts_ns"] == 12345

    def test_append_assigns_ts_ns_when_zero(self, tmp_path: Path) -> None:
        clock = iter([1_000_000_000, 2_000_000_000])
        m = SessionMemory(
            root=tmp_path / "memory",
            task_id="t",
            session_id="s",
            clock_ns=lambda: next(clock),
        )
        # Default ts_ns is set in __post_init__ from time.time_ns; force
        # zero so the SessionMemory clock takes over.
        turn = Turn(role="user", content="hi", ts_ns=1)
        turn.ts_ns = 0
        m.append_turn(turn)
        record = json.loads(m.episodic_path.read_text(encoding="utf-8"))
        assert record["ts_ns"] == 1_000_000_000

    @pytest.mark.parametrize("role", ["user", "assistant", "system", "tool"])
    def test_append_accepts_canonical_roles(self, mem: SessionMemory, role: str) -> None:
        mem.append_turn(Turn(role=role, content="payload"))

    def test_append_rejects_unknown_role(self, mem: SessionMemory) -> None:
        with pytest.raises(ValueError, match="role"):
            mem.append_turn(Turn(role="random", content="x"))

    def test_append_rejects_empty_content(self, mem: SessionMemory) -> None:
        with pytest.raises(ValueError, match="content"):
            mem.append_turn(Turn(role="user", content=""))

    def test_append_rejects_comma_in_tag(self, mem: SessionMemory) -> None:
        with pytest.raises(ValueError, match=","):
            mem.append_turn(Turn(role="user", content="x", tags=["a,b"]))

    def test_append_rejects_empty_tag(self, mem: SessionMemory) -> None:
        with pytest.raises(ValueError, match="tags"):
            mem.append_turn(Turn(role="user", content="x", tags=[""]))


# ---------------------------------------------------------------------------
# recall
# ---------------------------------------------------------------------------


class TestRecall:
    def _seed(self, mem: SessionMemory) -> None:
        mem.append_turn(Turn(role="user", content="how do we design the API", tags=["api"]))
        mem.append_turn(Turn(role="assistant", content="REST first, gRPC later", tags=["api"]))
        mem.append_turn(Turn(role="user", content="what about the database schema", tags=["db"]))

    def test_recall_returns_bm25_ordered_hits(self, mem: SessionMemory) -> None:
        self._seed(mem)
        hits = mem.recall("API")
        assert hits, "expected at least one hit"
        assert all(isinstance(h, RecallHit) for h in hits)
        # The two API-tagged turns should outrank the db one.
        contents = [h.content for h in hits]
        assert any("design the API" in c for c in contents)

    def test_recall_returns_empty_for_empty_query(self, mem: SessionMemory) -> None:
        self._seed(mem)
        assert mem.recall("") == []
        assert mem.recall("   ") == []

    def test_recall_returns_empty_when_no_index_yet(self, mem: SessionMemory) -> None:
        # Index file does not exist until first append.
        assert not mem.semantic_db_path.exists()
        assert mem.recall("anything") == []

    def test_recall_respects_k(self, mem: SessionMemory) -> None:
        self._seed(mem)
        # The token "the" should match every seeded turn via FTS5.
        hits = mem.recall("the", k=1)
        assert len(hits) == 1

    def test_recall_rejects_non_positive_k(self, mem: SessionMemory) -> None:
        with pytest.raises(ValueError, match="k"):
            mem.recall("x", k=0)
        with pytest.raises(ValueError, match="k"):
            mem.recall("x", k=-1)

    def test_recall_filters_by_tag(self, mem: SessionMemory) -> None:
        self._seed(mem)
        hits = mem.recall("the", k=10, tag="db")
        # Only the db-tagged turn survives the tag filter.
        assert {h.content for h in hits} == {"what about the database schema"}

    def test_recall_filter_by_task_id_scopes_index(self, tmp_path: Path) -> None:
        root = tmp_path / "memory"
        m_a = SessionMemory(root=root, task_id="task-a", session_id="s1")
        m_b = SessionMemory(root=root, task_id="task-b", session_id="s1")
        m_a.append_turn(Turn(role="user", content="alpha design", tags=["x"]))
        m_b.append_turn(Turn(role="user", content="alpha design", tags=["x"]))
        # Without filter we get both copies.
        assert len(m_a.recall("alpha", k=10)) == 2
        # With a task_id filter we get just one.
        only_a = m_a.recall("alpha", k=10, task_id="task-a")
        assert len(only_a) == 1
        assert only_a[0].task_id == "task-a"

    def test_recall_rejects_comma_tag(self, mem: SessionMemory) -> None:
        self._seed(mem)
        with pytest.raises(ValueError, match=","):
            mem.recall("anything", tag="a,b")

    def test_recall_sanitises_fts_special_chars(self, mem: SessionMemory) -> None:
        # A query with FTS5 operator characters used to break MATCH.
        # The sanitiser should keep recall functional.
        mem.append_turn(Turn(role="user", content="auth flow", tags=["a"]))
        hits = mem.recall("auth: (flow)")
        assert hits
        assert hits[0].content == "auth flow"


# ---------------------------------------------------------------------------
# read_episodic
# ---------------------------------------------------------------------------


class TestReadEpisodic:
    def test_read_returns_all_turns(self, mem: SessionMemory) -> None:
        mem.append_turn(Turn(role="user", content="one"))
        mem.append_turn(Turn(role="assistant", content="two"))
        entries = mem.read_episodic()
        assert [e["content"] for e in entries] == ["one", "two"]

    def test_read_returns_empty_when_no_log(self, mem: SessionMemory) -> None:
        assert mem.read_episodic() == []

    def test_read_skips_malformed_lines(self, mem: SessionMemory, caplog: pytest.LogCaptureFixture) -> None:
        mem.append_turn(Turn(role="user", content="good"))
        # Append garbage manually.
        with mem.episodic_path.open("a", encoding="utf-8") as fh:
            fh.write("{not json\n")
            fh.write("\n")
            fh.write('"a string not an object"\n')
        with caplog.at_level("WARNING", logger="bernstein.core.memory.session_memory"):
            entries = mem.read_episodic()
        assert [e["content"] for e in entries] == ["good"]
        assert any("malformed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# prune
# ---------------------------------------------------------------------------


class TestPrune:
    def test_prune_removes_old_turns_from_episodic(self, mem: SessionMemory) -> None:
        mem.append_turn(Turn(role="user", content="old", ts_ns=1_000))
        mem.append_turn(Turn(role="user", content="new", ts_ns=10_000))
        removed = mem.prune(older_than_ns=5_000)
        assert removed == 1
        survivors = mem.read_episodic()
        assert [e["content"] for e in survivors] == ["new"]

    def test_prune_removes_old_turns_from_semantic_index(self, mem: SessionMemory) -> None:
        mem.append_turn(Turn(role="user", content="old", ts_ns=1_000))
        mem.append_turn(Turn(role="user", content="newer", ts_ns=10_000))
        mem.prune(older_than_ns=5_000)
        with sqlite3.connect(str(mem.semantic_db_path)) as conn:
            rows = conn.execute("SELECT content FROM turns_fts ORDER BY ts_ns").fetchall()
        assert [r[0] for r in rows] == ["newer"]

    def test_prune_is_scoped_to_this_task(self, tmp_path: Path) -> None:
        root = tmp_path / "memory"
        m_a = SessionMemory(root=root, task_id="task-a", session_id="s1")
        m_b = SessionMemory(root=root, task_id="task-b", session_id="s1")
        m_a.append_turn(Turn(role="user", content="old-a", ts_ns=1_000))
        m_b.append_turn(Turn(role="user", content="old-b", ts_ns=1_000))
        m_a.prune(older_than_ns=5_000)
        with sqlite3.connect(str(root / "semantic.sqlite")) as conn:
            rows = conn.execute("SELECT content, task_id FROM turns_fts").fetchall()
        # Only task-a's old turn went away. task-b's data is intact.
        assert ("old-b", "task-b") in rows
        assert ("old-a", "task-a") not in rows

    def test_prune_returns_zero_when_no_log(self, mem: SessionMemory) -> None:
        assert mem.prune(older_than_ns=1_000) == 0

    def test_prune_rejects_non_positive_cutoff(self, mem: SessionMemory) -> None:
        with pytest.raises(ValueError, match="older_than_ns"):
            mem.prune(older_than_ns=0)
        with pytest.raises(ValueError, match="older_than_ns"):
            mem.prune(older_than_ns=-1)


# ---------------------------------------------------------------------------
# Concurrency-safety smoke
# ---------------------------------------------------------------------------


class TestConcurrency:
    def test_two_instances_same_root_can_both_append(self, tmp_path: Path) -> None:
        root = tmp_path / "memory"
        m_a = SessionMemory(root=root, task_id="task-a", session_id="s")
        m_b = SessionMemory(root=root, task_id="task-b", session_id="s")
        m_a.append_turn(Turn(role="user", content="from a"))
        m_b.append_turn(Turn(role="user", content="from b"))
        # Each task has its own episodic shard.
        assert m_a.episodic_path != m_b.episodic_path
        # The shared semantic index has both.
        with sqlite3.connect(str(root / "semantic.sqlite")) as conn:
            rows = conn.execute("SELECT task_id, content FROM turns_fts ORDER BY task_id").fetchall()
        assert rows == [
            ("task-a", "from a"),
            ("task-b", "from b"),
        ]


# ---------------------------------------------------------------------------
# load_recent_turns
# ---------------------------------------------------------------------------


class TestLoadRecentTurns:
    def test_returns_newest_first(self, tmp_path: Path) -> None:
        root = tmp_path / "memory"
        m = SessionMemory(root=root, task_id="task-a", session_id="s")
        m.append_turn(Turn(role="user", content="first", ts_ns=1_000))
        m.append_turn(Turn(role="user", content="second", ts_ns=2_000))
        m.append_turn(Turn(role="user", content="third", ts_ns=3_000))
        hits = load_recent_turns(root, task_id="task-a", k=2)
        assert [h.content for h in hits] == ["third", "second"]

    def test_returns_empty_when_no_index(self, tmp_path: Path) -> None:
        assert load_recent_turns(tmp_path, task_id="task-a", k=5) == []

    def test_returns_empty_when_task_has_no_turns(self, tmp_path: Path) -> None:
        root = tmp_path / "memory"
        m = SessionMemory(root=root, task_id="task-a", session_id="s")
        m.append_turn(Turn(role="user", content="only a"))
        assert load_recent_turns(root, task_id="task-b", k=5) == []

    def test_rejects_non_positive_k(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="k"):
            load_recent_turns(tmp_path, task_id="task-a", k=0)

    def test_rejects_bad_task_id(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="task_id"):
            load_recent_turns(tmp_path, task_id="bad id", k=5)
