"""CLI command group: ``bernstein quality`` -- citation/reference verifier.

Subcommand:

* ``citations`` -- read a file (or stdin via ``-``) and verify every
  citation-like span resolves. Prints a human-readable summary and
  optionally a JSON report.

The verifier itself lives in :mod:`bernstein.core.quality.citation_verifier`;
this module is the thin CLI wrapper that wires it into Click.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from bernstein.core.quality.citation_verifier import CitationReport, verify_citations


@click.group("quality")
def quality_group() -> None:
    """Run on-demand quality gates (citation verifier, etc.)."""


@quality_group.command("citations")
@click.argument("path", type=click.Path(dir_okay=False, path_type=Path), required=False)
@click.option("--offline", is_flag=True, help="Skip every network probe.")
@click.option(
    "--allowed-host",
    "allowed_hosts",
    multiple=True,
    help="Allow-list of hostnames (repeatable). URLs not on the list are skipped.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit a JSON report instead of a human-readable summary.",
)
@click.option(
    "--repo-root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Repository root for filesystem path citations.",
)
def citations_cmd(
    path: Path | None,
    offline: bool,
    allowed_hosts: tuple[str, ...],
    as_json: bool,
    repo_root: Path | None,
) -> None:
    """Verify citations inside PATH (or read from stdin when PATH is omitted)."""
    text = sys.stdin.read() if path is None else path.read_text(encoding="utf-8", errors="replace")

    report = verify_citations(
        text,
        offline=offline,
        allowed_hosts=list(allowed_hosts) if allowed_hosts else None,
        repo_root=repo_root,
    )

    if as_json:
        click.echo(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        _print_human(report)

    if not report.ok:
        sys.exit(1)


def _print_human(report: CitationReport) -> None:
    """Render *report* as a compact human-readable summary."""
    click.echo(
        f"total={report.total}"
        f" resolved={len(report.resolved)}"
        f" unresolved={len(report.unresolved)}"
        f" skipped={len(report.skipped)}"
        f" suspicious={len(report.suspicious)}",
    )
    if report.unresolved:
        click.echo("unresolved:")
        for c in report.unresolved:
            click.echo(f"  - {c.kind}: {c.value} (offset {c.offset})")
    if report.suspicious:
        click.echo("suspicious:")
        for c in report.suspicious:
            click.echo(f"  - {c.kind}: {c.value} (offset {c.offset})")
