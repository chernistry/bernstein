"""Telegram notification sink.

Sends notifications via the standard ``python-telegram-bot`` long-poll
driver at :class:`bernstein.core.chat.drivers.telegram.TelegramBridge`.
One bot identity per sink, one chat id per sink. Plug in a bot API
token (from ``@BotFather``) and a chat id and you are done.

Required config keys::

    id: <unique sink id>
    kind: telegram
    chat_id: "-100123456"

One transport must be configured, either by passing a live bridge or
by giving the sink a token to build one with::

    bridge: <live TelegramBridge instance>     # share the chat-server bridge
    token:  "${BERNSTEIN_TG_TOKEN}"            # build our own on first deliver
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from bernstein.core.notifications.protocol import (
    NotificationDeliveryError,
    NotificationEvent,
    NotificationPermanentError,
)

if TYPE_CHECKING:
    from bernstein.core.chat.drivers.telegram import TelegramBridge

__all__ = ["TelegramSink"]


class TelegramSink:
    """Send notifications via the Telegram long-poll bridge."""

    kind: str = "telegram"

    def __init__(self, config: dict[str, Any]) -> None:
        self.sink_id = str(config["id"])
        chat_id = config.get("chat_id") or config.get("thread_id")
        if not chat_id:
            raise NotificationPermanentError(
                f"telegram sink {self.sink_id!r} requires 'chat_id'",
            )
        self._chat_id = str(chat_id)

        bridge = config.get("bridge")
        token = _resolve(config.get("token"))
        if bridge is None and not token:
            raise NotificationPermanentError(
                f"telegram sink {self.sink_id!r} requires either 'bridge' or 'token'.",
            )
        self._bridge: TelegramBridge | None = bridge
        self._token: str | None = token
        self._owns_bridge = bridge is None

    async def deliver(self, event: NotificationEvent) -> None:
        """Push the event headline + body to the configured chat."""
        bridge = await self._ensure_bridge()
        text = event.title
        if event.body:
            text = f"{text}\n\n{event.body}"
        try:
            await bridge.send_message(self._chat_id, text)
        except RuntimeError as exc:
            raise NotificationDeliveryError(f"telegram bridge not ready: {exc}") from exc
        except Exception as exc:
            raise NotificationDeliveryError(f"telegram send failed: {exc}") from exc

    async def close(self) -> None:
        """Stop the bridge if we constructed it ourselves."""
        if self._bridge is not None and self._owns_bridge:
            try:
                await self._bridge.stop()
            finally:
                self._bridge = None

    async def _ensure_bridge(self) -> TelegramBridge:
        if self._bridge is not None:
            return self._bridge
        # Lazy construction so importing the sink doesn't require
        # python-telegram-bot to be installed.
        from bernstein.core.chat.drivers.telegram import TelegramBridge as Bridge

        if self._token is None:  # pragma: no cover - defensive, validated in __init__
            raise NotificationPermanentError(
                f"telegram sink {self.sink_id!r} has no transport configured",
            )
        bridge = Bridge(token=self._token)
        await bridge.start()
        self._bridge = bridge
        return bridge


def _resolve(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    if value.startswith("${") and value.endswith("}"):
        return os.environ.get(value[2:-1])
    return value
