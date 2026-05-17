"""CLI surface for blast-radius scoring (issue #1322).

Two entry points:

* ``bernstein blast-radius show <task_id>`` -- pretty-print a saved report.
* ``bernstein blast-radius score`` -- ad-hoc scoring from explicit
  ``--file`` / ``--diff-file`` arguments. Useful for CI scripts and for
  reviewers who want to inspect a change without persisting it.

The ``--max-blast-radius`` flag on ``bernstein run`` lives in
:mod:`bernstein.cli.run_bootstrap`; it propagates the operator ceiling via
the ``BERNSTEIN_MAX_BLAST_RADIUS`` env var so subprocesses see the same
gate value.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from bernstein.core.quality.blast_radius import (
    BlastRadiusReport,
    BlastRadiusScorer,
    evaluate_gate,
    load_report,
    save_report,
)

__all__ = ["blast_radius_group"]


@click.group(name="blast-radius")
def blast_radius_group() -> None:
    """Inspect blast-radius scores for changes (issue #1322).

    A blast-radius score in [0, 1] estimates how irreversible a change is.
    Hard one-way detectors (DROP/DELETE SQL, rm -rf, schema migrations,
    secrets / .env writes) force the score to 1.0.
    """


def _format_report(report: BlastRadiusReport, *, fmt: str) -> str:
    if fmt == "json":
        return json.dumps(report.to_dict(), indent=2, sort_keys=True)
    lines: list[str] = []
    lines.append(f"score:          {report.score:.2f}")
    lines.append(f"hard_one_way:   {report.hard_one_way}")
    lines.append(f"files_touched:  {report.files_touched}")
    lines.append("components:")
    for comp in report.components:
        lines.append(f"  - {comp.name:>22s}: {comp.value:.2f}  ({comp.detail})")
    if report.hits:
        lines.append("detectors that fired:")
        for hit in report.hits:
            mark = "!" if hit.hard_one_way else "*"
            lines.append(f"  [{mark}] {hit.detector_id} (w={hit.weight:.2f}, sev={hit.severity}): {hit.description}")
            for path in hit.matched_paths[:3]:
                lines.append(f"        path: {path}")
            for snippet in hit.matched_snippets[:2]:
                lines.append(f"        diff: {snippet}")
    else:
        lines.append("detectors that fired: (none)")
    lines.append("")
    lines.append(report.rationale)
    return "\n".join(lines)


@blast_radius_group.command("show")
@click.argument("task_id")
@click.option(
    "--workdir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Workdir containing .sdd/metrics/blast_radius/. Defaults to cwd.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(("text", "json"), case_sensitive=False),
    default="text",
    help="Output format.",
)
def show_cmd(task_id: str, workdir: Path | None, fmt: str) -> None:
    """Pretty-print the saved blast-radius report for ``TASK_ID``."""
    report = load_report(task_id, workdir=workdir)
    if report is None:
        click.echo(f"No blast-radius report for task {task_id!r}.", err=True)
        sys.exit(2)
    click.echo(_format_report(report, fmt=fmt.lower()))


@blast_radius_group.command("score")
@click.option(
    "--file",
    "files",
    multiple=True,
    help="Path touched by the change. May be repeated.",
)
@click.option(
    "--files-from",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Read newline-separated changed file paths from FILE.",
)
@click.option(
    "--diff-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Read diff body (for content_regex detectors) from FILE.",
)
@click.option(
    "--max-score",
    type=click.FloatRange(0.0, 1.0),
    default=None,
    help="If set, exit non-zero when the score exceeds this ceiling.",
)
@click.option(
    "--save-as",
    "save_as",
    type=str,
    default=None,
    help="Persist the report under .sdd/metrics/blast_radius/<TASK_ID>.json.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(("text", "json"), case_sensitive=False),
    default="text",
    help="Output format.",
)
def score_cmd(
    files: tuple[str, ...],
    files_from: Path | None,
    diff_file: Path | None,
    max_score: float | None,
    save_as: str | None,
    fmt: str,
) -> None:
    """Score a change described on the command line (no orchestrator needed)."""
    paths: list[str] = list(files)
    if files_from is not None:
        for line in files_from.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped:
                paths.append(stripped)
    diff_text = diff_file.read_text(encoding="utf-8") if diff_file is not None else ""

    scorer = BlastRadiusScorer()
    report = scorer.score(files=paths, diff_text=diff_text)
    click.echo(_format_report(report, fmt=fmt.lower()))

    if save_as is not None:
        target = save_report(report, task_id=save_as)
        click.echo(f"\nReport persisted to: {target}", err=True)

    decision = evaluate_gate(report, max_score=max_score)
    if not decision.allowed:
        click.echo(f"\nGate refused: {decision.reason}", err=True)
        sys.exit(3)
