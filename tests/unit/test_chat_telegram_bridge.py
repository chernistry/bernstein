"""Unit tests for the bridge-delegated Telegram driver.

The new :class:`bernstein.core.chat.drivers.telegram.TelegramBridge`
delegates to the standalone ``thisnotabot-router`` service via the
``thisnotabot-bridge`` SDK. These tests verify:

  1. Command registrations land in the SDK's ``BridgeRouter`` keyed by
     ``project="bernstein"``.
  2. ``send_message`` POSTs to the SDK's :class:`BridgeNotifier` with
     the right severity and chat-id override.
  3. ``BERNSTEIN_CHAT_USE_LEGACY=1`` reroutes the factory back to the
     long-poll driver (the webhook-secret rejection lives in the
     standalone router service repo, not bernstein).
"""

from __future__ import annotations

import asyncio
import importlib
from typing import TYPE_CHECKING, Any

import pytest

# ``thisnotabot-bridge`` is currently distributed as a path install from
# ``personal_core_services/thisnotabot`` (see ``pyproject.toml`` for the
# tracking note). CI runners — and any contributor who does not have the
# sibling repo checked out — will not have it on ``sys.path``. Skip the
# whole module rather than fail collection so the rest of the suite stays
# green until the SDK lands on the private index and we can pin it.
pytest.importorskip("thisnotabot_bridge")

if TYPE_CHECKING:
    from bernstein.core.chat.bridge import ChatMessage


@pytest.fixture(autouse=True)
def _reset_bridge_registry() -> None:
    """Clear the SDK's process-global router registry between tests.

    The SDK exposes :func:`reset_registry` for exactly this case --
    leaving handlers from one test bleeding into the next would mask
    real bugs in registration semantics.
    """
    sdk = importlib.import_module("thisnotabot_bridge.decorators")
    sdk.reset_registry()
    yield
    sdk.reset_registry()


# ---------------------------------------------------------------------------
# 1. Aiogram router registration with prefix bernstein:
# ---------------------------------------------------------------------------


def test_bridge_register_command_lands_under_bernstein_project() -> None:
    """``on_command`` must register against the ``bernstein`` project namespace."""
    from bernstein.core.chat.drivers.telegram import (
        BRIDGE_PROJECT,
        TelegramBridge,
    )

    bridge = TelegramBridge()

    async def _noop(_msg: ChatMessage) -> None:
        return None

    bridge.on_command("status", _noop)
    bridge.on_command("approve", _noop)

    sdk = importlib.import_module("thisnotabot_bridge")
    decorators = importlib.import_module("thisnotabot_bridge.decorators")
    router = decorators.get_router(BRIDGE_PROJECT)

    assert router.project == "bernstein"
    names = sorted(spec.name for spec in router.commands)
    assert names == ["approve", "status"]
    # And the SDK's aiogram materialiser must succeed -- proves the
    # handler signature is compatible with aiogram dispatch.
    aiogram_router = router.to_aiogram()
    # aiogram Router exposes ``.name`` and our SDK names them
    # ``bridge.<project>``.
    assert getattr(aiogram_router, "name", "") == "bridge.bernstein"
    assert sdk.BridgeRouter is type(router)


# ---------------------------------------------------------------------------
# 2. Notification bridge HTTP shim called with correct severity
# ---------------------------------------------------------------------------


class _FakeNotifier:
    """Captures :meth:`BridgeNotifier.notify` calls instead of HTTP-ing."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def notify(
        self,
        title: str,
        body: str = "",
        *,
        severity: Any,
        chat_id_override: int | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "title": title,
                "body": body,
                "severity": str(severity),
                "chat_id_override": chat_id_override,
            }
        )
        return {"ok": True}


def test_bridge_send_message_invokes_notifier_with_info_severity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``send_message`` must call ``BridgeNotifier.notify`` with INFO severity."""
    from bernstein.core.chat.bridge import PendingApproval
    from bernstein.core.chat.drivers.telegram import TelegramBridge

    bridge = TelegramBridge()
    fake = _FakeNotifier()
    # Inject the fake notifier so we don't perform real HTTP I/O.
    bridge._notifier = fake  # type: ignore[attr-defined]

    async def scenario() -> None:
        await bridge.send_message("12345", "Task t-1 queued -- adapter=claude")
        # Approval cards should map to WARNING severity and append the
        # /approve /reject instructions to the body.
        await bridge.push_approval(
            PendingApproval(
                approval_id="t-7",
                title="Approve shell command?",
                body="rm -rf /tmp/scratch",
                thread_id="12345",
            ),
        )

    asyncio.run(scenario())

    assert len(fake.calls) == 2
    info = fake.calls[0]
    assert info["title"] == "Task t-1 queued -- adapter=claude"
    assert info["severity"].endswith("info")
    assert info["chat_id_override"] == 12345

    warn = fake.calls[1]
    assert warn["title"] == "Approve shell command?"
    assert warn["severity"].endswith("warning")
    assert "Reply with /approve or /reject" in warn["body"]
    assert warn["chat_id_override"] == 12345


# ---------------------------------------------------------------------------
# 3. Legacy env flag forces the python-telegram-bot driver
# ---------------------------------------------------------------------------


def test_legacy_env_flag_routes_factory_to_long_poll_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``BERNSTEIN_CHAT_USE_LEGACY=1`` must yield the legacy class.

    This is bernstein's analogue of the router service's webhook-secret
    rejection -- if the operator misconfigures the new path the env
    flag is the documented escape hatch. We cover it here because the
    actual webhook-secret enforcement lives in
    ``thisnotabot_router/app.py`` and is tested upstream.
    """
    from bernstein.core.chat import load_driver
    from bernstein.core.chat.drivers._legacy_telegram import (
        TelegramBridge as LegacyTelegramBridge,
    )
    from bernstein.core.chat.drivers.telegram import (
        TelegramBridge as BridgeTelegramBridge,
    )

    # Default: bridge-mode driver.
    monkeypatch.delenv("BERNSTEIN_CHAT_USE_LEGACY", raising=False)
    assert load_driver("telegram") is BridgeTelegramBridge

    # Opt-in to legacy.
    monkeypatch.setenv("BERNSTEIN_CHAT_USE_LEGACY", "1")
    assert load_driver("telegram") is LegacyTelegramBridge

    # Other truthy variants honoured.
    for value in ("true", "YES", "On"):
        monkeypatch.setenv("BERNSTEIN_CHAT_USE_LEGACY", value)
        assert load_driver("telegram") is LegacyTelegramBridge

    # Falsy variants fall back to bridge.
    for value in ("0", "false", ""):
        monkeypatch.setenv("BERNSTEIN_CHAT_USE_LEGACY", value)
        assert load_driver("telegram") is BridgeTelegramBridge
