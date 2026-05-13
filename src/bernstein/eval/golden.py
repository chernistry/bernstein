"""Golden benchmark suite — curated tasks for eval.

Tasks are stored as markdown files with YAML frontmatter.  The loader
checks two sources in order:

1. **Operator overrides** under ``.sdd/eval/golden/<tier>/*.md`` (or any
   explicit ``golden_dir`` passed in).  This is the historical layout
   and stays authoritative when the directory exists and is non-empty.
2. **Packaged defaults** shipped inside the wheel at
   ``bernstein.eval.golden_data.<tier>``.  Discovered via
   :mod:`importlib.resources` so a fresh ``pip install bernstein``
   exposes a working smoke tier without requiring the operator to seed
   ``.sdd/`` (which is gitignored by repo hygiene CI).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import yaml

if TYPE_CHECKING:
    from importlib.resources.abc import Traversable

logger = logging.getLogger(__name__)

Tier = Literal["smoke", "standard", "stretch", "adversarial"]

_TIERS: tuple[Tier, ...] = ("smoke", "standard", "stretch", "adversarial")

_PACKAGED_ROOT = "bernstein.eval.golden_data"


@dataclass(frozen=True)
class GoldenTask:
    """A golden benchmark task loaded from disk.

    Attributes:
        id: Unique task identifier.
        tier: Difficulty tier.
        title: Short task title.
        description: Full task description for the agent.
        role: Agent role to assign.
        expected_files_modified: Files the agent should modify.
        expected_test_outcomes: Test commands and expected pass/fail.
        completion_signals: Signals for verifying task completion.
        max_cost_usd: Cost budget for this task.
        max_duration_s: Time budget in seconds.
        owned_files: Files the agent is allowed to modify.
    """

    id: str
    tier: Tier
    title: str
    description: str
    role: str = "backend"
    expected_files_modified: list[str] = field(default_factory=list[str])
    expected_test_outcomes: dict[str, bool] = field(default_factory=dict[str, bool])
    completion_signals: list[str] = field(default_factory=list[str])
    max_cost_usd: float = 1.0
    max_duration_s: int = 300
    owned_files: list[str] = field(default_factory=list[str])


def _parse_golden_text(text: str, tier: Tier, source: str) -> GoldenTask | None:
    """Parse golden task markdown text into a :class:`GoldenTask`.

    Args:
        text: Raw markdown text (YAML frontmatter + body).
        tier: Tier this task belongs to.
        source: Human-readable origin string used in log lines and as a
            stem fallback for ``id`` / ``title``.

    Returns:
        Parsed GoldenTask, or None if parsing fails.
    """
    if not text.startswith("---"):
        logger.warning("Golden task missing YAML frontmatter: %s", source)
        return None

    parts = text.split("---", 2)
    if len(parts) < 3:
        logger.warning("Golden task has malformed frontmatter: %s", source)
        return None

    try:
        meta = yaml.safe_load(parts[1])
    except yaml.YAMLError as exc:
        logger.warning("YAML parse error in %s: %s", source, exc)
        return None

    if not isinstance(meta, dict):
        logger.warning("Golden task frontmatter is not a dict: %s", source)
        return None

    m: dict[str, Any] = dict(cast("dict[str, Any]", meta))
    body = parts[2].strip()
    # Stem from the source path/name (drop ``.md`` if present).
    stem = source.rsplit("/", 1)[-1].removesuffix(".md")
    task_id: str = str(m.get("id", stem))

    return GoldenTask(
        id=task_id,
        tier=tier,
        title=str(m.get("title", stem)),
        description=body or str(m.get("description", "")),
        role=str(m.get("role", "backend")),
        expected_files_modified=[str(x) for x in m.get("expected_files_modified", [])],
        expected_test_outcomes={str(k): bool(v) for k, v in dict(m.get("expected_test_outcomes", {})).items()},
        completion_signals=[str(x) for x in m.get("completion_signals", [])],
        max_cost_usd=float(m.get("max_cost_usd", 1.0)),
        max_duration_s=int(m.get("max_duration_s", 300)),
        owned_files=[str(x) for x in m.get("owned_files", [])],
    )


def _parse_golden_file(path: Path, tier: Tier) -> GoldenTask | None:
    """Parse a single golden task markdown file from disk.

    Expected format: YAML frontmatter between --- markers, followed by
    the task description in markdown body.

    Args:
        path: Path to the markdown file.
        tier: The tier directory this task belongs to.

    Returns:
        Parsed GoldenTask, or None if parsing fails.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        logger.warning("Cannot read golden task file: %s", path)
        return None
    return _parse_golden_text(text, tier, str(path))


