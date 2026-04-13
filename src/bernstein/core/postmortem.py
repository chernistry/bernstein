"""Automated post-mortem report generation for failed orchestration runs.

When a run fails or produces poor results, ``bernstein postmortem <run-id>``
generates a structured report with:
  - Timeline of events from the replay log
  - Root cause analysis using a failure pattern database
  - Contributing factors and agent decision traces
  - Recommended actions
  - Exportable as HTML

Usage::

    from bernstein.core.postmortem import (
        generate_postmortem,
        build_timeline,
        analyze_root_cause,
        export_report_html,
        PostMortemReport,
        TimelineEvent,
    )

    report = generate_postmortem("20240315-143022", sdd_dir=Path(".sdd"))
    html = export_report_html(report)
"""

from __future__ import annotations

import html as html_mod
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TimelineEvent:
    """An event in the post-mortem timeline.

    Attributes:
        timestamp: When the event occurred.
        event_type: Category of the event (e.g. ``"task_claimed"``).
        description: Human-readable summary.
        agent_id: Agent that produced the event, if applicable.
        severity: Severity level — ``"info"``, ``"warning"``, ``"error"``,
            or ``"critical"``.
    """

    timestamp: datetime
    event_type: str
    description: str
    agent_id: str | None = None
    severity: str = "info"


@dataclass(frozen=True)
class PostMortemReport:
    """A complete post-mortem report for a failed run.

    Attributes:
        run_id: The run identifier.
        summary: One-paragraph executive summary of the failure.
        timeline: Ordered events from the run.
        root_cause: Primary root cause description.
        contributing_factors: Secondary factors that contributed.
        failed_tasks: IDs of tasks that failed.
        recommendations: Actionable recommendations.
        generated_at: When the report was generated.
    """

    run_id: str
    summary: str
    timeline: tuple[TimelineEvent, ...]
    root_cause: str
    contributing_factors: tuple[str, ...]
    failed_tasks: tuple[str, ...]
    recommendations: tuple[str, ...]
    generated_at: datetime


# ---------------------------------------------------------------------------
# Failure pattern database
# ---------------------------------------------------------------------------

_FAILURE_PATTERNS: list[dict[str, Any]] = [
    {
        "name": "timeout",
        "signals": ("timeout", "timed out", "deadline exceeded", "elapsed"),
        "root_cause": "Task execution exceeded the allocated time budget.",
        "recommendations": (
            "Increase the timeout budget for this task type.",
            "Break the task into smaller sub-tasks with individual timeouts.",
            "Profile the agent to identify slow operations.",
        ),
    },
    {
        "name": "api_rate_limit",
        "signals": ("rate limit", "429", "too many requests", "throttl"),
        "root_cause": "External API rate limiting prevented task completion.",
        "recommendations": (
            "Implement exponential backoff with jitter for retries.",
            "Add request queuing or batching to reduce API calls.",
            "Cache responses where possible to avoid redundant requests.",
        ),
    },
    {
        "name": "auth_failure",
        "signals": ("authentication", "unauthorized", "401", "403", "forbidden", "invalid token"),
        "root_cause": "Authentication or authorization failure during API calls.",
        "recommendations": (
            "Verify API credentials are still valid and not expired.",
            "Implement credential rotation with automatic renewal.",
            "Add health checks that validate auth before task execution.",
        ),
    },
    {
        "name": "dependency_missing",
        "signals": ("module not found", "import error", "no module named", "cannot import", "dependency"),
        "root_cause": "Missing or incompatible dependency prevented execution.",
        "recommendations": (
            "Pin dependency versions in the lock file.",
            "Add a pre-flight dependency check before task execution.",
            "Use a virtual environment with explicit requirements.",
        ),
    },
    {
        "name": "disk_full",
        "signals": ("no space left", "disk full", "enospc", "storage"),
        "root_cause": "Insufficient disk space for task artifacts or logs.",
        "recommendations": (
            "Add disk space monitoring with alerts before running tasks.",
            "Implement log rotation and artifact cleanup.",
            "Use streaming writes instead of buffering large outputs.",
        ),
    },
    {
        "name": "oom",
        "signals": ("out of memory", "oom", "memory error", "cannot allocate", "killed"),
        "root_cause": "Process exceeded available memory.",
        "recommendations": (
            "Add memory limits to the task configuration.",
            "Process data in chunks instead of loading everything into memory.",
            "Profile memory usage and optimize hot paths.",
        ),
    },
    {
        "name": "test_failure",
        "signals": ("test failed", "assertionerror", "pytest", "unittest", "test error"),
        "root_cause": "One or more tests failed during quality gate validation.",
        "recommendations": (
            "Review the failing test output for the specific assertion.",
            "Check if the test expectations match the intended behavior.",
            "Run tests incrementally during development to catch failures early.",
        ),
    },
    {
        "name": "syntax_error",
        "signals": ("syntaxerror", "syntax error", "invalid syntax", "parse error"),
        "root_cause": "Generated code contains syntax errors.",
        "recommendations": (
            "Add a syntax validation step before running tests.",
            "Use the language's built-in AST parser for early detection.",
            "Include linting in the quality gate pipeline.",
        ),
    },
]

