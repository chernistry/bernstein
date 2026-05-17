"""CLI command for side-by-side adapter comparison.

Usage::

    bernstein compare ./task-spec.md --adapters claude,codex
    bernstein compare ./task-spec.md --adapters claude --keep-worktrees

Runs the same task spec in parallel against up to four adapters in
isolated per-adapter worktrees, diffs the produced changes against the
baseline, and writes a JSON sidecar + Markdown summary. The existing
single-adapter ``bernstein run`` path is untouched.
"""

from __future__ import annotations

import contextlib
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import click

from bernstein.cli.helpers import console
from bernstein.core.orchestration.compare_runner import (
    MAX_ADAPTERS,
    AdapterRun,
    CompareRun,
    CompareTaskSpec,
    Executor,
    parse_adapters_flag,
    render_markdown,
    run_compare,
    write_sidecar,
)


def _default_traces_dir() -> Path:
    override = os.environ.get("BERNSTEIN_TRACES_DIR")
    if override:
        return Path(override)
    return Path.cwd() / ".sdd" / "traces"


def _real_adapter_executor(adapter_name: str, task: CompareTaskSpec, worktree: Path) -> AdapterRun:
    """Spawn the named adapter against ``worktree`` and return its AdapterRun.

    This is the production executor used by ``bernstein compare``. It
    intentionally degrades to a clear error rather than crashing when an
    adapter binary is missing — the compare result still includes the
    failure for side-by-side inspection.
    """
    from bernstein.adapters.registry import get_adapter

    t0 = time.monotonic()
    try:
        adapter = get_adapter(adapter_name)
    except ValueError as exc:
        return AdapterRun(
            adapter=adapter_name,
            worktree=worktree,
            exit_code=2,
            duration_ms=(time.monotonic() - t0) * 1000.0,
            error=f"unknown adapter: {exc}",
        )

    # Lazy import so ``bernstein compare --help`` stays fast.
    from bernstein.core.tasks.models import ModelConfig

    session_id = f"compare-{task.task_id}-{adapter_name}"
    log_path = worktree / f".bernstein-compare-{adapter_name}.log"
    try:
        # ModelConfig is loaded via TYPE_CHECKING in adapters.base — pyright
        # cannot recover its concrete type at the call site.
        result = adapter.spawn(  # pyright: ignore[reportUnknownMemberType]
            prompt=task.prompt,
            workdir=worktree,
            model_config=ModelConfig(model="default", effort="normal"),
            session_id=session_id,
        )
    except Exception as exc:
        return AdapterRun(
            adapter=adapter_name,
            worktree=worktree,
            exit_code=1,
            duration_ms=(time.monotonic() - t0) * 1000.0,
            error=f"spawn failed: {exc!r}",
        )

    # Best-effort wait — the production runner uses a watchdog; here we
    # just poll until the process exits or the deadline passes. Adapters
    # that do not expose ``.proc`` return immediately.
    proc = getattr(result, "proc", None)
    if proc is not None:
        with contextlib.suppress(Exception):
            proc.wait(timeout=task.seed or 1800)
    exit_code = getattr(proc, "returncode", 0) if proc is not None else 0
    stdout_tail = ""
    if log_path.exists():
        try:
            stdout_tail = "\n".join(log_path.read_text(encoding="utf-8").splitlines()[-20:])
        except OSError:
            stdout_tail = ""
    return AdapterRun(
        adapter=adapter_name,
        worktree=worktree,
        exit_code=int(exit_code or 0),
        duration_ms=(time.monotonic() - t0) * 1000.0,
        stdout_tail=stdout_tail,
    )


def _parallel_executor_factory(max_workers: int) -> tuple[Executor, ThreadPoolExecutor]:
    """Return an executor that runs adapter spawns in parallel threads.

    Used internally by ``compare_cmd`` to honour the spec's "parallel
    across adapters" requirement. The runner itself stays single-threaded
    for clean cleanup semantics; this wraps the real executor so the
    expensive spawn happens off the main thread. Returning the pool lets
    the caller shut it down explicitly.
    """
    pool = ThreadPoolExecutor(max_workers=max(1, max_workers), thread_name_prefix="compare")

    def _exec(adapter_name: str, task: CompareTaskSpec, worktree: Path) -> AdapterRun:
        future = pool.submit(_real_adapter_executor, adapter_name, task, worktree)
        return future.result()

    return _exec, pool


