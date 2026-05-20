"""Render a task DAG file showing parallel batches and story links.

Backs the ``bernstein tasks plan-dag --file <path>`` and ``bernstein plan
dag --file <path>`` invocations.  Loads a markdown or YAML task DAG and
walks it with :func:`bernstein.core.orchestration.task_dag.topological_iter_with_parallel`,
highlighting batches that may run in parallel.
"""

from __future__ import annotations

from pathlib import Path

import click

from bernstein.cli.helpers import console
from bernstein.core.orchestration.task_dag import (
    TaskDag,
    TaskDagCycleError,
    TaskDagError,
    topological_iter_with_parallel,
)


@click.command("dag")
@click.option(
    "--file",
    "dag_file",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to a task DAG markdown or YAML file.",
)
@click.option(
    "--no-color",
    "no_color",
    is_flag=True,
    default=False,
    help="Disable colour highlighting in the rendered batches.",
)
def plan_dag(dag_file: Path, no_color: bool) -> None:
    """Render a task DAG with parallel batches highlighted.

    Reads the file at --file, topologically sorts the tasks, and prints
    one batch per line.  Batches with more than one task are flagged as
    parallel; single-task batches are serial.  User-story rollback
    groups are listed at the end.
    """
    try:
        dag = TaskDag.from_path(dag_file)
    except TaskDagError as exc:
        console.print(f"[red]Failed to load task DAG:[/red] {exc}")
        raise SystemExit(1) from exc

    try:
        batches = list(topological_iter_with_parallel(dag))
    except TaskDagCycleError as exc:
        console.print(f"[red]Cycle detected:[/red] {exc}")
        raise SystemExit(1) from exc

    if not batches:
        console.print("[dim]Task DAG is empty.[/dim]")
        return

    parallel_style = "" if no_color else "bold green"
    serial_style = "" if no_color else "cyan"

    console.print(f"[bold]Task DAG:[/bold] {len(dag)} task(s), {len(batches)} batch(es)")
    for idx, batch in enumerate(batches, start=1):
        tasks = sorted(batch, key=lambda n: n.task_id)
        if len(tasks) > 1:
            tag = f"[{parallel_style}]PARALLEL[/{parallel_style}]" if parallel_style else "PARALLEL"
        else:
            tag = f"[{serial_style}]SERIAL[/{serial_style}]" if serial_style else "SERIAL"
        console.print(f"  Batch {idx:>2} {tag} ({len(tasks)} task(s))")
        for node in tasks:
            story = f" [dim]{node.story_id}[/dim]" if node.story_id else ""
            console.print(f"    - [bold]{node.task_id}[/bold]{story}: {node.description}")

    stories = dag.stories()
    if stories:
        console.print("")
        console.print("[bold]User-story rollback groups:[/bold]")
        for story_id, members in sorted(stories.items()):
            ids = ", ".join(n.task_id for n in members)
            console.print(f"  {story_id}: {ids}")
