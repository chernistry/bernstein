"""Periodic synthesis pass over diary entries.

The synthesiser groups recent :class:`bernstein.core.knowledge.diary.DiaryEntry`
records into themes by tag-overlap Jaccard similarity, then drafts a
markdown report plus a per-theme prompt-diff proposal. The output lands
under ``.sdd/runtime/syntheses/<date>.md`` and is HITL-gated: the apply
path requires an explicit ``--apply`` flag and never mutates role
prompts on its own.

Clustering is deliberately stdlib-only. The Jaccard threshold is
configurable; below that, entries form a singleton cluster and are
emitted with a single-entry theme. Themes are sorted by size descending
so the operator sees the strongest signals first.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from bernstein.core.persistence.atomic_write import write_atomic_text

if TYPE_CHECKING:
    from bernstein.core.knowledge.diary import DiaryEntry

logger = logging.getLogger(__name__)


DEFAULT_JACCARD_THRESHOLD = 0.34
DEFAULT_WINDOW_DAYS = 14
DEFAULT_MIN_CLUSTER_SIZE = 1
DEFAULT_SYNTHESIS_SUBPATH = Path(".sdd") / "runtime" / "syntheses"


class SynthesizerError(Exception):
    """Raised on synthesis configuration or storage failures."""


@dataclass(frozen=True)
class Theme:
    """A cluster of diary entries sharing a tag fingerprint.

    ``shared_tags`` is the intersection of tag sets across the cluster.
    ``proposed_diff`` is the markdown body the operator reviews before
    landing changes against role prompts; it is never applied
    automatically.
    """

    theme_id: str
    label: str
    entries: tuple[DiaryEntry, ...]
    shared_tags: tuple[str, ...]
    proposed_diff: str

    @property
    def size(self) -> int:
        """Return the number of diary entries in the cluster."""
        return len(self.entries)


@dataclass(frozen=True)
class SynthesisReport:
    """Top-level synthesis result for a given window.

    ``approved`` defaults to ``False``; the CLI flips it to ``True``
    when the operator runs the ``--apply`` workflow. Persisting an
    approved report writes a marker line so re-runs are idempotent.
    """

    generated_at: str
    window_days: int
    themes: tuple[Theme, ...]
    approved: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def theme_count(self) -> int:
        """Return the total number of themes in this report."""
        return len(self.themes)


# ---------------------------------------------------------------------------
# Clustering helpers
# ---------------------------------------------------------------------------


def jaccard(a: tuple[str, ...] | frozenset[str], b: tuple[str, ...] | frozenset[str]) -> float:
    """Return the Jaccard similarity of two tag sets.

    Empty sets return ``0.0`` because there is nothing to cluster on.
    The implementation is symmetric and bounded in ``[0.0, 1.0]``.
    """
    set_a = frozenset(a)
    set_b = frozenset(b)
    if not set_a and not set_b:
        return 0.0
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def _within_window(entry: DiaryEntry, now: datetime, window: timedelta) -> bool:
    """Return True iff *entry* falls within *window* before *now*.

    Entries with unparseable ``created_at`` are kept; treating them as
    in-window is the conservative choice because dropping them would
    silently shrink the synthesis input.
    """
    try:
        created = datetime.fromisoformat(entry.created_at)
    except ValueError:
        return True
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return now - created <= window


def filter_recent(entries: list[DiaryEntry], window_days: int, *, now: datetime | None = None) -> list[DiaryEntry]:
    """Return only the entries within ``window_days`` of *now*.

    ``window_days <= 0`` short-circuits to the input list unchanged so
    callers can opt out of windowing without a magic sentinel.
    """
    if window_days <= 0:
        return entries.copy()
    moment = now if now is not None else datetime.now(UTC)
    window = timedelta(days=window_days)
    return [e for e in entries if _within_window(e, moment, window)]


def cluster_entries(
    entries: list[DiaryEntry],
    *,
    threshold: float = DEFAULT_JACCARD_THRESHOLD,
    min_cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE,
) -> list[list[DiaryEntry]]:
    """Group entries into clusters by tag-overlap Jaccard similarity.

    The algorithm is a single-linkage merge with a fixed threshold. It
    is stdlib-only and stable (clusters preserve insertion order),
    matching the acceptance-criteria constraint of no embeddings in v1.
    Clusters smaller than ``min_cluster_size`` are dropped so noise
    cannot drown the report.
    """
    if not 0.0 <= threshold <= 1.0:
        raise SynthesizerError(f"threshold out of range: {threshold!r}")
    if min_cluster_size < 1:
        raise SynthesizerError("min_cluster_size must be >= 1")
    clusters: list[list[DiaryEntry]] = []
    for entry in entries:
        placed = False
        for cluster in clusters:
            if any(jaccard(entry.tags, member.tags) >= threshold for member in cluster):
                cluster.append(entry)
                placed = True
                break
        if not placed:
            clusters.append([entry])
    if min_cluster_size > 1:
        clusters = [c for c in clusters if len(c) >= min_cluster_size]
    clusters.sort(key=len, reverse=True)
    return clusters


def _slug(text: str, *, max_len: int = 40) -> str:
    """Return a lower-cased slug suitable for theme ids."""
    cleaned = re.sub(r"[^A-Za-z0-9-]+", "-", text.lower()).strip("-")
    if not cleaned:
        return "untitled"
    return cleaned[:max_len]


def _shared_tags(cluster: list[DiaryEntry]) -> tuple[str, ...]:
    """Return the tag intersection across *cluster*, deterministically."""
    if not cluster:
        return ()
    shared = set(cluster[0].tags)
    for member in cluster[1:]:
        shared &= set(member.tags)
    return tuple(sorted(shared))


def _theme_label(shared: tuple[str, ...], cluster: list[DiaryEntry]) -> str:
    """Pick a human-readable label for a theme.

    Falls back to the first tag of the first entry if the intersection
    is empty (a single-entry cluster). Returns ``"misc"`` only as a
    last resort so the report is never blank.
    """
    if shared:
        return " / ".join(shared[:3])
    for member in cluster:
        if member.tags:
            return member.tags[0]
    return "misc"


def _theme_id(index: int, label: str) -> str:
    """Build a stable theme id from the cluster index and label slug."""
    return f"theme-{index:03d}-{_slug(label)}"


def _build_proposed_diff(label: str, cluster: list[DiaryEntry]) -> str:
    """Render a markdown diff proposal for a theme.

    The body summarises observed failures and successes across the
    cluster. The output is intentionally diff-shaped (``+``/``-``
    prefixes) but is **not** a unified diff; it is a review aid that
    the operator translates into a real edit during ``--apply``.
    """
    lines: list[str] = []
    lines.extend((f"## Proposed adjustment: {label}", ""))
    failed_bullets: list[str] = []
    worked_bullets: list[str] = []
    for entry in cluster:
        failed_bullets.extend(entry.failed)
        worked_bullets.extend(entry.worked)
    if failed_bullets:
        lines.append("Recurring failure patterns (consider guarding against):")
        for bullet in sorted(set(failed_bullets))[:8]:
            lines.append(f"- {bullet}")
        lines.append("")
    if worked_bullets:
        lines.append("Recurring success patterns (consider amplifying):")
        for bullet in sorted(set(worked_bullets))[:8]:
            lines.append(f"+ {bullet}")
        lines.append("")
    if not failed_bullets and not worked_bullets:
        lines.extend(("No actionable tried/worked/failed bullets in this cluster.", "Rationale snippets:"))
        for entry in cluster[:3]:
            if entry.rationale:
                lines.append(f"> {entry.rationale}")
    return "\n".join(lines).rstrip() + "\n"


def build_themes(clusters: list[list[DiaryEntry]]) -> tuple[Theme, ...]:
    """Convert raw cluster lists into :class:`Theme` records."""
    themes: list[Theme] = []
    for idx, cluster in enumerate(clusters):
        shared = _shared_tags(cluster)
        label = _theme_label(shared, cluster)
        theme = Theme(
            theme_id=_theme_id(idx, label),
            label=label,
            entries=tuple(cluster),
            shared_tags=shared,
            proposed_diff=_build_proposed_diff(label, cluster),
        )
        themes.append(theme)
    return tuple(themes)


# ---------------------------------------------------------------------------
# Synthesis entry points
# ---------------------------------------------------------------------------


def synthesize(
    entries: list[DiaryEntry],
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    threshold: float = DEFAULT_JACCARD_THRESHOLD,
    min_cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE,
    now: datetime | None = None,
) -> SynthesisReport:
    """Run the full synthesis pipeline on *entries* and return a report.

    The function is pure (no filesystem side effects). The CLI layer
    is responsible for persisting the report and gating the apply
    workflow.
    """
    recent = filter_recent(entries, window_days, now=now)
    clusters = cluster_entries(recent, threshold=threshold, min_cluster_size=min_cluster_size)
    themes = build_themes(clusters)
    moment = now if now is not None else datetime.now(UTC)
    notes: list[str] = []
    if not entries:
        notes.append("No diary entries available; report is empty.")
    elif not recent:
        notes.append(f"No diary entries within the last {window_days} day(s); report is empty.")
    return SynthesisReport(
        generated_at=moment.isoformat(timespec="seconds"),
        window_days=window_days,
        themes=themes,
        notes=tuple(notes),
    )


def render_report(report: SynthesisReport) -> str:
    """Render *report* as a markdown document.

    The document carries a YAML-like frontmatter so downstream tools
    can grep without parsing markdown.
    """
    lines: list[str] = []
    lines.extend(
        (
            "---",
            f"generated_at: {report.generated_at}",
            f"window_days: {report.window_days}",
            f"theme_count: {report.theme_count}",
            f"approved: {'true' if report.approved else 'false'}",
            "---",
            "",
            "# Diary synthesis report",
            "",
        )
    )
    if report.notes:
        for note in report.notes:
            lines.append(f"> {note}")
        lines.append("")
    if not report.themes:
        lines.extend(("_No themes detected._", ""))
        return "\n".join(lines)
    for theme in report.themes:
        lines.extend((f"## {theme.theme_id}: {theme.label}", "", f"- Cluster size: {theme.size}"))
        if theme.shared_tags:
            lines.append(f"- Shared tags: {', '.join(theme.shared_tags)}")
        lines.extend(
            (
                "- Task ids: " + ", ".join(e.task_id for e in theme.entries),
                "",
                "<details><summary>Proposed adjustment (HITL-gated)</summary>",
                "",
                theme.proposed_diff,
                "",
                "</details>",
                "",
            )
        )
    return "\n".join(lines)


def write_report(report: SynthesisReport, sdd_dir: Path) -> Path:
    """Persist *report* to ``<sdd_dir>/runtime/syntheses/<date>.md``.

    The filename uses the report's ``generated_at`` date so multiple
    runs in a single day overwrite the same file deterministically;
    the synthesiser is idempotent at the day granularity.
    """
    target_dir = sdd_dir / "runtime" / "syntheses"
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        stamp = datetime.fromisoformat(report.generated_at).date().isoformat()
    except ValueError:
        stamp = datetime.now(UTC).date().isoformat()
    target = target_dir / f"{stamp}.md"
    write_atomic_text(target, render_report(report))
    logger.debug("synthesis.write path=%s themes=%d", target, report.theme_count)
    return target


def approve(report: SynthesisReport) -> SynthesisReport:
    """Return a copy of *report* with ``approved=True``.

    The HITL gate lives here: only after the CLI invokes this function
    can a downstream consumer treat the report as ready to apply.
    """
    return SynthesisReport(
        generated_at=report.generated_at,
        window_days=report.window_days,
        themes=report.themes,
        approved=True,
        notes=report.notes,
    )


def parse_duration(value: str) -> timedelta:
    """Parse a short duration string like ``7d``, ``24h``, ``30m``.

    Supports days (``d``), hours (``h``), minutes (``m``), and seconds
    (``s``). Plain integers are interpreted as days for the common
    ``--since 14`` shortcut.
    """
    if not value or not value.strip():
        raise SynthesizerError("duration must be non-empty")
    text = value.strip().lower()
    if text.isdigit():
        return timedelta(days=int(text))
    match = re.fullmatch(r"(\d+)([dhms])", text)
    if not match:
        raise SynthesizerError(f"unrecognised duration: {value!r}")
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "d":
        return timedelta(days=amount)
    if unit == "h":
        return timedelta(hours=amount)
    if unit == "m":
        return timedelta(minutes=amount)
    return timedelta(seconds=amount)


__all__ = [
    "DEFAULT_JACCARD_THRESHOLD",
    "DEFAULT_MIN_CLUSTER_SIZE",
    "DEFAULT_SYNTHESIS_SUBPATH",
    "DEFAULT_WINDOW_DAYS",
    "SynthesisReport",
    "SynthesizerError",
    "Theme",
    "approve",
    "build_themes",
    "cluster_entries",
    "filter_recent",
    "jaccard",
    "parse_duration",
    "render_report",
    "synthesize",
    "write_report",
]
