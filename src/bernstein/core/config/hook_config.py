"""Parser for the top-level ``hooks:`` section in ``bernstein.yaml``.

Users declare hooks in one of two shapes:

* A bare string, interpreted as a script path with the default timeout.
* A mapping with either a ``script:`` or ``plugin:`` key.

Example::

    hooks:
      pre_task: ["scripts/pre-flight.sh"]
      post_merge:
        - script: "scripts/notify.sh"
          timeout: 10
        - plugin: "bernstein_plugin_jira"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from bernstein.core.lifecycle.hooks import DEFAULT_TIMEOUT_SECONDS, HookRegistry, LifecycleEvent

__all__ = [
    "HookConfig",
    "HookConfigError",
    "PluginHookEntry",
    "ScriptHookEntry",
    "apply_config",
    "parse_hook_config",
]


class HookConfigError(ValueError):
    """Raised when ``hooks:`` is syntactically invalid."""


@dataclass(frozen=True, slots=True)
class ScriptHookEntry:
    """A script-hook declaration resolved from config."""

    path: Path
    timeout: int = DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True, slots=True)
class PluginHookEntry:
    """A reference to a pluggy plugin expected to be loaded elsewhere."""

    name: str


@dataclass(frozen=True, slots=True)
class HookConfig:
    """Resolved hook configuration, grouped by lifecycle event."""

    scripts: dict[LifecycleEvent, list[ScriptHookEntry]] = field(
        default_factory=lambda: {event: [] for event in LifecycleEvent},
    )
    plugins: dict[LifecycleEvent, list[PluginHookEntry]] = field(
        default_factory=lambda: {event: [] for event in LifecycleEvent},
    )

    def is_empty(self) -> bool:
        """Return True if no hooks were declared for any event."""
        return not any(self.scripts.values()) and not any(self.plugins.values())


def parse_hook_config(raw: object) -> HookConfig:
    """Parse a raw ``hooks:`` mapping (already YAML-decoded) into a :class:`HookConfig`.

    A value of ``None`` (the key was present but empty) yields an empty
    :class:`HookConfig`. Unknown event names raise :class:`HookConfigError`.
    """
    if raw is None:
        return HookConfig()
    if not isinstance(raw, dict):
        raise HookConfigError(f"'hooks:' must be a mapping, got {type(raw).__name__}")

    config = HookConfig()
    raw_mapping = cast("dict[object, object]", raw)
    for event_key, entries in raw_mapping.items():
        if not isinstance(event_key, str):
            raise HookConfigError(f"hook event name must be a string, got {type(event_key).__name__}")
        try:
            event = LifecycleEvent(event_key)
        except ValueError as exc:
            valid = ", ".join(e.value for e in LifecycleEvent)
            raise HookConfigError(f"unknown hook event '{event_key}'; expected one of: {valid}") from exc

        if entries is None:
            continue
        if not isinstance(entries, list):
            raise HookConfigError(f"hooks.{event_key} must be a list, got {type(entries).__name__}")

        items = cast("list[object]", entries)
        for item in items:
            _parse_entry(event, item, config)

    return config


def _parse_entry(event: LifecycleEvent, item: object, config: HookConfig) -> None:
    if isinstance(item, str):
        config.scripts[event].append(ScriptHookEntry(path=Path(item)))
        return

    if not isinstance(item, dict):
        raise HookConfigError(
            f"hooks.{event.value} entries must be a string or mapping, got {type(item).__name__}",
        )

    item_map = cast("dict[object, object]", item)
    if "script" in item_map:
        path_value = item_map["script"]
        if not isinstance(path_value, str):
            raise HookConfigError(f"hooks.{event.value}[].script must be a string path")
        timeout_raw = item_map.get("timeout", DEFAULT_TIMEOUT_SECONDS)
        if not isinstance(timeout_raw, int) or isinstance(timeout_raw, bool):
            raise HookConfigError(f"hooks.{event.value}[].timeout must be an integer (seconds)")
        config.scripts[event].append(ScriptHookEntry(path=Path(path_value), timeout=timeout_raw))
        return

    if "plugin" in item_map:
        plugin_value = item_map["plugin"]
        if not isinstance(plugin_value, str):
            raise HookConfigError(f"hooks.{event.value}[].plugin must be a string name")
        config.plugins[event].append(PluginHookEntry(name=plugin_value))
        return

    raise HookConfigError(
        f"hooks.{event.value} entry must contain either 'script:' or 'plugin:' keys",
    )


def apply_config(registry: HookRegistry, config: HookConfig) -> None:
    """Register every script declaration from ``config`` against ``registry``.

    Plugin references are informational — they are resolved by the
    pluggy manager at plugin-discovery time, not here.
    """
    for event, entries in config.scripts.items():
        for entry in entries:
            registry.register_script(event, entry.path, timeout=entry.timeout)
