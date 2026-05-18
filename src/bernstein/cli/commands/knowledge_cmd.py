"""CLI surface for the diary + synthesis knowledge layer.

Subcommands:

* ``bernstein knowledge diary list`` -- list diaries in the active SDD tree.
* ``bernstein knowledge diary show <task>`` -- pretty-print one entry.
* ``bernstein knowledge synthesize --since <duration>`` -- run the
  synthesis pass and write a markdown report. The ``--apply`` flag flips
  the HITL gate and writes the approved marker.

The module is intentionally thin: business logic lives in
:mod:`bernstein.core.knowledge.diary` and
:mod:`bernstein.core.knowledge.synthesizer`. The CLI only owns argument
parsing, path resolution, and Rich output formatting.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click

from bernstein.core.knowledge.diary import (
    DiaryError,
    load_diaries,
    load_diary,
)
from bernstein.core.knowledge.synthesizer import (
    DEFAULT_JACCARD_THRESHOLD,
    DEFAULT_MIN_CLUSTER_SIZE,
    SynthesizerError,
    approve,
    parse_duration,
    synthesize,
    write_report,
)

if TYPE_CHECKING:
    from bernstein.core.knowledge.diary import DiaryEntry


def _resolve_sdd_dir(explicit: str | None) -> Path:
    """Return the ``.sdd`` directory to operate on.

    Resolution order:
      1. Explicit ``--sdd-dir`` flag value.
      2. ``$BERNSTEIN_SDD_DIR`` env var.
      3. ``./.sdd`` under the current working directory.
    """
    if explicit:
        return Path(explicit).resolve()
    import os

    env = os.environ.get("BERNSTEIN_SDD_DIR")
    if env:
        return Path(env).resolve()
    return (Path.cwd() / ".sdd").resolve()


@click.group("knowledge")
def knowledge_group() -> None:
    """Diary + synthesis knowledge layer.

    \b
    Examples:
      bernstein knowledge diary list
      bernstein knowledge diary show task-42
      bernstein knowledge synthesize --since 14d
      bernstein knowledge synthesize --since 7d --apply
    """


@knowledge_group.group("diary")
def diary_group() -> None:
    """Inspect per-task diary entries."""


@diary_group.command("list")
@click.option(
    "--sdd-dir",
    default=None,
    help="Override the SDD root directory.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit machine-readable JSON instead of a table.",
)
def diary_list_cmd(sdd_dir: str | None, as_json: bool) -> None:
    """List every diary entry in the active SDD tree."""
    target = _resolve_sdd_dir(sdd_dir)
    entries = load_diaries(target)
    if as_json:
        click.echo(json.dumps([e.to_dict() for e in entries], indent=2, sort_keys=True))
        return
    if not entries:
        click.echo(f"No diary entries under {target / 'runtime' / 'diaries'}.")
        return
    click.echo(f"{'task_id':<32} {'created_at':<26} {'tags':<40}")
    click.echo("-" * 100)
    for entry in entries:
        tags = ", ".join(entry.tags[:6])
        if len(entry.tags) > 6:
            tags += ", ..."
        click.echo(f"{entry.task_id:<32} {entry.created_at:<26} {tags:<40}")


@diary_group.command("show")
@click.argument("task_id")
@click.option(
    "--sdd-dir",
    default=None,
    help="Override the SDD root directory.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit machine-readable JSON instead of formatted text.",
)
def diary_show_cmd(task_id: str, sdd_dir: str | None, as_json: bool) -> None:
    """Show the diary entry for ``task_id``."""
    target = _resolve_sdd_dir(sdd_dir)
    diary_path = target / "runtime" / "diaries" / f"{task_id}.json"
    if not diary_path.exists():
        click.echo(f"No diary entry for task {task_id!r} at {diary_path}.", err=True)
        sys.exit(1)
    try:
        entry = load_diary(diary_path)
    except DiaryError as exc:
        click.echo(f"Failed to load diary {diary_path}: {exc}", err=True)
        sys.exit(1)
    if as_json:
        click.echo(json.dumps(entry.to_dict(), indent=2, sort_keys=True))
        return
    _render_entry(entry)


def _render_entry(entry: DiaryEntry) -> None:
    """Pretty-print a single diary entry to stdout."""
    click.echo(f"Diary entry: {entry.task_id}")
    click.echo(f"  created_at:     {entry.created_at}")
    click.echo(f"  schema_version: {entry.schema_version}")
    click.echo(f"  redaction_hash: {entry.redaction_hash}")
    click.echo(f"  tags:           {', '.join(entry.tags) if entry.tags else '(none)'}")
    click.echo("")
    _render_section("Tried", entry.tried)
    _render_section("Worked", entry.worked)
    _render_section("Failed", entry.failed)
    click.echo("Rationale:")
    click.echo(f"  {entry.rationale or '(none)'}")


def _render_section(name: str, bullets: tuple[str, ...]) -> None:
    """Print a labelled bullet section."""
    click.echo(f"{name}:")
    if not bullets:
        click.echo("  (none)")
    else:
        for bullet in bullets:
            click.echo(f"  - {bullet}")
    click.echo("")


@knowledge_group.command("synthesize")
@click.option(
    "--since",
    default="14d",
    show_default=True,
    help="Lookback window. Accepts NNd, NNh, NNm, NNs, or a bare integer (days).",
)
@click.option(
    "--threshold",
    type=float,
    default=DEFAULT_JACCARD_THRESHOLD,
    show_default=True,
    help="Tag-overlap Jaccard threshold for clustering (0..1).",
)
@click.option(
    "--min-cluster-size",
    type=int,
    default=DEFAULT_MIN_CLUSTER_SIZE,
    show_default=True,
    help="Drop clusters smaller than this size.",
)
@click.option(
    "--apply",
    "apply_flag",
    is_flag=True,
    default=False,
    help="Flip the HITL gate: mark the report as approved and persist it.",
)
@click.option(
    "--sdd-dir",
    default=None,
    help="Override the SDD root directory.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print the report to stdout without writing to disk.",
)
def synthesize_cmd(
    since: str,
    threshold: float,
    min_cluster_size: int,
    apply_flag: bool,
    sdd_dir: str | None,
    dry_run: bool,
) -> None:
    """Aggregate recent diaries into themes and write a markdown report.

    Without ``--apply`` the report lands on disk with ``approved: false``
    in its frontmatter; downstream consumers MUST refuse to mutate role
    prompts based on an unapproved report.
    """
    target = _resolve_sdd_dir(sdd_dir)
    try:
        delta = parse_duration(since)
    except SynthesizerError as exc:
        click.echo(f"Invalid --since value: {exc}", err=True)
        sys.exit(2)
    window_days = max(1, delta.days or 1)
    entries = load_diaries(target)
    try:
        report = synthesize(
            entries,
            window_days=window_days,
            threshold=threshold,
            min_cluster_size=min_cluster_size,
        )
    except SynthesizerError as exc:
        click.echo(f"Synthesis failed: {exc}", err=True)
        sys.exit(1)
    if apply_flag:
        report = approve(report)
    if dry_run:
        from bernstein.core.knowledge.synthesizer import render_report

        click.echo(render_report(report))
        return
    path = write_report(report, target)
    click.echo(f"Wrote synthesis report: {path}")
    click.echo(f"  themes:   {report.theme_count}")
    click.echo(f"  approved: {report.approved}")


__all__ = ["knowledge_group"]
