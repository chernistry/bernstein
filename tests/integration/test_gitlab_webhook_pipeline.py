"""End-to-end webhook+mapping tests using captured fixture payloads.

These tests exercise the GitLab webhook ingress + parser + mapper as a
pipeline.  They do *not* spin up an HTTP server — instead they import the
public functions and run them against the JSON fixtures committed under
``tests/integration/gitlab/fixtures/``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bernstein.gitlab_app.cost_reporter import build_cost_summary
from bernstein.gitlab_app.mapper import (
    merge_request_to_tasks,
    note_to_task,
    pipeline_to_tasks,
)
from bernstein.gitlab_app.pipelines import build_status_body, conclusion_to_state
from bernstein.gitlab_app.webhooks import parse_webhook

FIXTURE_DIR = Path(__file__).parent / "gitlab" / "fixtures"


def _load(name: str) -> tuple[dict[str, Any], bytes]:
    path = FIXTURE_DIR / name
    payload = json.loads(path.read_text())
    return payload, path.read_bytes()


def _headers(event: str) -> dict[str, str]:
    return {"X-Gitlab-Event": event, "Content-Type": "application/json"}


class TestEndToEndMergeRequest:
    def test_mr_open_creates_one_task(self) -> None:
        _, body = _load("merge_request_open.json")
        event = parse_webhook(_headers("Merge Request Hook"), body)
        tasks = merge_request_to_tasks(event)
        assert len(tasks) == 1
        t = tasks[0]
        assert t["task_type"] == "standard"
        assert t["role"] == "backend"
        assert "GL-MR!7" in t["title"]
        assert "acme/widgets" in t["description"]

    def test_mr_close_no_task(self) -> None:
        _, body = _load("merge_request_close.json")
        event = parse_webhook(_headers("Merge Request Hook"), body)
        assert merge_request_to_tasks(event) == []

    def test_mr_with_security_label_escalates_priority(self) -> None:
        _, body = _load("merge_request_with_labels.json")
        event = parse_webhook(_headers("Merge Request Hook"), body)
        tasks = merge_request_to_tasks(event)
        assert tasks[0]["priority"] == 1
        assert tasks[0]["role"] == "security"


class TestEndToEndPipeline:
    def test_failed_pipeline_creates_fix_task(self) -> None:
        _, body = _load("pipeline_failed.json")
        event = parse_webhook(_headers("Pipeline Hook"), body)
        tasks = pipeline_to_tasks(event)
        assert len(tasks) == 1
        t = tasks[0]
        assert t["task_type"] == "fix"
        assert t["role"] == "qa"
        assert "ci-fix" in t["title"]
        assert "99876" in t["description"]

    def test_failed_pipeline_with_retry_uses_opus(self) -> None:
        _, body = _load("pipeline_failed.json")
        event = parse_webhook(_headers("Pipeline Hook"), body)
        tasks = pipeline_to_tasks(event, retry_count=2)
        assert tasks[0]["model"] == "opus"

    def test_success_pipeline_no_task(self) -> None:
        _, body = _load("pipeline_success.json")
        event = parse_webhook(_headers("Pipeline Hook"), body)
        assert pipeline_to_tasks(event) == []


class TestEndToEndNote:
    def test_actionable_mr_note(self) -> None:
        _, body = _load("note_mr.json")
        event = parse_webhook(_headers("Note Hook"), body)
        task = note_to_task(event)
        assert task is not None
        assert task["task_type"] == "fix"

    def test_slash_command_note(self) -> None:
        _, body = _load("note_slash_command.json")
        event = parse_webhook(_headers("Note Hook"), body)
        task = note_to_task(event)
        assert task is not None
        assert task["task_type"] == "planning"
        assert task["role"] == "manager"

    def test_non_actionable_note(self) -> None:
        _, body = _load("note_non_actionable.json")
        event = parse_webhook(_headers("Note Hook"), body)
        assert note_to_task(event) is None


class TestSnapshotOutputs:
    """Snapshot-like checks: stable, deterministic output strings."""

    def test_cost_summary_snapshot(self) -> None:
        out = build_cost_summary(cost_usd=0.1234, task_count=3, model="claude-sonnet-4-6")
        # Strip dynamic bits — we just check stable structure.
        assert "bernstein-cost-annotation" in out
        assert "Tasks completed: 3" in out
        assert "$0.1234" in out
        assert "claude-sonnet-4-6" in out

    def test_status_body_running_snapshot(self) -> None:
        body = build_status_body(state="running", description="Working", target_url="https://x")
        assert body == {
            "state": "running",
            "description": "Working",
            "name": "bernstein / agent verification",
            "target_url": "https://x",
        }

    def test_status_body_success_snapshot(self) -> None:
        body = build_status_body(state="success", description="done")
        assert body["state"] == "success"
        assert body["name"] == "bernstein / agent verification"

    def test_conclusion_state_table_snapshot(self) -> None:
        # Pin the mapping table so accidental changes show up as test diffs.
        assert {
            "success": conclusion_to_state("success"),
            "failure": conclusion_to_state("failure"),
            "neutral": conclusion_to_state("neutral"),
            "cancelled": conclusion_to_state("cancelled"),
            "timed_out": conclusion_to_state("timed_out"),
            "action_required": conclusion_to_state("action_required"),
            "weirdo": conclusion_to_state("weirdo"),
        } == {
            "success": "success",
            "failure": "failed",
            "neutral": "success",
            "cancelled": "canceled",
            "timed_out": "failed",
            "action_required": "failed",
            "weirdo": "failed",
        }


class TestFixtureBytesParse:
    """Smoke test that every fixture parses without raising."""

    @pytest.mark.parametrize(
        "fixture,event",
        [
            ("merge_request_open.json", "Merge Request Hook"),
            ("merge_request_close.json", "Merge Request Hook"),
            ("merge_request_with_labels.json", "Merge Request Hook"),
            ("pipeline_failed.json", "Pipeline Hook"),
            ("pipeline_success.json", "Pipeline Hook"),
            ("note_mr.json", "Note Hook"),
            ("note_slash_command.json", "Note Hook"),
            ("note_non_actionable.json", "Note Hook"),
        ],
    )
    def test_parses(self, fixture: str, event: str) -> None:
        _, body = _load(fixture)
        evt = parse_webhook(_headers(event), body)
        assert evt.project_path
