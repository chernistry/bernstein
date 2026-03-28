"""Tick pipeline helpers: task fetching, batching, and server interaction.

Pure functions and TypedDicts extracted from orchestrator.py to reduce file size
while keeping the Orchestrator class as the single entry point.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import defaultdict
from typing import TYPE_CHECKING, Any, TypedDict

from bernstein.core.models import Task, TaskType

if TYPE_CHECKING:
    from pathlib import Path

    import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TypedDicts shared across orchestrator sub-modules
# ---------------------------------------------------------------------------


class _RuffLocation(TypedDict, total=False):
    row: int
    column: int


class RuffViolation(TypedDict, total=False):
    """A single violation from ``ruff check --output-format=json``."""

    code: str
    filename: str
    message: str
    location: _RuffLocation


class TestResults(TypedDict, total=False):
    """Parsed pytest output with pass/fail counts and a one-line summary."""

    passed: int
    failed: int
    summary: str


class CompletionData(TypedDict):
    """Structured data extracted from an agent's runtime log after task completion."""

    files_modified: list[str]
    test_results: TestResults


# ---------------------------------------------------------------------------
# Task server interaction helpers
# ---------------------------------------------------------------------------


def _task_from_dict(raw: dict[str, Any]) -> Task:  # type: ignore[reportUnusedFunction]
    """Deserialise a server JSON response into a domain Task (delegates to Task.from_dict)."""
    return Task.from_dict(raw)


def fetch_all_tasks(
    client: httpx.Client,
    base_url: str,
    statuses: list[str] | None = None,
) -> dict[str, list[Task]]:
    """Fetch all tasks from the server in a single GET /tasks call.

    Makes exactly one HTTP request and buckets the results client-side by
    status, keeping per-tick round-trips to a minimum.

    Args:
        client: httpx client.
        base_url: Server base URL.
        statuses: Status keys to include in the result dict.  Defaults to
            ["open", "claimed", "done", "failed"].

    Returns:
        Dict mapping status string -> list of Tasks.  Always includes keys for
        every requested status even if the list is empty.
        NOTE: "open" here includes tasks with unmet dependencies; callers
        that need the dependency-filtered view should apply their own dep check.
    """
    if statuses is None:
        statuses = ["open", "claimed", "done", "failed"]
    by_status: dict[str, list[Task]] = {s: [] for s in statuses}
    resp = client.get(f"{base_url}/tasks")
    resp.raise_for_status()
    for raw in resp.json():
        task = Task.from_dict(raw)
        key = task.status.value
        if key not in by_status:
            by_status[key] = []
        by_status[key].append(task)
    return by_status


def fail_task(client: httpx.Client, base_url: str, task_id: str, reason: str) -> None:
    """POST /tasks/{task_id}/fail to mark a task as failed.

    Args:
        client: httpx client.
        base_url: Server base URL.
        task_id: ID of the task to fail.
        reason: Why the task failed.
    """
    resp = client.post(f"{base_url}/tasks/{task_id}/fail", json={"reason": reason})
    resp.raise_for_status()


def block_task(client: httpx.Client, base_url: str, task_id: str, reason: str) -> None:
    """POST /tasks/{task_id}/block to mark a task as blocked (requires human intervention).

    Args:
        client: httpx client.
        base_url: Server base URL.
        task_id: ID of the task to block.
        reason: Why the task is blocked.
    """
    resp = client.post(f"{base_url}/tasks/{task_id}/block", json={"reason": reason})
    resp.raise_for_status()


def complete_task(client: httpx.Client, base_url: str, task_id: str, result_summary: str) -> None:
    """POST /tasks/{task_id}/complete to mark a task as done.

    Args:
        client: httpx client.
        base_url: Server base URL.
        task_id: ID of the task to complete.
        result_summary: Human-readable summary of what was accomplished.
    """
    resp = client.post(
        f"{base_url}/tasks/{task_id}/complete",
        json={"result_summary": result_summary},
    )
    resp.raise_for_status()


def group_by_role(tasks: list[Task], max_per_batch: int) -> list[list[Task]]:
    """Group open tasks by role into batches of up to max_per_batch.

    Tasks are sorted by priority (ascending, 1=critical first) within each
    role before batching. Upgrade proposal tasks get a priority boost
    (effective priority reduced by 1) to ensure self-evolution tasks are
    processed promptly.

    Args:
        tasks: Open tasks to batch.
        max_per_batch: Maximum tasks per batch (typically 1-3).

    Returns:
        List of batches, each a list of same-role tasks.
    """
    by_role: dict[str, list[Task]] = defaultdict(list)
    for task in tasks:
        by_role[task.role].append(task)

    batches: list[list[Task]] = []
    for role_tasks in by_role.values():
        # Sort by effective priority: upgrade proposals get a boost (lower priority value)
        def _sort_key(t: Task) -> tuple[int, int]:
            # Priority boost for upgrade proposals: subtract 1 from priority value
            # (lower = higher priority). Second element is original priority for ties.
            priority_boost = t.priority - 1 if t.task_type == TaskType.UPGRADE_PROPOSAL else t.priority
            return (priority_boost, t.priority)

        role_tasks.sort(key=_sort_key)
        for i in range(0, len(role_tasks), max_per_batch):
            batches.append(role_tasks[i : i + max_per_batch])

    # Sort batches by best (lowest) priority so critical work goes first.
    batches.sort(key=lambda b: b[0].priority)
    return batches


