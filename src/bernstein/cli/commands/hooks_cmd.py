"""``bernstein hooks`` CLI group.

Provides user-facing commands to introspect and smoke-test lifecycle
hooks declared in ``bernstein.yaml``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast

import click
import yaml

from bernstein.core.config.hook_config import (
    HookConfig,
    HookConfigError,
    apply_config,
    parse_hook_config,
)
from bernstein.core.lifecycle.hooks import (
    HookFailure,
    HookRegistry,
    LifecycleContext,
    LifecycleEvent,
)

__all__ = ["hooks"]


_DEFAULT_CONFIG_PATH = Path("bernstein.yaml")


@click.group("hooks")
def hooks() -> None:
    """Inspect and exercise lifecycle hooks."""


@hooks.command("list")
@click.option(
    "--config",
    "config_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=_DEFAULT_CONFIG_PATH,
    show_default=True,
    help="Path to bernstein.yaml.",
)
def hooks_list(config_path: Path) -> None:
    """Print registered hooks for each lifecycle event."""
    registry, config = _build_registry(config_path)

    for event in LifecycleEvent:
        labels = registry.registered(event)
        plugin_refs = [entry.name for entry in config.plugins.get(event, [])]
        total = len(labels) + len(plugin_refs)
        click.echo(f"{event.value} ({total}):")
        if total == 0:
            click.echo("  <none>")
            continue
        for label in labels:
            click.echo(f"  - {label}")
        for plugin_name in plugin_refs:
            click.echo(f"  - plugin:{plugin_name}")


@hooks.command("run")
@click.argument("event", type=click.Choice([e.value for e in LifecycleEvent]))
@click.option(
    "--config",
    "config_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=_DEFAULT_CONFIG_PATH,
    show_default=True,
    help="Path to bernstein.yaml.",
)
def hooks_run(event: str, config_path: Path) -> None:
    """Fire EVENT with an empty context (useful for smoke-testing)."""
    lifecycle_event = LifecycleEvent(event)
    registry, _ = _build_registry(config_path)
    context = LifecycleContext(event=lifecycle_event)
    try:
        registry.run(lifecycle_event, context)
    except HookFailure as exc:
        click.echo(f"FAIL: {exc}", err=True)
        raise SystemExit(1) from exc
    click.echo(f"OK: {event}")


@hooks.command("check")
@click.option(
    "--config",
    "config_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=_DEFAULT_CONFIG_PATH,
    show_default=True,
    help="Path to bernstein.yaml.",
)
def hooks_check(config_path: Path) -> None:
    """Validate hook-config syntax and script availability."""
    config = _load_config_or_exit(config_path)

    problems: list[str] = []
    for event, entries in config.scripts.items():
        for entry in entries:
            resolved = entry.path if entry.path.is_absolute() else (config_path.parent / entry.path)
            if not resolved.exists():
                problems.append(f"{event.value}: script does not exist: {entry.path}")
                continue
            if not os.access(resolved, os.X_OK):
                problems.append(f"{event.value}: script is not executable: {entry.path}")

    if problems:
        for problem in problems:
            click.echo(f"FAIL: {problem}", err=True)
        raise SystemExit(1)

    total_scripts = sum(len(v) for v in config.scripts.values())
    total_plugins = sum(len(v) for v in config.plugins.values())
    click.echo(f"OK: {total_scripts} script(s), {total_plugins} plugin reference(s).")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_config_or_exit(config_path: Path) -> HookConfig:
    """Load ``hooks:`` from ``config_path`` or exit with a friendly error."""
    if not config_path.exists():
        # A missing file is treated as "no hooks configured" to keep the
        # commands usable before the user has created a config.
        return HookConfig()
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        click.echo(f"FAIL: cannot parse {config_path}: {exc}", err=True)
        raise SystemExit(1) from exc

    hooks_section = cast("dict[object, object]", raw).get("hooks") if isinstance(raw, dict) else None

    try:
        return parse_hook_config(hooks_section)
    except HookConfigError as exc:
        click.echo(f"FAIL: invalid hooks config: {exc}", err=True)
        raise SystemExit(1) from exc


def _build_registry(config_path: Path) -> tuple[HookRegistry, HookConfig]:
    """Load config and return a ready-to-use :class:`HookRegistry`."""
    config = _load_config_or_exit(config_path)
    registry = HookRegistry()
    apply_config(registry, config)
    return registry, config
