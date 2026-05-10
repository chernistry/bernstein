"""CLI command: ``bernstein export`` — generate shareable run reports."""

from __future__ import annotations

import logging
from pathlib import Path

import click

logger = logging.getLogger(__name__)


@click.command("export")
@click.option(
    "--last",
    is_flag=True,
    default=False,
    help="Export the most recent completed run.",
)
@click.option(
    "--run-id",
    default=None,
    help="Specific run ID to export.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["html", "md"], case_sensitive=False),
    default="html",
    help="Output format (default: html).",
)
@click.option(
    "--output",
    "-o",
    "output_path",
    default=None,
    type=click.Path(),
    help="Output file path. Defaults to stdout or .sdd/reports/.",
)
@click.option(
    "--workdir",
    default=".",
    type=click.Path(exists=True),
    help="Project root directory.",
)
def export_cmd(
    last: bool,
    run_id: str | None,
    fmt: str,
    output_path: str | None,
    workdir: str,
) -> None:
    """Generate a shareable HTML or Markdown run report.

    \b
      bernstein export                    # latest run as HTML
      bernstein export --last             # same (explicit)
      bernstein export --run-id abc123    # specific run
      bernstein export --format md        # markdown instead
      bernstein export -o report.html     # write to file
    """
    from bernstein.core.observability.run_export import export_run_report

    if last and run_id:
        raise click.BadParameter("Cannot specify both --last and --run-id.")

    _run_id = run_id if run_id else None

    workdir_path = Path(workdir).resolve()
    out = export_run_report(
        workdir=workdir_path,
        run_id=_run_id,
        fmt=fmt,
        output_path=output_path,
    )

    logger.info("Report saved to %s", out)
