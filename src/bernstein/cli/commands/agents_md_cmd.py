"""``bernstein agents-md`` -- canonical AGENTS.md generator + cross-CLI sync.

Subcommands:

* ``bernstein agents-md generate`` -- print canonical AGENTS.md to stdout.
* ``bernstein agents-md write [--target T]`` -- write one target's files.
  ``T`` ∈ ``canonical | cursor | claude | aider | goose``. Default:
  ``canonical``.
* ``bernstein agents-md sync`` -- write *all* target formats; the
  killer-feature command.
* ``bernstein agents-md verify [--target T]`` -- exit non-zero when any
  on-disk file diverges from the generated content. CI-friendly.
* ``bernstein agents-md diff [--target T]`` -- print a human-readable
  unified diff between disk and generated; exit 0 either way.

Design follows ``bernstein.cli.commands.lineage_cmd``: click ``@group``
with subcommands declared inline (small ones) or attached at module
bottom (heavier sibling files). All heavy imports are lazy inside command
bodies so the top-level CLI startup stays fast.
"""

from __future__ import annotations

import difflib
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from bernstein.core.knowledge.agents_md_bridge import BridgeOutput, Target


_TARGET_CHOICES = ("canonical", "cursor", "claude", "aider", "goose")


# ---------------------------------------------------------------------------
# Group
# ---------------------------------------------------------------------------


@click.group(name="agents-md", invoke_without_command=True)
@click.pass_context
def agents_md_cmd(ctx: click.Context) -> None:
    """Canonical AGENTS.md generator with cross-CLI rewrite.

    \b
    Examples:
      bernstein agents-md generate            # print canonical AGENTS.md
      bernstein agents-md sync                # write all 5 target files
      bernstein agents-md write --target cursor
      bernstein agents-md verify              # exit 1 if any file is stale
      bernstein agents-md diff --target claude
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


@agents_md_cmd.command(name="generate")
@click.option(
    "--workdir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=Path.cwd,
    show_default="cwd",
    help="Repository root.",
)
@click.option(
    "--target",
    type=click.Choice(_TARGET_CHOICES),
    default="canonical",
    show_default=True,
    help="Which target's content to print.",
)
@click.option(
    "--repo-name",
    default=None,
    help="Display name for the H1. Defaults to the workdir basename.",
)
def agents_md_generate(workdir: Path, target: str, repo_name: str | None) -> None:
    """Print one target's content to stdout. No file is written."""
    sections, name = _generate_sections(workdir, repo_name)
    output = _render_target(sections, target, name)
    # Each target's BridgeOutput has 1+ files; print them concatenated with
    # a clear separator so the operator can see the structure.
    files = list(output.files.items())
    if len(files) == 1:
        click.echo(files[0][1])
        return
    for relpath, content in files:
        click.echo(f"--- {relpath} ---")
        click.echo(content)


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------


@agents_md_cmd.command(name="write")
@click.option(
    "--workdir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=Path.cwd,
    show_default="cwd",
    help="Repository root.",
)
@click.option(
    "--target",
    type=click.Choice(_TARGET_CHOICES),
    default="canonical",
    show_default=True,
    help="Which target's files to write.",
)
@click.option(
    "--repo-name",
    default=None,
    help="Display name for the H1. Defaults to the workdir basename.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be written without touching disk.",
)
def agents_md_write(workdir: Path, target: str, repo_name: str | None, dry_run: bool) -> None:
    """Write one target's files to disk."""
    sections, name = _generate_sections(workdir, repo_name)
    output = _render_target(sections, target, name)
    written = _write_output(output, workdir, dry_run=dry_run)
    if dry_run:
        click.echo(f"[dry-run] {len(output.files)} file(s) would be written under {workdir}")
        for rel in output.files:
            click.echo(f"  · {rel}")
    else:
        click.echo(f"Wrote {written} file(s) under {workdir}")


# ---------------------------------------------------------------------------
# sync — the killer-feature command
# ---------------------------------------------------------------------------


@agents_md_cmd.command(name="sync")
@click.option(
    "--workdir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=Path.cwd,
    show_default="cwd",
    help="Repository root.",
)
@click.option(
    "--repo-name",
    default=None,
    help="Display name for the H1. Defaults to the workdir basename.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be written without touching disk.",
)
def agents_md_sync(workdir: Path, repo_name: str | None, dry_run: bool) -> None:
    """Write all target formats so all five files agree."""
    from bernstein.core.knowledge.agents_md_bridge import render_all

    sections, name = _generate_sections(workdir, repo_name)
    outputs = render_all(sections, repo_name=name)
    total = 0
    for target, output in outputs.items():
        written = _write_output(output, workdir, dry_run=dry_run)
        total += written
        if dry_run:
            for rel in output.files:
                click.echo(f"[dry-run] would write {rel}  (target={target})")
        else:
            for rel in output.files:
                click.echo(f"  · {rel}  ({target})")
    if dry_run:
        click.echo(f"[dry-run] {total} file(s) across {len(outputs)} target(s) would be synced")
    else:
        click.echo(f"Synced {total} file(s) across {len(outputs)} target(s) under {workdir}")


