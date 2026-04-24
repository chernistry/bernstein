"""Unit tests for the interactive tool-call approval queue (op-002).

Covers FIFO ordering, TTL expiry, atomic disk writes, concurrent resolve
idempotency, and the always-allow promotion path.
"""

from __future__ import annotations

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml

from bernstein.core.approval.models import (
    ApprovalDecision,
    ApprovalTimeoutError,
    PendingApproval,
)
from bernstein.core.approval.queue import (
    DEFAULT_TTL_SECONDS,
    ApprovalQueue,
    promote_to_always_allow,
)


def _push(queue: ApprovalQueue, *, tool: str = "shell", session: str = "S-1") -> PendingApproval:
    """Helper: push a fresh pending approval onto *queue*."""
    approval = PendingApproval(
        session_id=session,
        agent_role="backend",
        tool_name=tool,
        tool_args={"command": f"echo {tool}"},
        ttl_seconds=30,
    )
    return queue.push(approval)


def test_push_writes_pending_json_atomically(tmp_path: Path) -> None:
    queue = ApprovalQueue(base_dir=tmp_path)
    approval = _push(queue)

    written = tmp_path / f"{approval.id}.json"
    assert written.exists(), "push() must persist the pending JSON file"
    data = json.loads(written.read_text())
    assert data["id"] == approval.id
    assert data["tool_name"] == "shell"
    assert data["tool_args"] == {"command": "echo shell"}


def test_list_pending_returns_fifo_order(tmp_path: Path) -> None:
    queue = ApprovalQueue(base_dir=tmp_path)
    first = _push(queue, tool="a")
    time.sleep(0.001)
    second = _push(queue, tool="b")
    time.sleep(0.001)
    third = _push(queue, tool="c")

    pending = queue.list_pending()
    assert [a.id for a in pending] == [first.id, second.id, third.id]


def test_list_pending_filters_by_session(tmp_path: Path) -> None:
    queue = ApprovalQueue(base_dir=tmp_path)
    _push(queue, session="S-1")
    target = _push(queue, session="S-2")

    only_s2 = queue.list_pending(session_id="S-2")
    assert [a.id for a in only_s2] == [target.id]


def test_resolve_records_decision_and_removes_pending_file(tmp_path: Path) -> None:
    queue = ApprovalQueue(base_dir=tmp_path)
    approval = _push(queue)

    resolution = queue.resolve(approval.id, ApprovalDecision.ALLOW, reason="ok")

    assert resolution.decision is ApprovalDecision.ALLOW
    assert resolution.reason == "ok"
    assert not (tmp_path / f"{approval.id}.json").exists()
    resolved_file = tmp_path / f"{approval.id}.resolved.json"
    assert resolved_file.exists()
    assert json.loads(resolved_file.read_text())["decision"] == "allow"


def test_concurrent_resolve_is_idempotent(tmp_path: Path) -> None:
    queue = ApprovalQueue(base_dir=tmp_path)
    approval = _push(queue)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(queue.resolve, approval.id, ApprovalDecision.ALLOW, reason=f"r{i}") for i in range(4)]
        results = [f.result() for f in futures]

    # Every call must return the SAME resolution (first writer wins).
    assert len({r.resolved_at for r in results}) == 1
    assert len({r.reason for r in results}) == 1


def test_wait_for_returns_resolution_when_resolved(tmp_path: Path) -> None:
    queue = ApprovalQueue(base_dir=tmp_path)
    approval = _push(queue)

    async def scenario() -> None:
        async def resolver() -> None:
            await asyncio.sleep(0.01)
            queue.resolve(approval.id, ApprovalDecision.ALLOW)

        resolver_task = asyncio.create_task(resolver())
        resolution = await queue.wait_for(approval.id, timeout_seconds=2.0)
        await resolver_task
        assert resolution.decision is ApprovalDecision.ALLOW

    asyncio.run(scenario())


def test_wait_for_times_out_and_rejects(tmp_path: Path) -> None:
    queue = ApprovalQueue(base_dir=tmp_path)
    approval = queue.push(
        PendingApproval(
            session_id="S",
            agent_role="backend",
            tool_name="shell",
            tool_args={"command": "rm -rf /"},
            ttl_seconds=1,
        )
    )

    async def scenario() -> None:
        with pytest.raises(ApprovalTimeoutError) as excinfo:
            await queue.wait_for(approval.id, timeout_seconds=0.05)
        # Error message must mention the approval id and tool name.
        assert approval.id in str(excinfo.value)
        assert "shell" in str(excinfo.value)

    asyncio.run(scenario())

    # After the timeout, a REJECT resolution is persisted so other
    # resolvers observe the terminal state.
    resolution = queue.get_resolution(approval.id)
    assert resolution is not None
    assert resolution.decision is ApprovalDecision.REJECT


def test_evict_expired_rejects_old_approvals(tmp_path: Path) -> None:
    queue = ApprovalQueue(base_dir=tmp_path)
    approval = queue.push(
        PendingApproval(
            session_id="S",
            agent_role="backend",
            tool_name="shell",
            tool_args={"command": "ls"},
            ttl_seconds=1,
        )
    )

    future = time.time() + 3600
    evicted = queue.evict_expired(now=future)

    assert approval.id in evicted
    assert queue.get_resolution(approval.id) is not None
    assert queue.get_resolution(approval.id).decision is ApprovalDecision.REJECT  # type: ignore[union-attr]


def test_queue_rehydrates_from_disk(tmp_path: Path) -> None:
    q1 = ApprovalQueue(base_dir=tmp_path)
    approval = _push(q1)

    q2 = ApprovalQueue(base_dir=tmp_path)
    pending = q2.list_pending()
    assert [a.id for a in pending] == [approval.id]


def test_default_ttl_constant_is_ten_minutes() -> None:
    assert DEFAULT_TTL_SECONDS == 600


def test_promote_to_always_allow_writes_rule(tmp_path: Path) -> None:
    queue = ApprovalQueue(base_dir=tmp_path / ".sdd/runtime/approvals")
    approval = queue.push(
        PendingApproval(
            session_id="S",
            agent_role="backend",
            tool_name="write_file",
            tool_args={"path": "src/app/main.py"},
            ttl_seconds=60,
        )
    )

    target = promote_to_always_allow(approval, workdir=tmp_path)

    assert target.exists()
    parsed = yaml.safe_load(target.read_text())
    assert "always_allow" in parsed
    rules = parsed["always_allow"]
    assert any(rule["tool"] == "write_file" and rule["input_pattern"] == "src/app/main.py" for rule in rules)


def test_resolve_unknown_id_raises(tmp_path: Path) -> None:
    queue = ApprovalQueue(base_dir=tmp_path)
    with pytest.raises(KeyError):
        queue.resolve("ap-doesnotexist", ApprovalDecision.ALLOW)
