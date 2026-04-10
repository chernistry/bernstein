"""Unit tests for task diff preview before merge approval."""

from __future__ import annotations

from bernstein.core.task_diff_preview import (
    FileDiff,
    build_diff_summary,
    format_compact_diff,
    format_diff_preview,
    parse_git_diff_stat,
)

# ---------------------------------------------------------------------------
# parse_git_diff_stat — normal output
# ---------------------------------------------------------------------------


class TestParseGitDiffStatNormal:
    def test_single_modified_file(self) -> None:
        output = "10\t5\tsrc/foo.py\n"
        result = parse_git_diff_stat(output)
        assert len(result) == 1
        assert result[0] == FileDiff(
            path="src/foo.py",
            status="modified",
            lines_added=10,
            lines_removed=5,
        )

    def test_added_file(self) -> None:
        output = "20\t0\tsrc/new_module.py\n"
        result = parse_git_diff_stat(output)
        assert len(result) == 1
        assert result[0].status == "added"
        assert result[0].lines_added == 20
        assert result[0].lines_removed == 0

    def test_deleted_file(self) -> None:
        output = "0\t35\tobs/old_file.py\n"
        result = parse_git_diff_stat(output)
        assert len(result) == 1
        assert result[0].status == "deleted"
        assert result[0].lines_added == 0
        assert result[0].lines_removed == 35

    def test_multiple_files(self) -> None:
        output = "10\t2\tsrc/a.py\n3\t0\tsrc/b.py\n0\t7\tsrc/c.py\n"
        result = parse_git_diff_stat(output)
        assert len(result) == 3
        assert result[0].path == "src/a.py"
        assert result[1].path == "src/b.py"
        assert result[2].path == "src/c.py"

    def test_binary_file_treated_as_zero(self) -> None:
        output = "-\t-\tassets/logo.png\n"
        result = parse_git_diff_stat(output)
        assert len(result) == 1
        assert result[0].lines_added == 0
        assert result[0].lines_removed == 0
        # Binary with no line changes is classified as modified (0 add, 0 remove).
        assert result[0].status == "modified"


# ---------------------------------------------------------------------------
# parse_git_diff_stat — empty input
# ---------------------------------------------------------------------------


class TestParseGitDiffStatEmpty:
    def test_empty_string(self) -> None:
        assert parse_git_diff_stat("") == []

    def test_whitespace_only(self) -> None:
        assert parse_git_diff_stat("   \n\n  ") == []

    def test_non_numstat_lines_ignored(self) -> None:
        # --stat lines like " src/foo.py | 5 ++-" are not numstat format
        output = " src/foo.py | 5 ++-\n 2 files changed, 3 insertions(+)\n"
        assert parse_git_diff_stat(output) == []


# ---------------------------------------------------------------------------
# parse_git_diff_stat — renames
# ---------------------------------------------------------------------------


class TestParseGitDiffStatRenames:
    def test_brace_rename(self) -> None:
        output = "2\t1\tsrc/{old_name.py => new_name.py}\n"
        result = parse_git_diff_stat(output)
        assert len(result) == 1
        assert result[0].status == "renamed"
        assert result[0].path == "src/new_name.py"
        assert result[0].old_path == "src/old_name.py"

    def test_arrow_rename(self) -> None:
        output = "0\t0\told/path.py => new/path.py\n"
        result = parse_git_diff_stat(output)
        assert len(result) == 1
        assert result[0].status == "renamed"
        assert result[0].path == "new/path.py"
        assert result[0].old_path == "old/path.py"

    def test_brace_rename_nested(self) -> None:
        output = "5\t3\tsrc/core/{utils.py => helpers.py}\n"
        result = parse_git_diff_stat(output)
        assert result[0].old_path == "src/core/utils.py"
        assert result[0].path == "src/core/helpers.py"


# ---------------------------------------------------------------------------
# build_diff_summary
# ---------------------------------------------------------------------------