# ---------------------------------------------------------------------------
# verify — CI-friendly drift detector
# ---------------------------------------------------------------------------


@agents_md_cmd.command(name="verify")
@click.option(
    "--workdir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=Path.cwd,
    show_default="cwd",
    help="Repository root.",
)
@click.option(
    "--target",
    type=click.Choice([*_TARGET_CHOICES, "all"]),
    default="all",
    show_default=True,
    help="Which target(s) to verify against on-disk content.",
)
@click.option(
    "--repo-name",
    default=None,
    help="Display name for the H1. Defaults to the workdir basename.",
)
def agents_md_verify(workdir: Path, target: str, repo_name: str | None) -> None:
    """Exit 1 if any on-disk file diverges from the generated content.

    Designed for CI gating::

        bernstein agents-md verify || (echo 'AGENTS.md drift; run sync' && exit 1)
    """
    from bernstein.core.knowledge.agents_md_bridge import (
        ALL_TARGETS,
        render,
    )

    sections, name = _generate_sections(workdir, repo_name)
    targets: tuple[Target, ...] = ALL_TARGETS if target == "all" else (target,)  # type: ignore[assignment]

    drift_count = 0
    for t in targets:
        output = render(sections, t, repo_name=name)
        for rel, expected in output.files.items():
            on_disk = workdir / rel
            if not on_disk.is_file():
                click.echo(f"MISSING  {rel}  (target={t})")
                drift_count += 1
                continue
            actual = on_disk.read_text(encoding="utf-8")
            if actual != expected:
                click.echo(f"DRIFT    {rel}  (target={t})")
                drift_count += 1
    if drift_count:
        click.echo(
            f"\n{drift_count} file(s) drift. Run `bernstein agents-md sync` to fix.",
            err=True,
        )
        sys.exit(1)
    click.echo(f"OK       all {sum(len(render(sections, t, repo_name=name).files) for t in targets)} file(s) in sync")


# ---------------------------------------------------------------------------
# diff — informational, no exit-code drama
# ---------------------------------------------------------------------------


@agents_md_cmd.command(name="diff")
@click.option(
    "--workdir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=Path.cwd,
    show_default="cwd",
    help="Repository root.",
)
@click.option(
    "--target",
    type=click.Choice([*_TARGET_CHOICES, "all"]),
    default="all",
    show_default=True,
    help="Which target(s) to diff.",
)
@click.option(
    "--repo-name",
    default=None,
    help="Display name for the H1. Defaults to the workdir basename.",
)
def agents_md_diff(workdir: Path, target: str, repo_name: str | None) -> None:
    """Print unified diff between on-disk and generated for each target file."""
    from bernstein.core.knowledge.agents_md_bridge import (
        ALL_TARGETS,
        render,
    )

    sections, name = _generate_sections(workdir, repo_name)
    targets: tuple[Target, ...] = ALL_TARGETS if target == "all" else (target,)  # type: ignore[assignment]

    any_diff = False
    for t in targets:
        output = render(sections, t, repo_name=name)
        for rel, expected in output.files.items():
            on_disk = workdir / rel
            actual = on_disk.read_text(encoding="utf-8") if on_disk.is_file() else ""
            if actual == expected:
                continue
            any_diff = True
            click.echo(f"\n# {rel}  (target={t})\n")
            for line in difflib.unified_diff(
                actual.splitlines(keepends=True),
                expected.splitlines(keepends=True),
                fromfile=f"a/{rel}",
                tofile=f"b/{rel}",
                n=3,
            ):
                click.echo(line, nl=False)
    if not any_diff:
        click.echo("No drift across selected target(s).")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _generate_sections(workdir: Path, repo_name: str | None) -> tuple[list, str]:
    """Run the generator + return ``(sections, repo_name)``.

    ``repo_name`` falls back to the basename of ``workdir`` when not
    provided, so the canonical H1 always says something useful.
    """
    from bernstein.core.knowledge.agents_md_generator import generate

    sections = generate(workdir.resolve())
    if not sections:
        click.echo(f"No content derived from {workdir} — is this a repository?", err=True)
        sys.exit(2)
    return sections, repo_name or workdir.resolve().name


def _render_target(sections: list, target: str, repo_name: str) -> BridgeOutput:
    """Single-target render bridge."""
    from bernstein.core.knowledge.agents_md_bridge import render

    return render(sections, target, repo_name=repo_name)  # type: ignore[arg-type]


def _write_output(output: BridgeOutput, repo_root: Path, *, dry_run: bool) -> int:
    """Write one ``BridgeOutput`` under ``repo_root``. Returns count written."""
    if dry_run:
        return 0
    written = 0
    for rel, content in output.files.items():
        path = repo_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written += 1
    return written


__all__ = ["agents_md_cmd"]
