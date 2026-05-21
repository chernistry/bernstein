"""Unit tests for the Discord bidirectional driver.

Covers the acceptance criteria from issue #1795:

* slash command dispatch via Discord interaction events
* approve/reject button decode (custom_id ``approve:<id>`` / ``reject:<id>``)
* edit-debounce (rate-limit guard)
* missing-SDK error path
* outbound message signature verification
* worktree-pinned approval scope (cross-worktree resolution rejection)
* audit-chain entry shape for approvals (covers the
  ``(approver, interaction_id, decision, tool_call_hash, prev_chain_digest)``
  AC tuple)
* channel-scoped scheduling fence -- tasks emitted from one channel
  cannot land on workers attached to a different channel.
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
# Fake discord.py module surface
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _FakeDiscordMessage:
    """Recorded sent/edited message."""

    channel_id: str
    content: str
    components: list[Any] = field(default_factory=list)
    message_id: str = ""


@dataclass(slots=True)
class _FakeDiscordChannel:
    """Channel handle returned by ``client.get_channel``/``fetch_channel``."""

    id: int
    sent: list[_FakeDiscordMessage] = field(default_factory=list)
    counter: int = 0

    async def send(
        self,
        content: str = "",
        *,
        view: Any | None = None,
        embed: Any | None = None,
    ) -> _FakeDiscordSentMessage:  # NOSONAR
        self.counter += 1
        message_id = f"msg-{self.id}-{self.counter}"
        components: list[Any] = []
        if view is not None:
            components = list(getattr(view, "children", []))
        record = _FakeDiscordMessage(
            channel_id=str(self.id),
            content=content,
            components=components,
            message_id=message_id,
        )
        self.sent.append(record)
        del embed  # unused in the fake; placeholder for SDK parity.
        return _FakeDiscordSentMessage(id=message_id, channel=self)


@dataclass(slots=True)
class _FakeDiscordSentMessage:
    """Return value of ``channel.send``."""

    id: str
    channel: _FakeDiscordChannel
    edits: list[str] = field(default_factory=list)

    async def edit(self, *, content: str) -> None:  # NOSONAR
        self.edits.append(content)


@dataclass
class _FakeDiscordClient:
    """``discord.Client`` stand-in capturing handlers and lifecycle."""

    intents: Any = None
    channels: dict[int, _FakeDiscordChannel] = field(default_factory=dict)
    started: bool = False
    closed: bool = False
    handlers: dict[str, Any] = field(default_factory=dict)

    def event(self, func: Any) -> Any:
        """``@client.event`` decorator -- store the coroutine by name."""
        self.handlers[func.__name__] = func
        return func

    def get_channel(self, channel_id: int) -> _FakeDiscordChannel:
        return self.channels.setdefault(channel_id, _FakeDiscordChannel(id=channel_id))

    async def fetch_channel(self, channel_id: int) -> _FakeDiscordChannel:  # NOSONAR
        return self.get_channel(channel_id)

    async def start(self, token: str) -> None:  # NOSONAR
        del token
        self.started = True

    async def close(self) -> None:  # NOSONAR
        self.closed = True

    async def login(self, token: str) -> None:  # NOSONAR
        del token

    async def connect(self) -> None:  # NOSONAR
        self.started = True


@dataclass(slots=True)
class _FakeInteractionResponse:
    """Recorded ``interaction.response`` activity for assertions."""

    deferred: bool = False
    sent_messages: list[str] = field(default_factory=list)

    async def defer(self, *, ephemeral: bool = False) -> None:  # NOSONAR
        del ephemeral
        self.deferred = True

    async def send_message(self, content: str, *, ephemeral: bool = False) -> None:  # NOSONAR
        del ephemeral
        self.sent_messages.append(content)


@dataclass(slots=True)
class _FakeUser:
    id: int
    name: str = "tester"


@dataclass(slots=True)
class _FakeGuild:
    id: int


@dataclass(slots=True)
class _FakeInteractionData:
    """Mirror of ``interaction.data`` for an application command."""

    name: str
    options: list[dict[str, Any]] = field(default_factory=list)
    custom_id: str = ""


@dataclass
class _FakeInteraction:
    """Mirror of ``discord.Interaction`` for tests."""

    type: int
    channel_id: int
    user: _FakeUser
    data: _FakeInteractionData
    guild: _FakeGuild | None = None
    id: int = 1
    response: _FakeInteractionResponse = field(default_factory=_FakeInteractionResponse)


class _FakeIntents:
    """Stub for ``discord.Intents``."""

    def __init__(self) -> None:
        self.guilds = False
        self.guild_messages = False
        self.message_content = False

    @classmethod
    def default(cls) -> _FakeIntents:
        return cls()


# discord.py uses an "InteractionType" IntEnum; we mirror the only two we use.
class _FakeInteractionType:
    application_command = 2
    component = 3


@pytest.fixture
def fake_discord(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a synthetic ``discord`` package tree in ``sys.modules``.

    The driver only touches a tiny slice of the SDK: ``discord.Client``,
    ``discord.Intents``, ``discord.Interaction``, ``discord.InteractionType``,
    and a couple of helper symbols. Recreating just those keeps the fake
    self-contained and avoids any pull on the real ``discord.py`` install.
    """

    pkg = types.ModuleType("discord")
    pkg.Client = _FakeDiscordClient  # type: ignore[attr-defined]
    pkg.Intents = _FakeIntents  # type: ignore[attr-defined]
    pkg.Interaction = _FakeInteraction  # type: ignore[attr-defined]
    pkg.InteractionType = _FakeInteractionType  # type: ignore[attr-defined]

    # ``discord.ui`` is the module that exposes View / Button. The driver
    # constructs a View with two Buttons; the fake records button labels
    # and custom_ids so the test can assert the rendered payload.
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

    # ``discord.ButtonStyle`` is an IntEnum in the real SDK; the fake
    # exposes just the names the driver references.
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


