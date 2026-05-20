"""Observability doctor subcommands for Bernstein.

This package collects the per-backend ``bernstein doctor`` probes for
the operator-facing observability surface.

Currently registered subcommands:

* ``bernstein doctor glitchtip`` -- GlitchTip issue counts and the top
  unresolved issues for the last 24h, plus a 7-day trend.
* ``bernstein doctor dt`` -- Dependency-Track vulnerability counts by
  severity for the configured project.
* ``bernstein doctor code-scanning`` -- GitHub Code Scanning alerts by
  severity for the current repository.
* ``bernstein doctor observe`` -- umbrella that runs every backend
  probe (Sonar, GlitchTip, Dependency-Track, Code Scanning) and renders
  one aggregated table. Supports ``--json`` and ``--watch``.

The Sonar probe lives in a separate module
(``bernstein.cli.commands.doctor.sonar``) when present, owned by its
sibling agent. The umbrella picks it up at runtime via
``bernstein.cli.commands.doctor.backends`` so the doctor surface stays
uniform.

Each per-backend module exposes a ``register(parent_group)`` helper
that the CLI bootstrap calls from
:mod:`bernstein.cli.commands.advanced_cmd` (and re-asserted from
:mod:`bernstein.cli.main`) so wiring is explicit and side-effect-free
outside the doctor group.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bernstein.cli.commands.doctor.backends import (
    BackendReport,
    MetricRow,
    ProbeStatus,
    apply_deltas,
    load_previous,
    probe_code_scanning,
    probe_dt,
    probe_glitchtip,
    probe_sonar,
    save_snapshot,
)
from bernstein.cli.commands.doctor.code_scanning import (
    code_scanning_cmd,
)
from bernstein.cli.commands.doctor.code_scanning import (
    register as register_code_scanning,
)
from bernstein.cli.commands.doctor.dt import (
    dt_cmd,
)
from bernstein.cli.commands.doctor.dt import (
    register as register_dt,
)
from bernstein.cli.commands.doctor.glitchtip import (
    glitchtip_cmd,
)
from bernstein.cli.commands.doctor.glitchtip import (
    register as register_glitchtip,
)
from bernstein.cli.commands.doctor.observe import (
    observe_cmd,
)
from bernstein.cli.commands.doctor.observe import (
    register as register_observe,
)

if TYPE_CHECKING:
    import click

__all__ = [
    "BackendReport",
    "MetricRow",
    "ProbeStatus",
    "apply_deltas",
    "code_scanning_cmd",
    "dt_cmd",
    "glitchtip_cmd",
    "load_previous",
    "observe_cmd",
    "probe_code_scanning",
    "probe_dt",
    "probe_glitchtip",
    "probe_sonar",
    "register_code_scanning",
    "register_dt",
    "register_glitchtip",
    "register_observe",
    "save_snapshot",
]


def register_all(parent_group: click.Group) -> None:
    """Attach every per-backend subcommand to a Click group."""

    register_glitchtip(parent_group)
    register_dt(parent_group)
    register_code_scanning(parent_group)
    register_observe(parent_group)
