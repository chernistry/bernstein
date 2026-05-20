"""``bernstein doctor code-scanning`` -- GitHub Code Scanning surface.

Reads ``GITHUB_TOKEN`` and ``GITHUB_REPOSITORY`` (``owner/repo``) from
the environment. Reports open Code Scanning alerts grouped by
severity. Soft-fails when either env-var is missing.
"""

from __future__ import annotations

import click

from bernstein.cli.commands.doctor._render import render_single_backend
from bernstein.cli.commands.doctor.backends import probe_code_scanning
from bernstein.cli.helpers import console


@click.command("code-scanning")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit machine-readable JSON instead of the Rich table.",
)
def code_scanning_cmd(as_json: bool) -> None:
    """Report open GitHub Code Scanning alerts for the current repository.

    \b
    Reads:
      GITHUB_TOKEN         (token with security_events: read)
      GITHUB_REPOSITORY    (owner/repo, auto-populated in Actions)
      GITHUB_API_URL       (optional, defaults to https://api.github.com)

    \b
    Examples:
      bernstein doctor code-scanning
      bernstein doctor code-scanning --json
    """
    report = probe_code_scanning()
    exit_code = render_single_backend(report, console=console, as_json=as_json)
    raise SystemExit(exit_code)


def register(parent: click.Group) -> None:
    """Attach the Code Scanning subcommand to the parent ``doctor`` group."""

    parent.add_command(code_scanning_cmd)
