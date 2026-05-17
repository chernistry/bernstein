"""Side-by-side adapter comparison runner.

Drives the same task spec through N adapters (cap 4) inside isolated
per-adapter worktrees, then diffs the produced changes against the
baseline workspace snapshot. Pure-Python and adapter-agnostic: the actual
adapter spawn is injected via the ``executor`` callable so unit tests can
exercise the orchestration logic without real subprocesses.

Companion to ``bernstein.eval.ab_runner`` (prompt-vs-prompt eval) and to
``bernstein.core.ab_test`` (live model-vs-model on a single task). This
module covers adapter-vs-adapter on the same workspace.

Design notes:
    * Identical seeds, identical role prompt, identical workspace
      snapshot for all adapters — the snapshot is materialised once and
      ``shutil.copytree``'d into each adapter's worktree before spawn.
    * Adapter count is hard-capped at ``MAX_ADAPTERS`` (4).
    * Worktree cleanup happens unconditionally unless
      ``keep_worktrees=True``; the caller may then inspect the directory.
    * Output JSON sidecar lives at ``.sdd/traces/compare-<id>.json``;
      Markdown summary is returned as a string for the CLI to print.
    * Telemetry: each ``AdapterRun`` carries ``compare_run_id`` so the
      eval harness can ingest historical comparisons (#feat-cli-comparison-mode).
"""

from __future__ import annotations

import difflib
import hashlib
import json
import shutil
import time
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_ADAPTERS: int = 4
"""Hard cap on the number of adapters that can be compared in one run."""

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompareTaskSpec:
    """Inputs to one compare-run.

    Attributes:
        task_id: Stable id used in the JSON sidecar / Markdown summary.
        prompt: Role-rendered prompt sent to each adapter unchanged.
        role: Agent role name (e.g. ``"backend"``).
        seed: Deterministic seed forwarded to adapters that honour it.
    """

    task_id: str
    prompt: str
    role: str = "backend"
    seed: int = 0


@dataclass(frozen=True)
class AdapterRun:
    """Result of running one adapter inside its isolated worktree.

    Attributes:
        adapter: Adapter name (registry key, e.g. ``"claude"``).
        worktree: Absolute path to the per-adapter worktree (may be
            cleaned up by the time the caller reads this).
        exit_code: Adapter-reported exit code; ``0`` => success.
        duration_ms: Wall-clock duration of the adapter invocation.
        changed_files: Map of repo-relative path -> unified diff against
            the baseline snapshot. Empty when no files were modified.
        stdout_tail: Last few lines of the adapter's stdout (truncated).
        error: Free-form error message when ``exit_code != 0``.
        compare_run_id: Group id linking this run to the parent compare.
    """

    adapter: str
    worktree: Path
    exit_code: int
    duration_ms: float
    changed_files: dict[str, str] = field(default_factory=dict[str, str])
    stdout_tail: str = ""
    error: str = ""
    compare_run_id: str = ""


@dataclass(frozen=True)
class CompareRun:
    """Aggregate artefact for one compare invocation."""

    compare_run_id: str
    task: CompareTaskSpec
    adapters: tuple[str, ...]
    runs: tuple[AdapterRun, ...]
    started_at: float
    finished_at: float

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic dict suitable for JSON serialisation."""
        return {
            "compare_run_id": self.compare_run_id,
            "task": {
                "task_id": self.task.task_id,
                "prompt_sha256": _sha256(self.task.prompt),
                "role": self.task.role,
                "seed": self.task.seed,
            },
            "adapters": list(self.adapters),
            "runs": [_run_to_dict(r) for r in self.runs],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": (self.finished_at - self.started_at) * 1000.0,
        }

    def to_json(self, *, indent: int = 2) -> str:
        """Render as a deterministic JSON string (``sort_keys=True``)."""
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)


# ---------------------------------------------------------------------------
# Executor protocol (typed callable)
# ---------------------------------------------------------------------------


Executor = Callable[[str, CompareTaskSpec, Path], AdapterRun]
"""Run one adapter against a prepared worktree and return its AdapterRun.

Signature: ``(adapter_name, task_spec, worktree_path) -> AdapterRun``.

