"""``bernstein doctor dt`` -- Dependency-Track insights surface.

Reads ``DTRACK_URL``, ``DTRACK_TOKEN``, and ``DTRACK_PROJECT`` (project
UUID) from the environment. Reports vulnerability counts by severity
for the configured project. Soft-fails when not configured.
"""

from __future__ import annotations

import click

from bernstein.cli.commands.doctor._render import render_single_backend
from bernstein.cli.commands.doctor.backends import probe_dt
from bernstein.cli.helpers import console


@click.command("dt")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit machine-readable JSON instead of the Rich table.",
)
def dt_cmd(as_json: bool) -> None:
    """Report Dependency-Track findings for the configured project.

    \b
    Reads:
      DTRACK_URL           (e.g. https://dtrack.example.com)
      DTRACK_TOKEN         (API key with VULNERABILITY_ANALYSIS scope)
      DTRACK_PROJECT       (project UUID)

    \b
    Examples:
      bernstein doctor dt
      bernstein doctor dt --json
    """
    report = probe_dt()
    exit_code = render_single_backend(report, console=console, as_json=as_json)
    raise SystemExit(exit_code)


def register(parent: click.Group) -> None:
    """Attach the Dependency-Track subcommand to the parent ``doctor`` group."""

    parent.add_command(dt_cmd)
