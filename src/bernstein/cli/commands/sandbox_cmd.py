"""``bernstein sandbox`` - sandbox-adjacent operator commands.

Subcommands:

* ``sandbox web-test`` - drive a Playwright self-test against a dev
  server URL using scenarios declared in a YAML file. Artefacts land
  under ``.sdd/sandbox/<task-id>/``.

The CLI is intentionally thin: it loads scenarios, invokes
:class:`bernstein.core.sandbox.playwright_runner.PlaywrightRunner`, and
prints the structured self-test block on stdout. The same block is
intended to be fed back into the agent's next prompt.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

import click

from bernstein.cli.helpers import console
from bernstein.core.sandbox.playwright_runner import (
    PlaywrightRunner,
    PlaywrightScenarioError,
    PlaywrightUnavailableError,
    load_scenarios,
)

if TYPE_CHECKING:
    from bernstein.core.sandbox.playwright_runner import PlaywrightRunResult

logger = logging.getLogger(__name__)


_DEFAULT_OUTPUT_ROOT = Path(".sdd/sandbox")
# Allowed task_id shape: must be a single safe slug (letters, digits, dot,
# hyphen, underscore). Rejects path separators and traversal sequences so
# `_DEFAULT_OUTPUT_ROOT / task_id` cannot escape the sandbox root.
_TASK_ID_PATTERN = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


@click.group("sandbox")
def sandbox_group() -> None:
    """Sandbox-adjacent operator commands."""


@sandbox_group.command("web-test")
@click.argument("task_id", type=str)
@click.option(
    "--url",
    "base_url",
    required=True,
    help="Base URL of the dev server (e.g. http://localhost:5173).",
)
@click.option(
    "--scenarios",
    "scenarios_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to the scenarios YAML file.",
)
@click.option(
    "--output-dir",
    "output_dir",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help=("Directory for screenshots, console logs, and the run summary. Defaults to .sdd/sandbox/<task-id>/."),
)
@click.option(
    "--task-description",
    "task_description",
    default="",
    help="Task description forwarded to the LLM judge prompt.",
)
@click.option(
    "--judge/--no-judge",
    default=False,
    show_default=True,
    help="Run the LLM judge against the scenario result.",
)
@click.option(
    "--judge-model",
    default="anthropic/claude-sonnet-4",
    show_default=True,
    help="LLM judge model identifier.",
)
@click.option(
    "--judge-provider",
    default="openrouter_free",
    show_default=True,
    help="LLM judge provider.",
)
@click.option(
    "--headless/--headed",
    default=True,
    show_default=True,
    help="Whether to launch Chromium headless.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Print the full run summary as JSON instead of the self-test block.",
)
def web_test(
    task_id: str,
    base_url: str,
    scenarios_path: Path,
    output_dir: Path | None,
    task_description: str,
    judge: bool,
    judge_model: str,
    judge_provider: str,
    headless: bool,
    as_json: bool,
) -> None:
    """Run Playwright scenarios and emit a structured self-test block."""
    if not _TASK_ID_PATTERN.fullmatch(task_id):
        raise click.BadParameter(
            "task_id must match [A-Za-z0-9][A-Za-z0-9._-]{0,127}: no path separators or traversal segments allowed.",
            param_hint="TASK_ID",
        )

    try:
        scenarios = load_scenarios(scenarios_path)
    except (FileNotFoundError, PlaywrightScenarioError) as exc:
        raise click.ClickException(f"Scenario load failed: {exc}") from exc

    target_dir = output_dir or _DEFAULT_OUTPUT_ROOT / task_id
    runner = PlaywrightRunner(
        base_url=base_url,
        output_dir=target_dir,
        headless=headless,
    )

    judge_instance = None
    if judge:
        # Imported lazily so the CLI does not eagerly load the LLM client
        # when --no-judge is used.
        from bernstein.eval.judge import EvalJudge

        judge_instance = EvalJudge(model=judge_model, provider=judge_provider)

    try:
        result: PlaywrightRunResult = asyncio.run(
            runner.run(
                scenarios,
                task_description=task_description,
                judge=judge_instance,
            )
        )
    except PlaywrightUnavailableError as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        summary_path = Path(result.summary_path)
        console.print(summary_path.read_text(encoding="utf-8"))
    else:
        console.print(result.to_self_test_block())
        console.print(f"\n[dim]summary: {result.summary_path}[/dim]")

    if not result.passed:
        raise click.exceptions.Exit(code=1)


__all__ = ["sandbox_group", "web_test"]
