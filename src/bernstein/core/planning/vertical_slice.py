"""Vertical-slice shape checker for LLM-emitted plans.

Issue #1321: enforce vertical slicing so the planner refuses plans where
each task lives in only one architectural layer ("DB → DB → DB"), or
where a single task is large enough that it cannot be reviewed in one
pass.  The shape-checker runs *after* the LLM emits a plan and either
accepts it or returns a structured violation report which the planner
uses to re-prompt the LLM with a "your previous plan was too coarse"
correction.

Public API:
    - ``ShapeConfig``: thresholds (LOC, files, modules) — loaded from
      ``bernstein.yaml`` ``[plan]`` table when present.
    - ``ShapeViolation``: a single rule failure attached to a task or to
      a pair of consecutive tasks.
    - ``check_plan(tasks, config)``: returns a list of violations.  An
      empty list means the plan is accepted.
    - ``summarise_slice(task)``: one-line shape summary, used for
      operator-visible plan output ("slice 1: routes/ + tests/ + UI ·
      ~180 LOC").
    - ``load_shape_config(workdir)``: parse the optional ``[plan]`` table
      from ``bernstein.yaml`` for repo-level threshold overrides.

The module is deliberately I/O-light and side-effect free so it is easy
to unit-test and to call from the planner's retry loop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from bernstein.core.tasks.models import Task

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

# Per issue #1321: ideal ≤200 LOC, hard ≤400 LOC, ≤10 files, ≤2 modules.
DEFAULT_MAX_LOC_HARD = 400
DEFAULT_MAX_LOC_IDEAL = 200
DEFAULT_MAX_FILES = 10
DEFAULT_MAX_MODULES = 2

# Heuristic LOC estimate when the LLM does not state one explicitly.
# Each owned file is assumed to grow by this many lines.
_LOC_PER_FILE_ESTIMATE = 40

# Recognised layer markers in owned-files paths.  A vertical slice should
# touch at least two of these layers (or any of them plus tests/UI).
_LAYER_KEYWORDS: dict[str, tuple[str, ...]] = {
    "tests": ("tests/", "/test_", "_test.", "spec/", "/__tests__/"),
    "ui": (
        "ui/",
        "frontend/",
        "components/",
        "pages/",
        "templates/",
        ".tsx",
        ".jsx",
        ".vue",
        ".svelte",
        ".html",
    ),
    "api": ("routes/", "api/", "handlers/", "controllers/", "endpoints/", "views/"),
    "domain": ("models/", "domain/", "services/", "core/"),
    "db": ("migrations/", "schema/", "db/", "alembic/", "repositories/"),
    "infra": ("infra/", "deploy/", "terraform/", "k8s/", "ops/"),
    "docs": ("docs/", "README", ".md"),
}


@dataclass(frozen=True)
class ShapeConfig:
    """Thresholds for the shape checker.

    Defaults match the issue spec.  All numeric thresholds may be
    overridden via ``bernstein.yaml`` ``[plan]`` table, e.g.

        plan:
          max_loc: 400
          max_loc_ideal: 200
          max_files: 10
          max_modules: 2
          enforce_vertical: true
    """

    enforce_vertical: bool = True
    max_loc_hard: int = DEFAULT_MAX_LOC_HARD
    max_loc_ideal: int = DEFAULT_MAX_LOC_IDEAL
    max_files: int = DEFAULT_MAX_FILES
    max_modules: int = DEFAULT_MAX_MODULES


@dataclass(frozen=True)
class ShapeViolation:
    """A single shape rule failure.

    Attributes:
        rule: Short rule identifier (e.g. ``"max_loc_hard"``).
        message: Human-readable explanation, suitable for the re-prompt.
        task_index: Index of the offending task in the original list, or
            ``-1`` if the violation spans multiple tasks.
        task_title: Title of the offending task, for log/UX clarity.
        severity: ``"error"`` (rejects the plan) or ``"warn"`` (surfaced
            but does not trigger a re-prompt).
    """

    rule: str
    message: str
    task_index: int = -1
    task_title: str = ""
    severity: str = "error"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _layers_for_path(path: str) -> set[str]:
    """Return the set of layer labels that *path* belongs to."""
    lowered = path.lower()
    layers: set[str] = set()
    for layer, markers in _LAYER_KEYWORDS.items():
        for marker in markers:
            if marker.lower() in lowered:
                layers.add(layer)
                break
    return layers


def _layers_for_task(task: Task) -> set[str]:
    """Aggregate layer labels for every owned file on a task."""
    layers: set[str] = set()
    for path in task.owned_files:
        layers.update(_layers_for_path(path))
    return layers


def _module_roots(task: Task) -> set[str]:
    """Extract distinct top-level "modules" (first 2-3 path segments).

    A "module" here is a coarse subpackage marker — e.g.
    ``src/bernstein/core/planning/`` and
    ``src/bernstein/core/planning/foo/`` collapse to the same module.
    """
    roots: set[str] = set()
    for path in task.owned_files:
        # Normalise and split.
        parts = [seg for seg in path.replace("\\", "/").split("/") if seg]
        if not parts:
            continue
        # Use the first three segments to give "src/<pkg>/<subpkg>"
        # granularity which matches Python project layout in practice.
        depth = min(3, len(parts))
        roots.add("/".join(parts[:depth]))
    return roots


def _estimate_loc(task: Task) -> int:
    """Best-effort LOC estimate for a task.

    Looks at task ``scope`` first (small/medium/large), then falls back
    to ``len(owned_files) * _LOC_PER_FILE_ESTIMATE``.
    """
    # ``scope`` is an enum; coerce to its value safely.
    scope_value = getattr(task.scope, "value", str(task.scope)).lower()
    scope_loc = {
        "small": 80,
        "medium": 200,
        "large": 500,
    }
    base = scope_loc.get(scope_value, 200)

    file_estimate = len(task.owned_files) * _LOC_PER_FILE_ESTIMATE
    return max(base, file_estimate)


def _same_module_tree(task_a: Task, task_b: Task) -> bool:
    """Heuristic: do *task_a* and *task_b* live in the same subpackage tree?

    True when at least one module root from *task_a* is a prefix of (or
    equal to) one from *task_b* and neither task touches tests/UI.
    """
    roots_a = _module_roots(task_a)
    roots_b = _module_roots(task_b)
    if not roots_a or not roots_b:
        return False
    for a in roots_a:
        for b in roots_b:
            if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
                return True
    return False


def _is_horizontal(task: Task) -> bool:
    """A task is "horizontal" when it touches only one architectural
    layer and that layer is *not* tests or UI.
    """
    layers = _layers_for_task(task)
    if not layers:
        # If we cannot tell from owned_files, treat as horizontal only
        # when the task explicitly belongs to a single backend role with
        # no test/UI markers — be lenient here to avoid false positives.
        return False
    if "tests" in layers or "ui" in layers:
        return False
    return len(layers) == 1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def summarise_slice(task: Task) -> str:
    """One-line operator-friendly shape summary for *task*.

    Example output::

        routes/ + tests/ + UI · ~180 LOC

    Args:
        task: The task to summarise.

    Returns:
        Short label suitable for printing alongside a plan listing.
    """
    layers = sorted(_layers_for_task(task))
    layer_str = "no-files" if not layers else " + ".join(layers)
    return f"{layer_str} · ~{_estimate_loc(task)} LOC"


def check_plan(tasks: list[Task], config: ShapeConfig | None = None) -> list[ShapeViolation]:
    """Run the shape-check pass over an LLM-emitted plan.

    Args:
        tasks: Tasks parsed from the LLM response.
        config: Threshold overrides; defaults are used when ``None``.

    Returns:
        List of :class:`ShapeViolation`.  Empty list means the plan
        passed; the caller may still log ``severity="warn"`` entries.
    """
    cfg = config or ShapeConfig()
    if not cfg.enforce_vertical:
        return []

    violations: list[ShapeViolation] = []

    for idx, task in enumerate(tasks):
        loc = _estimate_loc(task)
        files = len(task.owned_files)
        modules = len(_module_roots(task))

        if loc > cfg.max_loc_hard:
            violations.append(
                ShapeViolation(
                    rule="max_loc_hard",
                    message=(
                        f"Task {idx + 1} '{task.title}' is too large "
                        f"(~{loc} LOC > {cfg.max_loc_hard} hard cap). "
                        "Split it into multiple vertical slices."
                    ),
                    task_index=idx,
                    task_title=task.title,
                )
            )
        elif loc > cfg.max_loc_ideal:
            violations.append(
                ShapeViolation(
                    rule="max_loc_ideal",
                    message=(
                        f"Task {idx + 1} '{task.title}' is above the ideal LOC budget (~{loc} > {cfg.max_loc_ideal})."
                    ),
                    task_index=idx,
                    task_title=task.title,
                    severity="warn",
                )
            )

        if files > cfg.max_files:
            violations.append(
                ShapeViolation(
                    rule="max_files",
                    message=(f"Task {idx + 1} '{task.title}' touches {files} files (> {cfg.max_files} cap)."),
                    task_index=idx,
                    task_title=task.title,
                )
            )

        if modules > cfg.max_modules:
            violations.append(
                ShapeViolation(
                    rule="max_modules",
                    message=(f"Task {idx + 1} '{task.title}' touches {modules} modules (> {cfg.max_modules} cap)."),
                    task_index=idx,
                    task_title=task.title,
                )
            )

    # Horizontal-phasing check: two consecutive tasks that both live in
    # only one layer, same subpackage tree, and neither touches tests/UI.
    for i in range(len(tasks) - 1):
        a, b = tasks[i], tasks[i + 1]
        if _is_horizontal(a) and _is_horizontal(b) and _same_module_tree(a, b):
            layers_a = sorted(_layers_for_task(a))
            layers_b = sorted(_layers_for_task(b))
            violations.append(
                ShapeViolation(
                    rule="horizontally_phased",
                    message=(
                        f"Tasks {i + 1} '{a.title}' and {i + 2} '{b.title}' "
                        f"are horizontally phased — both stay inside the "
                        f"{layers_a}/{layers_b} layer without crossing into "
                        "tests or UI. Re-shape into vertical slices that "
                        "cross every layer per task."
                    ),
                    task_index=i,
                    task_title=a.title,
                )
            )

    return violations


def format_violations_for_reprompt(violations: list[ShapeViolation]) -> str:
    """Render *violations* into a re-prompt body for the LLM.

    Only ``severity="error"`` violations drive the re-prompt; warnings
    are skipped here because we don't want to over-correct the LLM.

    Args:
        violations: The list returned by :func:`check_plan`.

    Returns:
        Multi-line string suitable to inline into a follow-up prompt.
    """
    errors = [v for v in violations if v.severity == "error"]
    if not errors:
        return ""
    lines = [
        "Your previous plan was too coarse. Specifically:",
        "",
    ]
    lines.extend(f"- {v.message}" for v in errors)
    lines.extend(
        [
            "",
            "Re-emit the plan as vertical slices. Each slice must:",
            "  - Stay under 400 LOC (ideal 200 LOC).",
            "  - Touch ≤10 files and ≤2 modules.",
            "  - Cross at least two layers (e.g. routes + tests, or "
            "domain + UI). One user-visible behaviour per slice.",
            "Output ONLY the corrected JSON array.",
        ]
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------


def load_shape_config(workdir: Path | None) -> ShapeConfig:
    """Load shape thresholds from ``bernstein.yaml`` ``[plan]`` table.

    Missing file, missing table, or parse errors fall back to defaults
    silently — the planner stays opinionated by default but never blows
    up on a malformed repo.

    Args:
        workdir: Project root.  ``None`` returns defaults.

    Returns:
        Validated :class:`ShapeConfig` instance.
    """
    if workdir is None:
        return ShapeConfig()
    path = workdir / "bernstein.yaml"
    if not path.is_file():
        return ShapeConfig()
    try:
        import yaml

        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Failed to parse bernstein.yaml for [plan] overrides: %s", exc)
        return ShapeConfig()

    block = raw.get("plan") if isinstance(raw, dict) else None
    if not isinstance(block, dict):
        return ShapeConfig()

    def _int(key: str, default: int) -> int:
        value = block.get(key, default)
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _bool(key: str, default: bool) -> bool:
        value = block.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return default

    return ShapeConfig(
        enforce_vertical=_bool("enforce_vertical", True),
        max_loc_hard=_int("max_loc", DEFAULT_MAX_LOC_HARD),
        max_loc_ideal=_int("max_loc_ideal", DEFAULT_MAX_LOC_IDEAL),
        max_files=_int("max_files", DEFAULT_MAX_FILES),
        max_modules=_int("max_modules", DEFAULT_MAX_MODULES),
    )


__all__ = [
    "DEFAULT_MAX_FILES",
    "DEFAULT_MAX_LOC_HARD",
    "DEFAULT_MAX_LOC_IDEAL",
    "DEFAULT_MAX_MODULES",
    "ShapeConfig",
    "ShapeViolation",
    "check_plan",
    "format_violations_for_reprompt",
    "load_shape_config",
    "summarise_slice",
]
