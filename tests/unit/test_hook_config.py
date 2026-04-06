"""Tests for HOOK-010 — hook configuration from YAML."""

from __future__ import annotations

from typing import Any

import pytest

from bernstein.core.hook_config import (
    HookConfigEntry,
    _build_filter,
    _resolve_events,
    load_hooks_into_registry,
    parse_hook_config,
    parse_hooks_section,
)
from bernstein.core.hook_events import HookEvent
from bernstein.core.hook_registry import AsyncHookRegistry, HookFilter


# ---------------------------------------------------------------------------
# _resolve_events
# ---------------------------------------------------------------------------


class TestResolveEvents:
    """_resolve_events maps string names to HookEvent members."""

    def test_single_event(self) -> None:
        events = _resolve_events(["task.completed"])
        assert HookEvent.TASK_COMPLETED in events
        assert len(events) == 1

    def test_multiple_events(self) -> None:
        events = _resolve_events(["task.completed", "task.failed"])
        assert HookEvent.TASK_COMPLETED in events
        assert HookEvent.TASK_FAILED in events
        assert len(events) == 2

    def test_wildcard_matches_all(self) -> None:
        events = _resolve_events(["*"])
        assert len(events) == len(HookEvent)

    def test_unknown_event_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown hook event"):
            _resolve_events(["nonexistent.event"])


# ---------------------------------------------------------------------------
# _build_filter
# ---------------------------------------------------------------------------


class TestBuildFilter:
    """_build_filter creates HookFilter from config dict."""

    def test_empty_dict_returns_none(self) -> None:
        assert _build_filter({}) is None

    def test_role_filter(self) -> None:
        f = _build_filter({"role": "backend*"})
        assert f is not None
        assert f.role == "backend*"
        assert f.status is None

    def test_all_fields(self) -> None:
        f = _build_filter({"role": "qa", "status": "fail*", "adapter": "claude"})
        assert f is not None
        assert f.role == "qa"
        assert f.status == "fail*"
        assert f.adapter == "claude"


# ---------------------------------------------------------------------------
# parse_hook_config
# ---------------------------------------------------------------------------


class TestParseHookConfig:
    """parse_hook_config parses a single raw dict."""

    def test_minimal_exec_hook(self) -> None:
        raw: dict[str, Any] = {
            "name": "notify",
            "events": ["task.completed"],
            "type": "exec",
            "command": "echo done",
        }
        entry = parse_hook_config(raw)
        assert entry.name == "notify"
        assert entry.events == ["task.completed"]
        assert entry.hook_type == "exec"
        assert entry.command == "echo done"
        assert entry.priority == 100
        assert entry.enabled is True

    def test_prompt_hook(self) -> None:
        raw: dict[str, Any] = {
            "name": "inject-ctx",
            "events": ["agent.spawned"],
            "type": "prompt",
            "template": "Security: {role}",
            "position": "prepend",
            "priority": 50,
        }
        entry = parse_hook_config(raw)
        assert entry.hook_type == "prompt"
        assert entry.template == "Security: {role}"
        assert entry.position == "prepend"
        assert entry.priority == 50

    def test_disabled_hook(self) -> None:
        raw: dict[str, Any] = {
            "name": "noop",
            "events": ["*"],
            "type": "exec",
            "command": "true",
            "enabled": False,
        }
        entry = parse_hook_config(raw)
        assert entry.enabled is False

    def test_filter_config(self) -> None:
        raw: dict[str, Any] = {
            "name": "filtered",
            "events": ["task.failed"],
            "type": "exec",
            "command": "echo fail",
            "filter": {"role": "backend*", "adapter": "claude"},
        }
        entry = parse_hook_config(raw)
        assert entry.filter_config["role"] == "backend*"
        assert entry.filter_config["adapter"] == "claude"

    def test_extra_env(self) -> None:
        raw: dict[str, Any] = {
            "name": "with-env",
            "events": ["task.completed"],
            "type": "exec",
            "command": "echo $SLACK_URL",
            "extra_env": {"SLACK_URL": "https://hooks.slack.com/xxx"},
        }
        entry = parse_hook_config(raw)
        assert entry.extra_env["SLACK_URL"] == "https://hooks.slack.com/xxx"


