"""Telegram notification sink.

Two transports are supported:

  * ``thisnotabot-bridge`` (default) -- the sink POSTs to the router's
    ``/tg/notify`` endpoint via :class:`thisnotabot_bridge.BridgeNotifier`.
    This shares one bot identity across every project on the VPS.
  * Legacy chat bridge -- if ``BERNSTEIN_CHAT_USE_LEGACY=1`` (or the
    operator passes a live ``TelegramBridge`` via ``config["bridge"]``),
    fall back to :meth:`TelegramBridge.send_message` so the historical
    edit-throttle behaviour is preserved.

Both paths converge on the same chat id so an operator can flip
between them without losing notifications.
"""

from __future__ import annotations

import importlib
import os
from typing import TYPE_CHECKING, Any

from bernstein.core.notifications.protocol import (
    NotificationDeliveryError,
    NotificationEvent,
    NotificationPermanentError,
)

if TYPE_CHECKING:
    from bernstein.core.chat.drivers._legacy_telegram import TelegramBridge

__all__ = ["TelegramSink"]


class TelegramSink:
    """Send notifications via the existing Telegram chat bridge.

    Required config keys::

        id: <unique sink id>
        kind: telegram
        chat_id: "-100123456"

    Optional config keys (one of them must be set unless the bridge
    SDK is on PYTHONPATH and the bernstein-bridge env vars are
    present)::

        bridge: <live TelegramBridge instance>
        token:  "${BERNSTEIN_TG_TOKEN}"  # legacy long-poll only
        project: "bernstein"             # bridge namespace; defaults to bernstein
        prefer_bridge: true              # force the bridge SDK path
    """

    kind: str = "telegram"

    def __init__(self, config: dict[str, Any]) -> None:
        self.sink_id = str(config["id"])
        chat_id = config.get("chat_id") or config.get("thread_id")
        if not chat_id:
            raise NotificationPermanentError(
                f"telegram sink {self.sink_id!r} requires 'chat_id'",
            )
        self._chat_id = str(chat_id)
        self._project = str(config.get("project") or os.environ.get("THISNOTABOT_PROJECT") or "bernstein")
        self._prefer_bridge = bool(
            config.get("prefer_bridge")
            or os.environ.get("BERNSTEIN_NOTIFY_USE_BRIDGE", "").lower() in {"1", "true", "yes"}
        )

        bridge = config.get("bridge")
        token = _resolve(config.get("token"))
        # No bridge, no token, no SDK env => permanent config error.
        if bridge is None and not token and not _bridge_env_configured():
            raise NotificationPermanentError(
                f"telegram sink {self.sink_id!r} requires either 'bridge', 'token', "
                "or the thisnotabot-bridge env vars "
                "(THISNOTABOT_NOTIFY_URL + TELEGRAM_THISNOTABOT_INTERNAL_TOKEN).",
            )
        self._bridge: TelegramBridge | None = bridge
        self._token: str | None = token
        self._owns_bridge = bridge is None
        self._notifier: Any = None

    async def deliver(self, event: NotificationEvent) -> None:
        """Push the event headline + body to the configured chat."""
        if self._should_use_bridge():
            await self._deliver_via_bridge(event)
            return
        await self._deliver_via_legacy(event)

    async def close(self) -> None:
        """Stop the legacy bridge if we constructed it ourselves."""
        if self._bridge is not None and self._owns_bridge:
            try:
                await self._bridge.stop()
            finally:
                self._bridge = None
        self._notifier = None

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def _should_use_bridge(self) -> bool:
        """Pick between the SDK and the legacy long-poll bridge."""
        if self._bridge is not None:
            # Explicit bridge wins -- caller wants the legacy path.
            return False
        if self._prefer_bridge:
            return True
        # Default: prefer the bridge whenever the SDK is importable and
        # the env is configured. Otherwise fall back to the legacy path
        # which builds its own ``TelegramBridge`` lazily.
        return _bridge_available() and _bridge_env_configured()

    async def _deliver_via_bridge(self, event: NotificationEvent) -> None:
        notifier = self._ensure_notifier()
        try:
            chat_override: int | None
            try:
                chat_override = int(self._chat_id)
            except (TypeError, ValueError):
                chat_override = None
            await notifier.notify(
                event.title,
                event.body or "",
                severity=_event_severity(event),
                chat_id_override=chat_override,
            )
        except Exception as exc:
            raise NotificationDeliveryError(
                f"telegram bridge notify failed: {exc}",
            ) from exc

    async def _deliver_via_legacy(self, event: NotificationEvent) -> None:
        bridge = await self._ensure_legacy_bridge()
        text = event.title
        if event.body:
            text = f"{text}\n\n{event.body}"
        try:
            await bridge.send_message(self._chat_id, text)
        except RuntimeError as exc:
            raise NotificationDeliveryError(f"telegram bridge not ready: {exc}") from exc
        except Exception as exc:
            raise NotificationDeliveryError(f"telegram send failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Lazy construction
    # ------------------------------------------------------------------

    def _ensure_notifier(self) -> Any:
        """Build the SDK's :class:`BridgeNotifier` on first use."""
        if self._notifier is not None:
            return self._notifier
        try:
            sdk: Any = importlib.import_module("thisnotabot_bridge")
        except ImportError as exc:
            raise NotificationDeliveryError(
                "thisnotabot-bridge is not installed; set BERNSTEIN_CHAT_USE_LEGACY=1 to use the long-poll driver",
            ) from exc
        notifier_cls: Any = sdk.BridgeNotifier
        self._notifier = notifier_cls.from_env(project=self._project)
        return self._notifier

    async def _ensure_legacy_bridge(self) -> TelegramBridge:
        if self._bridge is not None:
            return self._bridge
        # Lazy construction so importing the sink doesn't require
        # python-telegram-bot to be installed.
        from bernstein.core.chat.drivers._legacy_telegram import (
            TelegramBridge as LegacyTelegramBridge,
        )

        if self._token is None:  # pragma: no cover - defensive, validated in __init__
            raise NotificationPermanentError(
                f"telegram sink {self.sink_id!r} has no transport configured",
            )
        bridge = LegacyTelegramBridge(token=self._token)
        await bridge.start()
        self._bridge = bridge
        return bridge


def _resolve(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    if value.startswith("${") and value.endswith("}"):
        return os.environ.get(value[2:-1])
    return value


def _bridge_available() -> bool:
    """Return True iff the SDK package is importable in this venv."""
    try:
        importlib.import_module("thisnotabot_bridge")
    except ImportError:
        return False
    return True


def _bridge_env_configured() -> bool:
    """Confirm the env vars the SDK expects are at least populated."""
    return bool(os.environ.get("TELEGRAM_THISNOTABOT_INTERNAL_TOKEN"))


def _event_severity(event: NotificationEvent) -> str:
    """Map bernstein severity strings to the SDK's ``NotifySeverity``.

    Returned as plain strings so the SDK does the StrEnum conversion
    itself; saves us a hard import in the type-only branch.
    """
    raw = getattr(event, "severity", "info") or "info"
    normalised = raw.lower() if isinstance(raw, str) else str(raw).lower()
    if normalised in {"critical", "error", "fatal"}:
        return "critical"
    if normalised in {"warning", "warn"}:
        return "warning"
    return "info"
