"""Tests for AgentStatusNotification in bulletin board."""

from __future__ import annotations

from bernstein.core.bulletin import AgentStatusNotification, BulletinBoard


def test_agent_status_notification_roundtrip() -> None:
    """Notification serializes and deserializes cleanly."""
    orig = AgentStatusNotification(
        agent_id="agent-001",
        task_id="task-abc123",
        status="completed",
        summary="Refactored auth module",
        result={"files": ["auth.py", "config.py"]},
        usage_tokens=15000,
        usage_cost_usd=0.45,
        timestamp=1234567890.0,
    )
    data = orig.to_dict()
    assert data["status"] == "completed"
    assert data["usage_tokens"] == 15000

    restored = AgentStatusNotification.from_dict(data)
    assert restored.agent_id == "agent-001"
    assert restored.status == "completed"


def test_bulletin_post_and_consume_status_notification() -> None:
    """BulletinBoard stores and drains notifications."""
    board = BulletinBoard()
    notif = AgentStatusNotification(
        agent_id="agent-42",
        task_id="task-xyz",
        status="failed",
        summary="Tests failed on line 42",
    )
    board.post_status_notification(notif)

    drained = board.consume_status_notifications()
    assert len(drained) == 1
    assert drained[0].status == "failed"

    # Second drain returns empty (cleared)
    assert board.consume_status_notifications() == []