def _packaged_tier_files(tier: Tier) -> list[Traversable]:
    """Return packaged ``*.md`` fixtures for ``tier`` shipped in the wheel.

    Returns an empty list if the tier sub-package is missing (e.g. only
    ``smoke`` ships with defaults) or if the resource cannot be opened.
    """
    try:
        tier_root = resources.files(_PACKAGED_ROOT).joinpath(tier)
    except (ModuleNotFoundError, FileNotFoundError):
        return []
    if not tier_root.is_dir():
        return []
    return sorted(
        (entry for entry in tier_root.iterdir() if entry.name.endswith(".md")),
        key=lambda e: e.name,
    )


def _load_packaged_tier(tier: Tier) -> list[GoldenTask]:
    """Parse and return all packaged fixtures for ``tier``."""
    tasks: list[GoldenTask] = []
    for entry in _packaged_tier_files(tier):
        try:
            text = entry.read_text(encoding="utf-8")
        except OSError:
            logger.warning("Cannot read packaged golden task: %s", entry)
            continue
        task = _parse_golden_text(text, tier, f"{_PACKAGED_ROOT}.{tier}/{entry.name}")
        if task is not None:
            tasks.append(task)
    return tasks


def load_golden_tasks(
    golden_dir: Path | None = None,
    tier_filter: Tier | None = None,
) -> list[GoldenTask]:
    """Load all golden benchmark tasks.

    For each tier, operator overrides under ``<golden_dir>/<tier>/*.md``
    win when the directory exists and contains at least one ``*.md``
    file.  Otherwise, fall back to fixtures packaged in the wheel under
    ``bernstein.eval.golden_data.<tier>`` so a fresh install can still
    run the smoke suite.

    Args:
        golden_dir: Root directory containing tier subdirectories.
            Defaults to ``.sdd/eval/golden/`` in the current directory.
        tier_filter: If set, only load tasks from this tier.

    Returns:
        List of parsed GoldenTask objects, ordered by tier then by
        on-disk sort.
    """
    if golden_dir is None:
        golden_dir = Path(".sdd/eval/golden")

    tasks: list[GoldenTask] = []
    tiers_to_scan = (tier_filter,) if tier_filter else _TIERS

    for tier in tiers_to_scan:
        tier_dir = golden_dir / tier
        on_disk: list[GoldenTask] = []
        if tier_dir.is_dir():
            for md_file in sorted(tier_dir.glob("*.md")):
                task = _parse_golden_file(md_file, tier)
                if task is not None:
                    on_disk.append(task)
        if on_disk:
            tasks.extend(on_disk)
            continue
        # Fallback to packaged fixtures (wheel-shipped defaults).
        packaged = _load_packaged_tier(tier)
        if packaged:
            logger.debug("Using %d packaged golden tasks for tier=%s", len(packaged), tier)
            tasks.extend(packaged)
        else:
            logger.debug("No golden tasks for tier=%s (checked %s and packaged)", tier, tier_dir)

    logger.info("Loaded %d golden tasks (override_root=%s)", len(tasks), golden_dir)
    return tasks


def load_single_task(golden_dir: Path, task_id: str) -> GoldenTask | None:
    """Load a single golden task by ID.

    Searches operator overrides first, then packaged defaults.  Returns
    the first match across all tier directories.

    Args:
        golden_dir: Root golden directory.
        task_id: Task ID to find.

    Returns:
        The matching GoldenTask, or None if not found.
    """
    for tier in _TIERS:
        tier_dir = golden_dir / tier
        if tier_dir.is_dir():
            for md_file in tier_dir.glob("*.md"):
                task = _parse_golden_file(md_file, tier)
                if task is not None and task.id == task_id:
                    return task
        for task in _load_packaged_tier(tier):
            if task.id == task_id:
                return task
    return None
