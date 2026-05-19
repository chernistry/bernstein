"""Debug bundle export -- ``bernstein debug bundle``.

Collects a self-contained, redacted ZIP that an operator can attach to a
GitHub issue without manual back-and-forth on logs, configuration, and
environment data. Reduces support friction for both reporter and
maintainer and standardises the artefact attached to issues.

Bundle contents (every text artefact is run through
:mod:`bernstein.core.security.redactor` before being written):

- ``manifest.json``      -- bernstein version, python version, OS,
                             install method, selection (task/run/last),
                             file count, redaction count.
- ``bernstein.yaml``     -- redacted copy of the project config.
- ``doctor.json``        -- output of ``bernstein doctor --json``.
- ``traces/``            -- recent ``.sdd/traces/`` for the selected
                             task/run.
- ``metrics/``           -- recent ``.sdd/metrics/`` for the same window.
- ``logs/``              -- last 200 lines of each ``.sdd/runtime/*.log``.
- ``source/`` (optional) -- the N most-recently-changed git files under
                             ``src/`` when ``--include-source-snippets``
                             is set.

The ZIP plumbing reuses :mod:`bernstein.cli.run_archive`; the redaction
pass reuses :mod:`bernstein.core.security.redactor`. This module owns
selection logic, the manifest schema, and the CLI entry point.
"""

from __future__ import annotations

import importlib.metadata
import json
import platform
import subprocess
import sys
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click

import bernstein
from bernstein.cli.helpers import console
from bernstein.cli.run_archive import ArchiveManifest  # re-used for plumbing parity
from bernstein.core.security.redactor import redact_file, redact_text

# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

_RUNTIME_LOG_TAIL_LINES = 200
_TRACE_FILE_LIMIT = 50
_METRIC_FILE_LIMIT = 50
_SOURCE_SNIPPET_BYTE_BUDGET = 64 * 1024


@dataclass(frozen=True)
class DebugManifest:
    """Schema for ``manifest.json`` written at the bundle root.

    Attributes:
        schema_version: Format version of this manifest. Bump on any
            backwards-incompatible field change.
        created_at: ISO-8601 UTC timestamp of bundle creation.
        bernstein_version: ``bernstein.__version__``.
        python_version: ``platform.python_version()``.
        os: Short OS descriptor (``platform.platform()``).
        install_method: Detected install method: ``pip``, ``pipx``,
            ``uv-tool``, ``editable``, or ``unknown``.
        selection: Which selection produced the bundle: dict with
            keys ``mode`` (``task``/``run``/``last``) and ``id``
            (``str | None``).
        files: Sorted list of archive-relative file paths included.
        redactions_applied: Total number of secret patterns substituted
            during the redaction pass over all text artefacts.
    """

    schema_version: int
    created_at: str
    bernstein_version: str
    python_version: str
    os: str
    install_method: str
    selection: dict[str, str | None]
    files: list[str] = field(default_factory=list)
    redactions_applied: int = 0


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def _resolve_last_run(workdir: Path) -> str | None:
    """Return the most recent run id recorded in ``.sdd/runtime/run_id``."""
    run_id_path = workdir / ".sdd" / "runtime" / "run_id"
    if not run_id_path.is_file():
        return None
    try:
        text = run_id_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


@dataclass(frozen=True)
class Selection:
    """Resolved bundle selection.

    Attributes:
        mode: Which selector won: ``task``, ``run``, or ``last``.
        ident: Optional task or run id; ``None`` when no run yet exists.
    """

    mode: str
    ident: str | None


def resolve_selection(
    workdir: Path,
    task: str | None,
    run: str | None,
    last: bool,
) -> Selection:
    """Resolve the bundle selection flags into a single selection.

    Precedence: explicit ``--task`` beats explicit ``--run`` beats
    ``--last`` beats the default (``--last``).

    Args:
        workdir: Project root directory.
        task: Optional task id from ``--task``.
        run: Optional run id from ``--run``.
        last: Truthy when ``--last`` was passed (default behaviour).

    Returns:
        The selected :class:`Selection`. ``mode == "last"`` may have
        ``ident is None`` when no run has been recorded yet -- that
        case is handled at write time, not here.
    """
    if task is not None:
        return Selection(mode="task", ident=task)
    if run is not None:
        return Selection(mode="run", ident=run)
    # Default == --last
    return Selection(mode="last", ident=_resolve_last_run(workdir))