# ---------------------------------------------------------------------------
# Constructor / token validation
# ---------------------------------------------------------------------------


def test_discord_empty_token_rejected() -> None:
    """An empty bot token must be rejected eagerly, no network needed."""
    from bernstein.core.chat.drivers.discord import DiscordBridge

    with pytest.raises(ValueError):
        DiscordBridge(token="")


# ---------------------------------------------------------------------------
# Missing-SDK error path
# ---------------------------------------------------------------------------


def test_discord_start_without_sdk_raises_structured_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``start()`` must raise DiscordDependencyError when discord.py is missing."""
    from bernstein.core.chat.drivers.discord import (
        DiscordBridge,
        DiscordDependencyError,
    )

    # Force the import path to fail even if discord.py is on disk.
    for modname in list(sys.modules):
        if modname == "discord" or modname.startswith("discord."):
            monkeypatch.delitem(sys.modules, modname, raising=False)
    monkeypatch.setitem(sys.modules, "discord", None)  # type: ignore[arg-type]

    bridge = DiscordBridge(token="MTAxNTY3.fake.token")
    with pytest.raises(DiscordDependencyError) as excinfo:
        asyncio.run(bridge.start())
    assert "bernstein[discord]" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Slash command dispatch via interaction events
# ---------------------------------------------------------------------------


def test_discord_slash_command_routes_to_registered_handler(fake_discord: None) -> None:
    """A ``/run goal:"Add JWT auth"`` interaction must dispatch."""
    from bernstein.core.chat.drivers.discord import DiscordBridge

    received: list[ChatMessage] = []

    async def handler(msg: ChatMessage) -> None:  # NOSONAR
        received.append(msg)

    bridge = DiscordBridge(token="MTAxNTY3.fake.token")
    bridge.on_command("run", handler)

    async def scenario() -> None:
        await bridge.start()
        interaction = _slash_interaction(
            command="run",
            options=[{"name": "goal", "value": "Add JWT auth"}],
            channel_id=42,
            user_id=7,
        )
        await bridge.dispatch_interaction(interaction)
        await bridge.stop()

    asyncio.run(scenario())
    assert len(received) == 1
    assert received[0].thread_id == "42"
    assert received[0].user_id == "7"
    # Options carry through as the args list, normalised to strings.
    assert received[0].args == ["goal=Add JWT auth"]


def test_discord_slash_command_ignores_unknown_subcommand(fake_discord: None) -> None:
    """Unknown commands must be ignored, not raise."""
    from bernstein.core.chat.drivers.discord import DiscordBridge

    bridge = DiscordBridge(token="MTAxNTY3.fake.token")

    async def scenario() -> None:
        await bridge.start()
        interaction = _slash_interaction(
            command="nope",
            options=[],
            channel_id=42,
            user_id=7,
        )
        # Must not raise.
        await bridge.dispatch_interaction(interaction)
        await bridge.stop()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Button decode (approve / reject)
# ---------------------------------------------------------------------------


def test_discord_button_decode_round_trip(fake_discord: None) -> None:
    """A component interaction with ``approve:<id>`` custom_id must decode."""
    from bernstein.core.chat.drivers.discord import DiscordBridge

    decisions: list[tuple[str, str, str]] = []

    async def button(thread_id: str, approval_id: str, decision: str) -> None:  # NOSONAR
        decisions.append((thread_id, approval_id, decision))

    bridge = DiscordBridge(token="MTAxNTY3.fake.token")
    bridge.on_button(button)

    async def scenario() -> None:
        await bridge.start()
        await bridge.dispatch_interaction(
            _component_interaction(custom_id="approve:t-42", channel_id=99, user_id=7),
        )
        await bridge.dispatch_interaction(
            _component_interaction(custom_id="reject:t-43", channel_id=99, user_id=7),
        )
        await bridge.stop()

    asyncio.run(scenario())
    assert decisions == [("99", "t-42", "approve"), ("99", "t-43", "reject")]


def test_discord_button_decode_ignores_unknown_action(fake_discord: None) -> None:
    """Component custom_ids with an unknown verb must not call the handler."""
    from bernstein.core.chat.drivers.discord import DiscordBridge

    decisions: list[tuple[str, str, str]] = []

    async def button(thread_id: str, approval_id: str, decision: str) -> None:  # NOSONAR
        decisions.append((thread_id, approval_id, decision))

    bridge = DiscordBridge(token="MTAxNTY3.fake.token")
    bridge.on_button(button)

    async def scenario() -> None:
        await bridge.start()
        await bridge.dispatch_interaction(
            _component_interaction(custom_id="snooze:t-42", channel_id=99, user_id=7),
        )
        await bridge.stop()

    asyncio.run(scenario())
    assert decisions == []


# ---------------------------------------------------------------------------
# push_approval renders a button row with the right custom_ids
# ---------------------------------------------------------------------------


def test_discord_push_approval_renders_button_row(fake_discord: None) -> None:
    """``push_approval`` must post a message with two buttons encoding the approval id."""
    from bernstein.core.chat.drivers.discord import DiscordBridge

    bridge = DiscordBridge(
        token="MTAxNTY3.fake.token",
        install_id="install-test",
        session_id="sess-1",
    )

    async def scenario() -> list[_FakeDiscordMessage]:
        await bridge.start()
        await bridge.push_approval(
            PendingApproval(
                approval_id="t-7",
                title="Approve shell command?",
                body="rm -rf /tmp/scratch",
                thread_id="42",
            ),
        )
        client = bridge._client  # type: ignore[attr-defined]
        sent: list[_FakeDiscordMessage] = []
        for channel in client.channels.values():
            sent.extend(channel.sent)
        await bridge.stop()
        return sent

    sent = asyncio.run(scenario())
    assert len(sent) == 1
    components = sent[0].components
    assert len(components) == 2, "approval message must carry two buttons"
    custom_ids = [c.custom_id for c in components]
    assert custom_ids == ["approve:t-7", "reject:t-7"]


# ---------------------------------------------------------------------------
# Edit debounce (rate limit guard)
# ---------------------------------------------------------------------------


def test_discord_edit_debounce_collapses_rapid_updates(fake_discord: None) -> None:
    """Five rapid edits to the same message id must collapse into one edit."""
    from bernstein.core.chat.drivers.discord import DiscordBridge

    bridge = DiscordBridge(token="MTAxNTY3.fake.token")

    async def scenario() -> list[str]:
        await bridge.start()
        # Send via the bridge so the message handle is registered for
        # the debounced edit path to find on follow-up writes.
        message_id = await bridge.send_message("42", "seed")
        # Burst five edits in succession.
        for i in range(5):
            await bridge.edit_message("42", message_id, f"tick {i}")
        client = bridge._client  # type: ignore[attr-defined]
        channel = client.get_channel(42)
        sent_record = next(m for m in channel.sent if m.message_id == message_id)
        # The fake records edits on the original return value, not the
        # channel record -- look the handle up on the bridge.
        handle = bridge._sent_messages[f"42:{message_id}"]  # type: ignore[attr-defined]
        edits = list(handle.edits)
        await bridge.stop()
        del sent_record
        return edits

    edits = asyncio.run(scenario())
    assert len(edits) == 1, f"expected a single throttled edit, got {edits}"
    assert edits[0] == "tick 0"


# ---------------------------------------------------------------------------
# Outbound message signing
# ---------------------------------------------------------------------------


def test_discord_send_message_includes_signed_envelope(
    fake_discord: None,
    tmp_path: Path,
) -> None:
    """``send_message`` must mint a signed envelope identifying the install."""
    from bernstein.core.chat.drivers.discord import (
        DiscordBridge,
        verify_chat_signature,
    )

    bridge = DiscordBridge(
        token="MTAxNTY3.fake.token",
        install_id="install-A",
        session_id="sess-1",
        key_dir=tmp_path / "keys-A",
    )

    async def scenario() -> str:
        await bridge.start()
        message_id = await bridge.send_message("42", "hello operator")
        await bridge.stop()
        return message_id

    message_id = asyncio.run(scenario())
    assert message_id, "send_message must return the platform-native id"

    envelope = bridge.last_signed_envelope()
    assert envelope is not None, "outbound message must record an attestation envelope"
    assert envelope["install_id"] == "install-A"
    assert envelope["session_id"] == "sess-1"
    assert "content_hash" in envelope
    assert "signature" in envelope
    assert verify_chat_signature(
        install_id="install-A",
        session_id="sess-1",
        content="hello operator",
        signature=envelope["signature"],
        public_key_pem=bridge.public_key_pem(),
    )


def test_discord_signature_rejects_foreign_install(
    fake_discord: None,
    tmp_path: Path,
) -> None:
    """A signature minted by install-B must not verify against install-A's key."""
    from bernstein.core.chat.drivers.discord import (
        DiscordBridge,
        verify_chat_signature,
    )

    bridge_a = DiscordBridge(
        token="MTAxNTY3.fake.tokenA",
        install_id="install-A",
        session_id="sess-1",
        key_dir=tmp_path / "keys-A",
    )
    bridge_b = DiscordBridge(
        token="MTAxNTY3.fake.tokenB",
        install_id="install-B",
        session_id="sess-2",
        key_dir=tmp_path / "keys-B",
    )

    async def scenario() -> tuple[str, bytes]:
        await bridge_a.start()
        await bridge_b.start()
        await bridge_b.send_message("42", "spoofed")
        envelope_b = bridge_b.last_signed_envelope()
        assert envelope_b is not None
        signature = envelope_b["signature"]
        public_a = bridge_a.public_key_pem()
        await bridge_a.stop()
        await bridge_b.stop()
        return signature, public_a

    foreign_signature, public_key_a = asyncio.run(scenario())
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


