"""Telegram driver -- thin shim over the ``thisnotabot-bridge`` SDK.

Bernstein historically embedded a ``python-telegram-bot`` long-poll
loop inside its own process. That meant the bot died with bernstein
and the same token could not be shared with sibling projects on the
VPS. The new architecture is a standalone ``thisnotabot-router``
service on the VPS which owns the webhook + dispatcher; each project
ships its own :class:`thisnotabot_bridge.BridgeRouter` of slash
commands and pushes notifications via :class:`BridgeNotifier`.

This module is the bernstein-side glue:

  * :meth:`on_command` registers handlers in the SDK's per-project
    registry under ``project="bernstein"``. The standalone router
    discovers them via :func:`thisnotabot_bridge.decorators.get_router`
    and merges them into its dispatcher.
  * :meth:`send_message` POSTs to ``/tg/notify`` with severity ``info``.
  * :meth:`edit_message` and :meth:`push_approval` degrade gracefully
    when the router does not yet support those primitives; the
    transitional plan is documented in
    ``research/telegram_bot_modernization_2026-05-09/design.md``.
  * :meth:`start` and :meth:`stop` are essentially no-ops: the router
    runs out-of-process so there is nothing for bernstein to listen on.

If :envvar:`BERNSTEIN_CHAT_USE_LEGACY` is set to ``1``/``true``/``yes``
the factory in :mod:`bernstein.core.chat` reroutes back to the
:class:`bernstein.core.chat.drivers._legacy_telegram.TelegramBridge`
long-poll driver so an operator can fall back without code changes.
"""

from __future__ import annotations

import importlib
import logging
import os
from typing import TYPE_CHECKING, Any

