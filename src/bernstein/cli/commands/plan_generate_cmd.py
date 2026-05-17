"""AI-powered plan generator from a natural language description.

Usage:
  bernstein plan generate "Add rate limiting to all API endpoints with Redis backing"

Analyses the repo structure, identifies relevant files, generates a
multi-stage YAML plan with appropriate roles and dependencies, and
prints a cost estimate.  The result is written to plans/<slug>.yaml
by default, or to the path specified with --output.
"""

from __future__ import annotations

import asyncio
import re
import textwrap
from pathlib import Path
from typing import Any

import click
import yaml

from bernstein.cli.helpers import console
from bernstein.core.plan_schema import KNOWN_ROLES

# ---------------------------------------------------------------------------
# Repo context helpers
# ---------------------------------------------------------------------------

_MAX_CONTEXT_BYTES = 4_000  # cap context sent to LLM


def _read_trunc(path: Path, max_bytes: int = 800) -> str:
    """Read a file, truncating to max_bytes."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > max_bytes:
            return text[:max_bytes] + "\n... (truncated)"
        return text
    except OSError:
        return ""


def _find_first_file(workdir: Path, names: tuple[str, ...], max_bytes: int) -> str | None:
    """Return the truncated content of the first existing file from *names*, or None."""
    for name in names:
        p = workdir / name
        if p.exists():
            return f"=== {name} ===\n{_read_trunc(p, max_bytes)}"
    return None


_SKIP_DIRS = {"__pycache__", "node_modules"}


def _should_skip_entry(entry: Path) -> bool:
    """Return True if a directory entry should be excluded from the tree."""
    return entry.name.startswith(".") or entry.name in _SKIP_DIRS


def _list_dir_children(directory: Path) -> list[str]:
    """List up to 6 visible children of a directory."""
    lines: list[str] = []
    try:
        for child in sorted(directory.iterdir())[:6]:
            if not child.name.startswith("."):
                lines.append(f"    {child.name}{'/' if child.is_dir() else ''}")
    except PermissionError:
        pass
    return lines


def _build_directory_tree(workdir: Path) -> list[str]:
    """Build a 2-level directory tree listing, skipping hidden/noise dirs."""
    tree_lines: list[str] = []
    try:
        for entry in sorted(workdir.iterdir()):
            if _should_skip_entry(entry):
                continue
            if entry.is_dir():
                tree_lines.append(f"  {entry.name}/")
                tree_lines.extend(_list_dir_children(entry))
            else:
                tree_lines.append(f"  {entry.name}")
    except PermissionError:
        pass
    return tree_lines


def _gather_repo_context(workdir: Path) -> str:
    """Collect lightweight repo context for the LLM prompt.

    Reads CLAUDE.md (instructions), pyproject.toml/package.json (stack),
    and the top-level directory tree to give the LLM enough context to
    produce relevant plan stages and file hints.

    Args:
        workdir: Project root directory.

    Returns:
        A plain-text context block, capped at ``_MAX_CONTEXT_BYTES``.
    """
    parts: list[str] = []

    instructions = _find_first_file(workdir, ("CLAUDE.md", "AGENTS.md", "GEMINI.md"), 600)
    if instructions:
        parts.append(instructions)

    stack = _find_first_file(workdir, ("pyproject.toml", "package.json", "Cargo.toml", "go.mod"), 400)
    if stack:
        parts.append(stack)

    tree_lines = _build_directory_tree(workdir)
    if tree_lines:
        parts.append("=== Directory tree (2 levels) ===\n" + "\n".join(tree_lines))

    raw = "\n\n".join(parts)
    if len(raw) > _MAX_CONTEXT_BYTES:
        raw = raw[:_MAX_CONTEXT_BYTES] + "\n... (truncated)"
    return raw


# ---------------------------------------------------------------------------
# LLM prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = textwrap.dedent("""\
    You are a senior software architect.  Your job is to turn a one-line
    project description into a multi-stage Bernstein YAML plan.

    Output ONLY valid YAML — no prose, no fences.  Follow the schema exactly.

    Schema rules:
    - Top-level keys: name, description, stages
    - Each stage: name, description (optional), depends_on (list of stage names),
      steps (list)
    - Each step: title, description, role, scope (small|medium|large),
      complexity (low|medium|high)
    - Valid roles: {roles}
    - Keep stages to 2-5; keep steps per stage to 2-6
    - Use depends_on to express sequential dependencies between stages
    - Descriptions must be actionable agent instructions, not vague
    - Do NOT include budget, max_agents, cli, or context_files keys
    """).format(roles=", ".join(KNOWN_ROLES))


def _build_prompt(description: str, repo_context: str) -> str:
    """Build the LLM prompt.

    Args:
        description: Natural language description from the user.
        repo_context: Repo context gathered by ``_gather_repo_context``.

    Returns:
        Full prompt string with system instructions prepended.
    """
    return (
        f"{_SYSTEM_PROMPT}\n\n"
        f"Repository context:\n{repo_context}\n\n"
        f"Generate a Bernstein YAML plan for:\n{description}\n"
    )


# ---------------------------------------------------------------------------
# YAML extraction and cost estimate
# ---------------------------------------------------------------------------


def _extract_yaml(raw: str) -> str:
    """Strip markdown fences if the LLM wrapped the output.

    Args:
        raw: Raw LLM response.

    Returns:
        Clean YAML string.
    """
    # Remove leading/trailing whitespace
    raw = raw.strip()
    # Strip ```yaml ... ``` fences
    raw = re.sub(r"^```(?:yaml)?\s*\n", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\n```\s*$", "", raw, flags=re.MULTILINE)
    return raw.strip()


_COST_PER_STEP = 0.08  # rough USD per agent step (medium complexity, sonnet)
_SCOPE_MULT: dict[str, float] = {"small": 0.5, "medium": 1.0, "large": 2.0}
_COMPLEXITY_MULT: dict[str, float] = {"low": 0.6, "medium": 1.0, "high": 1.6}


def _estimate_cost(plan_data: dict[str, Any]) -> tuple[int, float]:
    """Estimate plan cost from YAML data.

    Args:
        plan_data: Parsed YAML dict.

    Returns:
        Tuple of (step_count, estimated_usd).
    """
    steps = 0
    cost = 0.0
    for stage in plan_data.get("stages", []):
        for step in stage.get("steps", []):
            steps += 1
            scope_m = _SCOPE_MULT.get(step.get("scope", "medium"), 1.0)
            complexity_m = _COMPLEXITY_MULT.get(step.get("complexity", "medium"), 1.0)
            cost += _COST_PER_STEP * scope_m * complexity_m
    return steps, cost


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _slug(text: str, max_len: int = 40) -> str:
    """Convert text to a filesystem-safe slug.

    Args:
        text: Input text.
        max_len: Maximum slug length.

    Returns:
        Slug string.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len]


