"""Event-to-task mapper: converts GitHub webhook events into Bernstein task dicts.

Each function accepts a :class:`~bernstein.github_app.webhooks.WebhookEvent`
and returns one or more task payload dicts suitable for ``POST /tasks``.
The returned dicts intentionally use only primitive types so callers can
serialise them directly to JSON without extra conversion steps.

Role assignment heuristics
---------------------------
Issues are mapped to roles based on label prefixes:

- ``bug`` / ``fix`` labels  ->  ``"backend"``
- ``security`` labels       ->  ``"security"``
- ``docs`` labels           ->  ``"docs"``
- ``test`` labels           ->  ``"qa"``
- everything else           ->  ``"backend"`` (safe default)

Complexity heuristics
---------------------
- issue body > 500 chars or body contains a code block  ->  ``"high"``
- issue body 100-500 chars                              ->  ``"medium"``
- short body / no body                                  ->  ``"low"``
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bernstein.github_app.webhooks import WebhookEvent

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_SECURITY_LABELS = frozenset({"security", "vulnerability", "cve", "security-bug"})
_BUG_LABELS = frozenset({"bug", "fix", "regression", "broken"})
_DOCS_LABELS = frozenset({"documentation", "docs", "doc"})
_QA_LABELS = frozenset({"test", "tests", "testing", "qa"})


def _extract_labels(issue_or_pr: dict[str, Any]) -> list[str]:
    """Return the list of label name strings from an issue/PR payload."""
    raw: list[dict[str, Any]] = issue_or_pr.get("labels") or []
    return [lbl.get("name", "") for lbl in raw if isinstance(lbl, dict)]


def _role_from_labels(labels: list[str]) -> str:
    """Determine the most appropriate Bernstein role from GitHub label names."""
    lower = {lbl.lower() for lbl in labels}
    if lower & _SECURITY_LABELS:
        return "security"
    if lower & _DOCS_LABELS:
        return "docs"
    if lower & _QA_LABELS:
        return "qa"
    # bug / fix labels and the default both land on backend
    return "backend"


def _complexity_from_body(body: str | None) -> str:
    """Estimate task complexity from the issue/PR body text."""
    if not body:
        return "low"
    if len(body) > 500 or "```" in body:
        return "high"
    if len(body) >= 100:
        return "medium"
    return "low"


def _priority_from_labels(labels: list[str]) -> int:
    """Map labels to Bernstein priority (1=critical, 2=normal, 3=nice-to-have)."""
    lower = {lbl.lower() for lbl in labels}
    if lower & {"critical", "blocker", "p0", "priority: critical"}:
        return 1
    if lower & {"low-priority", "nice-to-have", "wontfix", "p3"}:
        return 3
    return 2


# ---------------------------------------------------------------------------
# Public mappers
# ---------------------------------------------------------------------------


def issue_to_tasks(event: WebhookEvent) -> list[dict[str, Any]]:
    """Convert a new GitHub issue into Bernstein task payloads.

    Only ``action == "opened"`` produces tasks; all other actions return an
    empty list so that edits and closures are silently ignored.

    Args:
        event: A parsed webhook event with ``event_type == "issues"``.

    Returns:
        A list of task payload dicts (zero or one element).  Each dict is
        ready to ``POST`` to ``/tasks``.
    """
    if event.action != "opened":
        return []

    issue: dict[str, Any] = event.payload.get("issue") or {}
    title: str = issue.get("title") or "(untitled issue)"
    body: str | None = issue.get("body")
    issue_number: int = issue.get("number") or 0
    html_url: str = issue.get("html_url") or ""
    labels = _extract_labels(issue)

    description_parts = [
        f"GitHub issue #{issue_number} opened in {event.repo}.",
        f"URL: {html_url}" if html_url else "",
        "",
        body or "(no description provided)",
    ]
    description = "\n".join(p for p in description_parts if p or p == "")

    return [
        {
            "title": f"[GH#{issue_number}] {title}",
            "description": description.strip(),
            "role": _role_from_labels(labels),
            "priority": _priority_from_labels(labels),
            "complexity": _complexity_from_body(body),
            "scope": "small",
            "estimated_minutes": 30,
            "task_type": "standard",
            "owned_files": [],
        }
    ]


def pr_comment_to_task(event: WebhookEvent) -> dict[str, Any] | None:
    """Convert a PR review comment into a fix task if actionable.

    Only ``action == "created"`` on ``"pull_request_review_comment"`` or
    ``"issue_comment"`` on a PR produces a task.  Comments that look like
    review approvals (containing ``"LGTM"``, ``"Approved"``, ``"+1"``) are
    skipped.

    Args:
        event: A parsed webhook event.  Handles both
            ``"pull_request_review_comment"`` and ``"issue_comment"``
            event types when issued on a pull request.

    Returns:
        A single task payload dict, or ``None`` if the comment is not
        actionable.
    """
    if event.action != "created":
        return None

    # Normalise: both event types carry a comment body under "comment"
    comment: dict[str, Any] = event.payload.get("comment") or {}
    body: str = comment.get("body") or ""

    # Skip approval / non-actionable comments
    skip_phrases = ("lgtm", "approved", "+1", "looks good", ":+1:", "\U0001f44d")
    if any(phrase in body.lower() for phrase in skip_phrases):
        return None

    # Determine PR number — may be in "pull_request" or "issue" sub-object
    pr_obj: dict[str, Any] = event.payload.get("pull_request") or {}
    issue_obj: dict[str, Any] = event.payload.get("issue") or {}
    pr_number: int = pr_obj.get("number") or issue_obj.get("number") or 0

    # For issue_comment events on issues (not PRs), skip
    if event.event_type == "issue_comment" and not pr_obj:
        # Check via issue.pull_request field
        if not issue_obj.get("pull_request"):
            return None
        pr_number = issue_obj.get("number") or 0

    html_url: str = comment.get("html_url") or ""
    commenter: str = (comment.get("user") or {}).get("login") or "unknown"

    description_parts = [
        f"Review comment on PR #{pr_number} in {event.repo} by @{commenter}.",
        f"URL: {html_url}" if html_url else "",
        "",
        body,
    ]
    description = "\n".join(p for p in description_parts if p or p == "")

    return {
        "title": f"[PR#{pr_number}] Address review comment",
        "description": description.strip(),
        "role": "backend",
        "priority": 2,
        "complexity": _complexity_from_body(body),
        "scope": "small",
        "estimated_minutes": 20,
        "task_type": "fix",
        "owned_files": [],
    }


def push_to_tasks(event: WebhookEvent) -> list[dict[str, Any]]:
    """Convert a push event into CI / review tasks.

    Creates a single review task for non-merge commits pushed to the default
    branch (``refs/heads/main`` or ``refs/heads/master``).  Force-pushes and
    empty pushes are silently ignored.

    Args:
        event: A parsed webhook event with ``event_type == "push"``.

    Returns:
        A list of task payload dicts (zero or one element).
    """
    ref: str = event.payload.get("ref") or ""
    # Only act on pushes to main/master
    if ref not in ("refs/heads/main", "refs/heads/master"):
        return []

    commits: list[dict[str, Any]] = event.payload.get("commits") or []
    if not commits:
        return []

    after_sha: str = event.payload.get("after") or ""
    before_sha: str = event.payload.get("before") or ""

    # Skip force pushes where before is the zero SHA
    zero_sha = "0000000000000000000000000000000000000000"
    if before_sha == zero_sha:
        return []

    commit_count = len(commits)
    branch = ref.split("/")[-1]
    compare_url: str = event.payload.get("compare") or ""
    pusher: str = (event.payload.get("pusher") or {}).get("name") or "unknown"

    short_after = after_sha[:8] if after_sha else "unknown"
    commit_word = "commit" if commit_count == 1 else "commits"
    description_parts = [
        f"{commit_count} {commit_word} pushed to {branch} in {event.repo} by {pusher}.",
        f"HEAD: {short_after}",
        f"Compare: {compare_url}" if compare_url else "",
        "",
        "Commit messages:",
    ]
    for commit in commits[:10]:  # cap at 10 to keep description readable
        msg_line = (commit.get("message") or "").splitlines()[0]
        sha_short = (commit.get("id") or "")[:8]
        description_parts.append(f"  - {sha_short}: {msg_line}")

    description = "\n".join(p for p in description_parts if p is not None)

    return [
        {
            "title": f"[CI] Review {commit_count} {commit_word} on {branch} ({short_after})",
            "description": description.strip(),
            "role": "qa",
            "priority": 2,
            "complexity": "medium" if commit_count > 3 else "low",
            "scope": "small",
            "estimated_minutes": 15,
            "task_type": "standard",
            "owned_files": [],
        }
    ]
