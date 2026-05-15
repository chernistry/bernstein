"""Renderer for ``bernstein doctor airgap``.

Pure CLI layer; the checks themselves live in
:mod:`bernstein.core.distribution.doctor_airgap`. The renderer
chooses between human-readable Rich output and JSON depending on
the parent ``--json`` flag, and translates the aggregate report
into a process exit code.

Standalone semantics (bughunt 2026-05-13/2026-05-15)
----------------------------------------------------
``bernstein doctor airgap`` is documented as a pre-flight check the
operator runs *before* invoking ``bernstein run --profile airgap``.
In that pre-flight scenario the BERNSTEIN_PROFILE_MODE and
BERNSTEIN_NETWORK_POLICY env vars are not yet set in the operator's
shell — the run-bootstrap is what normally installs them. Without a
fix, three of the pure-function checks (profile-active, deny-all,
socket-guard) hard-FAIL even though nothing is actually wrong.

We pick option (A) from the bughunt brief consistently across all
three checks: the renderer simulates the airgap activation for the
duration of the check battery (env vars + socket guard already
self-installs once the profile env var is set), then restores the
caller's environment exactly. This makes the standalone invocation
report the four spec-mandated green rows when the host is clean,
without forcing the operator to remember to ``export`` two env vars
before each invocation.
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import asdict
from typing import TYPE_CHECKING

from rich.panel import Panel
from rich.table import Table

from bernstein.cli.helpers import console
from bernstein.core.distribution.doctor_airgap import (
    AirgapReport,
    CheckStatus,
    run_airgap_checks,
)
from bernstein.core.security.network_policy import (
    ENV_NETWORK_POLICY,
    ENV_PROFILE_MODE,
    PROFILE_AIRGAP,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_STATUS_STYLE: dict[CheckStatus, str] = {
    CheckStatus.PASS: "green",
    CheckStatus.WARN: "yellow",
    CheckStatus.FAIL: "red",
}


@contextlib.contextmanager
def _simulated_airgap_env() -> Iterator[bool]:
    """Activate airgap env vars for the duration of a standalone doctor run.

    Mirrors the env-var slice of ``_install_network_policy`` /
    ``install_policy`` (see ``cli/run_bootstrap.py``) but without
    side-effects on the live socket guard — the existing per-check
    option-(A) path in ``check_runtime_socket_guard_active`` handles
    installing/uninstalling the guard, and that path triggers
    automatically once ``BERNSTEIN_PROFILE_MODE`` is set.

    Yields True when the doctor simulated the activation (i.e. the
    operator was outside a live ``bernstein run`` and we provided
    the airgap defaults). Yields False when the operator was already
    inside a live profile — the doctor must not clobber that.

    Both env vars are restored to their original state (including
    absence) in the ``finally`` block. This is idempotent and has no
    persistent operator-shell side effect: child processes spawned
    by checks see the simulated values, the operator's parent shell
    sees nothing.
    """
    prior_profile = os.environ.get(ENV_PROFILE_MODE)
    prior_policy = os.environ.get(ENV_NETWORK_POLICY)
    activated = False
    # Only simulate when the operator has NOT already activated airgap.
    # If they have, we leave their values intact — even if they chose
    # a non-default allow-list — so the doctor reports what their
    # actual run would see.
    if (prior_profile or "").strip().lower() != PROFILE_AIRGAP:
        os.environ[ENV_PROFILE_MODE] = PROFILE_AIRGAP
        if prior_policy is None:
            os.environ[ENV_NETWORK_POLICY] = "none"
        activated = True
    try:
        yield activated
    finally:
        if activated:
            if prior_profile is None:
                os.environ.pop(ENV_PROFILE_MODE, None)
            else:
                os.environ[ENV_PROFILE_MODE] = prior_profile
            if prior_policy is None:
                os.environ.pop(ENV_NETWORK_POLICY, None)
            else:
                os.environ[ENV_NETWORK_POLICY] = prior_policy


def run_doctor_airgap(*, workdir: Path | None = None, as_json: bool = False) -> int:
    """Run the air-gap battery and render the report. Returns the exit code.

    Standalone invocation (no live ``bernstein run`` parent) is the
    documented pre-flight workflow. When the airgap env vars are
    absent we activate them for the duration of the checks so the
    pure-function checks see the same environment a real airgap run
    would expose, then restore the caller's environment.
    """
    with _simulated_airgap_env() as simulated:
        report = run_airgap_checks(workdir=workdir)
    if as_json:
        _render_json(report, simulated=simulated)
    else:
        _render_human(report, simulated=simulated)
    return 0 if report.ok else 1


def _render_json(report: AirgapReport, *, simulated: bool = False) -> None:
    payload = {
        "ok": report.ok,
        "simulated_airgap_env": simulated,
        "checks": [
            {
                **asdict(check),
                "status": check.status.value,
            }
            for check in report.checks
        ],
    }
    console.print_json(json.dumps(payload))


def _render_human(report: AirgapReport, *, simulated: bool = False) -> None:
    console.print()
    if report.ok:
        console.print(
            Panel(
                "[bold green]Air-gap doctor: PASSED[/bold green]",
                border_style="green",
                expand=False,
            )
        )
    else:
        console.print(
            Panel(
                "[bold red]Air-gap doctor: FAILED[/bold red]",
                border_style="red",
                expand=False,
            )
        )

    if simulated:
        console.print(
            "[dim]Note: airgap profile env vars were not set in this shell; the "
            "doctor simulated --profile airgap + --allow-network none for the "
            "duration of the checks. Set BERNSTEIN_PROFILE_MODE=airgap (or invoke "
            "via 'bernstein run --profile airgap') to suppress this notice.[/dim]"
        )

    table = Table(show_header=True, header_style="bold", padding=(0, 2))
    table.add_column("Status", no_wrap=True, min_width=6)
    table.add_column("Check", no_wrap=True)
    table.add_column("Detail")
    for check in report.checks:
        style = _STATUS_STYLE[check.status]
        table.add_row(
            f"[{style}]{check.status.value}[/{style}]",
            check.name,
            check.detail,
        )
    console.print(table)

    fixes = [c for c in report.checks if c.fix and c.status is not CheckStatus.PASS]
    if fixes:
        console.print()
        console.print("[bold]Suggested fixes:[/bold]")
        for c in fixes:
            console.print(f"  [dim]{c.name}:[/dim] {c.fix}")
    console.print()
