"""CLI surface for per-task criterion profile (issue #1346).

Adds:

* ``bernstein criterion-profile show <task_id>`` - print the resolved
  weight vector and the named preset (or ``"inline"``) for a task.
* ``bernstein criterion-profile list`` - enumerate available presets.

The ``--criterion-profile`` flag on ``bernstein run`` and on ``bernstein
add-task`` lives in their respective modules so they're discoverable
through ``--help`` on each surface.
"""

from __future__ import annotations

import json
from typing import Any, cast

import click

from bernstein.cli.helpers import (
    console,
    is_json,
    print_json,
    server_get,
)
from bernstein.core.routing.criterion_profile import (
    AXES,
    CRITERION_PROFILE_REGISTRY,
    CriterionProfile,
    CriterionProfileError,
    describe,
    is_enabled,
    resolve,
)


@click.group("criterion-profile")
def criterion_profile_group() -> None:
    """Inspect per-task criterion profiles (issue #1346)."""


@criterion_profile_group.command("show")
@click.argument("task_id")
def show_cmd(task_id: str) -> None:
    """Print the resolved criterion profile for *task_id*.

    Output format (when stdout is a TTY):

        Task <task_id>
          preset: <name|inline>
          weights:
            correctness: 0.600
            cost:        0.100
            latency:     0.100
            reversibility: 0.200

    When ``--json`` is set globally, emits a JSON object with the same fields.
    """
    if not is_enabled():
        if is_json():
            print_json({"task_id": task_id, "enabled": False})
        else:
            console.print("[yellow]criterion-profile routing is disabled (BERNSTEIN_CRITERION_PROFILE=0)[/yellow]")
        return

    data = server_get(f"/tasks/{task_id}")
    if data is None:
        from bernstein.cli.errors import (
            server_unreachable,  # pyright: ignore[reportMissingImports, reportUnknownVariableType]
        )

        server_unreachable().print()  # pyright: ignore[reportUnknownMemberType]
        raise SystemExit(1)

    metadata_raw: object = data.get("metadata")
    metadata: dict[str, Any] = cast("dict[str, Any]", metadata_raw) if isinstance(metadata_raw, dict) else {}
    spec: object = metadata.get("criterion_profile")
    if spec is None:
        if is_json():
            print_json({"task_id": task_id, "criterion_profile": None})
        else:
            console.print(f"[dim]Task {task_id} has no criterion_profile metadata.[/dim]")
        return

    try:
        profile = resolve(spec)
    except CriterionProfileError as exc:
        if is_json():
            print_json({"task_id": task_id, "error": str(exc), "raw_spec": spec})
        else:
            console.print(f"[red]Invalid criterion_profile on task {task_id}:[/red] {exc}")
        raise SystemExit(2) from None

    if is_json():
        payload = {
            "task_id": task_id,
            "preset": profile.name,
            "weights": profile.as_dict(),
        }
        print_json(payload)
        return

    console.print(f"Task [bold]{task_id}[/bold]")
    console.print(f"  preset: [cyan]{profile.name}[/cyan]")
    console.print("  weights:")
    for axis in AXES:
        value = getattr(profile, axis)
        console.print(f"    {axis:<14} {value:.3f}")


@criterion_profile_group.command("list")
def list_cmd() -> None:
    """List the criterion profile presets registered in this process."""
    if is_json():
        payload = [
            {
                "name": name,
                "weights": profile.as_dict(),
                "describe": describe(profile),
            }
            for name, profile in sorted(CRITERION_PROFILE_REGISTRY.items())
        ]
        print(json.dumps(payload, indent=2))
        return

    console.print("[bold]Criterion profile presets[/bold]")
    for name, profile in sorted(CRITERION_PROFILE_REGISTRY.items()):
        console.print(f"  [cyan]{name}[/cyan] -> {_format_profile(profile)}")


def _format_profile(profile: CriterionProfile) -> str:
    """Format a profile as a single line for tabular output."""
    weights = profile.as_dict()
    return " ".join(f"{axis}={weights[axis]:.2f}" for axis in AXES)


__all__ = ["criterion_profile_group"]
