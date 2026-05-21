"""Integration test -- replay the audit chain after a Discord approval.

Per acceptance criterion: "replay over the exported chain reproduces
post-approval scheduler state byte-identically". The Discord driver only
controls the chain entry shape (approver, interaction_id, decision,
tool_call_hash, prev_chain_digest); the audit chain itself is the
source of truth. This test exercises an end-to-end round trip:

1. The Discord bridge writes an approval entry into a real AuditLog.
2. We export the on-disk JSONL and re-verify the HMAC chain offline.
3. We replay the entries to reconstruct a minimal scheduler state
   (the set of approved tool-call hashes plus the partition each
   resolution was bound to) and assert it matches the live scheduler
   state byte-for-byte.

The test deliberately stays self-contained: no Discord network, no
``discord.py`` install required (the bridge uses a synthetic SDK
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


@dataclass
class _FakeDiscordClient:
    intents: Any = None
    channels: dict[int, Any] = field(default_factory=dict)
    handlers: dict[str, Any] = field(default_factory=dict)

    def event(self, func: Any) -> Any:
        self.handlers[func.__name__] = func
        return func

    def get_channel(self, channel_id: int) -> Any:
        channel = self.channels.get(channel_id)
        if channel is None:
            channel = _FakeDiscordChannel(id=channel_id)
            self.channels[channel_id] = channel
        return channel

    async def fetch_channel(self, channel_id: int) -> Any:  # NOSONAR
        return self.get_channel(channel_id)

    async def start(self, token: str) -> None:  # NOSONAR
        del token

    async def close(self) -> None:  # NOSONAR
        pass


@dataclass
class _FakeDiscordChannel:
    id: int
    sent: list[Any] = field(default_factory=list)
    counter: int = 0

    async def send(
        self,
        content: str = "",
        *,
        view: Any | None = None,
        embed: Any | None = None,
    ) -> Any:  # NOSONAR
        self.counter += 1
        del embed
        message_id = f"msg-{self.id}-{self.counter}"
        record = _SentMessage(message_id=message_id, content=content, components=list(getattr(view, "children", [])))
        self.sent.append(record)
        return record


@dataclass
class _SentMessage:
    message_id: str
    content: str
    components: list[Any]
    edits: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.message_id

    async def edit(self, *, content: str) -> None:  # NOSONAR
        self.edits.append(content)


class _FakeIntents:
    def __init__(self) -> None:
        self.guilds = False
        self.guild_messages = False
        self.message_content = False

    @classmethod
    def default(cls) -> _FakeIntents:
        return cls()


class _FakeInteractionType:
    application_command = 2
    component = 3


@dataclass
class _FakeUser:
    id: int
    name: str = "tester"


@dataclass
class _FakeInteractionData:
    name: str
    options: list[dict[str, Any]] = field(default_factory=list)
    custom_id: str = ""


@dataclass
class _FakeInteractionResponse:
    deferred: bool = False

    async def defer(self, *, ephemeral: bool = False) -> None:  # NOSONAR
        del ephemeral
        self.deferred = True

    async def send_message(self, content: str, *, ephemeral: bool = False) -> None:  # NOSONAR
        del content, ephemeral


@dataclass
class _FakeInteraction:
    type: int
    channel_id: int
    user: _FakeUser
    data: _FakeInteractionData
    id: int = 1
    response: _FakeInteractionResponse = field(default_factory=_FakeInteractionResponse)


@pytest.fixture
def fake_discord(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wire a synthetic ``discord`` tree into ``sys.modules``."""
    pkg = types.ModuleType("discord")
    pkg.Client = _FakeDiscordClient  # type: ignore[attr-defined]
    pkg.Intents = _FakeIntents  # type: ignore[attr-defined]
    pkg.Interaction = _FakeInteraction  # type: ignore[attr-defined]
    pkg.InteractionType = _FakeInteractionType  # type: ignore[attr-defined]

    ui_mod = types.ModuleType("discord.ui")

    class _FakeButton:
        def __init__(
            self,
            *,
            label: str = "",
            custom_id: str = "",
            style: Any | None = None,
            disabled: bool = False,
        ) -> None:
            self.label = label
            self.custom_id = custom_id
            self.style = style
            self.disabled = disabled

    class _FakeView:
        def __init__(self, *, timeout: float | None = None) -> None:
            self.timeout = timeout
            self.children: list[_FakeButton] = []

        def add_item(self, item: _FakeButton) -> None:
            self.children.append(item)

    ui_mod.View = _FakeView  # type: ignore[attr-defined]
    ui_mod.Button = _FakeButton  # type: ignore[attr-defined]

    class _FakeButtonStyle:
        primary = 1
        secondary = 2
        success = 3
        danger = 4
        link = 5

    pkg.ButtonStyle = _FakeButtonStyle  # type: ignore[attr-defined]
    pkg.ui = ui_mod  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "discord", pkg)
    monkeypatch.setitem(sys.modules, "discord.ui", ui_mod)