# ---------------------------------------------------------------------------
# Timeline building
# ---------------------------------------------------------------------------


def build_timeline(
    run_id: str,
    events: list[dict[str, Any]] | None = None,
    *,
    sdd_dir: Path | None = None,
) -> list[TimelineEvent]:
    """Build an event timeline from run logs and state changes.

    If *events* is provided, uses it directly.  Otherwise reads from the
    replay JSONL file at ``<sdd_dir>/runs/<run_id>/replay.jsonl``.

    Args:
        run_id: The run identifier.
        events: Pre-parsed event dictionaries.  Each must have at least
            ``"event"`` (str) and optionally ``"timestamp"`` (float epoch),
            ``"agent_id"`` (str), and other metadata.
        sdd_dir: Path to the ``.sdd`` directory.  Required when *events*
            is ``None``.

    Returns:
        An ordered list of :class:`TimelineEvent` instances.
    """
    raw_events = events

    if raw_events is None:
        if sdd_dir is None:
            logger.warning("No events or sdd_dir provided; returning empty timeline")
            return []
        replay_path = sdd_dir / "runs" / run_id / "replay.jsonl"
        if not replay_path.is_file():
            logger.warning("Replay file not found: %s", replay_path)
            return []
        raw_events = _read_replay_file(replay_path)

    timeline: list[TimelineEvent] = []
    for entry in raw_events:
        ts = entry.get("timestamp", 0.0)
        timestamp = datetime.fromtimestamp(ts, tz=UTC) if isinstance(ts, (int, float)) else datetime.now(tz=UTC)

        event_type = entry.get("event", "unknown")
        agent_id = entry.get("agent_id")

        # Build description from available fields
        desc_parts: list[str] = []
        for key in ("task_id", "model", "files_modified", "cost_usd", "error", "message", "status"):
            val = entry.get(key)
            if val is not None:
                desc_parts.append(f"{key}={val}")
        description = f"{event_type}" + (f": {', '.join(desc_parts)}" if desc_parts else "")

        # Infer severity
        severity = _infer_severity(event_type, entry)

        timeline.append(TimelineEvent(
            timestamp=timestamp,
            event_type=event_type,
            description=description,
            agent_id=agent_id,
            severity=severity,
        ))

    return timeline


def _read_replay_file(path: Path) -> list[dict[str, Any]]:
    """Read and parse a JSONL replay file.

    Args:
        path: Path to the JSONL file.

    Returns:
        A list of parsed JSON dictionaries.
    """
    events: list[dict[str, Any]] = []
    try:
        import json

        with open(path) as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except ValueError:
                    logger.warning("Invalid JSON on line %d in %s", line_num, path)
    except OSError as exc:
        logger.error("Failed to read replay file %s: %s", path, exc)
    return events


def _infer_severity(event_type: str, entry: dict[str, Any]) -> str:
    """Infer event severity from type and content.

    Args:
        event_type: The event type string.
        entry: The full event dictionary.

    Returns:
        One of ``"info"``, ``"warning"``, ``"error"``, ``"critical"``.
    """
    critical_keywords = ("fatal", "crash", "killed", "oom", "panic")
    error_keywords = ("error", "fail", "exception", "timeout", "reject")
    warning_keywords = ("warn", "retry", "slow", "degrad")

    text = (event_type + " " + " ".join(str(v) for v in entry.values())).lower()

    if any(kw in text for kw in critical_keywords):
        return "critical"
    if any(kw in text for kw in error_keywords):
        return "error"
    if any(kw in text for kw in warning_keywords):
        return "warning"
    return "info"


# ---------------------------------------------------------------------------
# Root cause analysis
# ---------------------------------------------------------------------------


