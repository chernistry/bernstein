"""Documentation gap hints for ``bernstein doctor --suggest-docs``.

Operators have no in-CLI signal for which documentation pages would
help them most. This module loads a small operator-curated JSON
file shipped in the wheel and surfaces the top-N entries on demand.

The file lives at
``src/bernstein/_default_templates/docs/_unanswered.json`` and is
refreshed by the maintainer on each release.

Each entry is an object with the following keys:

- ``topic``: free-form short string describing the gap
- ``related_command``: the closest existing CLI command, for navigation
- ``doc_page_proposed``: a proposed repo-relative path for the new page
- ``source``: short tag for provenance, e.g. ``operator-curated-YYYY-MM-DD``
- ``count``: integer weight used for ordering (higher = surfaced first)

The loader is intentionally tolerant: missing file, empty list,
malformed JSON, or invalid entries all degrade to an empty result
so the doctor flag never crashes the diagnostic surface.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

    from rich.console import Console


# Default number of entries surfaced when the operator runs the flag
# without a custom limit.
DEFAULT_TOP_N = 5


@dataclass(frozen=True)
class UnansweredTopic:
    """A single curated documentation gap entry."""

    topic: str
    related_command: str
    doc_page_proposed: str
    source: str
    count: int


def _packaged_path() -> Path:
    """Return the path to the packaged ``_unanswered.json`` file.

    Dev layout: ``<repo>/src/bernstein/cli/doctor/suggest_docs.py`` ->
    ``<repo>/src/bernstein/_default_templates/docs/_unanswered.json``.

    Wheel layout: the file is shipped via the ``artifacts`` rule in
    ``pyproject.toml`` and resolves to the same relative location.
    """
    here = Path(__file__).resolve()
    # cli/doctor/suggest_docs.py -> cli/doctor -> cli -> bernstein
    package_root = here.parent.parent.parent
    return package_root / "_default_templates" / "docs" / "_unanswered.json"


def _coerce_entry(raw: Any) -> UnansweredTopic | None:
    """Convert a single JSON object into ``UnansweredTopic`` or ``None``.

    Returns ``None`` for entries that are not objects, are missing
    required keys, or carry a non-integer ``count``.
    """
    if not isinstance(raw, dict):
        return None
    try:
        topic = str(raw["topic"])
        related_command = str(raw["related_command"])
        doc_page_proposed = str(raw["doc_page_proposed"])
        source = str(raw["source"])
        count_raw = raw["count"]
    except KeyError:
        return None
    if isinstance(count_raw, bool) or not isinstance(count_raw, int):
        return None
    if not topic.strip() or not related_command.strip() or not doc_page_proposed.strip():
        return None
    return UnansweredTopic(
        topic=topic,
        related_command=related_command,
        doc_page_proposed=doc_page_proposed,
        source=source,
        count=count_raw,
    )


def load_unanswered_topics(path: Path | None = None) -> list[UnansweredTopic]:
    """Load and validate the curated unanswered-topics file.

    Missing file, malformed JSON, non-list root, or invalid entries
    are all treated as an empty list. Valid entries are returned in
    file order; sorting happens at render time so callers can apply
    custom ordering.
    """
    target = path or _packaged_path()
    if not target.exists():
        return []
    try:
        raw_text = target.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    entries: list[UnansweredTopic] = []
    for raw in data:
        entry = _coerce_entry(raw)
        if entry is not None:
            entries.append(entry)
    return entries


def top_n_topics(
    topics: Iterable[UnansweredTopic],
    n: int = DEFAULT_TOP_N,
) -> list[UnansweredTopic]:
    """Return the top ``n`` topics sorted by ``count`` descending.

    Stable on equal counts to keep the rendered order deterministic
    across runs. ``n`` is clamped to ``[0, len(topics)]``.
    """
    if n <= 0:
        return []
    sorted_topics = sorted(topics, key=lambda t: t.count, reverse=True)
    return sorted_topics[:n]


def format_topic_line(topic: UnansweredTopic) -> str:
    """Render a single topic as the operator-facing one-liner.

    Schema: ``-> <topic> (related: <command>). proposed page: <path>``.
    Uses a plain ASCII arrow so the line renders cleanly on every
    terminal regardless of glyph support.
    """
    return f"-> {topic.topic} (related: {topic.related_command}). proposed page: {topic.doc_page_proposed}"


def render_suggestions(
    console: Console,
    topics: list[UnansweredTopic],
    *,
    limit: int = DEFAULT_TOP_N,
) -> None:
    """Print the curated suggestions to the supplied Rich console.

    Falls back to a friendly "no gaps recorded" line when the file
    is empty or malformed, so the flag is still useful as a sanity
    check on a fresh install.
    """
    top = top_n_topics(topics, n=limit)
    if not top:
        console.print(
            "[dim]No documentation gaps recorded. The curated list is "
            "empty or unreadable; this is expected on a fresh install "
            "between maintainer refreshes.[/dim]"
        )
        return
    console.print("[bold]Top documentation gaps[/bold]")
    for topic in top:
        console.print(format_topic_line(topic))


def hint_line() -> str:
    """Return the trailing hint shown after a default doctor run.

    Kept as a single short line so it does not clutter the existing
    status table footer.
    """
    return "Run `bernstein doctor --suggest-docs` to see the top documentation gaps."
