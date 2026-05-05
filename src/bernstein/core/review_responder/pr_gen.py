"""PR-body assembly for bernstein-authored pull requests.

Wires the abstract-diff layer (intent summaries + pseudocode) into the
markdown body posted on PR creation. The raw description supplied by
the spawning task is preserved at the top; the abstracted "Intent"
section follows, with collapsible ``<details>`` blocks that hold the
raw diff for drill-down.

This module is intentionally thin — the heavy lifting lives in
``bernstein.core.quality.review_pipeline.abstract_diff``. The split is
about layering: the responder owns *what shows up on the PR*, the
review pipeline owns *how the abstraction is computed*.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from bernstein.core.quality.review_pipeline.abstract_diff import (
    IntentSummary,
    TaskContext,
    render_pr_body,
    summarize_diff,
)

logger = logging.getLogger(__name__)


LLMCaller = Callable[..., Awaitable[str]]


async def build_pr_body(
    *,
    description: str,
    diff: str,
    task_context: TaskContext,
    raw_diff_link: str = "",
    llm_caller: LLMCaller | None = None,
    enabled: bool | None = None,
    max_files: int | None = None,
) -> str:
    """Compose the markdown body for a PR opened by Bernstein.

    Calls :func:`summarize_diff` to produce per-file intent bullets and
    pseudocode, then concatenates the supplied ``description`` with the
    rendered "Intent" section.  Falls back to ``description`` alone when
    the abstraction layer is disabled or returns an empty result.

    Args:
        description: Free-form PR description (carried over from the
            spawning task).  Always rendered first.
        diff: Unified diff of the PR changes.  Truncation is handled by
            the summariser.
        task_context: Title / writer-model context for the summariser.
        raw_diff_link: GitHub URL for the raw diff; embedded as a
            drill-down link per file when no inline diff is present.
        llm_caller: Optional override for tests.
        enabled: Override the global ``ABSTRACT_DIFF_ENABLED`` toggle.
        max_files: Override the global ``ABSTRACT_DIFF_MAX_FILES`` cap.
    """
    summaries = await summarize_diff(
        diff,
        task_context,
        llm_caller=llm_caller,
        raw_diff_link=raw_diff_link,
        enabled=enabled,
        max_files=max_files,
    )
    body = description.rstrip()
    intent = render_pr_body(summaries, raw_diff=diff)
    if not intent:
        return body + ("\n" if body and not body.endswith("\n") else "")
    sep = "\n\n" if body else ""
    return f"{body}{sep}{intent}"


__all__ = [
    "IntentSummary",
    "TaskContext",
    "build_pr_body",
]
