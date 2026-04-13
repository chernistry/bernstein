"""Tests for bernstein.core.postmortem.

Covers timeline building, severity inference, root cause analysis,
report generation, and HTML export.
"""

from __future__ import annotations

import json
from pathlib import Path

from bernstein.core.postmortem import (
    analyze_root_cause,
    build_timeline,
    export_report_html,
    generate_postmortem,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_event(
    event: str = "task_started",
    timestamp: float = 1700000000.0,
    agent_id: str | None = "agent-1",
    **extra: object,
) -> dict:
    """Build a minimal replay event dict."""
    d: dict = {"event": event, "timestamp": timestamp, "agent_id": agent_id}
    d.update(extra)
    return d


_SAMPLE_EVENTS = [
    _make_event("task_claimed", 1700000000.0, task_id="T-001"),
    _make_event("agent_spawned", 1700000001.0, model="sonnet"),
    _make_event("task_completed", 1700000010.0, status="fail", error="timeout"),
    _make_event("task_warning", 1700000005.0, message="slow response", agent_id=None),
]


# ---------------------------------------------------------------------------
# _infer_severity (tested indirectly via build_timeline)
# ---------------------------------------------------------------------------


class TestBuildTimeline:
    """Tests for timeline building."""

    def test_from_events_list(self) -> None:
        timeline = build_timeline("run-1", events=_SAMPLE_EVENTS)
        assert len(timeline) == 4
        assert timeline[0].event_type == "task_claimed"

    def test_timestamps_parsed(self) -> None:
        timeline = build_timeline("run-1", events=_SAMPLE_EVENTS)
        assert timeline[0].timestamp.year == 2023

    def test_from_replay_file(self, tmp_path: Path) -> None:
        replay_dir = tmp_path / "runs" / "run-1"
        replay_dir.mkdir(parents=True)
        replay_file = replay_dir / "replay.jsonl"
        with open(replay_file, "w") as f:
            for e in _SAMPLE_EVENTS:
                f.write(json.dumps(e) + "\n")

        timeline = build_timeline("run-1", sdd_dir=tmp_path)
        assert len(timeline) == 4

    def test_missing_replay_file(self, tmp_path: Path) -> None:
        timeline = build_timeline("nonexistent", sdd_dir=tmp_path)
        assert timeline == []

    def test_no_events_no_dir(self) -> None:
        timeline = build_timeline("any")
        assert timeline == []

    def test_agent_id_preserved(self) -> None:
        timeline = build_timeline("run-1", events=_SAMPLE_EVENTS)
        assert timeline[0].agent_id == "agent-1"
        assert timeline[3].agent_id is None  # warning has no agent

    def test_description_includes_metadata(self) -> None:
        timeline = build_timeline("run-1", events=_SAMPLE_EVENTS)
        assert "task_id=T-001" in timeline[0].description

    def test_empty_events(self) -> None:
        timeline = build_timeline("run-1", events=[])
        assert timeline == []

    def test_invalid_json_in_replay(self, tmp_path: Path) -> None:
        replay_dir = tmp_path / "runs" / "run-1"
        replay_dir.mkdir(parents=True)
        replay_file = replay_dir / "replay.jsonl"
        replay_file.write_text("valid json\n{bad json\nvalid again\n")
        with open(replay_file, "w") as f:
            f.write(json.dumps(_SAMPLE_EVENTS[0]) + "\n")
            f.write("{bad json\n")
            f.write(json.dumps(_SAMPLE_EVENTS[1]) + "\n")
        timeline = build_timeline("run-1", sdd_dir=tmp_path)
        assert len(timeline) == 2  # skips invalid line


# ---------------------------------------------------------------------------
# Severity inference
# ---------------------------------------------------------------------------


class TestSeverityInference:
    """Tests for event severity inference."""

    def test_error_event(self) -> None:
        timeline = build_timeline("r", events=[_make_event("task_failed", error="something broke")])
        assert timeline[0].severity == "error"

    def test_critical_event(self) -> None:
        timeline = build_timeline("r", events=[_make_event("process_killed", status="oom")])
        assert timeline[0].severity == "critical"

    def test_warning_event(self) -> None:
        timeline = build_timeline("r", events=[_make_event("task_slow", message="retrying")])
        assert timeline[0].severity == "warning"

    def test_info_event(self) -> None:
        timeline = build_timeline("r", events=[_make_event("task_started")])
        assert timeline[0].severity == "info"


# ---------------------------------------------------------------------------
# analyze_root_cause
# ---------------------------------------------------------------------------


class TestAnalyzeRootCause:
    """Tests for root cause analysis."""

    def test_timeout_pattern(self) -> None:
        events = [
            _make_event("task_failed", error="request timed out after 30s"),
        ]
        timeline = build_timeline("r", events=events)
        cause, _factors, recs = analyze_root_cause("r", timeline)
        assert "time budget" in cause.lower() or "timeout" in cause.lower()
        assert len(recs) >= 2

    def test_auth_failure_pattern(self) -> None:
        events = [
            _make_event("task_failed", error="401 unauthorized"),
        ]
        timeline = build_timeline("r", events=events)
        cause, _factors, _recs = analyze_root_cause("r", timeline)
        assert "auth" in cause.lower() or "credential" in cause.lower()

    def test_rate_limit_pattern(self) -> None:
        events = [
            _make_event("task_failed", error="429 rate limit exceeded"),
        ]
        timeline = build_timeline("r", events=events)
        cause, _, _ = analyze_root_cause("r", timeline)
        assert "rate limit" in cause.lower()

    def test_test_failure_pattern(self) -> None:
        events = [
            _make_event("quality_gate", status="fail", error="test failed: AssertionError"),
        ]
        timeline = build_timeline("r", events=events)
        cause, _, _ = analyze_root_cause("r", timeline)
        assert "test" in cause.lower()

    def test_no_events(self) -> None:
        cause, _factors, recs = analyze_root_cause("r", [])
        assert "No events" in cause
        assert len(recs) >= 1

    def test_no_matching_pattern(self) -> None:
        events = [
            _make_event("task_completed", status="unknown_issue"),
        ]
        timeline = build_timeline("r", events=events)
        cause, _, _recs = analyze_root_cause("r", timeline)
        # Should still return something useful
        assert isinstance(cause, str)
        assert len(cause) > 0

    def test_contributing_factors_from_warnings(self) -> None:
        events = [
            _make_event("task_warning", message="slow API response"),
            _make_event("task_warning", message="retrying request"),
            _make_event("task_failed", error="request timed out"),
        ]
        timeline = build_timeline("r", events=events)
        _, factors, _ = analyze_root_cause("r", timeline)
        assert len(factors) == 2

    def test_dependency_missing_pattern(self) -> None:
        events = [
            _make_event("task_failed", error="ModuleNotFoundError: No module named 'foo'"),
        ]
        timeline = build_timeline("r", events=events)
        cause, _, _recs = analyze_root_cause("r", timeline)
        assert "depend" in cause.lower()


# ---------------------------------------------------------------------------
# generate_postmortem
# ---------------------------------------------------------------------------


class TestGeneratePostmortem:
    """Tests for full report generation."""

    def test_basic_report(self) -> None:
        report = generate_postmortem("run-1", events=_SAMPLE_EVENTS)
        assert report.run_id == "run-1"
        assert len(report.timeline) == 4
        assert report.root_cause  # non-empty
        assert report.summary  # non-empty
        assert report.generated_at.tzinfo is not None  # timezone-aware

    def test_empty_run(self) -> None:
        report = generate_postmortem("empty", events=[])
        assert report.run_id == "empty"
        assert len(report.timeline) == 0
        assert "No events" in report.root_cause

    def test_from_replay_file(self, tmp_path: Path) -> None:
        replay_dir = tmp_path / "runs" / "run-1"
        replay_dir.mkdir(parents=True)
        replay_file = replay_dir / "replay.jsonl"
        with open(replay_file, "w") as f:
            for e in _SAMPLE_EVENTS:
                f.write(json.dumps(e) + "\n")
        report = generate_postmortem("run-1", sdd_dir=tmp_path)
        assert len(report.timeline) == 4

    def test_summary_includes_counts(self) -> None:
        report = generate_postmortem("run-1", events=_SAMPLE_EVENTS)
        assert "error" in report.summary.lower()
        assert "warning" in report.summary.lower()


# ---------------------------------------------------------------------------
# export_report_html
# ---------------------------------------------------------------------------


class TestExportReportHtml:
    """Tests for HTML export."""

    def test_valid_html(self) -> None:
        report = generate_postmortem("run-1", events=_SAMPLE_EVENTS)
        html = export_report_html(report)
        assert "<!DOCTYPE html>" in html
        assert "</html>" in html
        assert report.run_id in html

    def test_timeline_in_html(self) -> None:
        report = generate_postmortem("run-1", events=_SAMPLE_EVENTS)
        html = export_report_html(report)
        assert "task_claimed" in html
        assert "task_completed" in html

    def test_root_cause_in_html(self) -> None:
        report = generate_postmortem("run-1", events=_SAMPLE_EVENTS)
        html = export_report_html(report)
        assert "Root Cause" in html

    def test_severity_classes(self) -> None:
        report = generate_postmortem("run-1", events=_SAMPLE_EVENTS)
        html = export_report_html(report)
        assert "severity-error" in html
        assert "severity-warning" in html

    def test_html_escaping(self) -> None:
        events = [_make_event("task_failed", error="<script>alert('xss')</script>")]
        report = generate_postmortem("run-1", events=events)
        html = export_report_html(report)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_empty_timeline_html(self) -> None:
        report = generate_postmortem("empty", events=[])
        html = export_report_html(report)
        assert "<!DOCTYPE html>" in html

    def test_recommendations_in_html(self) -> None:
        events = [_make_event("task_failed", error="request timed out")]
        report = generate_postmortem("run-1", events=events)
        html = export_report_html(report)
        assert "Recommendations" in html
