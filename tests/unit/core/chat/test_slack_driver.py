"""Unit tests for the Slack bidirectional driver.

Covers the acceptance criteria from issue #1794:

* slash dispatch
* button-callback decode
* edit-debounce (rate-limit guard)
* missing-SDK error path
* outbound message signature verification
* worktree-pinned approval scope (cross-worktree resolution rejection)
* audit-chain entry shape for approvals
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

from bernstein.core.chat.bridge import ChatMessage, PendingApproval

# ---------------------------------------------------------------------------
# Fake slack_sdk packages
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _FakeWebClientResponse:
    """Mimics the dict-like response shape from slack_sdk."""

    data: dict[str, Any]

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


@dataclass(slots=True)
class _FakeWebClient:
    """Async Slack web client recording every call."""

    token: str = ""
    sent: list[dict[str, Any]] = field(default_factory=list)
    updated: list[dict[str, Any]] = field(default_factory=list)
    counter: int = 0

    async def chat_postMessage(self, **kwargs: Any) -> _FakeWebClientResponse:
        self.counter += 1
        ts = f"{self.counter}.{self.counter:06d}"
        self.sent.append({**kwargs, "ts": ts})
        return _FakeWebClientResponse(
            data={"ok": True, "ts": ts, "channel": kwargs.get("channel", "")},
        )

    async def chat_update(self, **kwargs: Any) -> _FakeWebClientResponse:
        self.updated.append(kwargs)
        return _FakeWebClientResponse(
            data={"ok": True, "ts": kwargs.get("ts", ""), "channel": kwargs.get("channel", "")},
        )


@dataclass(slots=True)
class _FakeSocketModeClient:
    """SocketModeClient stub: capture handlers + lifecycle."""

    app_token: str = ""
    web_client: _FakeWebClient = field(default_factory=_FakeWebClient)
    handlers: list[Any] = field(default_factory=list)
    connected: bool = False
    closed: bool = False

    def socket_mode_request_listeners(self) -> list[Any]:
        # python-slack-sdk exposes the listeners through this attribute on
        # the real client. The fake exposes the same list for inspection.
        return self.handlers

    async def connect(self) -> None:  # NOSONAR
        self.connected = True

    async def disconnect(self) -> None:  # NOSONAR
        self.connected = False

    async def close(self) -> None:  # NOSONAR
        self.closed = True


@dataclass
class _FakeSocketModeRequest:
    """A captured Socket Mode envelope mirroring slack_sdk's shape."""

    type: str
    envelope_id: str
    payload: dict[str, Any]


@dataclass(slots=True)
class _FakeSocketModeResponse:
    envelope_id: str


