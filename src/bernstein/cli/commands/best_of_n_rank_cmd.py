"""CLI surface for TOPSIS multi-criteria ranking of best-of-N candidates.

Usage::

    bernstein best-of-n show <task_id>
    bernstein best-of-n show <task_id> --rank-criteria correctness,cost,latency
    bernstein best-of-n show <task_id> --rank-criteria correctness,cost \\
        --weights 2.0,1.0

The command reads ``.sdd/runtime/best_of_n/<task_id>.json`` (the
artefact written by :class:`BestOfNRunner` when ranking is enabled) and
prints a ranked table.  Issue #1347 ships only the *show* surface; the
ranking itself runs inside the runner.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import click

from bernstein.core.orchestration.multi_criteria_rank import (
    Candidate,
    TopsisError,
    build_criterion_profile,
    parse_criteria_csv,
    rank_candidates,
    render_ranking_json,
)


@click.group("best-of-n")
def best_of_n_group() -> None:
    """Inspect best-of-N candidate ranking artefacts."""


@best_of_n_group.command("show")
@click.argument("task_id")
@click.option(
    "--rank-criteria",
    "rank_criteria",
    default=None,
    help="Comma-separated criterion names (e.g. correctness,cost,latency). "
    "When omitted, the ranking written by the runner is shown as-is.",
)
@click.option(
    "--weights",
    "weights_csv",
    default=None,
    help="Comma-separated per-criterion weights matching --rank-criteria. Defaults to identity weights.",
)
@click.option(
    "--artefact-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path(".sdd/runtime/best_of_n"),
    show_default=True,
    help="Directory containing best-of-N ranking artefacts.",
)
@click.option(
    "--output",
    "output_fmt",
    type=click.Choice(["table", "json"]),
    default="table",
    show_default=True,
    help="Output format.",
)
def show(
    task_id: str,
    rank_criteria: str | None,
    weights_csv: str | None,
    artefact_dir: Path,
    output_fmt: str,
) -> None:
    """Print the ranked candidate table for *task_id*."""
    path = artefact_dir / f"{task_id}.json"
    if not path.exists():
        raise click.ClickException(f"No best-of-N artefact at {path}")

    try:
        raw_payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise click.ClickException(f"Could not read {path}: {exc}") from exc

    if not isinstance(raw_payload, dict):
        raise click.ClickException(f"Artefact at {path} is not a JSON object")
    payload: dict[str, Any] = cast(dict[str, Any], raw_payload)

    if rank_criteria is not None:
        payload = _recompute_ranking(
            payload,
            rank_criteria=rank_criteria,
            weights_csv=weights_csv,
            artefact_path=path,
        )

    if output_fmt == "json":
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    _print_table(task_id, payload)


def _recompute_ranking(
    payload: dict[str, Any],
    *,
    rank_criteria: str,
    weights_csv: str | None,
    artefact_path: Path,
) -> dict[str, Any]:
    try:
        criteria = parse_criteria_csv(rank_criteria)
        weights: list[float] | None = None
        if weights_csv is not None:
            try:
                weights = [float(w.strip()) for w in weights_csv.split(",")]
            except ValueError as exc:
                raise click.ClickException(f"Invalid weights {weights_csv!r}: {exc}") from exc
        profile = build_criterion_profile(list(criteria), weights)
        raw_candidates = payload.get("candidates")
        if not isinstance(raw_candidates, list):
            raise click.ClickException(f"Artefact at {artefact_path} does not contain a 'candidates' list")
        cands: list[Candidate] = []
        for item in cast(list[Any], raw_candidates):
            if not isinstance(item, dict):
                continue
            entry = cast(dict[str, Any], item)
            key = str(entry.get("task_id", ""))
            raw_scores = entry.get("scores", {})
            if not isinstance(raw_scores, dict):
                continue
            scores: dict[str, float] = {}
            for sk, sv in cast(dict[str, Any], raw_scores).items():
                if isinstance(sv, (int, float)) and not isinstance(sv, bool):
                    scores[str(sk)] = float(sv)
            cands.append(Candidate(key=key, scores=scores))
        ranked = rank_candidates(cands, profile)
        return cast(dict[str, Any], render_ranking_json(ranked, profile))
    except TopsisError as exc:
        raise click.ClickException(str(exc)) from exc


def _print_table(task_id: str, payload: dict[str, Any]) -> None:
    ranking_raw = payload.get("ranking", [])
    if not isinstance(ranking_raw, list):
        raise click.ClickException("Artefact 'ranking' field is not a list")

    header = f"Best-of-N ranking for task {task_id}"
    click.echo(header)
    click.echo("=" * len(header))
    winner = payload.get("winner")
    if winner:
        click.echo(f"Winner: {winner}")
    click.echo()
    click.echo(f"{'rank':<6}{'key':<24}{'closeness':>12}")
    click.echo("-" * 42)
    for row in cast(list[Any], ranking_raw):
        if not isinstance(row, dict):
            continue
        entry = cast(dict[str, Any], row)
        rank = entry.get("rank", "?")
        key = str(entry.get("key", "?"))
        closeness_raw = entry.get("closeness", 0.0)
        try:
            closeness = float(closeness_raw)
        except (TypeError, ValueError):
            closeness = 0.0
        click.echo(f"{rank!s:<6}{key:<24}{closeness:>12.6f}")
