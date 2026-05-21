"""Integration test -- replay the audit chain after a Slack approval.

Per acceptance criterion: "replay over the exported chain reproduces
post-approval scheduler state byte-identically". The Slack driver only
controls the chain entry shape (approver, message_ts, decision,
tool_call_hash, prev_chain_digest); the audit chain itself is the
source of truth. This test exercises an end-to-end round trip:

1. The Slack bridge writes an approval entry into a real AuditLog.
2. We export the on-disk JSONL and re-verify the HMAC chain offline.
3. We replay the entries to reconstruct a minimal scheduler state
   (the set of approved tool-call hashes) and assert it matches the
   live scheduler state byte-for-byte.

The test deliberately stays self-contained: no Slack network, no
``slack_sdk`` install required (the bridge uses a synthetic SDK
inserted into ``sys.modules``).
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest


@dataclass(slots=True)
class _FakeWebClientResponse:
    data: dict[str, Any]

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


@dataclass(slots=True)
class _FakeWebClient:
    token: str = ""
    sent: list[dict[str, Any]] = field(default_factory=list)
    updated: list[dict[str, Any]] = field(default_factory=list)
    counter: int = 0

    async def chat_postMessage(self, **kwargs: Any) -> _FakeWebClientResponse:  # NOSONAR
        self.counter += 1
        ts = f"{self.counter}.{self.counter:06d}"
        self.sent.append({**kwargs, "ts": ts})
        return _FakeWebClientResponse({"ok": True, "ts": ts, "channel": kwargs.get("channel", "")})

    async def chat_update(self, **kwargs: Any) -> _FakeWebClientResponse:  # NOSONAR
        self.updated.append(kwargs)
        return _FakeWebClientResponse({"ok": True, "ts": kwargs.get("ts", "")})


@dataclass(slots=True)
class _FakeSocketModeClient:
    app_token: str = ""
    web_client: _FakeWebClient = field(default_factory=_FakeWebClient)
    handlers: list[Any] = field(default_factory=list)

    def socket_mode_request_listeners(self) -> list[Any]:
        return self.handlers

    async def connect(self) -> None:  # NOSONAR
        pass

    async def disconnect(self) -> None:  # NOSONAR
        pass

    async def close(self) -> None:  # NOSONAR
        pass


@dataclass
class _FakeSocketModeRequest:
    type: str
    envelope_id: str
    payload: dict[str, Any]


@dataclass(slots=True)
class _FakeSocketModeResponse:
    envelope_id: str


@pytest.fixture
def fake_slack(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wire a synthetic ``slack_sdk`` tree into ``sys.modules``."""
    web = types.ModuleType("slack_sdk.web.async_client")
    web.AsyncWebClient = _FakeWebClient  # type: ignore[attr-defined]

    socket = types.ModuleType("slack_sdk.socket_mode.aiohttp")
    socket.SocketModeClient = _FakeSocketModeClient  # type: ignore[attr-defined]

    socket_req = types.ModuleType("slack_sdk.socket_mode.request")
    socket_req.SocketModeRequest = _FakeSocketModeRequest  # type: ignore[attr-defined]

    socket_resp = types.ModuleType("slack_sdk.socket_mode.response")
    socket_resp.SocketModeResponse = _FakeSocketModeResponse  # type: ignore[attr-defined]

    socket_pkg = types.ModuleType("slack_sdk.socket_mode")
    socket_pkg.aiohttp = socket  # type: ignore[attr-defined]
    socket_pkg.request = socket_req  # type: ignore[attr-defined]
    socket_pkg.response = socket_resp  # type: ignore[attr-defined]

    pkg = types.ModuleType("slack_sdk")
    pkg.web = types.ModuleType("slack_sdk.web")  # type: ignore[attr-defined]
    pkg.web.async_client = web  # type: ignore[attr-defined]
    pkg.socket_mode = socket_pkg  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "slack_sdk", pkg)
    monkeypatch.setitem(sys.modules, "slack_sdk.web", pkg.web)
    monkeypatch.setitem(sys.modules, "slack_sdk.web.async_client", web)
    monkeypatch.setitem(sys.modules, "slack_sdk.socket_mode", socket_pkg)
    monkeypatch.setitem(sys.modules, "slack_sdk.socket_mode.aiohttp", socket)
    monkeypatch.setitem(sys.modules, "slack_sdk.socket_mode.request", socket_req)
    monkeypatch.setitem(sys.modules, "slack_sdk.socket_mode.response", socket_resp)