@pytest.fixture
def fake_slack(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a synthetic ``slack_sdk`` tree in ``sys.modules``."""
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


# ---------------------------------------------------------------------------
# Constructor / token validation
# ---------------------------------------------------------------------------


def test_slack_empty_bot_token_rejected() -> None:
    """An empty bot token must be rejected eagerly, no network needed."""
    from bernstein.core.chat.drivers.slack import SlackBridge

    with pytest.raises(ValueError):
        SlackBridge(token="", app_token="xapp-1")


def test_slack_empty_app_token_rejected() -> None:
    """An empty app token must be rejected eagerly."""
    from bernstein.core.chat.drivers.slack import SlackBridge

    with pytest.raises(ValueError):
        SlackBridge(token="xoxb-1", app_token="")


# ---------------------------------------------------------------------------
# Missing-SDK error path
# ---------------------------------------------------------------------------


def test_slack_start_without_sdk_raises_structured_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``start()`` must raise SlackDependencyError when slack_sdk is missing."""
    from bernstein.core.chat.drivers.slack import SlackBridge, SlackDependencyError

    # Force the import path to fail even if slack_sdk happens to be on disk.
    for modname in list(sys.modules):
        if modname == "slack_sdk" or modname.startswith("slack_sdk."):
            monkeypatch.delitem(sys.modules, modname, raising=False)
    monkeypatch.setitem(sys.modules, "slack_sdk", None)  # type: ignore[arg-type]

    bridge = SlackBridge(token="xoxb-1", app_token="xapp-1")
    with pytest.raises(SlackDependencyError) as excinfo:
        asyncio.run(bridge.start())
    assert "bernstein[slack]" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Slash command dispatch
# ---------------------------------------------------------------------------


def test_slack_slash_command_routes_to_registered_handler(fake_slack: None) -> None:
    """A ``/bernstein run "Add JWT auth"`` slash command must dispatch."""
    from bernstein.core.chat.drivers.slack import SlackBridge

    received: list[ChatMessage] = []

    async def handler(msg: ChatMessage) -> None:  # NOSONAR
        received.append(msg)

    bridge = SlackBridge(token="xoxb-1", app_token="xapp-1")
    bridge.on_command("run", handler)

    async def scenario() -> None:
        await bridge.start()
        envelope = _slash_envelope(text='run "Add JWT auth"', channel="C42", user="U7")
        await bridge._handle_socket_mode_request(envelope)  # type: ignore[attr-defined]
        await bridge.stop()

    asyncio.run(scenario())
    assert len(received) == 1
    assert received[0].thread_id == "C42"
    assert received[0].user_id == "U7"
    assert received[0].args == ['"Add', "JWT", 'auth"']


def test_slack_slash_command_ignores_unknown_subcommand(fake_slack: None) -> None:
    """Subcommands without a registered handler must be ignored, not raise."""
    from bernstein.core.chat.drivers.slack import SlackBridge

    bridge = SlackBridge(token="xoxb-1", app_token="xapp-1")

    async def scenario() -> None:
        await bridge.start()
        envelope = _slash_envelope(text="nope", channel="C42", user="U7")
        # Must not raise even though no handler is registered.
        await bridge._handle_socket_mode_request(envelope)  # type: ignore[attr-defined]
        await bridge.stop()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Button decode (approve / reject)
# ---------------------------------------------------------------------------


def test_slack_button_decode_round_trip(fake_slack: None) -> None:
    """A block-action payload must decode into (thread, approval_id, decision)."""
    from bernstein.core.chat.drivers.slack import SlackBridge

    decisions: list[tuple[str, str, str]] = []

    async def button(thread_id: str, approval_id: str, decision: str) -> None:  # NOSONAR
        decisions.append((thread_id, approval_id, decision))

    bridge = SlackBridge(token="xoxb-1", app_token="xapp-1")
    bridge.on_button(button)

    async def scenario() -> None:
        await bridge.start()
        await bridge._handle_socket_mode_request(  # type: ignore[attr-defined]
            _block_action_envelope(action_id="approve", value="t-42", channel="C99"),
        )
        await bridge._handle_socket_mode_request(  # type: ignore[attr-defined]
            _block_action_envelope(action_id="reject", value="t-43", channel="C99"),
        )
        await bridge.stop()

    asyncio.run(scenario())
    assert decisions == [("C99", "t-42", "approve"), ("C99", "t-43", "reject")]


def test_slack_button_decode_ignores_unknown_action(fake_slack: None) -> None:
    """Block actions with unknown ``action_id`` must not call the handler."""
    from bernstein.core.chat.drivers.slack import SlackBridge

    decisions: list[tuple[str, str, str]] = []

    async def button(thread_id: str, approval_id: str, decision: str) -> None:  # NOSONAR
        decisions.append((thread_id, approval_id, decision))

    bridge = SlackBridge(token="xoxb-1", app_token="xapp-1")
    bridge.on_button(button)

    async def scenario() -> None:
        await bridge.start()
        await bridge._handle_socket_mode_request(  # type: ignore[attr-defined]
            _block_action_envelope(action_id="snooze", value="t-42", channel="C99"),
        )
        await bridge.stop()

    asyncio.run(scenario())
    assert decisions == []


# ---------------------------------------------------------------------------
# push_approval renders blocks with attestation footer
# ---------------------------------------------------------------------------


def test_slack_push_approval_renders_attested_blocks(fake_slack: None) -> None:
    """push_approval must post a message with Approve/Reject block actions."""
    from bernstein.core.chat.drivers.slack import SlackBridge

    bridge = SlackBridge(
        token="xoxb-1",
        app_token="xapp-1",
        install_id="install-test",
        session_id="sess-1",
    )

    async def scenario() -> list[dict[str, Any]]:
        await bridge.start()
        await bridge.push_approval(
            PendingApproval(
                approval_id="t-7",
                title="Approve shell command?",
                body="rm -rf /tmp/scratch",
                thread_id="C42",
            ),
        )
        client = bridge._web  # type: ignore[attr-defined]
        sent = list(client.sent)
        await bridge.stop()
        return sent

    sent = asyncio.run(scenario())
    assert len(sent) == 1
    blocks = sent[0].get("blocks") or []
    assert blocks, "push_approval must attach Block Kit blocks"
    action_ids: list[str] = []
    action_values: list[str] = []
    for block in blocks:
        if block.get("type") == "actions":
            for element in block.get("elements", []):
                action_ids.append(element.get("action_id", ""))
                action_values.append(element.get("value", ""))
    assert action_ids == ["approve", "reject"]
    assert action_values == ["t-7", "t-7"]


# ---------------------------------------------------------------------------
# Edit debounce (rate limit guard)
# ---------------------------------------------------------------------------


def test_slack_edit_debounce_collapses_rapid_updates(fake_slack: None) -> None:
    """Five rapid edits to the same ts must collapse into one chat.update."""
    from bernstein.core.chat.drivers.slack import SlackBridge

    bridge = SlackBridge(token="xoxb-1", app_token="xapp-1")

    async def scenario() -> list[dict[str, Any]]:
        await bridge.start()
        client = bridge._web  # type: ignore[attr-defined]
        for i in range(5):
            await bridge.edit_message("C42", "100.001", f"tick {i}")
        edits = list(client.updated)
        await bridge.stop()
        return edits

    edits = asyncio.run(scenario())
    assert len(edits) == 1, f"expected a single throttled edit, got {edits}"
    assert edits[0]["text"] == "tick 0"


# ---------------------------------------------------------------------------
# Outbound message signing
# ---------------------------------------------------------------------------


def test_slack_send_message_includes_signed_envelope(
    fake_slack: None,
    tmp_path: Path,
) -> None:
    """send_message must include a signed envelope identifying the install."""
    from bernstein.core.chat.drivers.slack import SlackBridge, verify_chat_signature

    bridge = SlackBridge(
        token="xoxb-1",
        app_token="xapp-1",
        install_id="install-A",
        session_id="sess-1",
        key_dir=tmp_path / "keys-A",
    )

    async def scenario() -> dict[str, Any]:
        await bridge.start()
        client = bridge._web  # type: ignore[attr-defined]
        await bridge.send_message("C42", "hello operator")
        await bridge.stop()
        return client.sent[0]

    sent = asyncio.run(scenario())
    # Header text always contains the human-visible content.
    assert "hello operator" in sent["text"]
    # The bridge exposes its public key so verifiers can confirm authenticity.
    public_pem = bridge.public_key_pem()
    metadata = sent.get("metadata")
    assert metadata is not None, "outbound message must carry an attestation envelope"
    payload = metadata["event_payload"]
    assert payload["install_id"] == "install-A"
    assert payload["session_id"] == "sess-1"
    assert "content_hash" in payload
    assert "signature" in payload
    assert verify_chat_signature(
        install_id="install-A",
        session_id="sess-1",
        content="hello operator",
        signature=payload["signature"],
        public_key_pem=public_pem,
    )


def test_slack_signature_rejects_foreign_install(fake_slack: None, tmp_path: Path) -> None:
    """A signature minted by install-B must not verify against install-A's key."""
    from bernstein.core.chat.drivers.slack import SlackBridge, verify_chat_signature

    bridge_a = SlackBridge(
        token="xoxb-1",
        app_token="xapp-1",
        install_id="install-A",
        session_id="sess-1",
        key_dir=tmp_path / "keys-A",
    )
    bridge_b = SlackBridge(
        token="xoxb-2",
        app_token="xapp-2",
        install_id="install-B",
        session_id="sess-2",
        key_dir=tmp_path / "keys-B",
    )

    async def scenario() -> tuple[str, str]:
        await bridge_a.start()
        await bridge_b.start()
        client_b = bridge_b._web  # type: ignore[attr-defined]
        await bridge_b.send_message("C42", "spoofed")
        sig = client_b.sent[0]["metadata"]["event_payload"]["signature"]
        pub_a = bridge_a.public_key_pem()
        await bridge_a.stop()
        await bridge_b.stop()
        return sig, pub_a

    foreign_signature, public_key_a = asyncio.run(scenario())
    # Verifier must reject when the public key does not match the signer.
    assert not verify_chat_signature(
        install_id="install-B",
        session_id="sess-2",
        content="spoofed",
        signature=foreign_signature,
        public_key_pem=public_key_a,
    )


# ---------------------------------------------------------------------------
# Audit-chain entry shape for approvals
# ---------------------------------------------------------------------------


def test_slack_push_approval_audit_chain_entry_shape(
    fake_slack: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolving an approval emits a chained audit entry with the AC fields."""
    from bernstein.core.chat.drivers.slack import SlackBridge
    from bernstein.core.security.audit import AuditLog

    audit = AuditLog(audit_dir=tmp_path / "audit", key=b"deterministic-test-key")

    bridge = SlackBridge(
        token="xoxb-1",
        app_token="xapp-1",
        install_id="install-A",
        session_id="sess-1",
        worktree_id="wt-a",
        audit_log=audit,
        key_dir=tmp_path / "keys-A",
    )

    async def scenario() -> None:
        await bridge.start()
        # Register a pending approval keyed by approval_id with a known
        # tool_call_hash; the driver will read this on resolution.
        bridge.register_pending_approval(
            approval_id="t-7",
            tool_call_hash="hash-of-tool-call",
            worktree_id="wt-a",
            thread_id="C42",
        )
        # Trigger a Slack button press: approver U7 picks Approve.
        await bridge._handle_socket_mode_request(  # type: ignore[attr-defined]
            _block_action_envelope(
                action_id="approve",
                value="t-7",
                channel="C42",
                user="U7",
                message_ts="200.000200",
            ),
        )
        await bridge.stop()

    asyncio.run(scenario())

    entries = audit.query(event_type="chat.slack.approval")
    assert len(entries) == 1, f"expected one chained approval entry, got {entries}"
    entry = entries[0]
    details = entry.details
    assert details["approver"] == "U7"
    assert details["decision"] == "approve"
    assert details["tool_call_hash"] == "hash-of-tool-call"
    assert details["message_ts"] == "200.000200"
    assert details["worktree_id"] == "wt-a"
    # The chained digest links to the previous chain digest in the AuditLog.
    assert entry.prev_hmac
    assert entry.hmac
    # Chain replay rebuilds the post-approval scheduler state byte-identically.
    valid, errors = audit.verify()
    assert valid, errors


# ---------------------------------------------------------------------------
# Worktree pinning -- cross-worktree resolution rejection
# ---------------------------------------------------------------------------


def test_slack_cross_worktree_approval_rejected(
    fake_slack: None,
    tmp_path: Path,
) -> None:
    """An /approve from wt-a must refuse pending approvals bound to wt-b."""
    from bernstein.core.chat.drivers.slack import (
        CrossWorktreeApprovalError,
        SlackBridge,
    )
    from bernstein.core.security.audit import AuditLog

    audit = AuditLog(audit_dir=tmp_path / "audit", key=b"deterministic-test-key")

    bridge = SlackBridge(
        token="xoxb-1",
        app_token="xapp-1",
        install_id="install-A",
        session_id="sess-1",
        worktree_id="wt-a",
        audit_log=audit,
        key_dir=tmp_path / "keys-A",
    )

    async def scenario() -> bool:
        await bridge.start()
        # Pending approval is bound to a worker on wt-b.
        bridge.register_pending_approval(
            approval_id="t-7",
            tool_call_hash="hash-of-tool-call",
            worktree_id="wt-b",
            thread_id="C42",
        )
        try:
            await bridge._handle_socket_mode_request(  # type: ignore[attr-defined]
                _block_action_envelope(
                    action_id="approve",
                    value="t-7",
                    channel="C42",
                    user="U7",
                    message_ts="200.000200",
                ),
            )
        except CrossWorktreeApprovalError:
            raised = True
        else:
            raised = False
        await bridge.stop()
        return raised

    raised = asyncio.run(scenario())
    assert raised, "approve from a different worktree must be rejected"
    # The audit chain must not have a resolved-approval entry for the rejected
    # cross-worktree resolution.
    resolved = audit.query(event_type="chat.slack.approval")
    assert resolved == []
    # But the cross-worktree rejection itself is logged so operators can audit
    # the attempted bypass.
    rejected = audit.query(event_type="chat.slack.approval_rejected")
    assert len(rejected) == 1
    assert rejected[0].details["reason"] == "cross_worktree"


# ---------------------------------------------------------------------------
# Envelope helpers
# ---------------------------------------------------------------------------


def _slash_envelope(*, text: str, channel: str, user: str) -> _FakeSocketModeRequest:
    return _FakeSocketModeRequest(
        type="slash_commands",
        envelope_id="env-1",
        payload={
            "command": "/bernstein",
            "text": text,
            "channel_id": channel,
            "user_id": user,
            "trigger_id": "trig-1",
        },
    )


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


# Re-export for friendly import lines in other tests.
__all__ = [
    "_block_action_envelope",
    "_slash_envelope",
    "fake_slack",
]


# Module-level smoke check for json round-trip helpers.
def test_envelope_payload_round_trip_json() -> None:
    """Envelope payload must be plain JSON-serialisable."""
    env = _slash_envelope(text="run", channel="C1", user="U1")
    assert json.loads(json.dumps(env.payload))