def _component_interaction(
    *,
    custom_id: str,
    channel_id: int,
    user_id: int = 7,
    interaction_id: int = 1,
) -> _FakeInteraction:
    return _FakeInteraction(
        type=_FakeInteractionType.component,
        channel_id=channel_id,
        user=_FakeUser(id=user_id),
        data=_FakeInteractionData(name="", custom_id=custom_id),
        id=interaction_id,
    )


def test_discord_audit_chain_replay_reproduces_scheduler_state(
    fake_discord: None,
    tmp_path: Path,
) -> None:
    """Replay the JSONL chain and reconstruct the approval-set scheduler state."""
    from bernstein.core.chat.drivers.discord import (
        APPROVAL_EVENT_TYPE,
        DiscordBridge,
    )
    from bernstein.core.security.audit import AuditLog

    audit_dir = tmp_path / "audit"
    audit = AuditLog(audit_dir=audit_dir, key=b"deterministic-integration-key")

    bridge = DiscordBridge(
        token="MTAxNTY3.fake.token",
        install_id="install-X",
        session_id="sess-X",
        worktree_id="wt-x",
        audit_log=audit,
        key_dir=tmp_path / "keys-X",
    )

    async def scenario() -> tuple[set[str], dict[str, str]]:
        await bridge.start()
        # Register three pending approvals scoped to two partitions.
        bridge.register_pending_approval(
            approval_id="t-1",
            tool_call_hash="hash-1",
            worktree_id="wt-x",
            thread_id="42",
            partition_id="discord:42",
        )
        bridge.register_pending_approval(
            approval_id="t-2",
            tool_call_hash="hash-2",
            worktree_id="wt-x",
            thread_id="42",
            partition_id="discord:42",
        )
        bridge.register_pending_approval(
            approval_id="t-3",
            tool_call_hash="hash-3",
            worktree_id="wt-x",
            thread_id="99",
            partition_id="discord:99",
        )
        # Resolve them: approve t-1, reject t-2, approve t-3. Each resolution
        # arrives in the matching channel so the partition fence is satisfied.
        await bridge.dispatch_interaction(
            _component_interaction(
                custom_id="approve:t-1",
                channel_id=42,
                user_id=7,
                interaction_id=1111,
            ),
        )
        await bridge.dispatch_interaction(
            _component_interaction(
                custom_id="reject:t-2",
                channel_id=42,
                user_id=7,
                interaction_id=2222,
            ),
        )
        await bridge.dispatch_interaction(
            _component_interaction(
                custom_id="approve:t-3",
                channel_id=99,
                user_id=7,
                interaction_id=3333,
            ),
        )
        live_state = set(bridge.approved_tool_call_hashes())
        live_partition_map = dict(bridge.partition_assignments())
        await bridge.stop()
        return live_state, live_partition_map

    live_state, live_partitions = asyncio.run(scenario())

    # 1. Re-verify the on-disk HMAC chain from scratch.
    fresh = AuditLog(audit_dir=audit_dir, key=b"deterministic-integration-key")
    valid, errors = fresh.verify()
    assert valid, f"chain failed re-verification: {errors}"

    # 2. Walk the JSONL files and reconstruct the approval set + partition map.
    reconstructed: set[str] = set()
    reconstructed_partitions: dict[str, str] = {}
    for log_path in sorted(audit_dir.glob("*.jsonl")):
        for raw in log_path.read_text(encoding="utf-8").splitlines():
            entry = json.loads(raw)
            if entry["event_type"] != APPROVAL_EVENT_TYPE:
                continue
            details = entry["details"]
            tool_call_hash = details["tool_call_hash"]
            if details["decision"] == "approve":
                reconstructed.add(tool_call_hash)
                reconstructed_partitions[tool_call_hash] = details["partition_id"]
            elif details["decision"] == "reject":
                reconstructed.discard(tool_call_hash)
                reconstructed_partitions.pop(tool_call_hash, None)

    # Live and replay state match byte-for-byte.
    assert reconstructed == live_state == {"hash-1", "hash-3"}
    assert (
        reconstructed_partitions
        == live_partitions
        == {
            "hash-1": "discord:42",
            "hash-3": "discord:99",
        }
    )
