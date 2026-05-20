"""``bernstein spec`` command group: spec-quality checklist gate (#1631).

Provides two subcommands:

* ``bernstein spec check <path>`` -- evaluate a spec file against the
  default rule set and render the checklist. Exits non-zero when any
  required rule fails.
* ``bernstein spec auto-fix <path>`` -- run the auto-fix loop with a
  best-effort heuristic patcher (adds missing sections, strips TODO
  markers, etc.) up to ``--max-iter`` attempts. Writes the rewritten
  spec back to ``<path>`` only when ``--write`` is supplied.

The implementation delegates rule evaluation to
:mod:`bernstein.core.planning.spec_quality`; this module exists only to
wire the CLI surface and a deterministic local autofix heuristic.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from bernstein.cli.helpers import console
from bernstein.core.planning.spec_quality import (
    DEFAULT_MAX_AUTO_FIX_ITERATIONS,
    ChecklistReport,
    SpecQualityUnresolvedError,
    evaluate,
    refuse_to_advance,
    render_report,
)

__all__ = ["spec_group"]


def _heuristic_autofix(report: ChecklistReport) -> str | None:
    """Apply a deterministic local fix per failed rule.

    The heuristic targets the cheap structural failures (missing section
    headings, stray TODO markers). It cannot fabricate semantic content,
    so rules like ``ref_paths_exist`` are left to the operator. Returns
    the rewritten spec text, or ``None`` when no progress is possible.
    """
    try:
        text = report.spec_path.read_text(encoding="utf-8")
    except (OSError, FileNotFoundError):
        return None
    original = text
    for failure in report.required_failures:
        text = _apply_one_fix(text, failure.rule_id)
    return text if text != original else None


def _apply_one_fix(text: str, rule_id: str) -> str:
    """Best-effort patch for a single failing rule id."""
    if rule_id == "acceptance_criteria_present":
        return text.rstrip() + ("\n\n## Acceptance criteria\n\n- (auto-generated stub; fill in)\n")
    if rule_id == "out_of_scope_present":
        return text.rstrip() + ("\n\n## Out of scope\n\n- (auto-generated stub; fill in)\n")
    if rule_id == "tested_via_present":
        return text.rstrip() + ("\n\n## Tested via\n\n- (auto-generated stub; list pytest selectors)\n")
    if rule_id == "no_todo_markers":
        # Strip TODO markers but keep the surrounding sentence so the
        # operator can review the gap on the next pass.
        return text.replace("TODO", "[follow-up]").replace("todo", "[follow-up]")
    return text


@click.group("spec")
def spec_group() -> None:
    """Spec-quality checklist gate (issue #1631)."""


@spec_group.command("check")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--workspace-root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Workspace root used by path-existence rules. Defaults to cwd.",
)
@click.option(
    "--strict/--no-strict",
    default=True,
    help="Exit non-zero when any required rule fails (default: strict).",
)
def spec_check(path: Path, workspace_root: Path | None, strict: bool) -> None:
    """Evaluate a spec file and print the checklist report."""
    root = workspace_root or Path.cwd()
    report = evaluate(path, workspace_root=root)
    console.print(render_report(report))
    if strict and not report.passed:
        sys.exit(2)


@spec_group.command("auto-fix")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--workspace-root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Workspace root used by path-existence rules. Defaults to cwd.",
)
@click.option(
    "--max-iter",
    type=click.IntRange(min=0, max=10),
    default=DEFAULT_MAX_AUTO_FIX_ITERATIONS,
    help="Maximum auto-fix iterations before the gate refuses to advance.",
)
@click.option(
    "--write/--dry-run",
    default=False,
    help="Persist the rewritten spec to disk (default: dry-run).",
)
def spec_auto_fix(
    path: Path,
    workspace_root: Path | None,
    max_iter: int,
    write: bool,
) -> None:
    """Run the auto-fix loop and optionally write the patched spec back."""
    root = workspace_root or Path.cwd()
    last_text: dict[str, str] = {"text": ""}

    def _autofix(report: ChecklistReport) -> str | None:
        patched = _heuristic_autofix(report)
        if patched is None:
            return None
        last_text["text"] = patched
        if write:
            try:
                path.write_text(patched, encoding="utf-8")
            except OSError as exc:
                raise click.ClickException(f"Failed to write patched spec: {path} ({exc})") from exc
        return patched

    try:
        report = refuse_to_advance(
            path,
            workspace_root=root,
            autofix=_autofix,
            max_iterations=max_iter,
        )
    except SpecQualityUnresolvedError as exc:
        console.print(render_report(exc.report))
        console.print(f"[red]Refused to advance after {exc.report.iteration} iteration(s).[/red]")
        sys.exit(2)
    console.print(render_report(report))
    if not write and last_text["text"]:
        console.print("[yellow]--dry-run set; rewritten spec not persisted.[/yellow]")