# ---------------------------------------------------------------------------
# Backlog parsing
# ---------------------------------------------------------------------------


def parse_backlog_file(filename: str, content: str) -> dict[str, Any]:
    """Parse a backlog markdown file into a task creation payload.

    Extracts title, role, priority, and description from the markdown.
    Falls back to safe defaults for any missing fields.

    Args:
        filename: The filename (e.g. "100-fix-the-bug.md"), used to derive a
            slug for the title when no H1 heading is found.
        content: Full markdown text of the backlog file.

    Returns:
        Dict suitable for POST /tasks.
    """
    lines = content.splitlines()

    # Title: first H1 line, strip leading "# " and numeric prefix like "100 -- "
    title = filename.replace(".md", "").replace("-", " ")
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# "):
            raw = stripped[2:].strip()
            raw = re.sub(r"^\d+\s*--\s*", "", raw)
            title = raw
            break

    # Role: **Role:** backend
    role = "backend"
    role_match = re.search(r"\*\*Role:\*\*\s*(\S+)", content)
    if role_match:
        role = role_match.group(1).strip()

    # Priority: **Priority:** 2
    priority = 2
    priority_match = re.search(r"\*\*Priority:\*\*\s*(\d+)", content)
    if priority_match:
        priority = int(priority_match.group(1))

    # Description: everything after the header/front-matter lines
    desc_lines: list[str] = []
    past_header = False
    for line in lines:
        stripped = line.strip()
        if not past_header:
            if stripped.startswith("# ") or re.match(r"\*\*\w+:\*\*", stripped):
                past_header = True
                continue
            continue
        if re.match(r"\*\*\w+:\*\*", stripped):
            continue
        desc_lines.append(line)
    description = "\n".join(desc_lines).strip() or content.strip()

    return {
        "title": title,
        "description": description,
        "role": role,
        "priority": priority,
        "scope": "medium",
        "complexity": "medium",
    }


# ---------------------------------------------------------------------------
# Cost tracking helpers
# ---------------------------------------------------------------------------

# Cache for compute_total_spent: maps absolute metrics_dir path ->
# (cached_total, {file_path_str: (mtime_ns, file_total)}).
total_spent_cache: dict[str, tuple[float, dict[str, tuple[int, float]]]] = {}


def _parse_file_total(jsonl_file: Path) -> float:
    """Parse cost contributions from a single cost_efficiency JSONL file.

    Streams line-by-line to avoid loading the entire file into memory
    (files can grow to 100MB+ during long runs).
    """
    file_total = 0.0
    try:
        with open(jsonl_file, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    point = json.loads(line)
                    if "task_id" in point.get("labels", {}):
                        file_total += point.get("value", 0.0)
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return file_total


def compute_total_spent(workdir: Path) -> float:
    """Sum cost_efficiency metric values recorded for individual tasks.

    Reads all cost_efficiency_*.jsonl files in .sdd/metrics/ and returns the
    total cost in USD for entries that have a ``task_id`` label, avoiding
    double-counting the per-agent average entries that lack that label.

    Results are mtime-cached: files that have not changed since the last call
    are not re-read, making repeated calls on an unchanged metrics directory
    effectively free.

    Args:
        workdir: Project root directory.

    Returns:
        Total USD spent as recorded in metrics files.
    """
    metrics_dir = workdir / ".sdd" / "metrics"
    cache_key = str(metrics_dir)
    cached_total, cached_file_data = total_spent_cache.get(cache_key, (0.0, {}))

    try:
        current_files = list(metrics_dir.glob("cost_efficiency_*.jsonl"))
    except OSError:
        return cached_total

    current_paths = {str(f) for f in current_files}
    cached_paths = set(cached_file_data.keys())

    # If any previously-seen file was removed, subtract its contribution
    # from the cached total incrementally.
    removed_paths = cached_paths - current_paths
    total = cached_total
    new_file_data: dict[str, tuple[int, float]] = dict(cached_file_data)
    for removed in removed_paths:
        _, old_file_total = new_file_data.pop(removed)
        total -= old_file_total

    for jsonl_file in current_files:
        path_str = str(jsonl_file)
        try:
            mtime_ns = os.stat(jsonl_file).st_mtime_ns
        except OSError:
            continue

        cached_entry = new_file_data.get(path_str)
        if cached_entry is not None and cached_entry[0] == mtime_ns:
            # File unchanged - skip re-parsing.
            continue

        # Subtract old contribution for this file (if any), then add new.
        old_file_total = cached_entry[1] if cached_entry is not None else 0.0
        new_file_total = _parse_file_total(jsonl_file)
        total += new_file_total - old_file_total
        new_file_data[path_str] = (mtime_ns, new_file_total)

    total_spent_cache[cache_key] = (total, new_file_data)
    return total
