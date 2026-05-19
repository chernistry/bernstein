"""``bernstein secrets ...`` CLI commands.

Operator surface for the short-lived-token broker. The CLI is intentionally
small: ``list`` enumerates backend-visible secret names, and ``mint`` issues
a one-shot token for an out-of-band agent invocation. Routine in-process
minting happens via the orchestrator's broker instance, not this CLI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click
import yaml

from bernstein.core.security.redactor import mask
from bernstein.core.security.secrets_broker import (
    SecretsBrokerError,
    build_broker_from_config,
)

__all__ = ["secrets_group"]


def _load_secrets_block(config_path: Path) -> dict[str, Any]:
    """Load and validate the ``security.secrets`` block from a YAML file."""
    if not config_path.exists():
        raise click.ClickException(f"config not found: {config_path}")
    try:
        raw: object = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise click.ClickException(f"invalid YAML in {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise click.ClickException(f"top-level YAML in {config_path} must be a mapping")
    security: object = raw.get("security") or {}
    if not isinstance(security, dict):
        raise click.ClickException("security block must be a mapping")
    secrets_block: object = security.get("secrets")
    if not isinstance(secrets_block, dict):
        raise click.ClickException("security.secrets block is missing or not a mapping")
    return {str(k): v for k, v in secrets_block.items()}


@click.group(name="secrets")
def secrets_group() -> None:
    """Short-lived-token broker commands."""


@secrets_group.command(name="list")
@click.option(
    "--config",
    "config_path",
    default="bernstein.yaml",
    show_default=True,
    type=click.Path(dir_okay=False, path_type=Path),
)
def secrets_list(config_path: Path) -> None:
    """List secret names the configured backend can enumerate."""
    block = _load_secrets_block(config_path)
    try:
        broker = build_broker_from_config(block)
    except SecretsBrokerError as exc:
        raise click.ClickException(str(exc)) from exc
    names = broker.list_backend_secrets()
    if not names:
        click.echo("(backend does not enumerate secret names)")
        return
    for name in names:
        click.echo(name)


@secrets_group.command(name="mint")
@click.option(
    "--config",
    "config_path",
    default="bernstein.yaml",
    show_default=True,
    type=click.Path(dir_okay=False, path_type=Path),
)
@click.option("--task", "task_id", required=True, help="Bernstein task id that owns the token.")
@click.option("--secret", "secret_name", required=True, help="Backing secret name in the configured backend.")
@click.option(
    "--ttl",
    "ttl_seconds",
    type=int,
    default=None,
    help="TTL in seconds. Defaults to mint.ttl_seconds_default.",
)
@click.option(
    "--reveal",
    is_flag=True,
    default=False,
    help="Print the raw token value. Off by default: only metadata is printed.",
)
def secrets_mint(
    config_path: Path,
    task_id: str,
    secret_name: str,
    ttl_seconds: int | None,
    reveal: bool,
) -> None:
    """Mint a short-lived token for a backing secret."""
    block = _load_secrets_block(config_path)
    try:
        broker = build_broker_from_config(block)
        token = broker.mint(secret_name=secret_name, task_id=task_id, ttl_seconds=ttl_seconds)
    except SecretsBrokerError as exc:
        raise click.ClickException(str(exc)) from exc
    payload = {
        "token_id": token.token_id,
        "secret_name": token.secret_name,
        "task_id": token.task_id,
        "ttl_seconds": token.ttl_seconds,
        "expires_at": token.expires_at,
        "value": token.value if reveal else mask(token.value, keep=4),
    }
    click.echo(json.dumps(payload, sort_keys=True))
