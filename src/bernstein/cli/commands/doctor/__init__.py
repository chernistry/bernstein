"""Observability doctor subcommands for Bernstein.

This package collects the per-backend ``bernstein doctor`` probes for
the operator-facing observability surface.

Currently registered subcommands:

* ``bernstein doctor glitchtip`` -- GlitchTip issue counts and the top
  unresolved issues for the last 24h, plus a 7-day trend.

Each per-backend module exposes a ``register(parent_group)`` function
that the CLI bootstrap calls from
:mod:`bernstein.cli.commands.advanced_cmd` so the subcommands attach to
the existing ``bernstein doctor`` Click group without circular imports.
"""

from __future__ import annotations

from bernstein.cli.commands.doctor.glitchtip import (
    glitchtip_cmd,
)
from bernstein.cli.commands.doctor.glitchtip import (
    register as register_glitchtip,
)

__all__ = [
    "glitchtip_cmd",
    "register_glitchtip",
]


def register_all(parent_group: object) -> None:
    """Attach every per-backend subcommand to a Click group."""

    register_glitchtip(parent_group)