def _block_action_envelope(
    *,
    action_id: str,
    value: str,
    channel: str,
    user: str = "U7",
    message_ts: str = "100.000100",
) -> _FakeSocketModeRequest:
    return _FakeSocketModeRequest(
        type="interactive",
        envelope_id="env-2",
        payload={
            "type": "block_actions",
            "user": {"id": user},
            "channel": {"id": channel},
            "message": {"ts": message_ts},
            "actions": [
                {"action_id": action_id, "value": value, "block_id": "approval_block"},
            ],
        },
    )


def test_audit_chain_replay_reproduces_scheduler_state(
    fake_slack: None,
    tmp_path: Path,
) -> None:
    """Replay the JSONL chain and reconstruct the approval-set scheduler state."""
    from bernstein.core.chat.drivers.slack import SlackBridge
    from bernstein.core.security.audit import AuditLog

    audit_dir = tmp_path / "audit"
    audit = AuditLog(audit_dir=audit_dir, key=b"deterministic-integration-key")

    bridge = SlackBridge(
        token="xoxb-1",
        app_token="xapp-1",
        install_id="install-X",
        session_id="sess-X",
        worktree_id="wt-x",
        audit_log=audit,
        key_dir=tmp_path / "keys-X",
    )

    async def scenario() -> set[str]:
        await bridge.start()
        # Register and resolve three approvals.
        bridge.register_pending_approval(
            approval_id="t-1",
            tool_call_hash="hash-1",
            worktree_id="wt-x",
            thread_id="C42",
        )
        bridge.register_pending_approval(
            approval_id="t-2",
            tool_call_hash="hash-2",
            worktree_id="wt-x",
            thread_id="C42",
        )
        bridge.register_pending_approval(
            approval_id="t-3",
            tool_call_hash="hash-3",
            worktree_id="wt-x",
            thread_id="C42",
        )
        await bridge._handle_socket_mode_request(  # type: ignore[attr-defined]
            _block_action_envelope(
                action_id="approve",
                value="t-1",
                channel="C42",
                message_ts="1.111",
            ),
        )
        await bridge._handle_socket_mode_request(  # type: ignore[attr-defined]
            _block_action_envelope(
                action_id="reject",
                value="t-2",
                channel="C42",
                message_ts="2.222",
            ),
        )
        await bridge._handle_socket_mode_request(  # type: ignore[attr-defined]
            _block_action_envelope(
                action_id="approve",
                value="t-3",
                channel="C42",
                message_ts="3.333",
            ),
        )
        # Live scheduler state -- bridge exposes the approved set.
        live_state = set(bridge.approved_tool_call_hashes())
        await bridge.stop()
        return live_state

    live_state = asyncio.run(scenario())

    # 1. Re-verify the on-disk HMAC chain from scratch.
    fresh = AuditLog(audit_dir=audit_dir, key=b"deterministic-integration-key")
    valid, errors = fresh.verify()
    assert valid, f"chain failed re-verification: {errors}"

    # 2. Walk the JSONL files and reconstruct the approved set.
    reconstructed: set[str] = set()
    for log_path in sorted(audit_dir.glob("*.jsonl")):
        for raw in log_path.read_text(encoding="utf-8").splitlines():
            entry = json.loads(raw)
            if entry["event_type"] != "chat.slack.approval":
                continue
            details = entry["details"]
            if details["decision"] == "approve":
                reconstructed.add(details["tool_call_hash"])
            elif details["decision"] == "reject":
                reconstructed.discard(details["tool_call_hash"])

    assert reconstructed == live_state == {"hash-1", "hash-3"}
