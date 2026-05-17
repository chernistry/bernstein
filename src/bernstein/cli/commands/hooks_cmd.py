"""``bernstein hooks`` CLI group.

Provides user-facing commands to introspect and smoke-test lifecycle
hooks declared in ``bernstein.yaml`` and dropped under
``.bernstein/hooks/<event>.{sh,py}``.
"""

from __future__ import annotations

import json
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
    HookDenied,
    HookFailure,
    HookRegistry,
    LifecycleContext,
    LifecycleEvent,
    discover_default_hook_scripts,
)
from bernstein.core.lifecycle.payload_schemas import (
    PAYLOAD_SCHEMAS,
    PayloadSchemaError,
    validate_payload,
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


@hooks.command("dry-run")
@click.argument("event", type=click.Choice([e.value for e in LifecycleEvent]))
@click.option(
    "--payload",
    "payload_path",
    type=click.Path(dir_okay=False, exists=True, path_type=Path),
    default=None,
    help="JSON file with a payload to merge into LifecycleContext.data.",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=_DEFAULT_CONFIG_PATH,
    show_default=True,
    help="Path to bernstein.yaml.",
)
def hooks_dry_run(event: str, payload_path: Path | None, config_path: Path) -> None:
    """Fire EVENT with a synthetic payload to see what fires.

    The payload defaults to the documented sample for the chosen event;
    pass ``--payload <file>`` to override. The schema is validated
    before dispatch so an invalid payload is reported up-front rather
    than the hook crashing on bad input.
    """
    lifecycle_event = LifecycleEvent(event)
    data = _load_payload(lifecycle_event, payload_path)
    try:
        validate_payload(lifecycle_event, data)
    except PayloadSchemaError as exc:
        click.echo(f"FAIL: {exc}", err=True)
        raise SystemExit(1) from exc

    registry, _config = _build_registry(config_path)
    context = LifecycleContext(
        event=lifecycle_event,
        session_id=str(data.get("session_id") or "dryrun-session"),
        data=data,
    )
    try:
        result_ctx = registry.run(lifecycle_event, context)
    except HookDenied as denial:
        click.echo(
            f"DENIED: {event} blocked by {denial.hook}: {denial.reason}",
            err=True,
        )
        raise SystemExit(2) from denial
    except HookFailure as exc:
        click.echo(f"FAIL: {exc}", err=True)
        raise SystemExit(1) from exc

    click.echo(f"OK: {event}")
    click.echo(json.dumps(result_ctx.to_payload(), indent=2, sort_keys=True))


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
    """Load config and return a ready-to-use :class:`HookRegistry`.

    Scripts dropped under ``.bernstein/hooks/<event>.{sh,py}`` (relative
    to the config file's directory, or CWD if the file is missing) are
    auto-registered in addition to anything declared in
    ``bernstein.yaml``. This matches the convention that hook authors
    expect from neighbouring CLIs.
    """
    config = _load_config_or_exit(config_path)
    registry = HookRegistry()
    apply_config(registry, config)

    root = config_path.parent if config_path.exists() else Path.cwd()
    for event, scripts in discover_default_hook_scripts(root).items():
        for script in scripts:
            registry.register_script(event, script)

    return registry, config


_SAMPLE_PAYLOADS: dict[LifecycleEvent, dict[str, object]] = {
    LifecycleEvent.SESSION_START: {
        "session_id": "dryrun-session",
        "role": "backend",
        "prompt_template_sha": "0000000",
        "env_snapshot": {},
    },
    LifecycleEvent.USER_PROMPT_SUBMITTED: {
        "session_id": "dryrun-session",
        "prompt": "synthetic prompt for dry-run",
        "attached_files": [],
    },
    LifecycleEvent.PRE_TOOL_USE: {
        "session_id": "dryrun-session",
        "tool": "shell.run",
        "args": {"command": "echo dry-run"},
        "blast_radius_score": 0,
    },
    LifecycleEvent.POST_TOOL_USE: {
        "session_id": "dryrun-session",
        "tool": "shell.run",
        "args": {"command": "echo dry-run"},
        "result": "",
        "duration_ms": 0,
        "cost": 0.0,
        "success": True,
    },
    LifecycleEvent.ERROR_OCCURRED: {
        "session_id": "dryrun-session",
        "error_class": "DryRun",
        "message": "synthetic error",
        "recovery_path": "none",
    },
    LifecycleEvent.IDLE: {
        "session_id": "dryrun-session",
        "idle_duration_s": 0,
    },
    LifecycleEvent.SESSION_END: {
        "session_id": "dryrun-session",
        "status": "completed",
        "total_cost": 0.0,
        "total_tokens": 0,
    },
}


def _load_payload(event: LifecycleEvent, payload_path: Path | None) -> dict[str, object]:
    """Resolve the payload for ``hooks dry-run``."""
    if payload_path is not None:
        try:
            raw = json.loads(payload_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            click.echo(f"FAIL: cannot read payload {payload_path}: {exc}", err=True)
            raise SystemExit(1) from exc
        if not isinstance(raw, dict):
            click.echo("FAIL: payload file must contain a JSON object", err=True)
            raise SystemExit(1)
        return cast("dict[str, object]", raw)
    # Use the documented sample if the event has a known schema;
    # otherwise fall back to an empty payload for legacy events.
    sample = _SAMPLE_PAYLOADS.get(event)
    if sample is not None:
        return dict(sample)
    return {}


# Re-export for tests / docs consumers.
KNOWN_PAYLOAD_SCHEMAS = PAYLOAD_SCHEMAS
