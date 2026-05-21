"""Integration test: stall -> receipt -> audit chain -> verification roundtrip.

This exercises the operator-facing supervisor surface end-to-end against
a stubbed orchestrator runtime tree:

1. Lay down a synthetic ``.sdd/runtime/`` (agents.json, heartbeats,
   failures) that the upstream detectors would normally populate.
2. Build the aggregator snapshot via
   :func:`bernstein.core.orchestration.supervisor_aggregator.aggregator_snapshot`.
3. Assemble + sign an escalation receipt for one stuck worker.
4. Round-trip the receipt through JSON.
5. Verify the receipt against the install public key.

The test deliberately avoids spawning real agent processes - the
supervisor surface aggregates files the detectors emit, so the contract
under test is the *aggregation + receipt* layer, not the detectors
themselves.
"""

from __future__ import annotations

import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bernstein.core.orchestration.supervisor_aggregator import (
    aggregator_snapshot,
    load_recent_failures,
)
from bernstein.core.orchestration.supervisor_receipt import (
    IdentityTokens,
    StallReason,
    assemble_receipt,
    receipt_from_dict,
    receipt_to_dict,
    sign_receipt,
    verify_receipt,
)


def _seed_runtime_tree(workdir: Path, *, session_id: str, role: str = "manager") -> None:
    """Populate a minimal ``.sdd/runtime/`` tree that mimics a stalled session."""
    runtime = workdir / ".sdd" / "runtime"
    (runtime / "heartbeats").mkdir(parents=True, exist_ok=True)
    (runtime / "failures").mkdir(parents=True, exist_ok=True)
    (runtime / "spawn_supervisor").mkdir(parents=True, exist_ok=True)

    # agents.json: one live worker, role=manager, with a stalled diagnostic.
    agents_doc = {
        "agents": [
            {
                "id": session_id,
                "role": role,
                "status": "working",
                "task_ids": ["t-1"],
                "worker_id": "abc123def456",
                "worktree_id": "wt-A",
                "stalled_manager": {
                    "kind": "stalled_manager",
                    "session_id": session_id,
                    "runtime_s": 120.0,
                    "hook_event_count": 12,
                    "detected_at": 1700000000.0,
                },
            }
        ]
    }
    (runtime / "agents.json").write_text(json.dumps(agents_doc, sort_keys=True))

    # Heartbeat - aged so the aggregator flags the session as stuck.
    heartbeat = {
        "timestamp": 1700000000.0 - 600.0,  # 10 min old
        "phase": "implementing",
        "progress_pct": 0,
    }
    (runtime / "heartbeats" / f"{session_id}.json").write_text(json.dumps(heartbeat, sort_keys=True))

    # One failure record so the aggregator has something to feed into the
    # receipt's audit_entries slice.
    failure = {
        "kind": "stalled_manager",
        "session_id": session_id,
        "runtime_s": 120.0,
        "hook_event_count": 12,
        "detected_at": 1700000000.0,
    }
    (runtime / "failures" / f"manager-stalled-{session_id}.json").write_text(json.dumps(failure, sort_keys=True))


def test_stall_to_receipt_to_verification_roundtrip(tmp_path: Path) -> None:
    """End-to-end: stall artefacts -> aggregator -> signed receipt -> verify."""
    session_id = "sess-mgr-001"
    _seed_runtime_tree(tmp_path, session_id=session_id)

    # The orchestrator timestamp the heartbeat ages against is supplied
    # explicitly so the assertion is deterministic.
    snapshot = aggregator_snapshot(
        tmp_path,
        now=1700000000.0,
        heartbeat_stale_s=120.0,
    )
    assert snapshot.stuck_count >= 1
    [row] = [w for w in snapshot.workers if w.session_id == session_id]
    assert row.is_stuck
    assert row.stall_reason == StallReason.MANAGER_NO_CHILDREN

    failures = load_recent_failures(tmp_path, session_id)
    audit_entries = [
        {
            "event_type": str(rec.get("kind", "")),
            "session_id": session_id,
            "details": rec,
        }
        for rec in failures
    ]

    signing_key = Ed25519PrivateKey.generate()
    identity = IdentityTokens(
        install_rev="abc1234567890def",
        keyid="dummy-keyid-0123456789abcdef",
        run_id="run-1",
    )
    receipt = assemble_receipt(
        worker_id=row.worker_id,
        worktree_id=row.worktree_id,
        session_id=session_id,
        stall_reason=row.stall_reason,
        audit_entries=audit_entries,
        identity=identity,
        prev_chain_digest="0" * 64,
        respawn_budget_remaining=row.respawn_budget_remaining,
    )
    signed = sign_receipt(receipt, signing_key=signing_key)

    # JSON roundtrip - the receipt is meant to be portable.
    wire = json.dumps(receipt_to_dict(signed), sort_keys=True)
    reloaded = receipt_from_dict(json.loads(wire))

    result = verify_receipt(reloaded, signing_key.public_key())
    assert result.ok, result.errors


def test_receipt_recommended_action_matches_aggregator_row(tmp_path: Path) -> None:
    """The aggregator and the receipt produce the same recommendation.

    Both layers consume the deterministic ``recommend_action`` so a
    receipt the operator escalates carries the exact label the dashboard
    showed - no surprise downgrades between glance and command.
    """
    session_id = "sess-mgr-002"
    _seed_runtime_tree(tmp_path, session_id=session_id)
    snapshot = aggregator_snapshot(tmp_path, now=1700000000.0, heartbeat_stale_s=120.0)
    [row] = [w for w in snapshot.workers if w.session_id == session_id]

    failures = load_recent_failures(tmp_path, session_id)
    audit_entries = [
        {
            "event_type": str(rec.get("kind", "")),
            "session_id": session_id,
            "details": rec,
        }
        for rec in failures
    ]
    receipt = assemble_receipt(
        worker_id=row.worker_id,
        worktree_id=row.worktree_id,
        session_id=session_id,
        stall_reason=row.stall_reason,
        audit_entries=audit_entries,
        identity=IdentityTokens(keyid="x", install_rev="x", run_id=""),
        prev_chain_digest="0" * 64,
        respawn_budget_remaining=row.respawn_budget_remaining,
    )
    assert receipt.recommended_action == row.recommended_action