# ---------------------------------------------------------------------------
# Vertical-slice shape check on YAML plans
# ---------------------------------------------------------------------------


def _yaml_steps_to_pseudo_tasks(plan_data: dict[str, Any]) -> list[Any]:
    """Convert YAML plan steps into lightweight pseudo-Task objects.

    The shape checker reads ``title``, ``owned_files``, and ``scope``.
    We provide a minimal stand-in so ``plan generate`` can reuse the
    same checker without importing the full Task dataclass.
    """
    from dataclasses import dataclass

    @dataclass
    class _PseudoScope:
        value: str

    @dataclass
    class _PseudoTask:
        title: str
        owned_files: list[str]
        scope: _PseudoScope

    pseudo: list[Any] = []
    for stage in plan_data.get("stages", []) or []:
        for step in stage.get("steps", []) or []:
            pseudo.append(
                _PseudoTask(
                    title=str(step.get("title", "")),
                    owned_files=list(step.get("owned_files", []) or []),
                    scope=_PseudoScope(value=str(step.get("scope", "medium"))),
                )
            )
    return pseudo


def _shape_check_yaml_plan(
    plan_data: dict[str, Any],
    *,
    max_loc: int | None,
    max_files: int | None,
) -> None:
    """Run the vertical-slice shape checker against a YAML plan.

    Prints violations to the console for operator visibility.  Does not
    raise — the YAML is still written so the operator can edit it; the
    diagnostics make the issues obvious.
    """
    from bernstein.core.planning.vertical_slice import (
        ShapeConfig,
        check_plan,
        load_shape_config,
    )

    base = load_shape_config(Path("."))
    cfg = ShapeConfig(
        enforce_vertical=True,
        max_loc_hard=max_loc if max_loc is not None else base.max_loc_hard,
        max_loc_ideal=base.max_loc_ideal,
        max_files=max_files if max_files is not None else base.max_files,
        max_modules=base.max_modules,
    )
    pseudo = _yaml_steps_to_pseudo_tasks(plan_data)
    if not pseudo:
        return
    violations = check_plan(pseudo, cfg)
    if not violations:
        console.print("[dim]Vertical-slice shape check passed.[/dim]")
        return
    errors = [v for v in violations if v.severity == "error"]
    warns = [v for v in violations if v.severity == "warn"]
    if errors:
        console.print(f"[red]Vertical-slice shape check found {len(errors)} error(s):[/red]")
        for v in errors:
            console.print(f"  - [{v.rule}] {v.message}")
        console.print(
            "[dim]Re-run with --no-enforce-vertical to bypass, or edit the YAML to split oversized stages.[/dim]"
        )
    if warns:
        console.print(f"[yellow]{len(warns)} shape warning(s):[/yellow]")
        for v in warns:
            console.print(f"  - [{v.rule}] {v.message}")


