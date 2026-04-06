"""HOOK-010: Hook configuration from bernstein.yaml.

Parses a ``hooks:`` section in bernstein.yaml into typed hook
registration entries that can be loaded into the ``AsyncHookRegistry``.

Example YAML::

    hooks:
      - name: notify-slack
        events: ["task.completed", "task.failed"]
        type: exec
        command: "curl -X POST https://hooks.slack.com/..."
        priority: 10
        filter:
          role: "backend*"

      - name: inject-security-context
        events: ["agent.spawned"]
        type: prompt
        template: "Security review required for {role} tasks."
        position: prepend
        priority: 50

      - name: log-everything
        events: ["*"]
        type: exec
        command: "logger -t bernstein"
        enabled: false
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from bernstein.core.hook_events import HookEvent
from bernstein.core.hook_registry import (
    DEFAULT_PRIORITY,
    AsyncHookRegistry,
    HandlerType,
    HookFilter,
    RegisteredHook,
)

logger = logging.getLogger(__name__)


@dataclass
class HookConfigEntry:
    """A single hook definition from YAML configuration.

    Attributes:
        name: Unique hook name.
        events: List of event name strings (e.g. ``"task.failed"``).
            Use ``"*"`` to match all events.
        hook_type: Handler type (``"exec"``, ``"prompt"``, ``"callable"``).
        command: Shell command (exec type only).
        template: Prompt template string (prompt type only).
        position: Injection position for prompt type (``"append"`` or ``"prepend"``).
        priority: Execution priority (lower = runs first).
        enabled: Whether the hook is active.
        timeout_s: Timeout in seconds (exec type only).
        filter_config: Optional filter configuration.
        extra_env: Extra environment variables (exec type only).
    """

    name: str = ""
    events: list[str] = field(default_factory=list)
    hook_type: str = "exec"
    command: str = ""
    template: str = ""
    position: str = "append"
    priority: int = DEFAULT_PRIORITY
    enabled: bool = True
    timeout_s: float = 30.0
    filter_config: dict[str, str] = field(default_factory=dict)
    extra_env: dict[str, str] = field(default_factory=dict)


def _resolve_events(event_names: list[str]) -> frozenset[HookEvent]:
    """Resolve event name strings to HookEvent enum members.

    ``"*"`` expands to all events.

    Args:
        event_names: List of event value strings.

    Returns:
        Frozen set of matched HookEvent members.

    Raises:
        ValueError: If an event name does not match any HookEvent.
    """
    if "*" in event_names:
        return frozenset(HookEvent)

    resolved: set[HookEvent] = set()
    for name in event_names:
        found = False
        for member in HookEvent:
            if member.value == name:
                resolved.add(member)
                found = True
                break
        if not found:
            msg = f"Unknown hook event: {name!r}. Valid events: {[e.value for e in HookEvent]}"
            raise ValueError(msg)
    return frozenset(resolved)


def _build_filter(filter_config: dict[str, str]) -> HookFilter | None:
    """Build a HookFilter from a config dict.

    Args:
        filter_config: Dict with optional ``role``, ``status``, ``adapter`` keys.

    Returns:
        A HookFilter, or None if all fields are empty.
    """
    if not filter_config:
        return None
    return HookFilter(
        role=filter_config.get("role"),
        status=filter_config.get("status"),
        adapter=filter_config.get("adapter"),
    )


def parse_hook_config(raw: dict[str, Any]) -> HookConfigEntry:
    """Parse a single hook config dict from YAML.

    Args:
        raw: Raw dict from YAML.

    Returns:
        A typed HookConfigEntry.
    """
    filter_raw = raw.get("filter", {})
    if not isinstance(filter_raw, dict):
        filter_raw = {}
    extra_env_raw = raw.get("extra_env", {})
    if not isinstance(extra_env_raw, dict):
        extra_env_raw = {}

    return HookConfigEntry(
        name=str(raw.get("name", "")),
        events=list(raw.get("events", [])),
        hook_type=str(raw.get("type", "exec")),
        command=str(raw.get("command", "")),
        template=str(raw.get("template", "")),
        position=str(raw.get("position", "append")),
        priority=int(raw.get("priority", DEFAULT_PRIORITY)),
        enabled=bool(raw.get("enabled", True)),
        timeout_s=float(raw.get("timeout_s", 30.0)),
        filter_config={str(k): str(v) for k, v in filter_raw.items()},
        extra_env={str(k): str(v) for k, v in extra_env_raw.items()},
    )


def parse_hooks_section(hooks_list: list[dict[str, Any]]) -> list[HookConfigEntry]:
    """Parse the full ``hooks:`` YAML section.

    Args:
        hooks_list: List of raw hook config dicts.

    Returns:
        List of typed HookConfigEntry objects.
    """
    entries: list[HookConfigEntry] = []
    for raw in hooks_list:
        try:
            entry = parse_hook_config(raw)
            if not entry.name:
                logger.warning("Skipping hook with no name: %s", raw)
                continue
            if not entry.events:
                logger.warning("Skipping hook %r with no events", entry.name)
                continue
            entries.append(entry)
        except (TypeError, ValueError) as exc:
            logger.warning("Failed to parse hook config: %s — %s", raw, exc)
    return entries


def load_hooks_into_registry(
    entries: list[HookConfigEntry],
    registry: AsyncHookRegistry,
) -> list[str]:
    """Convert config entries to handlers and register them.

    Args:
        entries: Parsed hook config entries.
        registry: The registry to load hooks into.

    Returns:
        List of hook names that were successfully registered.
    """
    from bernstein.core.hook_exec_handler import ExecHookHandler
    from bernstein.core.hook_prompt_handler import PromptHookHandler

    registered: list[str] = []

    for entry in entries:
        if not entry.enabled:
            logger.debug("Skipping disabled hook: %s", entry.name)
            continue

        try:
            events = _resolve_events(entry.events)
        except ValueError as exc:
            logger.warning("Cannot register hook %r: %s", entry.name, exc)
            continue

        hook_filter = _build_filter(entry.filter_config)

        handler_type: HandlerType
        handler: Any

        if entry.hook_type == "exec":
            handler_type = HandlerType.EXEC
            handler = ExecHookHandler(
                command=entry.command,
                timeout_s=entry.timeout_s,
                extra_env=entry.extra_env,
            )
        elif entry.hook_type == "prompt":
            handler_type = HandlerType.PROMPT
            handler = PromptHookHandler(
                name=entry.name,
                template=entry.template,
                position=entry.position,
            )
        else:
            logger.warning(
                "Unknown handler type %r for hook %r — skipping",
                entry.hook_type,
                entry.name,
            )
            continue

        hook = RegisteredHook(
            name=entry.name,
            events=events,
            handler_type=handler_type,
            handler=handler,
            priority=entry.priority,
            hook_filter=hook_filter,
            enabled=True,
        )
        try:
            registry.register(hook)
            registered.append(entry.name)
        except ValueError as exc:
            logger.warning("Failed to register hook %r: %s", entry.name, exc)

    return registered