# ---------------------------------------------------------------------------
# Host introspection
# ---------------------------------------------------------------------------


def detect_install_method() -> str:
    """Best-effort detection of how Bernstein was installed.

    Uses ``importlib.metadata`` for distribution location and
    ``sys.executable`` for the launcher path. Returns one of
    ``editable``, ``pipx``, ``uv-tool``, ``pip``, or ``unknown``.
    """
    exe = sys.executable
    exe_lower = exe.lower()

    # pipx and uv tool place wrappers under a recognisable directory.
    if "pipx" in exe_lower or "/pipx/" in exe_lower:
        return "pipx"
    if "/uv/tools/" in exe_lower or exe_lower.endswith("/uv-tool"):
        return "uv-tool"

    # Editable installs expose a ``.pth`` or ``__editable__`` marker on
    # the distribution. importlib.metadata.files() returns None when
    # the metadata is missing; we treat that defensively.
    try:
        dist = importlib.metadata.distribution("bernstein")
        files = dist.files or []
        for file in files:
            name = str(file)
            if name.endswith(".pth") or "__editable__" in name:
                return "editable"
    except importlib.metadata.PackageNotFoundError:
        return "unknown"

    return "pip"


def build_host_fields() -> dict[str, str]:
    """Return the host-side fields that go into the manifest."""
    return {
        "bernstein_version": bernstein.__version__,
        "python_version": platform.python_version(),
        "os": platform.platform(),
        "install_method": detect_install_method(),
    }


# ---------------------------------------------------------------------------
# File collection
# ---------------------------------------------------------------------------


def _tail_lines(path: Path, max_lines: int) -> str:
    """Read up to *max_lines* trailing lines from *path*."""
    try:
        all_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    if max_lines <= 0 or len(all_lines) <= max_lines:
        return "\n".join(all_lines)
    return "\n".join(all_lines[-max_lines:])


def collect_traces(workdir: Path, selection: Selection) -> list[Path]:
    """Return the recent trace files for the selection.

    When ``selection.ident`` is set, filter by filename containing the
    id. Otherwise return the most-recently-modified traces, capped by
    ``_TRACE_FILE_LIMIT``.
    """
    trace_dir = workdir / ".sdd" / "traces"
    if not trace_dir.is_dir():
        return []
    candidates = [p for p in trace_dir.iterdir() if p.is_file()]
    if selection.ident:
        candidates = [p for p in candidates if selection.ident in p.name]
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[:_TRACE_FILE_LIMIT]


def collect_metrics(workdir: Path, selection: Selection) -> list[Path]:
    """Return the recent metric files for the selection window."""
    metric_dir = workdir / ".sdd" / "metrics"
    if not metric_dir.is_dir():
        return []
    candidates = [p for p in metric_dir.iterdir() if p.is_file()]
    if selection.ident:
        candidates = [p for p in candidates if selection.ident in p.name]
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[:_METRIC_FILE_LIMIT]


def collect_runtime_logs(workdir: Path) -> list[Path]:
    """Return the ``.sdd/runtime/*.log`` files in deterministic order."""
    runtime_dir = workdir / ".sdd" / "runtime"
    if not runtime_dir.is_dir():
        return []
    logs = sorted(p for p in runtime_dir.glob("*.log") if p.is_file())
    return logs