# ---------------------------------------------------------------------------
# parse_hooks_section
# ---------------------------------------------------------------------------


class TestParseHooksSection:
    """parse_hooks_section handles a list of raw hook dicts."""

    def test_valid_hooks_parsed(self) -> None:
        raw_list: list[dict[str, Any]] = [
            {"name": "a", "events": ["task.completed"], "type": "exec", "command": "echo a"},
            {"name": "b", "events": ["task.failed"], "type": "exec", "command": "echo b"},
        ]
        entries = parse_hooks_section(raw_list)
        assert len(entries) == 2
        assert entries[0].name == "a"
        assert entries[1].name == "b"

    def test_skips_hook_with_no_name(self) -> None:
        raw_list: list[dict[str, Any]] = [
            {"events": ["task.completed"], "type": "exec", "command": "echo x"},
        ]
        entries = parse_hooks_section(raw_list)
        assert len(entries) == 0

    def test_skips_hook_with_no_events(self) -> None:
        raw_list: list[dict[str, Any]] = [
            {"name": "empty", "type": "exec", "command": "echo x"},
        ]
        entries = parse_hooks_section(raw_list)
        assert len(entries) == 0


# ---------------------------------------------------------------------------
# load_hooks_into_registry
# ---------------------------------------------------------------------------


class TestLoadHooksIntoRegistry:
    """load_hooks_into_registry creates handlers and registers them."""

    def test_loads_exec_hook(self) -> None:
        entries = [
            HookConfigEntry(
                name="notify",
                events=["task.completed"],
                hook_type="exec",
                command="echo done",
                priority=10,
            ),
        ]
        registry = AsyncHookRegistry()
        registered = load_hooks_into_registry(entries, registry)
        assert registered == ["notify"]
        hook = registry.get("notify")
        assert hook is not None
        assert hook.priority == 10

    def test_loads_prompt_hook(self) -> None:
        entries = [
            HookConfigEntry(
                name="inject",
                events=["agent.spawned"],
                hook_type="prompt",
                template="Context: {role}",
                position="prepend",
            ),
        ]
        registry = AsyncHookRegistry()
        registered = load_hooks_into_registry(entries, registry)
        assert registered == ["inject"]

    def test_skips_disabled_hooks(self) -> None:
        entries = [
            HookConfigEntry(
                name="disabled",
                events=["task.completed"],
                hook_type="exec",
                command="echo x",
                enabled=False,
            ),
        ]
        registry = AsyncHookRegistry()
        registered = load_hooks_into_registry(entries, registry)
        assert registered == []

    def test_skips_unknown_handler_type(self) -> None:
        entries = [
            HookConfigEntry(
                name="unknown",
                events=["task.completed"],
                hook_type="webhook",
                command="echo x",
            ),
        ]
        registry = AsyncHookRegistry()
        registered = load_hooks_into_registry(entries, registry)
        assert registered == []

    def test_skips_invalid_event_names(self) -> None:
        entries = [
            HookConfigEntry(
                name="bad-events",
                events=["nonexistent.event"],
                hook_type="exec",
                command="echo x",
            ),
        ]
        registry = AsyncHookRegistry()
        registered = load_hooks_into_registry(entries, registry)
        assert registered == []

    def test_loads_with_filter(self) -> None:
        entries = [
            HookConfigEntry(
                name="filtered",
                events=["task.failed"],
                hook_type="exec",
                command="echo fail",
                filter_config={"role": "backend*"},
            ),
        ]
        registry = AsyncHookRegistry()
        load_hooks_into_registry(entries, registry)
        hook = registry.get("filtered")
        assert hook is not None
        assert hook.hook_filter is not None
        assert hook.hook_filter.role == "backend*"

    def test_wildcard_events(self) -> None:
        entries = [
            HookConfigEntry(
                name="all-events",
                events=["*"],
                hook_type="exec",
                command="echo x",
            ),
        ]
        registry = AsyncHookRegistry()
        load_hooks_into_registry(entries, registry)
        hook = registry.get("all-events")
        assert hook is not None
        assert len(hook.events) == len(HookEvent)
