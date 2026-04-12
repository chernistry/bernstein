"""Tests for automated changelog generation from agent-produced diffs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bernstein.core.quality.changelog_generator import (
    Changelog,
    ChangelogEntry,
    detect_breaking_changes,
    generate_changelog,
    group_by_component,
    render_markdown,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def archive_path(tmp_path: Path) -> Path:
    """Return path to a temporary archive JSONL file."""
    return tmp_path / "tasks.jsonl"


def _write_archive(path: Path, records: list[dict[str, object]]) -> None:
    """Write records to a JSONL archive file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")


SAMPLE_RECORDS: list[dict[str, object]] = [
    {
        "task_id": "task-001",
        "title": "Add user authentication",
        "role": "backend",
        "status": "done",
        "created_at": 1712900000.0,
        "completed_at": 1712901000.0,
        "duration_seconds": 1000.0,
        "result_summary": "Implemented JWT-based authentication",
        "cost_usd": 0.05,
        "assigned_agent": "agent-a",
        "owned_files": [
            "src/bernstein/core/security/auth.py",
            "src/bernstein/core/security/tokens.py",
        ],
        "tenant_id": "default",
        "claimed_by_session": "session-1",
    },
    {
        "task_id": "task-002",
        "title": "Update CLI help text",
        "role": "frontend",
        "status": "done",
        "created_at": 1712900500.0,
        "completed_at": 1712901500.0,
        "duration_seconds": 1000.0,
        "result_summary": "Improved CLI help output formatting",
        "cost_usd": 0.02,
        "assigned_agent": "agent-b",
        "owned_files": ["src/bernstein/cli/run_cmd.py"],
        "tenant_id": "default",
        "claimed_by_session": "session-1",
    },
    {
        "task_id": "task-003",
        "title": "Fix adapter timeout",
        "role": "backend",
        "status": "failed",
        "created_at": 1712901000.0,
        "completed_at": 1712902000.0,
        "duration_seconds": 1000.0,
        "result_summary": None,
        "cost_usd": 0.01,
        "assigned_agent": "agent-c",
        "owned_files": ["src/bernstein/adapters/claude.py"],
        "tenant_id": "default",
        "claimed_by_session": "session-1",
    },
]


# ---------------------------------------------------------------------------
# ChangelogEntry dataclass
# ---------------------------------------------------------------------------


class TestChangelogEntry:
    def test_frozen(self) -> None:
        entry = ChangelogEntry(
            component="core",
            summary="Added feature",
            task_id="t-1",
        )
        with pytest.raises(AttributeError):
            entry.component = "other"  # type: ignore[misc]

    def test_defaults(self) -> None:
        entry = ChangelogEntry(component="core", summary="s", task_id="t-1")
        assert entry.is_breaking is False
        assert entry.files_changed == ()

    def test_with_files(self) -> None:
        entry = ChangelogEntry(
            component="cli",
            summary="Updated help",
            task_id="t-2",
            files_changed=("a.py", "b.py"),
        )
        assert len(entry.files_changed) == 2


# ---------------------------------------------------------------------------
# Changelog dataclass
# ---------------------------------------------------------------------------


class TestChangelog:
    def test_frozen(self) -> None:
        cl = Changelog(run_id="run-1", version="1.0.0", date="2026-04-12")
        with pytest.raises(AttributeError):
            cl.run_id = "x"  # type: ignore[misc]

    def test_defaults(self) -> None:
        cl = Changelog(run_id="run-1", version="", date="2026-04-12")
        assert cl.entries == ()
        assert cl.breaking_changes == ()


# ---------------------------------------------------------------------------
# generate_changelog
# ---------------------------------------------------------------------------