def _load_task_spec(spec_path: Path) -> CompareTaskSpec:
    """Read the task spec file. Markdown / plain-text body becomes the prompt."""
    body = spec_path.read_text(encoding="utf-8")
    task_id = spec_path.stem or "compare-task"
    return CompareTaskSpec(task_id=task_id, prompt=body)


@click.command("compare")
@click.argument(
    "spec_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--adapters",
    "adapters_raw",
    required=True,
    help=(f"Comma-separated adapter names, e.g. 'claude,codex'. Maximum {MAX_ADAPTERS} adapters per run."),
)
@click.option(
    "--workspace",
    "workspace",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Workspace directory snapshot. Defaults to the current working directory.",
)
@click.option(
    "--role",
    default="backend",
    show_default=True,
    help="Agent role name applied identically across all adapters.",
)
@click.option(
    "--seed",
    type=int,
    default=0,
    show_default=True,
    help="Deterministic seed forwarded to adapters that honour it.",
)
@click.option(
    "--keep-worktrees",
    is_flag=True,
    default=False,
    help="Do not clean up per-adapter worktrees after the run.",
)
@click.option(
    "--traces-dir",
    "traces_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Override the output dir for the JSON sidecar (default: .sdd/traces).",
)
@click.option(
    "--no-sidecar",
    is_flag=True,
    default=False,
    help="Skip writing the JSON sidecar (useful for dry-runs and CI smoke).",
)
def compare_cmd(
    spec_path: Path,
    adapters_raw: str,
    workspace: Path | None,
    role: str,
    seed: int,
    keep_worktrees: bool,
    traces_dir: Path | None,
    no_sidecar: bool,
) -> None:
    """Run the same task spec across N adapters and diff their outputs.

    The exit code is non-zero when *all* adapters failed (``exit_code != 0``).
    Per-adapter results are reported in the Markdown summary regardless.

    \b
    Examples:
      bernstein compare ./task.md --adapters claude,codex
      bernstein compare ./task.md --adapters claude --keep-worktrees
      bernstein compare ./task.md --adapters claude,codex,gemini,aider
    """
    adapters = parse_adapters_flag(adapters_raw)
    if not adapters:
        console.print("[red]--adapters must list at least one adapter[/red]")
        raise SystemExit(2)
    if len(adapters) > MAX_ADAPTERS:
        console.print(f"[red]--adapters cap is {MAX_ADAPTERS}; got {len(adapters)} ({', '.join(adapters)})[/red]")
        raise SystemExit(2)

    workspace_root = workspace or Path.cwd()
    task = CompareTaskSpec(
        task_id=spec_path.stem or "compare-task",
        prompt=_load_task_spec(spec_path).prompt,
        role=role,
        seed=seed,
    )

    console.print(
        f"[bold]bernstein compare[/bold] task=[cyan]{task.task_id}[/cyan] adapters=[cyan]{','.join(adapters)}[/cyan]"
    )
    if len(adapters) == 1:
        console.print("[dim]single-adapter degenerate case — same flow, no comparison.[/dim]")

    executor, pool = _parallel_executor_factory(max_workers=len(adapters))
    try:
        run = run_compare(
            task,
            adapters,
            workspace_root,
            executor=executor,
            keep_worktrees=keep_worktrees,
        )
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    md = render_markdown(run)
    console.print(md)

    if not no_sidecar:
        out_dir = traces_dir or _default_traces_dir()
        path = write_sidecar(run, out_dir)
        console.print(f"[dim]JSON sidecar: {path}[/dim]")

    _maybe_exit_nonzero(run)


def _maybe_exit_nonzero(run: CompareRun) -> None:
    """Exit non-zero only when every adapter failed.

    A mixed-result run (some adapters succeeded, some failed) still exits
    zero — the operator can read the markdown / JSON to triage.
    """
    if not run.runs:
        raise SystemExit(2)
    if all(r.exit_code != 0 for r in run.runs):
        raise SystemExit(1)


__all__ = ["compare_cmd"]