def _git_recent_src_files(workdir: Path, limit: int) -> list[Path]:
    """Return the *limit* most-recently-changed git-tracked files under ``src/``.

    Falls back to an empty list when ``git`` is unavailable or the
    repository check fails.
    """
    if limit <= 0:
        return []
    try:
        proc = subprocess.run(
            ["git", "-C", str(workdir), "log", "--name-only", "--pretty=", "-n", "200"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    seen: set[str] = set()
    ordered: list[Path] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("src/"):
            continue
        if line in seen:
            continue
        seen.add(line)
        candidate = workdir / line
        if candidate.is_file():
            ordered.append(candidate)
        if len(ordered) >= limit:
            break
    return ordered


def collect_source_snippets(workdir: Path, limit: int) -> list[Path]:
    """Public entry point so tests can stub it for fixtures without git."""
    return _git_recent_src_files(workdir, limit)


# ---------------------------------------------------------------------------
# Doctor snapshot
# ---------------------------------------------------------------------------


def collect_doctor_snapshot() -> dict[str, Any]:
    """Run ``bernstein doctor --json`` and return its parsed output.

    Failures are recorded in-band so the bundle is never aborted by a
    flaky doctor invocation.
    """
    try:
        proc = subprocess.run(
            ["bernstein", "doctor", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"error": f"doctor invocation failed: {exc.__class__.__name__}"}
    if proc.returncode != 0:
        return {
            "error": "doctor returned non-zero exit code",
            "returncode": proc.returncode,
            "stderr_tail": proc.stderr.splitlines()[-20:],
        }
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {
            "error": "doctor output was not JSON",
            "stdout_tail": proc.stdout.splitlines()[-20:],
        }


# ---------------------------------------------------------------------------
# Bundle assembly
# ---------------------------------------------------------------------------


@dataclass
class BundleResult:
    """Outcome of :func:`build_debug_bundle`.

    Attributes:
        output_path: Where the ZIP was written. ``None`` for
            ``manifest_only`` runs.
        manifest: The manifest that was written (or would have been).
    """

    output_path: Path | None
    manifest: DebugManifest


def build_debug_bundle(
    workdir: Path,
    *,
    task: str | None = None,
    run: str | None = None,
    last: bool = True,
    out: Path | None = None,
    manifest_only: bool = False,
    include_source_snippets: int = 0,
) -> BundleResult:
    """Assemble the debug bundle and write it to *out*.

    Args:
        workdir: Project root directory.
        task: Optional task id selector.
        run: Optional run id selector.
        last: Truthy when caller wants the default ``--last`` behaviour.
        out: Output ZIP path. When ``None``, a timestamped path is
            chosen under the current working directory.
        manifest_only: When ``True``, skip ZIP creation and return only
            the in-memory manifest.
        include_source_snippets: Number of recently-changed source
            files to include under ``source/``. ``0`` disables.

    Returns:
        :class:`BundleResult` with manifest and (optionally) the output
        path of the written ZIP.
    """
    selection = resolve_selection(workdir, task=task, run=run, last=last)
    host = build_host_fields()

    entries: dict[str, str] = {}
    total_redactions = 0

    # bernstein.yaml -- redacted
    config_path = workdir / "bernstein.yaml"
    if config_path.is_file():
        cleaned, redactions = redact_file(config_path)
        entries["bernstein.yaml"] = cleaned
        total_redactions += redactions
    else:
        entries["bernstein.yaml"] = "# bernstein.yaml not found\n"

    # doctor.json
    doctor_payload = collect_doctor_snapshot()
    doctor_text = json.dumps(doctor_payload, indent=2, sort_keys=True)
    cleaned_doctor, doctor_redactions = redact_text(doctor_text)
    entries["doctor.json"] = cleaned_doctor
    total_redactions += doctor_redactions

    # traces
    for trace_path in collect_traces(workdir, selection):
        cleaned, redactions = redact_file(trace_path)
        entries[f"traces/{trace_path.name}"] = cleaned
        total_redactions += redactions

    # metrics
    for metric_path in collect_metrics(workdir, selection):
        cleaned, redactions = redact_file(metric_path)
        entries[f"metrics/{metric_path.name}"] = cleaned
        total_redactions += redactions

    # runtime logs -- last 200 lines per log file
    for log_path in collect_runtime_logs(workdir):
        raw = _tail_lines(log_path, _RUNTIME_LOG_TAIL_LINES)
        cleaned, redactions = redact_text(raw)
        entries[f"logs/{log_path.name}"] = cleaned
        total_redactions += redactions

    # Optional source snippets (file-inclusion budget enforced here)
    if include_source_snippets > 0:
        used_bytes = 0
        for src_path in collect_source_snippets(workdir, include_source_snippets):
            cleaned, redactions = redact_file(src_path)
            size = len(cleaned.encode("utf-8", errors="replace"))
            if used_bytes + size > _SOURCE_SNIPPET_BYTE_BUDGET:
                break
            used_bytes += size
            rel = src_path.relative_to(workdir).as_posix()
            entries[f"source/{rel}"] = cleaned
            total_redactions += redactions

    # Build manifest. Files list is sorted for determinism, manifest.json
    # itself is not in the file list (it is the index of the others).
    files_list = sorted(entries.keys())
    manifest = DebugManifest(
        schema_version=1,
        created_at=datetime.now(tz=UTC).isoformat(),
        bernstein_version=host["bernstein_version"],
        python_version=host["python_version"],
        os=host["os"],
        install_method=host["install_method"],
        selection={"mode": selection.mode, "id": selection.ident},
        files=files_list,
        redactions_applied=total_redactions,
    )

    if manifest_only:
        return BundleResult(output_path=None, manifest=manifest)

    # Resolve output path
    output_path = _resolve_output_path(workdir, out, selection)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(asdict(manifest), indent=2) + "\n")
        for arc_name in files_list:
            zf.writestr(arc_name, entries[arc_name])

    return BundleResult(output_path=output_path, manifest=manifest)


def _resolve_output_path(
    workdir: Path,
    out: Path | None,
    selection: Selection,
) -> Path:
    """Pick the ZIP destination path, generating a default when needed."""
    if out is not None:
        return out.resolve() if out.is_absolute() or out.parent != Path() else out
    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = f"-{selection.ident}" if selection.ident else ""
    return workdir / f"bernstein-debug-{ts}{suffix}.zip"


# ---------------------------------------------------------------------------
# Backward-compat alias for ZIP plumbing
# ---------------------------------------------------------------------------

# Re-exported for callers that want the older ArchiveManifest shape;
# the new debug bundle uses :class:`DebugManifest` so this avoids
# duplicating the run-archive type while keeping the import discoverable.
__all__ = [
    "ArchiveManifest",
    "BundleResult",
    "DebugManifest",
    "Selection",
    "build_debug_bundle",
    "collect_doctor_snapshot",
    "collect_metrics",
    "collect_runtime_logs",
    "collect_source_snippets",
    "collect_traces",
    "debug_group",
    "detect_install_method",
    "resolve_selection",
]


# ---------------------------------------------------------------------------
# Click group + bundle subcommand
# ---------------------------------------------------------------------------


@click.group("debug")
def debug_group() -> None:
    """Diagnostics utilities: ``bernstein debug bundle`` and friends."""


@debug_group.command("bundle")
@click.option("--task", type=str, default=None, help="Task id to filter traces/metrics by.")
@click.option("--run", type=str, default=None, help="Run id to filter traces/metrics by.")
@click.option(
    "--last/--no-last",
    "last",
    default=True,
    help="Use the most recent run (default).",
)
@click.option(
    "--out",
    "-o",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Destination ZIP path (default: timestamped file in CWD).",
)
@click.option(
    "--manifest-only",
    is_flag=True,
    default=False,
    help="Print the manifest JSON to stdout instead of writing a ZIP.",
)
@click.option(
    "--include-source-snippets",
    "include_source_snippets",
    type=click.IntRange(min=0),
    default=0,
    help="Include the N most-recently-changed src/ files (off by default).",
)
def bundle_cmd(
    task: str | None,
    run: str | None,
    last: bool,
    out: Path | None,
    manifest_only: bool,
    include_source_snippets: int,
) -> None:
    """Export a redacted debug bundle for bug reports.

    Default selection is ``--last``: traces, metrics, logs from the most
    recent run plus the project's ``bernstein.yaml`` and a
    ``doctor.json`` snapshot are zipped together. Every text artefact is
    passed through the secret-redaction pipeline first.
    """
    workdir = Path.cwd()
    result = build_debug_bundle(
        workdir,
        task=task,
        run=run,
        last=last,
        out=out,
        manifest_only=manifest_only,
        include_source_snippets=include_source_snippets,
    )

    if manifest_only:
        click.echo(json.dumps(asdict(result.manifest), indent=2))
        return

    assert result.output_path is not None  # invariant of non-manifest path
    size_bytes = result.output_path.stat().st_size
    size_kib = size_bytes / 1024
    console.print(
        f"Debug bundle written to [bold]{result.output_path}[/bold] "
        f"({size_kib:.1f} KiB, {len(result.manifest.files)} files, "
        f"{result.manifest.redactions_applied} redactions)."
    )