class TestGenerateChangelog:
    def test_empty_archive(self, archive_path: Path) -> None:
        cl = generate_changelog("run-1", archive_path)
        assert cl.run_id == "run-1"
        assert len(cl.entries) == 0

    def test_nonexistent_archive(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent" / "tasks.jsonl"
        cl = generate_changelog("run-1", missing)
        assert len(cl.entries) == 0

    def test_generates_entries_from_archive(self, archive_path: Path) -> None:
        _write_archive(archive_path, SAMPLE_RECORDS)
        cl = generate_changelog("run-1", archive_path)
        # Only 'done' tasks should be included (task-003 is 'failed')
        assert len(cl.entries) == 2
        task_ids = [e.task_id for e in cl.entries]
        assert "task-001" in task_ids
        assert "task-002" in task_ids
        assert "task-003" not in task_ids

    def test_infers_component(self, archive_path: Path) -> None:
        _write_archive(archive_path, SAMPLE_RECORDS)
        cl = generate_changelog("run-1", archive_path)
        by_id = {e.task_id: e for e in cl.entries}
        assert by_id["task-001"].component == "core"
        assert by_id["task-002"].component == "cli"

    def test_uses_result_summary_over_title(self, archive_path: Path) -> None:
        _write_archive(archive_path, SAMPLE_RECORDS)
        cl = generate_changelog("run-1", archive_path)
        by_id = {e.task_id: e for e in cl.entries}
        assert by_id["task-001"].summary == "Implemented JWT-based authentication"

    def test_falls_back_to_title(self, archive_path: Path) -> None:
        record: dict[str, object] = {
            "task_id": "t-fallback",
            "title": "Some title",
            "role": "backend",
            "status": "done",
            "created_at": 1712900000.0,
            "completed_at": 1712901000.0,
            "duration_seconds": 1000.0,
            "result_summary": None,
            "cost_usd": 0.0,
            "assigned_agent": None,
            "owned_files": [],
            "tenant_id": "default",
            "claimed_by_session": None,
        }
        _write_archive(archive_path, [record])
        cl = generate_changelog("run-1", archive_path)
        assert cl.entries[0].summary == "Some title"

    def test_version_passed_through(self, archive_path: Path) -> None:
        _write_archive(archive_path, SAMPLE_RECORDS)
        cl = generate_changelog("run-1", archive_path, version="2.0.0")
        assert cl.version == "2.0.0"

    def test_malformed_lines_skipped(self, archive_path: Path) -> None:
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with archive_path.open("w", encoding="utf-8") as f:
            f.write("not valid json\n")
            f.write(json.dumps(SAMPLE_RECORDS[0]) + "\n")
            f.write("{broken\n")
        cl = generate_changelog("run-1", archive_path)
        assert len(cl.entries) == 1


# ---------------------------------------------------------------------------
# group_by_component
# ---------------------------------------------------------------------------


class TestGroupByComponent:
    def test_groups_correctly(self) -> None:
        entries = [
            ChangelogEntry(component="core", summary="a", task_id="t-1"),
            ChangelogEntry(component="cli", summary="b", task_id="t-2"),
            ChangelogEntry(component="core", summary="c", task_id="t-3"),
        ]
        grouped = group_by_component(entries)
        assert list(grouped.keys()) == ["cli", "core"]
        assert len(grouped["core"]) == 2
        assert len(grouped["cli"]) == 1

    def test_empty_input(self) -> None:
        grouped = group_by_component([])
        assert grouped == {}

    def test_single_component(self) -> None:
        entries = [
            ChangelogEntry(component="adapters", summary="x", task_id="t-1"),
            ChangelogEntry(component="adapters", summary="y", task_id="t-2"),
        ]
        grouped = group_by_component(entries)
        assert list(grouped.keys()) == ["adapters"]
        assert len(grouped["adapters"]) == 2


# ---------------------------------------------------------------------------
# detect_breaking_changes
# ---------------------------------------------------------------------------


class TestDetectBreakingChanges:
    def test_removed_public_function(self) -> None:
        diff = """\
- def authenticate(user: str, password: str) -> bool:
-     '''Authenticate a user.'''
-     ...
"""
        breaks = detect_breaking_changes(diff)
        assert len(breaks) == 1
        assert "Removed public function `authenticate`" in breaks[0]

    def test_removed_private_function_not_flagged(self) -> None:
        diff = """\
- def _internal_helper(x: int) -> int:
-     return x + 1
"""
        breaks = detect_breaking_changes(diff)
        assert len(breaks) == 0

    def test_removed_public_class(self) -> None:
        diff = """\
- class UserManager:
-     '''Manage user state.'''
-     pass
"""
        breaks = detect_breaking_changes(diff)
        assert len(breaks) == 1
        assert "Removed public class `UserManager`" in breaks[0]

    def test_removed_private_class_not_flagged(self) -> None:
        diff = """\
- class _InternalCache:
-     pass
"""
        breaks = detect_breaking_changes(diff)
        assert len(breaks) == 0

    def test_changed_signature(self) -> None:
        diff = """\
- def process(data: str) -> None:
+ def process(data: str, strict: bool = False) -> None:
"""
        breaks = detect_breaking_changes(diff)
        assert len(breaks) == 1
        assert "Changed signature of `process`" in breaks[0]

    def test_unchanged_signature_not_flagged(self) -> None:
        diff = """\
- def process(data: str) -> None:
+ def process(data: str) -> None:
"""
        breaks = detect_breaking_changes(diff)
        assert len(breaks) == 0

    def test_no_diff(self) -> None:
        breaks = detect_breaking_changes("")
        assert breaks == []

    def test_only_additions(self) -> None:
        diff = """\
+ def new_feature(x: int) -> str:
+     return str(x)
"""
        breaks = detect_breaking_changes(diff)
        assert breaks == []


# ---------------------------------------------------------------------------
# render_markdown
# ---------------------------------------------------------------------------


class TestRenderMarkdown:
    def test_empty_changelog(self) -> None:
        cl = Changelog(run_id="run-1", version="1.0.0", date="2026-04-12")
        md = render_markdown(cl)
        assert "# Changelog" in md
        assert "1.0.0" in md
        assert "2026-04-12" in md
        assert "*No changes recorded.*" in md

    def test_with_entries(self, archive_path: Path) -> None:
        _write_archive(archive_path, SAMPLE_RECORDS)
        cl = generate_changelog("run-1", archive_path, version="1.0.0")
        md = render_markdown(cl)
        assert "## Changes" in md
        assert "### core" in md
        assert "### cli" in md
        assert "task-001" in md
        assert "task-002" in md

    def test_breaking_changes_section(self) -> None:
        breaking = ChangelogEntry(
            component="core",
            summary="Removed auth module",
            task_id="t-break",
            is_breaking=True,
        )
        cl = Changelog(
            run_id="run-1",
            version="2.0.0",
            date="2026-04-12",
            entries=(breaking,),
            breaking_changes=(breaking,),
        )
        md = render_markdown(cl)
        assert "## Breaking Changes" in md
        assert "Removed auth module" in md

    def test_files_listed(self) -> None:
        entry = ChangelogEntry(
            component="core",
            summary="Updated server",
            task_id="t-1",
            files_changed=("src/a.py", "src/b.py"),
        )
        cl = Changelog(
            run_id="run-1",
            version="1.0.0",
            date="2026-04-12",
            entries=(entry,),
        )
        md = render_markdown(cl)
        assert "`src/a.py`" in md
        assert "`src/b.py`" in md

    def test_many_files_truncated(self) -> None:
        files = tuple(f"src/f{i}.py" for i in range(8))
        entry = ChangelogEntry(
            component="core",
            summary="Big change",
            task_id="t-1",
            files_changed=files,
        )
        cl = Changelog(
            run_id="run-1",
            version="1.0.0",
            date="2026-04-12",
            entries=(entry,),
        )
        md = render_markdown(cl)
        assert "(+3 more)" in md

    def test_version_fallback_to_run_id(self) -> None:
        cl = Changelog(run_id="run-42", version="", date="2026-04-12")
        md = render_markdown(cl)
        assert "# Changelog — run-42" in md