def test_discord_push_approval_audit_chain_entry_shape(
    fake_discord: None,
    tmp_path: Path,
) -> None:
    """Resolving an approval emits a chained audit entry with the AC fields."""
    from bernstein.core.chat.drivers.discord import (
        APPROVAL_EVENT_TYPE,
        DiscordBridge,
    )
    from bernstein.core.security.audit import AuditLog

    audit = AuditLog(audit_dir=tmp_path / "audit", key=b"deterministic-test-key")

    bridge = DiscordBridge(
        token="MTAxNTY3.fake.token",
        install_id="install-A",
        session_id="sess-1",
        worktree_id="wt-a",
        audit_log=audit,
        key_dir=tmp_path / "keys-A",
    )

    async def scenario() -> None:
        await bridge.start()
        bridge.register_pending_approval(
            approval_id="t-7",
            tool_call_hash="hash-of-tool-call",
            worktree_id="wt-a",
            thread_id="42",
            partition_id="discord:42",
        )
        await bridge.dispatch_interaction(
            _component_interaction(
                custom_id="approve:t-7",
                channel_id=42,
                user_id=7,
                interaction_id=200200,
            ),
        )
        await bridge.stop()

    asyncio.run(scenario())

    entries = audit.query(event_type=APPROVAL_EVENT_TYPE)
    assert len(entries) == 1, f"expected one chained approval entry, got {entries}"
    entry = entries[0]
    details = entry.details
    assert details["approver"] == "7"
    assert details["decision"] == "approve"
    assert details["tool_call_hash"] == "hash-of-tool-call"
    assert details["interaction_id"] == "200200"
    assert details["worktree_id"] == "wt-a"
    assert details["partition_id"] == "discord:42"
    # Chained digest links to the previous chain digest.
    assert entry.prev_hmac
    assert entry.hmac
    valid, errors = audit.verify()
    assert valid, errors