# ---------------------------------------------------------------------------
# Click command
# ---------------------------------------------------------------------------


@click.command("generate")
@click.argument("description")
@click.option(
    "--output",
    "-o",
    default=None,
    metavar="FILE",
    help="Output path for the YAML plan. Defaults to plans/<slug>.yaml.",
)
@click.option(
    "--model",
    default="anthropic/claude-haiku-4-5",
    show_default=True,
    help="LLM model to use for generation.",
)
@click.option(
    "--provider",
    default="openrouter",
    show_default=True,
    help="LLM provider (openrouter, openai, ...).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print the generated plan without saving to disk.",
)
@click.option(
    "--workdir",
    default=".",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Project root directory to analyse.",
)
@click.option(
    "--enforce-vertical/--no-enforce-vertical",
    default=True,
    show_default=True,
    help=(
        "Enforce vertical-slice shape checks on the generated plan "
        "(issue #1321). Default on in the 2.x line; use "
        "``--no-enforce-vertical`` to opt out."
    ),
)
@click.option(
    "--max-loc",
    "max_loc",
    type=int,
    default=None,
    help="Hard LOC cap per slice. Overrides bernstein.yaml [plan].max_loc.",
)
@click.option(
    "--max-files",
    "max_files",
    type=int,
    default=None,
    help="Max files per slice. Overrides bernstein.yaml [plan].max_files.",
)
def plan_generate(
    description: str,
    output: str | None,
    model: str,
    provider: str,
    dry_run: bool,
    workdir: Path,
    enforce_vertical: bool,
    max_loc: int | None,
    max_files: int | None,
) -> None:
    """Generate a multi-stage YAML plan from a natural language description.

    \b
      bernstein plan generate "Add Redis-backed rate limiting to all API endpoints"
      bernstein plan generate "Migrate auth system from JWT to Paseto" --dry-run
      bernstein plan generate "Add OpenTelemetry tracing" -o plans/tracing.yaml
    """
    console.print(f"[dim]Analysing repo at {workdir.resolve()}...[/dim]")
    repo_context = _gather_repo_context(workdir)

    console.print(f"[dim]Calling {provider}/{model} to generate plan...[/dim]")
    prompt = _build_prompt(description, repo_context)

    try:
        from bernstein.core.llm import call_llm

        raw = asyncio.run(
            call_llm(
                prompt,
                model=model,
                provider=provider,
                max_tokens=2_000,
                temperature=0.3,
            )
        )
    except Exception as exc:
        console.print(f"[red]LLM call failed:[/red] {exc}")
        raise SystemExit(1) from exc

    yaml_text = _extract_yaml(raw)

    # Validate the YAML is parseable
    try:
        plan_data: dict[str, Any] = yaml.safe_load(yaml_text) or {}
    except yaml.YAMLError as exc:
        console.print(f"[red]LLM returned invalid YAML:[/red] {exc}")
        console.print("[dim]Raw output:[/dim]")
        console.print(yaml_text)
        raise SystemExit(1) from exc

    # Inject name/description from user input if LLM omitted them
    if not plan_data.get("name"):
        plan_data["name"] = description[:60]
    if not plan_data.get("description"):
        plan_data["description"] = description

    # Vertical-slice shape check (issue #1321).  Operates on the YAML
    # steps directly; reports violations but does not abort by default
    # — the LLM-generated YAML is then surfaced to the operator who can
    # re-run with ``--no-enforce-vertical`` if appropriate.
    if enforce_vertical:
        _shape_check_yaml_plan(plan_data, max_loc=max_loc, max_files=max_files)

    step_count, estimated_usd = _estimate_cost(plan_data)
    stage_count = len(plan_data.get("stages", []))

    # Re-serialise to ensure clean YAML
    final_yaml = yaml.dump(plan_data, default_flow_style=False, sort_keys=False, allow_unicode=True)

    saved_path: Path | None = None
    if dry_run:
        console.print("\n[bold cyan]Generated plan:[/bold cyan]\n")
        console.print(final_yaml)
    else:
        # Determine output path
        if output:
            saved_path = Path(output)
        else:
            slug = _slug(description)
            saved_path = workdir / "plans" / f"{slug}.yaml"

        saved_path.parent.mkdir(parents=True, exist_ok=True)
        saved_path.write_text(final_yaml, encoding="utf-8")
        console.print(f"[green]Plan saved to[/green] {saved_path}")

    console.print(
        f"\n[bold]Summary:[/bold] {stage_count} stage(s), {step_count} step(s), estimated cost ~${estimated_usd:.2f}"
    )
    if not dry_run and saved_path is not None:
        console.print(f"[dim]Run with:[/dim] bernstein run {saved_path}")
