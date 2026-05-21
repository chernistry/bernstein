"""Renderer for ``bernstein doctor sonar``.

Surfaces SonarQube measures in the operator's terminal so the daily
flow includes coverage / smells / bugs / vulnerabilities / hotspots
without a context switch to the Sonar UI. Pure CLI layer; the API
client lives in :mod:`bernstein.core.observability.sonar`.

Output modes:
- Default: a Rich table with the headline numbers, severity row,
  and top-5 cognitive-complexity hotspots.
- ``--json``: machine-readable payload for piping into other tools.

The command soft-fails (exit 0) when ``SONAR_HOST_URL`` /
``SONAR_TOKEN`` are not set, so the doctor never breaks operator
flow on machines that have not opted into the integration. A
short doc pointer is printed in that case.
"""

from __future__ import annotations

import json
from dataclasses import asdict

from rich.panel import Panel
from rich.table import Table

from bernstein.cli.helpers import console
from bernstein.core.observability.sonar import (
    DEFAULT_SMELL_NUDGE,
    DOC_POINTER,
    NudgeSignal,
    SonarInsights,
    baseline_path,
    collect_insights,
    evaluate_nudge,
    load_baseline,
    load_config,
    save_baseline,
)

_NOT_CONFIGURED_HINT = (
    "[yellow]Sonar integration not configured.[/yellow]\n"
    "Set [bold]SONAR_HOST_URL[/bold] and [bold]SONAR_TOKEN[/bold] to enable. "
    f"See [dim]{DOC_POINTER}[/dim] for the setup walkthrough."
)


def _format_coverage(coverage: float | None) -> str:
    if coverage is None:
        return "n/a"
    return f"{coverage:.1f}%"


def _format_hotspot(path: str, max_width: int = 60) -> str:
    """Truncate long file paths from the left so the basename stays visible."""
    if len(path) <= max_width:
        return path
    return "..." + path[-(max_width - 3) :]


def _render_human(
    insights: SonarInsights,
    nudge: NudgeSignal,
    *,
    smell_threshold: int,
) -> None:
    """Print the headline metrics + severity row + hotspots table."""
    console.print()
    if not insights.fetched:
        console.print(
            Panel(
                f"[bold yellow]Sonar insights unavailable[/bold yellow]\n"
                f"project: [cyan]{insights.project_key}[/cyan]\n"
                f"note: {insights.note}",
                border_style="yellow",
                expand=False,
            )
        )
        return

    headline = Table(
        title=f"Sonar insights: {insights.project_key}",
        header_style="bold cyan",
        show_lines=False,
    )
    headline.add_column("Metric")
    headline.add_column("Value", justify="right")
    headline.add_row("Coverage", _format_coverage(insights.coverage_pct))
    headline.add_row("Code smells (total)", str(insights.code_smells_total))
    headline.add_row("Bugs", str(insights.bugs))
    headline.add_row("Vulnerabilities", str(insights.vulnerabilities))
    headline.add_row("Security hotspots", str(insights.security_hotspots))
    headline.add_row("Cognitive complexity", str(insights.cognitive_complexity))
    headline.add_row("Lines of code (ncloc)", str(insights.ncloc))
    console.print(headline)

    sev = Table(
        title="Smells by severity",
        header_style="bold cyan",
        show_lines=False,
    )
    sev.add_column("BLOCKER", justify="right")
    sev.add_column("CRITICAL", justify="right")
    sev.add_column("MAJOR", justify="right")
    sev.add_column("MINOR", justify="right")
    sev.add_column("INFO", justify="right")
    by_sev = insights.smells_by_severity
    sev.add_row(
        str(by_sev.get("BLOCKER", 0)),
        str(by_sev.get("CRITICAL", 0)),
        str(by_sev.get("MAJOR", 0)),
        str(by_sev.get("MINOR", 0)),
        str(by_sev.get("INFO", 0)),
    )
    console.print(sev)

    if insights.hotspots:
        ht = Table(
            title="Cognitive complexity hotspots (top 5)",
            header_style="bold cyan",
            show_lines=False,
        )
        ht.add_column("#", justify="right", no_wrap=True)
        ht.add_column("File")
        ht.add_column("Cognitive complexity", justify="right")
        for idx, hotspot in enumerate(insights.hotspots, start=1):
            ht.add_row(str(idx), _format_hotspot(hotspot.path), str(hotspot.cognitive_complexity))
        console.print(ht)
    else:
        console.print("[dim]No cognitive complexity hotspots reported.[/dim]")

    if nudge.should_nudge:
        body = "\n".join(f"- {r}" for r in nudge.reasons)
        console.print(
            Panel(
                f"[bold yellow]Sonar nudge[/bold yellow] (threshold: {smell_threshold} smells)\n{body}",
                border_style="yellow",
                expand=False,
            )
        )
    console.print()


def _render_json(
    insights: SonarInsights,
    nudge: NudgeSignal,
    *,
    smell_threshold: int,
) -> None:
    """Emit a single JSON object combining insights and the nudge signal."""
    payload = {
        "project_key": insights.project_key,
        "fetched": insights.fetched,
        "note": insights.note,
        "coverage_pct": insights.coverage_pct,
        "code_smells_total": insights.code_smells_total,
        "smells_by_severity": dict(insights.smells_by_severity),
        "bugs": insights.bugs,
        "vulnerabilities": insights.vulnerabilities,
        "security_hotspots": insights.security_hotspots,
        "cognitive_complexity": insights.cognitive_complexity,
        "ncloc": insights.ncloc,
        "hotspots": [asdict(h) for h in insights.hotspots],
        "nudge": {
            "should_nudge": nudge.should_nudge,
            "reasons": list(nudge.reasons),
            "smell_threshold": smell_threshold,
        },
    }
    console.print_json(json.dumps(payload))


def run_doctor_sonar(  # NOSONAR python:S3516 - advisory renderer, always exit 0 by design (see docstring)
    *,
    as_json: bool = False,
    smell_threshold: int = DEFAULT_SMELL_NUDGE,
    update_baseline: bool = True,
) -> int:
    """Entry point used by both the CLI command and tests.

    Returns ``0`` in every soft-fail path so the doctor surface
    remains advisory. Returning a non-zero code is reserved for the
    case where the CLI is invoked with a flag that explicitly
    requests gate semantics (none yet - the renderer stays advisory
    by design).
    """
    config = load_config()
    if config is None:
        if as_json:
            console.print_json(
                json.dumps(
                    {
                        "configured": False,
                        "note": "SONAR_HOST_URL or SONAR_TOKEN not set",
                        "doc": DOC_POINTER,
                    }
                )
            )
        else:
            console.print(_NOT_CONFIGURED_HINT)
        return 0
    insights = collect_insights(config)
    baseline = load_baseline()
    nudge = evaluate_nudge(insights, baseline, smell_threshold=smell_threshold)
    if as_json:
        _render_json(insights, nudge, smell_threshold=smell_threshold)
    else:
        _render_human(insights, nudge, smell_threshold=smell_threshold)
    if update_baseline and insights.fetched:
        save_baseline(insights)
    return 0


__all__ = [
    "DEFAULT_SMELL_NUDGE",
    "DOC_POINTER",
    "baseline_path",
    "run_doctor_sonar",
]
