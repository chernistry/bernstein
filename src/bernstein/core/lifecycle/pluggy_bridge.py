"""Bridge between :class:`HookRegistry` and the project-wide pluggy manager.

The bridge defines a set of hookspecs matching the lifecycle events so
that regular plugins can subscribe via ``@hookimpl``, then wires the
pluggy ``PluginManager`` into a :class:`HookRegistry` instance so that
firing an event dispatches to both script/callable hooks and pluggy
implementations.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any

import pluggy

from bernstein.core.lifecycle.hooks import HookFailure, HookRegistry, LifecycleContext, LifecycleEvent
from bernstein.plugins import hookspec

if TYPE_CHECKING:
    from collections.abc import Callable

log = logging.getLogger(__name__)

__all__ = [
    "LifecycleHookSpec",
    "apply_hooks_to_existing_system",
    "build_pluggy_dispatcher",
    "make_plugin_manager",
]


class LifecycleHookSpec:
    """Pluggy hookspecs for the lifecycle events.

    Plugins implement one or more of these via ``@hookimpl`` and are
    picked up automatically once registered with the project plugin
    manager.
    """

    @hookspec
    def pre_task(self, ctx: LifecycleContext) -> None:
        """Fires before a task transitions out of ``open``."""

    @hookspec
    def post_task(self, ctx: LifecycleContext) -> None:
        """Fires after a task reaches a terminal state."""

    @hookspec
    def pre_merge(self, ctx: LifecycleContext) -> None:
        """Fires before a merge into the integration branch begins."""

    @hookspec
    def post_merge(self, ctx: LifecycleContext) -> None:
        """Fires after a merge completes (success or rollback)."""

    @hookspec
    def pre_spawn(self, ctx: LifecycleContext) -> None:
        """Fires before an agent session is spawned."""

    @hookspec
    def post_spawn(self, ctx: LifecycleContext) -> None:
        """Fires after an agent session has been spawned."""


def make_plugin_manager() -> pluggy.PluginManager:
    """Create a pluggy manager preloaded with :class:`LifecycleHookSpec`.

    Callers who already manage a project-wide ``PluginManager`` should
    instead call ``pm.add_hookspecs(LifecycleHookSpec)`` themselves;
    this helper is primarily for tests and standalone usage.
    """
    pm = pluggy.PluginManager("bernstein")
    pm.add_hookspecs(LifecycleHookSpec)
    return pm


def build_pluggy_dispatcher(
    pm: pluggy.PluginManager,
) -> Callable[[LifecycleEvent, LifecycleContext], None]:
    """Return a function that fans a lifecycle event out over pluggy.

    The dispatcher ignores ``None`` returns from hookimpls and converts
    any raised exception into :class:`HookFailure` so callers see a
    consistent error type regardless of whether a script or a plugin
    blew up.
    """

    def dispatch(event: LifecycleEvent, context: LifecycleContext) -> None:
        hook_caller: Any = getattr(pm.hook, event.value, None)
        if hook_caller is None:
            return
        try:
            hook_caller(ctx=context)
        except HookFailure:
            raise
        except Exception as exc:
            raise HookFailure(event, f"pluggy:{event.value}", cause=exc) from exc

    return dispatch


def apply_hooks_to_existing_system(
    registry: HookRegistry,
    pm: pluggy.PluginManager | None = None,
) -> pluggy.PluginManager:
    """One-shot helper that wires pluggy into a :class:`HookRegistry`.

    The parent bootstrap is expected to call this exactly once during
    startup, after plugin discovery, and pass the returned manager back
    in for any additional plugin registration. If ``pm`` is ``None``, a
    fresh manager is constructed via :func:`make_plugin_manager`.

    Args:
        registry: The lifecycle registry that owns script/callable hooks.
        pm: An existing plugin manager, or ``None`` to create one.

    Returns:
        The plugin manager with lifecycle hookspecs attached and the
        dispatcher wired into ``registry``.
    """
    if pm is None:
        pm = make_plugin_manager()
    else:
        # If the caller passed their own manager, make sure our specs
        # are registered; ``add_hookspecs`` is not idempotent, so
        # suppress the ``ValueError`` it raises on duplicate registration.
        with contextlib.suppress(ValueError):
            pm.add_hookspecs(LifecycleHookSpec)
    registry.attach_pluggy_dispatcher(build_pluggy_dispatcher(pm))
    return pm