from bernstein.core.chat.bridge import (
    BridgeProtocol,
    ButtonHandler,
    ChatMessage,
    CommandHandler,
    PendingApproval,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

__all__ = [
    "BRIDGE_PROJECT",
    "BridgeDependencyError",
    "TelegramBridge",
    "is_legacy_mode_enabled",
]

logger = logging.getLogger(__name__)

#: Project namespace registered with the SDK's ``@command`` decorator.
BRIDGE_PROJECT = "bernstein"

#: Env var that toggles the legacy long-poll driver as a hard fallback.
LEGACY_ENV_FLAG = "BERNSTEIN_CHAT_USE_LEGACY"


class BridgeDependencyError(RuntimeError):
    """Raised when ``thisnotabot-bridge`` is not installed."""


def is_legacy_mode_enabled() -> bool:
    """Return True when the operator opted into the legacy long-poll path.

    Truthy values: ``1``, ``true``, ``yes``, ``on`` (case-insensitive).
    Anything else (including an unset variable) keeps the modern bridge
    path active.
    """
    raw = os.environ.get(LEGACY_ENV_FLAG, "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class TelegramBridge(BridgeProtocol):
    """Telegram driver delegating to the ``thisnotabot-router`` service.

    The class still implements :class:`BridgeProtocol` so existing
    callers (the CLI, the ``ChatSession`` glue, the notification sink)
    keep working unchanged. The transport is HTTP-out only -- inbound
    Telegram updates are received by the standalone router service and
    dispatched into bernstein via the aiogram :class:`Router` returned
    by :meth:`thisnotabot_bridge.BridgeRouter.to_aiogram`.
    """

    platform: str = "telegram"

    def __init__(self, token: str = "", *, project: str | None = None) -> None:
        """Build a bridge bound to the bernstein project namespace.

        ``token`` is accepted for API parity with the legacy driver but
        ignored: the standalone router holds the actual Telegram token.
        Local code only needs the per-project ``X-Notify-Secret`` from
        the env -- :class:`thisnotabot_bridge.BridgeNotifier` reads it
        on demand.
        """
        # Token is accepted but never used here -- preserved purely for
        # call-site compatibility with the legacy ``TelegramBridge``.
        self._token = token
        self._project = project or BRIDGE_PROJECT
        self._command_handlers: dict[str, CommandHandler] = {}
        self._button_handler: ButtonHandler | None = None
        self._notifier: Any = None
        self._router: Any = None
        self._sdk: Any = None

    # ------------------------------------------------------------------
    # SDK plumbing
    # ------------------------------------------------------------------

    def _ensure_sdk(self) -> Any:
        """Import the SDK lazily; raise a useful error if it's missing.

        The bridge is the entire point of the shim, so the dependency
        is mandatory in this code path. Tests that monkeypatch the SDK
        replace the ``sys.modules`` entry before construction.
        """
        if self._sdk is not None:
            return self._sdk
        try:
            self._sdk = importlib.import_module("thisnotabot_bridge")
        except ImportError as exc:
            raise BridgeDependencyError(
                "thisnotabot-bridge is not installed. "
                "Install with: pip install -e /path/to/thisnotabot, "
                "or set BERNSTEIN_CHAT_USE_LEGACY=1 to use the long-poll driver.",
            ) from exc
        return self._sdk

    def _ensure_notifier(self) -> Any:
        """Build (lazily) the :class:`BridgeNotifier` HTTP client."""
        if self._notifier is not None:
            return self._notifier
        sdk = self._ensure_sdk()
        notifier_cls: Any = sdk.BridgeNotifier
        self._notifier = notifier_cls.from_env(project=self._project)
        return self._notifier

    def _ensure_router(self) -> Any:
        """Get-or-create the SDK's per-project :class:`BridgeRouter`."""
        if self._router is not None:
            return self._router
        sdk = self._ensure_sdk()
        get_router: Any = sdk.decorators.get_router  # public via sub-module
        self._router = get_router(self._project)
        return self._router

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def on_command(self, name: str, handler: CommandHandler) -> None:
        """Register ``handler`` for ``/<name>`` on the bernstein router.

        The SDK's ``@command`` decorator drops a :class:`CommandSpec`
        into a process-global registry keyed by project name. We bypass
        the decorator surface and write directly to that registry so
        the chat-session keeps owning lifecycle (the ``ChatSession``
        instantiates fresh handlers per ``serve`` invocation; we don't
        want module-level decoration to leak across runs).
        """
        clean = name.lstrip("/")
        self._command_handlers[clean] = handler
        sdk = self._ensure_sdk()
        spec_cls: Any = sdk.decorators.CommandSpec
        spec = spec_cls(
            name=clean,
            project=self._project,
            desc=_describe(clean),
            handler=_make_aiogram_adapter(handler),
        )
        router = self._ensure_router()
        # ``add`` is idempotent in the trivial sense -- duplicate names
        # on the same project are intentionally appended; the router
        # service de-duplicates on ``setMyCommands`` registration.
        router.add(spec)

    def on_button(self, handler: ButtonHandler) -> None:
        """Record the approval button handler.

        The current SDK does not yet expose inline-keyboard callback
        plumbing, so the handler is captured for future use only.
        Slash-command approve / reject (the bernstein default for chat
        approvals) work via :meth:`on_command` exactly as before.
        """
        self._button_handler = handler

    # ------------------------------------------------------------------
    # Lifecycle (router lives out-of-process; these are mostly no-ops)
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Validate the SDK and notifier are reachable.

        We never spawn a long-poll loop here -- the standalone
        ``thisnotabot-router`` does that. We do, however, eagerly
        materialise the notifier so any misconfiguration (missing
        ``TELEGRAM_THISNOTABOT_INTERNAL_TOKEN`` etc.) surfaces at boot
        rather than the first ``send_message``.
        """
        self._ensure_notifier()
        self._ensure_router()
        logger.info(
            "telegram-bridge: started in router-delegate mode (project=%s, commands=%s)",
            self._project,
            sorted(self._command_handlers),
        )

    async def stop(self) -> None:
        """Drop in-process state. The router service keeps running."""
        self._notifier = None
        self._router = None

    # ------------------------------------------------------------------
    # Outbound primitives
    # ------------------------------------------------------------------

    async def send_message(self, thread_id: str, text: str) -> str:
        """Push ``text`` to ``thread_id`` via the router's notify endpoint.

        Returns the empty string -- the router does not echo the
        Telegram message id back to the SDK. Callers that need a real
        id should keep using the legacy driver until the router learns
        to forward send results (planned).
        """
        notifier = self._ensure_notifier()
        chat_id = _coerce_chat_id(thread_id)
        sdk = self._ensure_sdk()
        severity = sdk.NotifySeverity.INFO
        title, body = _split_title_body(text)
        try:
            await notifier.notify(
                title,
                body,
                severity=severity,
                chat_id_override=chat_id,
            )
        except Exception as exc:  # pragma: no cover - HTTP-only path
            logger.warning(
                "telegram-bridge: notify failed thread=%s err=%s",
                thread_id,
                exc,
            )
        return ""

    async def edit_message(self, thread_id: str, message_id: str, text: str) -> None:
        """No-op shim for streaming edits.

        The router does not yet expose ``editMessageText`` over the
        notify protocol. Streaming agents that called ``edit_message``
        on the legacy driver will instead see a sequence of fresh
        notifications until the bridge gains an edit endpoint.
        """
        # Best-effort fallback: send a *new* message so the operator
        # still sees progress -- otherwise the chat goes silent.
        del message_id  # unused on this path
        await self.send_message(thread_id, text)

    async def push_approval(self, approval: PendingApproval) -> str:
        """Render an approval as a notify message with manual instructions.

        The SDK does not expose an inline-keyboard primitive yet, so we
        send a warning-severity notification asking the operator to
        type ``/approve`` or ``/reject``. The slash-command path is
        already wired through :meth:`on_command` and reads the same
        pending-approval directory the legacy buttons used.
        """
        sdk = self._ensure_sdk()
        notifier = self._ensure_notifier()
        chat_id = _coerce_chat_id(approval.thread_id)
        body = f"{approval.body}\n\nApproval id: {approval.approval_id}\nReply with /approve or /reject to resolve."
        try:
            await notifier.notify(
                approval.title,
                body,
                severity=sdk.NotifySeverity.WARNING,
                chat_id_override=chat_id,
            )
        except Exception as exc:  # pragma: no cover - HTTP-only path
            logger.warning(
                "telegram-bridge: push_approval failed approval=%s err=%s",
                approval.approval_id,
                exc,
            )
        return ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_COMMAND_DESCRIPTIONS: dict[str, str] = {
    "run": 'Start a new agent task: /run "<goal>"',
    "status": "Show the current chat-thread session.",
    "approve": "Approve the oldest pending tool call.",
    "reject": "Reject the oldest pending tool call.",
    "switch": "Re-dispatch the current goal: /switch <adapter>",
    "stop": "Stop the active session in this thread.",
    "handoff": "Issue or claim a cross-surface session handoff token.",
}


def _describe(name: str) -> str:
    """Lookup a human-readable description for ``setMyCommands``."""
    return _COMMAND_DESCRIPTIONS.get(name, f"bernstein /{name}")


def _coerce_chat_id(thread_id: str) -> int | None:
    """Convert the bernstein string thread-id to the int the router needs."""
    if not thread_id:
        return None
    try:
        return int(thread_id)
    except (TypeError, ValueError):
        return None


def _split_title_body(text: str) -> tuple[str, str]:
    """Split a multiline payload into ``(title, body)`` for the notify card.

    The title cap is 256 chars (the router's pydantic constraint); the
    rest goes into the body. Single-line inputs use the whole text as
    the title with an empty body.
    """
    if not text:
        return ("(empty)", "")
    head, _, tail = text.partition("\n")
    title = head.strip()[:256] or "(empty)"
    body = tail.strip()
    if not head.strip() and tail.strip():
        # Pathological case: blank first line.
        title = tail.strip().splitlines()[0][:256]
        body = "\n".join(tail.strip().splitlines()[1:])
    return title, body


def _make_aiogram_adapter(
    handler: CommandHandler,
) -> Callable[..., Awaitable[None]]:
    """Wrap a bernstein :class:`CommandHandler` for aiogram dispatch.

    The router service materialises the SDK's :class:`BridgeRouter`
    via :meth:`thisnotabot_bridge.BridgeRouter.to_aiogram`, which calls
    ``handler(message)`` with an ``aiogram.types.Message``. We adapt
    that into the platform-agnostic :class:`ChatMessage` bernstein's
    handlers expect.
    """

    async def _aiogram_handler(message: Any) -> None:
        text = str(getattr(message, "text", "") or "")
        chat = getattr(message, "chat", None)
        chat_id = str(getattr(chat, "id", "") or "")
        from_user = getattr(message, "from_user", None)
        user_id = str(getattr(from_user, "id", "") or "")
        message_id = str(getattr(message, "message_id", "") or "")
        # aiogram strips the leading ``/cmd`` token via ``Command`` filter
        # but the raw text still contains it; mirror legacy parsing.
        parts = text.split() if text else []
        args = parts[1:]
        await handler(
            ChatMessage(
                thread_id=chat_id,
                user_id=user_id,
                text=text,
                message_id=message_id,
                args=args,
                raw=message,
            ),
        )

    return _aiogram_handler
