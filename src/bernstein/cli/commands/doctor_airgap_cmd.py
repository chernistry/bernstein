"""Renderer for ``bernstein doctor airgap``.

Pure CLI layer; the checks themselves live in
:mod:`bernstein.core.distribution.doctor_airgap`. The renderer
chooses between human-readable Rich output and JSON depending on
the parent ``--json`` flag, and translates the aggregate report
into a process exit code.
"""

from __future__ import annotations

import json
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

if TYPE_CHECKING:
    from pathlib import Path

_STATUS_STYLE: dict[CheckStatus, str] = {
    CheckStatus.PASS: "green",
    CheckStatus.WARN: "yellow",
    CheckStatus.FAIL: "red",
}


def run_doctor_airgap(*, workdir: Path | None = None, as_json: bool = False) -> int:
    """Run the air-gap battery and render the report. Returns the exit code."""
    report = run_airgap_checks(workdir=workdir)
    if as_json:
        _render_json(report)
    else:
        _render_human(report)
    return 0 if report.ok else 1


def _render_json(report: AirgapReport) -> None:
    payload = {
        "ok": report.ok,
        "checks": [
            {
                **asdict(check),
                "status": check.status.value,
            }
            for check in report.checks
        ],
    }
    console.print_json(json.dumps(payload))


def _render_human(report: AirgapReport) -> None:
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