class TestBuildDiffSummary:
    def test_basic_summary(self) -> None:
        output = "10\t5\tsrc/a.py\n3\t0\tsrc/b.py\n"
        summary = build_diff_summary("task-001", output)
        assert summary.task_id == "task-001"
        assert summary.total_files == 2
        assert summary.total_added == 13
        assert summary.total_removed == 5
        assert len(summary.files) == 2
        assert summary.generated_at  # non-empty ISO timestamp

    def test_with_test_results(self) -> None:
        output = "1\t1\tREADME.md\n"
        summary = build_diff_summary(
            "task-002",
            output,
            test_results={"pytest": "passed", "ruff": "clean"},
        )
        assert summary.test_results == {"pytest": "passed", "ruff": "clean"}

    def test_empty_diff(self) -> None:
        summary = build_diff_summary("task-003", "")
        assert summary.total_files == 0
        assert summary.total_added == 0
        assert summary.total_removed == 0
        assert summary.files == []

    def test_none_test_results_becomes_empty_dict(self) -> None:
        summary = build_diff_summary("task-004", "", test_results=None)
        assert summary.test_results == {}


# ---------------------------------------------------------------------------
# format_diff_preview
# ---------------------------------------------------------------------------


class TestFormatDiffPreview:
    def test_contains_task_id(self) -> None:
        summary = build_diff_summary("task-010", "5\t2\tsrc/main.py\n")
        preview = format_diff_preview(summary)
        assert "task-010" in preview

    def test_contains_file_paths(self) -> None:
        summary = build_diff_summary(
            "task-011",
            "5\t2\tsrc/main.py\n3\t1\tsrc/util.py\n",
        )
        preview = format_diff_preview(summary)
        assert "src/main.py" in preview
        assert "src/util.py" in preview

    def test_contains_totals(self) -> None:
        summary = build_diff_summary("task-012", "10\t3\tsrc/a.py\n5\t2\tsrc/b.py\n")
        preview = format_diff_preview(summary)
        assert "2 file(s) changed" in preview
        assert "+15" in preview
        assert "-5" in preview

    def test_contains_test_results(self) -> None:
        summary = build_diff_summary(
            "task-013",
            "1\t0\tsrc/x.py\n",
            test_results={"pytest": "passed"},
        )
        preview = format_diff_preview(summary)
        assert "Test Results:" in preview
        assert "pytest: passed" in preview

    def test_empty_diff_shows_no_files(self) -> None:
        summary = build_diff_summary("task-014", "")
        preview = format_diff_preview(summary)
        assert "(no files changed)" in preview

    def test_rename_shows_arrow(self) -> None:
        summary = build_diff_summary(
            "task-015",
            "2\t1\tsrc/{old.py => new.py}\n",
        )
        preview = format_diff_preview(summary)
        assert "src/old.py -> src/new.py" in preview


# ---------------------------------------------------------------------------
# format_compact_diff
# ---------------------------------------------------------------------------


class TestFormatCompactDiff:
    def test_basic_compact(self) -> None:
        summary = build_diff_summary("task-020", "10\t5\tsrc/a.py\n3\t0\tsrc/b.py\n")
        compact = format_compact_diff(summary)
        assert "2 file(s) changed" in compact
        assert "+13" in compact
        assert "-5" in compact

    def test_with_test_results(self) -> None:
        summary = build_diff_summary(
            "task-021",
            "1\t1\tREADME.md\n",
            test_results={"pytest": "passed", "ruff": "clean"},
        )
        compact = format_compact_diff(summary)
        assert "pytest: passed" in compact
        assert "ruff: clean" in compact

    def test_no_test_results(self) -> None:
        summary = build_diff_summary("task-022", "1\t0\tsrc/x.py\n")
        compact = format_compact_diff(summary)
        # Should not contain a trailing comma or "tests:" when no results
        assert compact.endswith("-0")

    def test_empty_diff(self) -> None:
        summary = build_diff_summary("task-023", "")
        compact = format_compact_diff(summary)
        assert "0 file(s) changed" in compact