def analyze_root_cause(
    run_id: str,
    timeline: list[TimelineEvent],
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """Analyze the timeline to determine root cause and contributing factors.

    Matches events against a database of known failure patterns and returns
    the best match's root cause, contributing factors, and recommendations.

    Args:
        run_id: The run identifier.
        timeline: Ordered timeline events.

    Returns:
        A tuple of ``(root_cause, contributing_factors, recommendations)``.
    """
    if not timeline:
        return (
            "No events found in the timeline.",
            ("Run produced no log events."),
            ("Check that the run ID is correct and replay logs exist."),
        )

    # Collect all text from error/warning/critical events
    signal_text = " ".join(
        e.description.lower()
        for e in timeline
        if e.severity in ("error", "critical", "warning")
    )

    # Score each failure pattern
    best_match: dict[str, Any] | None = None
    best_score = 0

    for pattern in _FAILURE_PATTERNS:
        score = sum(1 for sig in pattern["signals"] if sig in signal_text)
        if score > best_score:
            best_score = score
            best_match = pattern

    # Also identify failed tasks
    tuple(
        e.description.split(":")[0].replace("task_completed", "").strip()
        for e in timeline
        if "fail" in e.severity or "fail" in e.event_type.lower()
        or "error" in e.severity
    )

    # Identify contributing factors from warning events
    contributing = tuple(
        e.description
        for e in timeline
        if e.severity == "warning"
    )

    if best_match is None or best_score == 0:
        # Generic analysis
        error_events = [e for e in timeline if e.severity in ("error", "critical")]
        if error_events:
            last_error = error_events[-1]
            root_cause = f"The run failed with a {last_error.severity} event: {last_error.description}"
        else:
            root_cause = "Run completed without explicit errors but may have produced poor results."
        return root_cause, contributing, ("Review the timeline for unexpected event sequences.",)

    return (
        best_match["root_cause"],
        contributing,
        best_match["recommendations"],
    )


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def generate_postmortem(
    run_id: str,
    events: list[dict[str, Any]] | None = None,
    *,
    sdd_dir: Path | None = None,
) -> PostMortemReport:
    """Generate a complete post-mortem report for a failed run.

    Args:
        run_id: The run identifier.
        events: Pre-parsed event dictionaries (optional).
        sdd_dir: Path to the ``.sdd`` directory (optional).

    Returns:
        A :class:`PostMortemReport` with full analysis.
    """
    timeline = build_timeline(run_id, events, sdd_dir=sdd_dir)
    root_cause, contributing, recommendations = analyze_root_cause(run_id, timeline)

    failed_tasks = tuple(
        e.event_type
        for e in timeline
        if e.severity in ("error", "critical")
    )

    # Generate summary
    error_count = sum(1 for e in timeline if e.severity in ("error", "critical"))
    warning_count = sum(1 for e in timeline if e.severity == "warning")
    summary = (
        f"Run {run_id} encountered {error_count} error(s) and "
        f"{warning_count} warning(s) across {len(timeline)} events. "
        f"Root cause: {root_cause}"
    )

    return PostMortemReport(
        run_id=run_id,
        summary=summary,
        timeline=tuple(timeline),
        root_cause=root_cause,
        contributing_factors=contributing,
        failed_tasks=failed_tasks,
        recommendations=recommendations,
        generated_at=datetime.now(tz=UTC),
    )


# ---------------------------------------------------------------------------
# HTML export
# ---------------------------------------------------------------------------


def export_report_html(report: PostMortemReport) -> str:
    """Export the post-mortem report as an HTML document.

    Args:
        report: The :class:`PostMortemReport` to export.

    Returns:
        A complete HTML string.
    """
    esc = html_mod.escape

    timeline_rows = "\n".join(
        f"<tr>"
        f"<td>{esc(e.timestamp.strftime('%H:%M:%S'))}</td>"
        f"<td><span class=\"severity-{esc(e.severity)}\">{esc(e.severity.upper())}</span></td>"
        f"<td>{esc(e.event_type)}</td>"
        f"<td>{esc(e.description)}</td>"
        f"<td>{esc(e.agent_id or '—')}</td>"
        f"</tr>"
        for e in report.timeline
    )

    factor_items = "\n".join(f"<li>{esc(f)}</li>" for f in report.contributing_factors)
    rec_items = "\n".join(f"<li>{esc(r)}</li>" for r in report.recommendations)

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Post-Mortem Report — {esc(report.run_id)}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       max-width: 960px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }}
h1 {{ border-bottom: 2px solid #e5e7eb; padding-bottom: 0.5rem; }}
h2 {{ color: #374151; margin-top: 2rem; }}
.meta {{ color: #6b7280; font-size: 0.875rem; }}
.severity-critical {{ color: #dc2626; font-weight: bold; }}
.severity-error {{ color: #ea580c; font-weight: bold; }}
.severity-warning {{ color: #ca8a04; }}
.severity-info {{ color: #2563eb; }}
table {{ width: 100%; border-collapse: collapse; margin: 1rem 0; }}
th, td {{ text-align: left; padding: 0.5rem; border-bottom: 1px solid #e5e7eb; font-size: 0.875rem; }}
th {{ background: #f9fafb; font-weight: 600; }}
.root-cause {{ background: #fef2f2; border-left: 4px solid #dc2626; padding: 1rem; margin: 1rem 0; }}
ul {{ padding-left: 1.5rem; }}
li {{ margin: 0.25rem 0; }}
</style>
</head>
<body>
<h1>Post-Mortem Report</h1>
<p class="meta">Run: <strong>{esc(report.run_id)}</strong> |
   Generated: {esc(report.generated_at.strftime('%Y-%m-%d %H:%M:%S UTC'))}</p>

<h2>Summary</h2>
<p>{esc(report.summary)}</p>

<h2>Root Cause</h2>
<div class="root-cause">{esc(report.root_cause)}</div>

<h2>Timeline</h2>
<table>
<tr><th>Time</th><th>Severity</th><th>Event</th><th>Description</th><th>Agent</th></tr>
{timeline_rows}
</table>

<h2>Contributing Factors</h2>
<ul>{factor_items}</ul>

<h2>Recommendations</h2>
<ul>{rec_items}</ul>

</body>
</html>"""
