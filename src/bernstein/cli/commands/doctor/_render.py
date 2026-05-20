"""Shared rendering helpers for ``bernstein doctor`` observability probes."""

from __future__ import annotations

import json as _json
from typing import Any

from bernstein.cli.commands.doctor.backends import (
    BackendReport,
    MetricRow,
    ProbeStatus,
    apply_deltas,
    save_snapshot,
)

_STATUS_STYLE = {
    ProbeStatus.OK: "[green]OK[/green]",
    ProbeStatus.WARN: "[yellow]WARN[/yellow]",
    ProbeStatus.FAIL: "[red]FAIL[/red]",
    ProbeStatus.SKIPPED: "[dim]SKIPPED[/dim]",
    ProbeStatus.ERROR: "[red]ERROR[/red]",
}

_THRESHOLD_STYLE = {
    "ok": "[green]ok[/green]",
    "warn": "[yellow]warn[/yellow]",
    "fail": "[red]fail[/red]",
    "info": "[dim]-[/dim]",
}


def status_label(status: ProbeStatus) -> str:
    """Return the Rich-coloured status label for ``status``."""

    return _STATUS_STYLE.get(status, "[white]?[/white]")


def render_single_backend(
    report: BackendReport,
    *,
    console: Any,
    as_json: bool = False,
    persist: bool = True,
) -> int:
    """Render a single backend probe report and return an exit code."""

    apply_deltas(report)
    if persist:
        save_snapshot(report)

    if as_json:
        console.print_json(_json.dumps(report.to_dict()))
        return _exit_for(report.status)

    from rich.table import Table

    console.print(f"[bold]doctor {report.backend}[/bold]: {status_label(report.status)} {report.detail}")
    if report.error:
        console.print(f"  [red]error[/red]: {report.error}")
    if not report.metrics:
        return _exit_for(report.status)
    table = Table(show_header=True, header_style="bold")
    table.add_column("metric")
    table.add_column("value", justify="right")
    table.add_column("delta", justify="right")
    table.add_column("threshold", justify="right")
    table.add_column("status")
    for row in report.metrics:
        table.add_row(
            row.name,
            row.value,
            row.delta,
            row.threshold or "-",
            _THRESHOLD_STYLE.get(row.threshold_status, row.threshold_status),
        )
    console.print(table)
    return _exit_for(report.status)


def render_aggregate(
    reports: list[BackendReport],
    *,
    console: Any,
    as_json: bool = False,
    persist: bool = True,
) -> int:
    """Render the aggregated table for ``observe``."""

    for report in reports:
        apply_deltas(report)
        if persist:
            save_snapshot(report)

    if as_json:
        payload = {
            "summary": _summary(reports),
            "backends": [r.to_dict() for r in reports],
        }
        console.print_json(_json.dumps(payload))
        return _exit_for_aggregate(reports)

    from rich.table import Table

    table = Table(
        title="bernstein doctor observe",
        show_header=True,
        header_style="bold",
        title_justify="left",
    )
    table.add_column("backend", style="cyan")
    table.add_column("metric")
    table.add_column("value", justify="right")
    table.add_column("delta", justify="right")
    table.add_column("threshold", justify="right")
    table.add_column("status")
    for report in reports:
        if not report.metrics:
            table.add_row(
                report.backend,
                "[dim](no metrics)[/dim]",
                "-",
                "-",
                "-",
                status_label(report.status),
            )
            continue
        for row in report.metrics:
            table.add_row(
                report.backend,
                row.name,
                row.value,
                row.delta,
                row.threshold or "-",
                _THRESHOLD_STYLE.get(row.threshold_status, row.threshold_status),
            )
    console.print(table)
    counts = _summary(reports)
    console.print(
        "[dim]"
        f"ok={counts['ok']} warn={counts['warn']} fail={counts['fail']} "
        f"skipped={counts['skipped']} error={counts['error']}"
        "[/dim]"
    )
    return _exit_for_aggregate(reports)


def _exit_for(status: ProbeStatus) -> int:
    if status in (ProbeStatus.OK, ProbeStatus.SKIPPED):
        return 0
    return 1


def _exit_for_aggregate(reports: list[BackendReport]) -> int:
    if any(r.status in (ProbeStatus.FAIL, ProbeStatus.ERROR) for r in reports):
        return 1
    if any(r.status == ProbeStatus.WARN for r in reports):
        return 1
    return 0


def _summary(reports: list[BackendReport]) -> dict[str, int]:
    counts = {"ok": 0, "warn": 0, "fail": 0, "skipped": 0, "error": 0}
    for r in reports:
        counts[r.status.value] = counts.get(r.status.value, 0) + 1
    return counts


__all__ = [
    "MetricRow",
    "render_aggregate",
    "render_single_backend",
    "status_label",
]