The runner takes care of worktree provisioning + diff computation; the
executor is only responsible for *driving* the adapter (or simulating it,
in tests). Implementations MUST NOT mutate any path outside ``worktree``.
"""


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------


def run_compare(
    task: CompareTaskSpec,
    adapters: Sequence[str],
    workspace_snapshot: Path,
    *,
    executor: Executor,
    worktree_root: Path | None = None,
    keep_worktrees: bool = False,
) -> CompareRun:
    """Run ``task`` against each adapter and produce a CompareRun.

    Args:
        task: Frozen task spec — identical for every adapter.
        adapters: Adapter registry names. Must be non-empty and
            ``len(adapters) <= MAX_ADAPTERS`` (cap 4). Duplicates rejected.
        workspace_snapshot: Directory used as the baseline workspace —
            copied into each adapter's worktree before spawn. Must exist.
        executor: Callable that runs one adapter against a worktree.
        worktree_root: Parent dir under which per-adapter worktrees are
            created. Defaults to a freshly-allocated temp dir. The parent
            is itself removed at the end unless ``keep_worktrees``.
        keep_worktrees: When True, skip cleanup so the operator can
            inspect each adapter's resulting tree.

    Returns:
        Populated :class:`CompareRun`. Order of ``runs`` matches ``adapters``.

    Raises:
        ValueError: If adapter count violates the cap, duplicates exist,
            or ``workspace_snapshot`` is missing.
    """
    validated = _validate_adapters(adapters)
    if not workspace_snapshot.exists() or not workspace_snapshot.is_dir():
        msg = f"workspace_snapshot {workspace_snapshot!s} must be an existing directory"
        raise ValueError(msg)

    compare_run_id = _new_run_id()
    started_at = time.time()

    root = worktree_root or Path(_mkdtemp_run(compare_run_id))
    root.mkdir(parents=True, exist_ok=True)

    runs: list[AdapterRun] = []
    try:
        for adapter_name in validated:
            worktree = root / adapter_name
            _materialise_worktree(workspace_snapshot, worktree)
            t0 = time.monotonic()
            try:
                raw = executor(adapter_name, task, worktree)
            except Exception as exc:
                duration_ms = (time.monotonic() - t0) * 1000.0
                raw = AdapterRun(
                    adapter=adapter_name,
                    worktree=worktree,
                    exit_code=1,
                    duration_ms=duration_ms,
                    error=f"executor raised: {exc!r}",
                )
            diffs = _diff_against_snapshot(workspace_snapshot, worktree)
            runs.append(
                AdapterRun(
                    adapter=raw.adapter or adapter_name,
                    worktree=worktree,
                    exit_code=raw.exit_code,
                    duration_ms=raw.duration_ms,
                    changed_files=diffs,
                    stdout_tail=raw.stdout_tail,
                    error=raw.error,
                    compare_run_id=compare_run_id,
                )
            )
    finally:
        if not keep_worktrees:
            _safe_rmtree(root)

    finished_at = time.time()
    return CompareRun(
        compare_run_id=compare_run_id,
        task=task,
        adapters=tuple(validated),
        runs=tuple(runs),
        started_at=started_at,
        finished_at=finished_at,
    )


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def render_markdown(run: CompareRun, *, max_diff_lines_per_file: int = 40) -> str:
    """Render a compact Markdown summary suitable for stdout.

    Args:
        run: The completed CompareRun.
        max_diff_lines_per_file: Truncate per-file diff blocks to this many
            lines so the summary stays readable on a terminal.

    Returns:
        A Markdown string (no trailing newline guarantee).
    """
    lines: list[str] = []
    lines.append(f"# Compare run `{run.compare_run_id}`")
    lines.append("")
    lines.append(f"- task: `{run.task.task_id}`  role: `{run.task.role}`  seed: `{run.task.seed}`")
    lines.append(f"- adapters: {', '.join(f'`{a}`' for a in run.adapters)}")
    lines.append(f"- wall-clock: {(run.finished_at - run.started_at):.2f}s")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| adapter | exit | duration ms | files changed |")
    lines.append("|---------|------|-------------|---------------|")
    for r in run.runs:
        status = "ok" if r.exit_code == 0 else f"fail({r.exit_code})"
        lines.append(f"| `{r.adapter}` | {status} | {r.duration_ms:.0f} | {len(r.changed_files)} |")
    lines.append("")
    for r in run.runs:
        lines.append(f"## `{r.adapter}`")
        lines.append("")
        if r.error:
            lines.append(f"> error: {r.error}")
            lines.append("")
        if not r.changed_files:
            lines.append("_no files changed_")
            lines.append("")
            continue
        for path in sorted(r.changed_files):
            diff_text = r.changed_files[path]
            truncated = _truncate_diff(diff_text, max_diff_lines_per_file)
            lines.append(f"### `{path}`")
            lines.append("")
            lines.append("```diff")
            lines.append(truncated)
            lines.append("```")
            lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON sidecar helpers
# ---------------------------------------------------------------------------


def write_sidecar(run: CompareRun, traces_dir: Path) -> Path:
    """Write the run as ``compare-<id>.json`` under ``traces_dir``.

    Args:
        run: CompareRun to serialise.
        traces_dir: Output directory (created if missing). Conventionally
            ``.sdd/traces/`` so the eval harness can pick it up.

    Returns:
        Path to the written file.
    """
    traces_dir.mkdir(parents=True, exist_ok=True)
    path = traces_dir / f"compare-{run.compare_run_id}.json"
    path.write_text(run.to_json() + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _validate_adapters(adapters: Sequence[str]) -> list[str]:
    if not adapters:
        msg = "at least one adapter is required"
        raise ValueError(msg)
    if len(adapters) > MAX_ADAPTERS:
        msg = f"cannot compare more than {MAX_ADAPTERS} adapters at once (got {len(adapters)})"
        raise ValueError(msg)
    cleaned = [a.strip() for a in adapters if a and a.strip()]
    if len(cleaned) != len(adapters):
        msg = "adapter names must be non-empty"
        raise ValueError(msg)
    if len(set(cleaned)) != len(cleaned):
        msg = f"duplicate adapter names in {cleaned!r}"
        raise ValueError(msg)
    return cleaned


def _new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def _mkdtemp_run(run_id: str) -> str:
    import tempfile

    return tempfile.mkdtemp(prefix=f"bernstein-compare-{run_id}-")


def _materialise_worktree(src: Path, dst: Path) -> None:
    """Copy ``src`` into ``dst``. ``dst`` must not exist yet."""
    if dst.exists():
        _safe_rmtree(dst)
    shutil.copytree(src, dst, symlinks=False, ignore=shutil.ignore_patterns(".git", "__pycache__"))


def _safe_rmtree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _diff_against_snapshot(snapshot: Path, worktree: Path) -> dict[str, str]:
    """Compute per-file unified diffs of ``worktree`` against ``snapshot``.

    Files that are identical are omitted. Binary files (paths whose
    content fails utf-8 decode) are reported with a ``Binary files differ``
    marker rather than a full diff.
    """
    diffs: dict[str, str] = {}
    snap_files = _walk_text_files(snapshot)
    work_files = _walk_text_files(worktree)
    all_rel = sorted(snap_files.keys() | work_files.keys())
    for rel in all_rel:
        snap_text = snap_files.get(rel)
        work_text = work_files.get(rel)
        if snap_text is None and work_text is None:
            continue
        if snap_text == work_text:
            continue
        if snap_text is None:
            diffs[rel] = _render_unified("/dev/null", rel, "", work_text or "")
            continue
        if work_text is None:
            diffs[rel] = _render_unified(rel, "/dev/null", snap_text, "")
            continue
        diffs[rel] = _render_unified(rel, rel, snap_text, work_text)
    return diffs


def _walk_text_files(root: Path) -> dict[str, str]:
    """Return a mapping of repo-relative path -> file contents (utf-8).

    Files that cannot be decoded as utf-8 are reported with a placeholder
    so the diff stage can still record they exist.
    """
    out: dict[str, str] = {}
    if not root.is_dir():
        return out
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        if any(part in {".git", "__pycache__"} for part in path.parts):
            continue
        rel = str(path.relative_to(root))
        try:
            out[rel] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            out[rel] = f"<binary:{path.stat().st_size}>"
    return out


def _render_unified(a_path: str, b_path: str, a_text: str, b_text: str) -> str:
    a_lines = a_text.splitlines(keepends=True)
    b_lines = b_text.splitlines(keepends=True)
    diff = difflib.unified_diff(
        a_lines,
        b_lines,
        fromfile=a_path,
        tofile=b_path,
        n=3,
    )
    return "".join(diff)


def _truncate_diff(diff: str, max_lines: int) -> str:
    lines = diff.splitlines()
    if len(lines) <= max_lines:
        return diff
    head = lines[:max_lines]
    head.append(f"... ({len(lines) - max_lines} more lines truncated)")
    return "\n".join(head)


def _run_to_dict(r: AdapterRun) -> dict[str, Any]:
    return {
        "adapter": r.adapter,
        "worktree": str(r.worktree),
        "exit_code": r.exit_code,
        "duration_ms": r.duration_ms,
        "changed_files": dict(r.changed_files),
        "stdout_tail": r.stdout_tail,
        "error": r.error,
        "compare_run_id": r.compare_run_id,
    }


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Iterable / Sequence helpers re-exported for the CLI
# ---------------------------------------------------------------------------


def parse_adapters_flag(raw: str | Iterable[str]) -> list[str]:
    """Parse a ``--adapters`` value into a normalised list.

    Accepts both a comma-separated string (``"claude,codex"``) and a
    pre-split iterable. Whitespace is trimmed; empty entries dropped.
    """
    if isinstance(raw, str):
        return [item.strip() for item in raw.split(",") if item.strip()]
    return [item.strip() for item in raw if item and item.strip()]


__all__ = [
    "MAX_ADAPTERS",
    "AdapterRun",
    "CompareRun",
    "CompareTaskSpec",
    "Executor",
    "parse_adapters_flag",
    "render_markdown",
    "run_compare",
    "write_sidecar",
]
