"""``bernstein skills``: list / show / verify skill packs (oai-004).

Layered customisation (issue 1624) is wired in via the
``--layered`` / ``--per-layer`` options on ``list`` and ``show``: when
set, the CLI consults :mod:`bernstein.core.skills.layered` instead of
the in-package skill loader. The two paths intentionally coexist so
existing operators keep their plugin-source view while teams adopting
the BASE/TEAM/USER layout get the layered view on demand.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from bernstein import get_templates_dir
from bernstein.cli.helpers import console


@click.group("skills")
def skills_group() -> None:
    """List and inspect progressive-disclosure skill packs.

    \b
      bernstein skills list           # compact overview
      bernstein skills list --layered # show layered (base/team/user) view
      bernstein skills show backend   # print SKILL.md body
      bernstein skills show backend --reference python-conventions.md
      bernstein skills show backend --per-layer  # merged + per-layer diff
    """


@skills_group.command("list")
@click.option(
    "--no-plugins",
    "no_plugins",
    is_flag=True,
    default=False,
    help="Skip third-party ``bernstein.skill_sources`` plugins.",
)
@click.option(
    "--layered",
    "layered",
    is_flag=True,
    default=False,
    help="List skills from the BASE/TEAM/USER layers, showing layer-of-origin.",
)
def skills_list(no_plugins: bool, layered: bool) -> None:
    """List every discoverable skill with a one-line description."""
    from rich.table import Table

    if layered:
        _skills_list_layered()
        return

    from bernstein.core.planning.role_resolver import get_loader

    templates_root = get_templates_dir(Path.cwd())
    templates_roles_dir = templates_root / "roles"
    try:
        loader = get_loader(templates_roles_dir, include_plugins=not no_plugins)
    except Exception as exc:
        console.print(f"[red]Failed to load skill index:[/red] {exc}")
        raise SystemExit(1) from exc

    skills = loader.list_all()
    if not skills:
        console.print(f"[dim]No skill packs found. Expected at {templates_root / 'skills'}[/dim]")
        return

    table = Table(
        title="Skill packs",
        show_lines=False,
        header_style="bold cyan",
    )
    table.add_column("NAME", style="dim", min_width=14)
    table.add_column("DESCRIPTION", min_width=50)
    table.add_column("REFS", justify="right", min_width=4)
    table.add_column("SCRIPTS", justify="right", min_width=6)
    table.add_column("SOURCE", min_width=8)

    for skill in skills:
        description = skill.description.strip().replace("\n", " ")
        if len(description) > 100:
            description = description[:97] + "..."
        table.add_row(
            skill.name,
            description,
            str(len(skill.references)),
            str(len(skill.scripts)),
            skill.source_name,
        )

    console.print(table)
    console.print(f"\n[dim]{len(skills)} skill(s) total[/dim]")


@skills_group.command("show")
@click.argument("name")
@click.option("--reference", "reference", help="Reference filename to load.")
@click.option("--script", "script", help="Script filename to load.")
@click.option(
    "--per-layer",
    "per_layer",
    is_flag=True,
    default=False,
    help="Print the merged skill plus a per-layer diff (BASE/TEAM/USER).",
)
def skills_show(name: str, reference: str | None, script: str | None, per_layer: bool) -> None:
    """Print the SKILL.md body for a skill (optionally a reference/script)."""
    if per_layer:
        _skills_show_layered(name)
        return

    from bernstein.core.skills.load_skill_tool import load_skill

    templates_root = get_templates_dir(Path.cwd())
    templates_roles_dir = templates_root / "roles"
    result = load_skill(
        name=name,
        reference=reference,
        script=script,
        templates_roles_dir=templates_roles_dir,
    )
    if result.error:
        console.print(f"[red]{result.error}[/red]")
        raise SystemExit(1)

    if reference is not None and result.reference_content is not None:
        console.print(result.reference_content)
        return
    if script is not None and result.script_content is not None:
        console.print(result.script_content)
        return

    console.print(result.body)
    if result.available_references:
        console.print("\n[dim]references: " + ", ".join(result.available_references) + "[/dim]")
    if result.available_scripts:
        console.print("[dim]scripts: " + ", ".join(result.available_scripts) + "[/dim]")


# ---------------------------------------------------------------------------
# Layered (issue 1624) helpers
# ---------------------------------------------------------------------------


def _skills_list_layered() -> None:
    """Render the layered (BASE/TEAM/USER) skill table."""
    from rich.table import Table

    from bernstein.core.skills.layered import LayeredSkillPaths, list_skills

    paths = LayeredSkillPaths.defaults()
    entries = list_skills(paths=paths)

    if not entries:
        console.print(
            "[dim]No layered skills found. Expected one of:\n"
            f"  base: {paths.base}\n  team: {paths.team}\n  user: {paths.user}[/dim]"
        )
        return

    table = Table(
        title="Skills (layered view)",
        show_lines=False,
        header_style="bold cyan",
    )
    table.add_column("NAME", style="dim", min_width=14)
    table.add_column("BASE", justify="center", min_width=4)
    table.add_column("TEAM", justify="center", min_width=4)
    table.add_column("USER", justify="center", min_width=4)
    table.add_column("ORIGIN", min_width=12)

    for name, layers in entries:
        labels = [layer.label for layer in layers]
        table.add_row(
            name,
            "x" if "base" in labels else "",
            "x" if "team" in labels else "",
            "x" if "user" in labels else "",
            "+".join(labels),
        )

    console.print(table)
    console.print(f"\n[dim]{len(entries)} skill(s) total[/dim]")


def _skills_show_layered(name: str) -> None:
    """Render the merged skill plus a per-layer diff."""
    from bernstein.core.skills.layered import (
        LayeredSkillPaths,
        SkillNotFoundError,
        load_skill,
        per_layer_view,
    )

    paths = LayeredSkillPaths.defaults()
    try:
        merged = load_skill(name, paths=paths)
    except SkillNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc

    console.print(f"[bold cyan]merged skill: {merged.name}[/bold cyan]")
    console.print("[dim]layers present: " + ", ".join(layer.label for layer in merged.layers_present) + "[/dim]")
    console.print(json.dumps(merged.as_dict(), indent=2, sort_keys=True))

    fragments = per_layer_view(name, paths=paths)
    for layer in sorted(fragments, key=lambda layer: layer.value):
        console.print(f"\n[bold]layer: {layer.label}[/bold] ([dim]{paths.for_layer(layer)}[/dim])")
        console.print(json.dumps(fragments[layer], indent=2, sort_keys=True))