# ---------------------------------------------------------------------------
# Worktree pinning -- cross-worktree resolution rejection
# ---------------------------------------------------------------------------


def test_discord_cross_worktree_approval_rejected(
    fake_discord: None,
    tmp_path: Path,
) -> None:
    """An /approve from wt-a must refuse pending approvals bound to wt-b."""
    from bernstein.core.chat.drivers.discord import (
        APPROVAL_REJECTED_EVENT_TYPE,
        CrossWorktreeApprovalError,
        DiscordBridge,
    )
    from bernstein.core.security.audit import AuditLog

    audit = AuditLog(audit_dir=tmp_path / "audit", key=b"deterministic-test-key")

    bridge = DiscordBridge(
        token="MTAxNTY3.fake.token",
        install_id="install-A",
        session_id="sess-1",
        worktree_id="wt-a",
        audit_log=audit,
        key_dir=tmp_path / "keys-A",
    )

    async def scenario() -> bool:
        await bridge.start()
        bridge.register_pending_approval(
            approval_id="t-7",
            tool_call_hash="hash-of-tool-call",
            worktree_id="wt-b",
            thread_id="42",
            partition_id="discord:42",
        )
        try:
            await bridge.dispatch_interaction(
                _component_interaction(
                    custom_id="approve:t-7",
                    channel_id=42,
                    user_id=7,
                    interaction_id=200200,
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

    from bernstein.core.chat.drivers.discord import APPROVAL_EVENT_TYPE

    resolved = audit.query(event_type=APPROVAL_EVENT_TYPE)
    assert resolved == []
    rejected = audit.query(event_type=APPROVAL_REJECTED_EVENT_TYPE)
    assert len(rejected) == 1
    assert rejected[0].details["reason"] == "cross_worktree"


# ---------------------------------------------------------------------------
# Channel-scoped scheduling fence
# ---------------------------------------------------------------------------


def test_discord_channel_partition_enforces_scheduler_fence(
    fake_discord: None,
    tmp_path: Path,
) -> None:
    """Tasks emitted from channel #ops cannot land on workers attached to #dev.

    Channel id 100 is mapped to partition ``discord:100`` (#ops); channel
    200 maps to ``discord:200`` (#dev). The bridge refuses to resolve a
    pending approval registered against a worker on partition ``discord:200``
    when the approve click arrives in channel 100.
    """
    from bernstein.core.chat.drivers.discord import (
        APPROVAL_REJECTED_EVENT_TYPE,
        ChannelPartitionMismatchError,
        DiscordBridge,
    )
    from bernstein.core.security.audit import AuditLog

    audit = AuditLog(audit_dir=tmp_path / "audit", key=b"deterministic-test-key")

    bridge = DiscordBridge(
        token="MTAxNTY3.fake.token",
        install_id="install-A",
        session_id="sess-1",
        worktree_id="wt-a",
        audit_log=audit,
        key_dir=tmp_path / "keys-A",
    )

    async def scenario() -> bool:
        await bridge.start()
        # Pending approval is bound to a worker on partition ``discord:200`` (#dev).
        bridge.register_pending_approval(
            approval_id="t-7",
            tool_call_hash="hash-of-tool-call",
            worktree_id="wt-a",
            thread_id="200",
            partition_id="discord:200",
        )
        # Operator clicks Approve from a different channel (#ops -> partition 100).
        try:
            await bridge.dispatch_interaction(
                _component_interaction(
                    custom_id="approve:t-7",
                    channel_id=100,
                    user_id=7,
                    interaction_id=300300,
                ),
            )
        except ChannelPartitionMismatchError:
            raised = True
        else:
            raised = False
        await bridge.stop()
        return raised

    raised = asyncio.run(scenario())
    assert raised, "approve from a different channel partition must be rejected"

    rejected = audit.query(event_type=APPROVAL_REJECTED_EVENT_TYPE)
    assert len(rejected) == 1
    details = rejected[0].details
    assert details["reason"] == "channel_partition_mismatch"
    assert details["pending_partition_id"] == "discord:200"
    assert details["request_partition_id"] == "discord:100"


def test_discord_channel_partition_resolves_when_matches(
    fake_discord: None,
    tmp_path: Path,
) -> None:
    """An approval with matching partition resolves cleanly and chains."""
    from bernstein.core.chat.drivers.discord import (
        APPROVAL_EVENT_TYPE,
        DiscordBridge,
    )
    from bernstein.core.security.audit import AuditLog

    audit = AuditLog(audit_dir=tmp_path / "audit", key=b"deterministic-test-key")

    bridge = DiscordBridge(
        token="MTAxNTY3.fake.token",
        install_id="install-A",
        session_id="sess-1",
        worktree_id="wt-a",
        audit_log=audit,
        key_dir=tmp_path / "keys-A",
    )

    async def scenario() -> None:
        await bridge.start()
        bridge.register_pending_approval(
            approval_id="t-9",
            tool_call_hash="hash-of-other-call",
            worktree_id="wt-a",
            thread_id="42",
            partition_id="discord:42",
        )
        await bridge.dispatch_interaction(
            _component_interaction(
                custom_id="approve:t-9",
                channel_id=42,
                user_id=7,
                interaction_id=400400,
            ),
        )
        await bridge.stop()

    asyncio.run(scenario())
    entries = audit.query(event_type=APPROVAL_EVENT_TYPE)
    assert len(entries) == 1
    assert entries[0].details["partition_id"] == "discord:42"


# ---------------------------------------------------------------------------
# Interaction-shape helpers
# ---------------------------------------------------------------------------


def _slash_interaction(
    *,
    command: str,
    options: list[dict[str, Any]],
    channel_id: int,
    user_id: int,
    interaction_id: int = 1,
) -> _FakeInteraction:
    return _FakeInteraction(
        type=_FakeInteractionType.application_command,
        channel_id=channel_id,
        user=_FakeUser(id=user_id),
        data=_FakeInteractionData(name=command, options=list(options)),
        id=interaction_id,
    )


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


# Re-export for friendly imports in sibling tests.
__all__ = [
    "_component_interaction",
    "_slash_interaction",
    "fake_discord",
]


def test_interaction_json_serialisable() -> None:
    """The interaction payload helpers must produce JSON-friendly shapes."""
    inter = _slash_interaction(
        command="run",
        options=[{"name": "goal", "value": "x"}],
        channel_id=1,
        user_id=1,
    )
    assert json.loads(json.dumps(inter.data.options)) == [{"name": "goal", "value": "x"}]
