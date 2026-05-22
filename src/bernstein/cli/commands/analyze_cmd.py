"""CLI command: `bernstein analyze` - orchestration-readiness scan.

Closes [#768](https://github.com/sipyourdrink-ltd/bernstein/issues/768).

Scans the current repo (or `--path`) and prints a readiness score plus
strengths / opportunities / recommended first run. Pure offline analysis;
no LLM calls, no network.

Use --json for a machine-readable report (e.g. for CI gates that want to
fail builds with low orchestration-readiness scores).
"""

from __future__ import annotations

import json as _json
from pathlib import Path

import click

from bernstein.cli.helpers import console
from bernstein.core.knowledge.repo_analyzer import RepoAnalysis, analyze_repo


@click.command("analyze")
@click.option(
    "--path",
    "path",
    type=click.Path(file_okay=False, dir_okay=True, exists=True),
    default=".",
    show_default=True,
    help="Repo root to analyze.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit a machine-readable JSON report instead of the rich console view.",
)
@click.option(
    "--min-score",
    "min_score",
    type=float,
    default=None,
    help="Exit with non-zero status if the readiness score is below this threshold (0-10).",
)
def analyze_cmd(path: str, as_json: bool, min_score: float | None) -> None:
    """Assess repo readiness for multi-agent orchestration.

    \b
    Scoring is the average of four components on a 0-10 scale:
      1. Test coverage  (estimated from test/source file ratio)
      2. Modularity     (penalises >300-line files + modules without tests)
      3. CI presence    (10 if a CI config exists, else 0)
      4. Type hints     (Python only; skipped on non-Python repos)

    \b
      bernstein analyze
      bernstein analyze --path other/repo
      bernstein analyze --json | jq .readiness_score
      bernstein analyze --min-score 6   # exits 1 if score < 6
    """
    root = Path(path).resolve()
    analysis = analyze_repo(root)

    if as_json:
        click.echo(_json.dumps(_to_json(analysis), indent=2))
    else:
        _render_rich(analysis)

    if min_score is not None and analysis.readiness_score < min_score:
        raise SystemExit(1)


def _to_json(a: RepoAnalysis) -> dict:
    """Convert an analysis to a JSON-serializable dict."""
    return {
        "root": str(a.root),
        "totals": {
            "files": a.total_files,
            "source_files": a.total_source_files,
            "lines": a.total_lines,
            "largest_file_lines": a.largest_file_lines,
            "largest_file_path": str(a.largest_file_path) if a.largest_file_path else None,
        },
        "languages": [{"name": lang.name, "files": lang.files, "pct": lang.pct} for lang in a.languages],
        "tests": {
            "test_files": a.test_files,
            "estimated_coverage_pct": a.test_coverage_estimate_pct,
            "modules_without_tests": [str(p) for p in a.modules_without_tests],
        },
        "ci": {"present": a.has_ci, "kind": a.ci_kind or None},
        "smells": {
            "files_over_300_lines": [{"path": str(p), "lines": n} for p, n in a.files_over_300_lines],
            "python_files_without_type_hints": a.python_files_without_type_hints,
        },
        "readiness_score": a.readiness_score,
        "strengths": a.strengths,
        "opportunities": a.opportunities,
        "recommended_first_run": a.recommended_first_run,
    }


def _render_rich(a: RepoAnalysis) -> None:
    """Pretty-print the analysis to the console."""
    console.print()
    console.print(f"[bold]Repo Analysis[/bold] - [cyan]{a.root}[/cyan]")
    console.print()

    # Codebase block
    console.print("  [bold]Codebase[/bold]:")
    if a.languages:
        lang_summary = ", ".join(f"{lang.name} ({lang.pct}%)" for lang in a.languages[:3])
    else:
        lang_summary = "(no recognized source files)"
    console.print(f"    Language: {lang_summary}")
    console.print(f"    Files: {a.total_files} (source: {a.total_source_files})")
    console.print(f"    Lines: {a.total_lines:,}")
    if a.test_coverage_estimate_pct:
        console.print(
            f"    Test coverage: ~{a.test_coverage_estimate_pct}% [dim](estimated from test file count)[/dim]"
        )
    else:
        console.print("    Test coverage: [yellow]no tests detected[/yellow]")
    console.print()

    # Score block
    if a.readiness_score >= 7:
        score_color = "green"
    elif a.readiness_score >= 4:
        score_color = "yellow"
    else:
        score_color = "red"
    console.print(f"  [bold]Orchestration readiness[/bold]: [{score_color}]{a.readiness_score:.1f}/10[/{score_color}]")
    console.print()

    # Strengths
    if a.strengths:
        console.print("  [bold green]Strengths[/bold green]:")
        for s in a.strengths:
            console.print(f"    [green]✓[/green] {s}")
        console.print()

    # Opportunities
    if a.opportunities:
        console.print("  [bold yellow]Opportunities[/bold yellow]:")
        for o in a.opportunities:
            console.print(f"    [yellow]→[/yellow] {o}")
        console.print()

    # Recommended first run
    if a.recommended_first_run:
        console.print("  [bold]Recommended first run[/bold]:")
        console.print(f"    [cyan]{a.recommended_first_run}[/cyan]")
        console.print()

    # Cost hint - keep loose; we don't try to predict actual model cost.
    console.print("  [dim]Estimated cost: ~$2.50 (5 agents x ~500 tokens each)[/dim]")
    console.print()
