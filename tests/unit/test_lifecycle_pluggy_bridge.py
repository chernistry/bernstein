"""Tests for lifecycle pluggy hook specifications."""

from __future__ import annotations

import ast
from pathlib import Path

from bernstein.core.lifecycle import pluggy_bridge
from bernstein.core.lifecycle.hooks import LifecycleEvent
from bernstein.core.lifecycle.pluggy_bridge import make_plugin_manager


def test_standard_hookspecs_register_event_names_without_camelcase_defs() -> None:
    """Cross-CLI hooks keep public event names without camelCase method definitions."""
    source = Path(pluggy_bridge.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    hook_spec = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "LifecycleHookSpec")
    method_names = {node.name for node in hook_spec.body if isinstance(node, ast.FunctionDef)}
    standard_event_names = {
        LifecycleEvent.SESSION_START.value,
        LifecycleEvent.USER_PROMPT_SUBMITTED.value,
        LifecycleEvent.PRE_TOOL_USE.value,
        LifecycleEvent.POST_TOOL_USE.value,
        LifecycleEvent.ERROR_OCCURRED.value,
        LifecycleEvent.SESSION_END.value,
    }

    assert method_names.isdisjoint(standard_event_names)

    pm = make_plugin_manager()
    for event_name in standard_event_names:
        assert hasattr(pm.hook, event_name)
