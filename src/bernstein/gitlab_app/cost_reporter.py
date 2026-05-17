"""MR cost annotation: post agent run cost summaries as GitLab MR comments.

Mirror of :mod:`bernstein.github_app.cost_reporter`.  Uses the GitLab
Notes API (``POST /projects/:id/merge_requests/:iid/notes``) and the
update endpoint (``PUT .../notes/:note_id``) so a single Bernstein cost
comment is kept up to date instead of stacking duplicates.

All operations degrade gracefully when ``httpx`` is not installed or
when the API returns non-2xx — the helpers simply return ``False``.
"""

from __future__ import annotations

import logging
from typing import Any

from bernstein.gitlab_app.app import build_api_url, build_auth_headers

logger = logging.getLogger(__name__)

# Marker so we can find and update an existing Bernstein note on the MR.
COST_NOTE_MARKER = "<!-- bernstein-cost-annotation -->"

_COST_NOTE_TEMPLATE = f"""\
{COST_NOTE_MARKER}
> 🤖 **Bernstein agent run cost**
> Tasks completed: {{task_count}} | Total cost: **${{cost_usd:.4f}}** | Model: {{model}}
"""


def build_cost_summary(cost_usd: float, task_count: int, model: str) -> str:
    """Build the markdown cost annotation string without posting it.

    Args:
        cost_usd: Total cost in USD.
        task_count: Number of completed tasks.
        model: Model identifier shown in the annotation.

    Returns:
        Formatted markdown string.
    """
    return _COST_NOTE_TEMPLATE.format(
        task_count=task_count,
        cost_usd=cost_usd,
        model=model,
    )


def aggregate_mr_cost(task_costs: list[dict[str, Any]]) -> float:
    """Sum the ``cost_usd`` field across task cost dicts.

    Missing keys count as zero.
    """
    return sum(float(t.get("cost_usd", 0.0)) for t in task_costs)


def post_mr_cost_comment(
    project_id: str | int,
    mr_iid: int,
    cost_usd: float,
    task_count: int = 1,
    model: str = "claude-sonnet-4-6",
    token: str = "",
    base_url: str | None = None,
) -> bool:
    """Post (or update in-place) a cost annotation note on a GitLab MR.

    Args:
        project_id: GitLab project ID or URL-encoded slug.
        mr_iid: MR internal ID (the user-visible ``!42``).
        cost_usd: Total agent run cost in USD.
        task_count: Number of completed tasks.
        model: Model identifier.
        token: API token (``PRIVATE-TOKEN`` header).
        base_url: GitLab base URL override.

    Returns:
        ``True`` on success, ``False`` when not configured or HTTP fails.
    """
    if not token or not project_id or not mr_iid:
        logger.debug("post_mr_cost_comment: missing token/project/iid — skip")
        return False

    body = build_cost_summary(cost_usd, task_count, model)

    existing_id = _find_existing_cost_note(project_id, mr_iid, token, base_url)
    if existing_id is not None:
        return _update_note(project_id, mr_iid, existing_id, body, token, base_url)
    return _create_note(project_id, mr_iid, body, token, base_url)


def _find_existing_cost_note(
    project_id: str | int,
    mr_iid: int,
    token: str,
    base_url: str | None,
) -> int | None:
    """Return the ID of an existing Bernstein cost note, if any."""
    try:
        import httpx
    except ImportError:
        return None

    url = build_api_url(f"/projects/{project_id}/merge_requests/{mr_iid}/notes", base_url)
    try:
        response: Any = httpx.get(
            url,
            headers=build_auth_headers(token),
            params={"per_page": "100"},
            timeout=15.0,
        )
        if response.status_code != 200:
            return None
        notes = response.json()
    except Exception as exc:  # pragma: no cover - depends on env
        logger.debug("list-notes failed: %s", exc)
        return None

    if not isinstance(notes, list):
        return None
    notes_typed: list[Any] = notes  # type: ignore[assignment]
    for note_item in notes_typed:
        if not isinstance(note_item, dict):
            continue
        note_typed: dict[str, Any] = note_item  # type: ignore[assignment]
        body_val: Any = note_typed.get("body", "")
        if COST_NOTE_MARKER in str(body_val):
            note_id: Any = note_typed.get("id")
            if isinstance(note_id, int):
                return note_id
    return None


def _create_note(
    project_id: str | int,
    mr_iid: int,
    body: str,
    token: str,
    base_url: str | None,
) -> bool:
    """POST a new MR note."""
    try:
        import httpx
    except ImportError:
        return False

    url = build_api_url(f"/projects/{project_id}/merge_requests/{mr_iid}/notes", base_url)
    try:
        response: Any = httpx.post(
            url,
            headers={**build_auth_headers(token), "Content-Type": "application/json"},
            json={"body": body},
            timeout=30.0,
        )
    except Exception as exc:  # pragma: no cover - depends on env
        logger.debug("create-note failed: %s", exc)
        return False
    if response.status_code not in {200, 201}:
        logger.warning("create-note returned HTTP %d", response.status_code)
        return False
    return True


def _update_note(
    project_id: str | int,
    mr_iid: int,
    note_id: int,
    body: str,
    token: str,
    base_url: str | None,
) -> bool:
    """PUT to update an existing MR note in-place."""
    try:
        import httpx
    except ImportError:
        return False

    url = build_api_url(
        f"/projects/{project_id}/merge_requests/{mr_iid}/notes/{note_id}",
        base_url,
    )
    try:
        response: Any = httpx.put(
            url,
            headers={**build_auth_headers(token), "Content-Type": "application/json"},
            json={"body": body},
            timeout=30.0,
        )
    except Exception as exc:  # pragma: no cover - depends on env
        logger.debug("update-note failed: %s", exc)
        return False
    if response.status_code not in {200, 201}:
        logger.warning("update-note returned HTTP %d", response.status_code)
        return False
    return True
