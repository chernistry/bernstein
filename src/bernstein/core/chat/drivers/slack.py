"""Slack driver stub.

Conforms to :class:`~bernstein.core.chat.bridge.BridgeProtocol` so the
rest of the chat surface stays uniform until the real driver lands in
a follow-up.

Every method, including the registration entry points
(``on_command`` / ``on_button``), raises :class:`NotImplementedError`
with a pointer to the follow-up ticket. Failing fast at wire-up keeps
operators from believing their handlers are live against a stub.
"""

from __future__ import annotations

from bernstein.core.chat.bridge import (
    BridgeProtocol,
    ButtonHandler,
    CommandHandler,
    PendingApproval,
)

__all__ = ["SLACK_STUB_MESSAGE", "SlackBridge"]

SLACK_STUB_MESSAGE = "Slack driver coming in op-001b -- PRs welcome"


class SlackBridge(BridgeProtocol):
    """Placeholder driver for Slack; raises on :meth:`start`."""

    platform: str = "slack"

    def __init__(self, token: str = "") -> None:
        """Accept a token so the CLI surface matches the Telegram driver."""
        self._token = token

    async def start(self) -> None:
        """Reject startup until the real driver lands."""
        raise NotImplementedError(SLACK_STUB_MESSAGE)

    async def stop(self) -> None:
        """No-op; nothing was ever started."""

    async def send_message(self, thread_id: str, text: str) -> str:
        raise NotImplementedError(SLACK_STUB_MESSAGE)

    async def edit_message(self, thread_id: str, message_id: str, text: str) -> None:
        raise NotImplementedError(SLACK_STUB_MESSAGE)

    async def push_approval(self, approval: PendingApproval) -> str:
        raise NotImplementedError(SLACK_STUB_MESSAGE)

    def on_command(self, name: str, handler: CommandHandler) -> None:
        """Reject registrations until the real driver lands."""
        raise NotImplementedError(SLACK_STUB_MESSAGE)

    def on_button(self, handler: ButtonHandler) -> None:
        """Reject registrations until the real driver lands."""
        raise NotImplementedError(SLACK_STUB_MESSAGE)
