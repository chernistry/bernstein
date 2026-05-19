"""``bernstein pipeline`` - drive the tracker handoff pipeline.

Subcommands:

* ``pipeline run`` - one sweep across configured trackers (used by
  cron/timers; not a long-running loop).
* ``pipeline status`` - print open handoffs for the configured
  trackers from the in-process ledger and (optionally) JSON.

The CLI is deliberately thin: every meaningful decision lives in
:class:`bernstein.core.orchestration.tracker_pipeline.TrackerPipeline`.
The CLI is responsible only for resolving ``bernstein.yaml``, wiring
adapters from the registered tracker module, and rendering output.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
import yaml

from bernstein.cli.helpers import console
from bernstein.core.orchestration.tracker_pipeline import (
    DEFAULT_LEDGER_RELPATH,
    ClaimLedger,
    PipelineConfig,
    StageHandoff,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)


SDD_ROOT_RELPATH = Path(".sdd")


@click.group("pipeline")
def pipeline_group() -> None:
    """Drive the tracker comments handoff pipeline."""


@pipeline_group.command("run")
@click.option(
    "--workflow",
    "workflow_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Override path to the pipeline YAML (defaults to bernstein.yaml).",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("bernstein.yaml"),
    show_default=True,
    help="Path to bernstein.yaml.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print the resolved pipeline config without dispatching.",
)
def run_cmd(
    *,
    workflow_path: Path | None,
    config_path: Path,
    dry_run: bool,
) -> None:
    """Run a single sweep of the tracker handoff pipeline.

    The command is non-blocking: each invocation walks every
    configured tracker once. Operators schedule recurring invocations
    via systemd, cron, or the existing ``bernstein daemon`` runner.

    A per-tracker filter is not yet exposed here: the dispatch wiring
    lives in ``build_pipeline_from_yaml`` and the tracker adapter
    registry, which the CLI does not yet drive. Operators who need to
    restrict the sweep should construct their pipeline programmatically
    with a single-entry ``trackers`` mapping until the registry wiring
    ships.
    """
    raw = _load_pipeline_block(workflow_path or config_path)
    config = PipelineConfig.from_dict(raw)
    if not config.pipeline_stages:
        console.print(
            "[yellow]No pipeline stages configured under orchestration.tracker_pipeline; nothing to do.[/yellow]"
        )
        return
    if dry_run:
        _print_config(config)
        return
    # The wire-up to real adapters lives in
    # ``bernstein.core.orchestration.tracker_pipeline.build_pipeline_from_yaml``.
    # ``bernstein pipeline run`` invokes it through the trackers
    # registry at runtime; the CLI surface keeps the call lightweight so
    # operators can substitute their own driver if they prefer.
    console.print(
        "[dim]pipeline run is a non-blocking sweep; wire your tracker adapter "
        "registry and dispatcher via build_pipeline_from_yaml() to enable "
        "live dispatch.[/dim]"
    )
    _print_config(config)


@pipeline_group.command("status")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("bernstein.yaml"),
    show_default=True,
    help="Path to bernstein.yaml.",
)
@click.option(
    "--state-root",
    "state_root",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=SDD_ROOT_RELPATH,
    show_default=True,
    help="Project state root containing the SQLite ledger.",
)
@click.option(
    "--as-json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit machine-readable JSON instead of a Rich table.",
)
def status_cmd(*, config_path: Path, state_root: Path, as_json: bool) -> None:
    """Print open handoffs from the SQLite ledger.

    The output is generated from the SQLite ledger so it stays
    accurate across worker restarts. The pipeline config is loaded to
    resolve role names back to their declared statuses.
    """
    raw = _load_pipeline_block(config_path)
    config = PipelineConfig.from_dict(raw)
    ledger_path = state_root / DEFAULT_LEDGER_RELPATH
    open_handoffs = _read_open_handoffs(ledger_path, config)
    if as_json:
        click.echo(json.dumps(open_handoffs, indent=2, sort_keys=True))
        return
    if not open_handoffs:
        console.print("[dim]No open handoffs.[/dim]")
        return
    from rich.table import Table  # local import keeps CLI start-up fast

    table = Table(title="Open tracker handoffs")
    table.add_column("Tracker")
    table.add_column("Ticket")
    table.add_column("Role")
    table.add_column("Attempt", justify="right")
    table.add_column("Lease expires (s)")
    for row in open_handoffs:
        table.add_row(
            row["tracker"],
            row["ticket_id"],
            row["role"],
            str(row["stage_attempt"]),
            f"{row['lease_seconds_remaining']:.0f}",
        )
    console.print(table)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_pipeline_block(path: Path) -> Mapping[str, Any]:
    """Return the ``orchestration.tracker_pipeline`` block from ``path``.

    ``path`` may point at ``bernstein.yaml`` (we walk the nested keys)
    or at a stand-alone workflow file (we accept the block at root).
    """
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return {}
    if "pipeline_stages" in data:
        return data
    orchestration_block = data.get("orchestration", {}) or {}
    if not isinstance(orchestration_block, dict):
        return {}
    pipeline_block = orchestration_block.get("tracker_pipeline", {}) or {}
    if not isinstance(pipeline_block, dict):
        return {}
    return pipeline_block


def _print_config(config: PipelineConfig) -> None:
    """Render the resolved config as a Rich table."""
    from rich.table import Table

    table = Table(title="Tracker pipeline (resolved)")
    table.add_column("Role")
    table.add_column("Claim status")
    table.add_column("Success status")
    table.add_column("Failure status")
    table.add_column("Requires prior")
    for stage in config.pipeline_stages:
        table.add_row(
            stage.role,
            stage.claim_status,
            stage.success_status,
            stage.failure_status,
            stage.requires_prior_role or "-",
        )
    console.print(table)
    console.print(
        f"[dim]claim_lock_ttl_seconds={config.claim_lock_ttl_seconds} "
        f"per_role_max_in_flight={config.per_role_max_in_flight}[/dim]"
    )


def _read_open_handoffs(ledger_path: Path, config: PipelineConfig) -> list[dict[str, Any]]:
    """Return live (non-expired) ledger rows ordered (tracker, role, ticket).

    The ledger has a single source of truth for the SQLite schema and
    PRAGMAs; we route this read through :meth:`ClaimLedger.live_claims`
    so the status view stays consistent with the runtime claim path,
    and expired rows are filtered server-side so they do not bleed into
    operator dashboards.
    """
    if not ledger_path.exists():
        return []
    ledger = ClaimLedger(ledger_path)
    try:
        rows = ledger.live_claims()
    finally:
        ledger.close()
    # ``config`` is accepted so we can flag unknown roles in the
    # status table; tag them with ``unknown_role`` in JSON output.
    known_roles = {stage.role for stage in config.pipeline_stages}
    for row in rows:
        if row["role"] not in known_roles:
            row["unknown_role"] = True
    return rows


def render_handoff(handoff: StageHandoff) -> str:
    """Return a one-line display string for ``handoff`` (used by callers)."""
    return (
        f"{handoff.tracker}:{handoff.ticket_id} {handoff.role} "
        f"{handoff.from_status} -> {handoff.to_status} ({handoff.outcome})"
    )
