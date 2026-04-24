"""Unit tests for the ``bernstein approve-tool`` / ``reject-tool`` commands (op-002)."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from bernstein.cli.commands.approval_cmd import approve_tool_cmd, reject_tool_cmd
from bernstein.core.approval.models import PendingApproval
from bernstein.core.approval.queue import ApprovalQueue


def _queue_at(workdir: Path) -> ApprovalQueue:
    return ApprovalQueue(base_dir=workdir / ".sdd" / "runtime" / "approvals")


def test_approve_tool_handles_empty_queue_gracefully(tmp_path: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(approve_tool_cmd, ["--workdir", str(tmp_path)])

    assert result.exit_code == 0
    assert "No pending approvals" in result.output


def test_approve_tool_resolves_oldest_by_default(tmp_path: Path) -> None:
    queue = _queue_at(tmp_path)
    oldest = queue.push(
        PendingApproval(session_id="S", agent_role="backend", tool_name="shell", tool_args={"command": "ls"})
    )
    queue.push(PendingApproval(session_id="S", agent_role="backend", tool_name="shell", tool_args={"command": "pwd"}))

    runner = CliRunner()
    result = runner.invoke(approve_tool_cmd, ["--workdir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    resolved = ApprovalQueue(base_dir=queue.base_dir).get_resolution(oldest.id)
    assert resolved is not None
    assert resolved.decision.value == "allow"


def test_approve_tool_with_always_promotes_rule(tmp_path: Path) -> None:
    queue = _queue_at(tmp_path)
    approval = queue.push(
        PendingApproval(
            session_id="S",
            agent_role="backend",
            tool_name="write_file",
            tool_args={"path": "src/lib/x.py"},
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        approve_tool_cmd,
        ["--workdir", str(tmp_path), "--always"],
    )

    assert result.exit_code == 0, result.output
    rules_file = tmp_path / ".bernstein" / "always_allow.yaml"
    assert rules_file.exists()
    # The resolution is recorded with decision=always.
    resolved = ApprovalQueue(base_dir=queue.base_dir).get_resolution(approval.id)
    assert resolved is not None
    assert resolved.decision.value == "always"


def test_approve_tool_with_id_selects_specific_approval(tmp_path: Path) -> None:
    queue = _queue_at(tmp_path)
    first = queue.push(
        PendingApproval(session_id="S", agent_role="backend", tool_name="shell", tool_args={"command": "ls"})
    )
    target = queue.push(
        PendingApproval(session_id="S", agent_role="backend", tool_name="shell", tool_args={"command": "pwd"})
    )

    runner = CliRunner()
    result = runner.invoke(
        approve_tool_cmd,
        ["--workdir", str(tmp_path), "--id", target.id],
    )

    assert result.exit_code == 0, result.output
    reopened = ApprovalQueue(base_dir=queue.base_dir)
    assert reopened.get_resolution(target.id) is not None
    # The non-targeted approval is untouched.
    assert reopened.get_resolution(first.id) is None


def test_approve_tool_unknown_id_fails(tmp_path: Path) -> None:
    queue = _queue_at(tmp_path)
    queue.push(PendingApproval(session_id="S", agent_role="backend", tool_name="shell", tool_args={"command": "ls"}))

    runner = CliRunner()
    result = runner.invoke(
        approve_tool_cmd,
        ["--workdir", str(tmp_path), "--id", "ap-unknown"],
    )

    assert result.exit_code == 1
    assert "ap-unknown" in result.output


def test_reject_tool_records_reject_decision(tmp_path: Path) -> None:
    queue = _queue_at(tmp_path)
    approval = queue.push(
        PendingApproval(session_id="S", agent_role="backend", tool_name="shell", tool_args={"command": "rm -rf /"})
    )

    runner = CliRunner()
    result = runner.invoke(reject_tool_cmd, ["--workdir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    resolved_file = queue.base_dir / f"{approval.id}.resolved.json"
    assert resolved_file.exists()
    assert json.loads(resolved_file.read_text())["decision"] == "reject"


def test_reject_tool_handles_empty_queue(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(reject_tool_cmd, ["--workdir", str(tmp_path)])
    assert result.exit_code == 0
    assert "No pending approvals" in result.output
