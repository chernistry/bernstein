"""Integration tests for the diary + synthesis pipeline.

These tests exercise the full path: synthetic transcripts -> diary
entries -> synthesis report -> markdown report -> CLI surface. They do
not spawn agents or hit the network; they verify the on-disk shape and
CLI behaviour that operators rely on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from bernstein.cli.commands.knowledge_cmd import knowledge_group
from bernstein.core.knowledge.diary import (
    load_diaries,
    load_diary,
    write_diary_from_transcript,
)
from bernstein.core.knowledge.synthesizer import (
    approve,
    render_report,
    synthesize,
    write_report,
)

# ---------------------------------------------------------------------------
# Synthetic transcripts
# ---------------------------------------------------------------------------


_TRANSCRIPT_BACKEND_RETRY_A = """
tried:
- naive request with no retry
- timeout of 5s
worked:
- exponential backoff on 503
failed:
- premature give-up on timeout
rationale:
- backend retry policy must mirror upstream SLA
"""

_TRANSCRIPT_BACKEND_RETRY_B = """
tried:
- exponential backoff with jitter
worked:
- exponential backoff on 503
- jitter prevents thundering herd
failed:
- single-shot retry on 502
rationale:
- prefer jittered backoff for backend retry semantics
"""

_TRANSCRIPT_FRONTEND_BUG = """
tried:
- CSS grid fallback for old Safari
worked:
- flexbox shim for layout
failed:
- pure CSS grid on iOS 13
rationale:
- frontend layout still needs flex fallback through 2026
"""


@pytest.fixture
def sdd_dir(tmp_path: Path) -> Path:
    """Return an isolated SDD tree for an integration test."""
    target = tmp_path / ".sdd"
    target.mkdir()
    return target


@pytest.fixture
def populated_sdd(sdd_dir: Path) -> Path:
    """SDD tree with three diaries spanning two themes."""
    write_diary_from_transcript("task-backend-1", _TRANSCRIPT_BACKEND_RETRY_A, sdd_dir)
    write_diary_from_transcript("task-backend-2", _TRANSCRIPT_BACKEND_RETRY_B, sdd_dir)
    write_diary_from_transcript("task-frontend-1", _TRANSCRIPT_FRONTEND_BUG, sdd_dir)
    return sdd_dir


# ---------------------------------------------------------------------------
# End-to-end diary -> synthesis -> report
# ---------------------------------------------------------------------------


def test_transcripts_to_diaries(populated_sdd: Path) -> None:
    """Three transcripts produce three diaries on disk."""
    diaries = load_diaries(populated_sdd)
    task_ids = {d.task_id for d in diaries}
    assert task_ids == {"task-backend-1", "task-backend-2", "task-frontend-1"}


def test_diary_files_under_runtime_dir(populated_sdd: Path) -> None:
    """Diaries land under the expected runtime subdirectory."""
    expected_dir = populated_sdd / "runtime" / "diaries"
    files = sorted(p.name for p in expected_dir.glob("*.json"))
    assert files == [
        "task-backend-1.json",
        "task-backend-2.json",
        "task-frontend-1.json",
    ]


def test_synthesis_clusters_backend(populated_sdd: Path) -> None:
    """Backend transcripts cluster together when tags overlap."""
    diaries = load_diaries(populated_sdd)
    report = synthesize(diaries, window_days=0, threshold=0.25)
    sizes = sorted((theme.size for theme in report.themes), reverse=True)
    assert sizes[0] >= 2  # backend pair clusters


def test_synthesis_persists_to_disk(populated_sdd: Path) -> None:
    """Synthesis write produces a markdown report on disk."""
    diaries = load_diaries(populated_sdd)
    report = synthesize(diaries, window_days=0)
    path = write_report(report, populated_sdd)
    assert path.exists()
    assert path.parent == populated_sdd / "runtime" / "syntheses"
    body = path.read_text()
    assert body.startswith("---\n")


def test_proposed_diff_mentions_backend_patterns(populated_sdd: Path) -> None:
    """Proposed diff for the backend cluster surfaces concrete bullets."""
    diaries = load_diaries(populated_sdd)
    report = synthesize(diaries, window_days=0, threshold=0.2)
    body = render_report(report)
    assert "exponential backoff on 503" in body


def test_hitl_gate_default_unapproved(populated_sdd: Path) -> None:
    """Synthesis runs default to ``approved: false``."""
    diaries = load_diaries(populated_sdd)
    report = synthesize(diaries, window_days=0)
    path = write_report(report, populated_sdd)
    assert "approved: false" in path.read_text()


def test_hitl_gate_apply_sets_approved(populated_sdd: Path) -> None:
    """Approve flips the marker and the rendered report reflects it."""
    diaries = load_diaries(populated_sdd)
    report = approve(synthesize(diaries, window_days=0))
    path = write_report(report, populated_sdd)
    assert "approved: true" in path.read_text()


def test_redaction_hash_round_trips_on_disk(populated_sdd: Path) -> None:
    """Loading a written diary recovers the same redaction hash."""
    diary_path = populated_sdd / "runtime" / "diaries" / "task-backend-1.json"
    loaded = load_diary(diary_path)
    payload = json.loads(diary_path.read_text())
    assert payload["redaction_hash"] == loaded.redaction_hash


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def test_cli_knowledge_diary_list_empty(sdd_dir: Path) -> None:
    """Empty diary directory yields a friendly message."""
    runner = CliRunner()
    result = runner.invoke(
        knowledge_group,
        ["diary", "list", "--sdd-dir", str(sdd_dir)],
    )
    assert result.exit_code == 0
    assert "No diary entries" in result.output


def test_cli_knowledge_diary_list_populated(populated_sdd: Path) -> None:
    """Populated diary directory lists every entry."""
    runner = CliRunner()
    result = runner.invoke(
        knowledge_group,
        ["diary", "list", "--sdd-dir", str(populated_sdd)],
    )
    assert result.exit_code == 0
    assert "task-backend-1" in result.output
    assert "task-frontend-1" in result.output


def test_cli_knowledge_diary_show(populated_sdd: Path) -> None:
    """Showing a task prints rationale, tags, and section labels."""
    runner = CliRunner()
    result = runner.invoke(
        knowledge_group,
        ["diary", "show", "task-backend-1", "--sdd-dir", str(populated_sdd)],
    )
    assert result.exit_code == 0
    assert "task-backend-1" in result.output
    assert "Tried" in result.output
    assert "Worked" in result.output


def test_cli_knowledge_diary_show_missing(sdd_dir: Path) -> None:
    """Showing a missing task exits non-zero."""
    runner = CliRunner()
    result = runner.invoke(
        knowledge_group,
        ["diary", "show", "no-such-task", "--sdd-dir", str(sdd_dir)],
    )
    assert result.exit_code != 0


def test_cli_synthesize_dry_run_prints_report(populated_sdd: Path) -> None:
    """Dry-run renders the report to stdout without writing."""
    runner = CliRunner()
    result = runner.invoke(
        knowledge_group,
        [
            "synthesize",
            "--since",
            "30d",
            "--dry-run",
            "--sdd-dir",
            str(populated_sdd),
        ],
    )
    assert result.exit_code == 0
    assert "generated_at:" in result.output
    assert "approved: false" in result.output
    # No file written on dry-run
    assert not (populated_sdd / "runtime" / "syntheses").exists() or not list(
        (populated_sdd / "runtime" / "syntheses").glob("*.md")
    )


def test_cli_synthesize_writes_report(populated_sdd: Path) -> None:
    """The default invocation writes a markdown report under the SDD tree."""
    runner = CliRunner()
    result = runner.invoke(
        knowledge_group,
        ["synthesize", "--since", "30d", "--sdd-dir", str(populated_sdd)],
    )
    assert result.exit_code == 0
    reports = list((populated_sdd / "runtime" / "syntheses").glob("*.md"))
    assert len(reports) == 1


def test_cli_synthesize_apply_marks_approved(populated_sdd: Path) -> None:
    """--apply persists an approved report."""
    runner = CliRunner()
    result = runner.invoke(
        knowledge_group,
        [
            "synthesize",
            "--since",
            "30d",
            "--apply",
            "--sdd-dir",
            str(populated_sdd),
        ],
    )
    assert result.exit_code == 0
    reports = list((populated_sdd / "runtime" / "syntheses").glob("*.md"))
    assert len(reports) == 1
    assert "approved: true" in reports[0].read_text()


def test_cli_synthesize_invalid_since(populated_sdd: Path) -> None:
    """Bad --since value exits non-zero with a descriptive message."""
    runner = CliRunner()
    result = runner.invoke(
        knowledge_group,
        ["synthesize", "--since", "forever", "--sdd-dir", str(populated_sdd)],
    )
    assert result.exit_code != 0
    assert "Invalid --since" in result.output


def test_cli_diary_list_json_output(populated_sdd: Path) -> None:
    """--json emits a parseable JSON array."""
    runner = CliRunner()
    result = runner.invoke(
        knowledge_group,
        ["diary", "list", "--json", "--sdd-dir", str(populated_sdd)],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert isinstance(payload, list)
    assert len(payload) == 3


def test_cli_diary_show_json_output(populated_sdd: Path) -> None:
    """--json emits the entry payload."""
    runner = CliRunner()
    result = runner.invoke(
        knowledge_group,
        ["diary", "show", "task-backend-1", "--json", "--sdd-dir", str(populated_sdd)],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["task_id"] == "task-backend-1"
