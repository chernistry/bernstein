"""GitLab Commit Statuses API client — pipeline status updates.

Posts and updates "external CI" commit statuses on the SHA backing an
MR so Bernstein agent verification appears as a native pipeline check
in the GitLab UI.

GitLab API: ``POST /projects/:id/statuses/:sha``.  Reference:
https://docs.gitlab.com/ee/api/commits.html#post-the-build-status-to-a-commit

All operations degrade gracefully when:

* The HTTP client (``httpx``) is not installed.
* The API call returns non-2xx.

Both conditions return ``None`` instead of raising.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from bernstein.gitlab_app.app import build_api_url, build_auth_headers

logger = logging.getLogger(__name__)

# Name shown for the Bernstein external pipeline status on GitLab.
PIPELINE_STATUS_NAME = "bernstein / agent verification"

# Valid GitLab commit status states.
_VALID_STATES: frozenset[str] = frozenset({"pending", "running", "success", "failed", "canceled"})

# Bernstein conclusion → GitLab state mapping.
_CONCLUSION_TO_STATE: dict[str, str] = {
    "success": "success",
    "failure": "failed",
    "neutral": "success",
    "cancelled": "canceled",
    "timed_out": "failed",
    "action_required": "failed",
}


@dataclass
class PipelineStatusResult:
    """Result of a commit-status create/update operation.

    Attributes:
        status_id: GitLab commit-status ID.
        state: Reported state (``pending``, ``running``, ``success`` …).
        target_url: URL pointing to the Bernstein details page.
    """

    status_id: int
    state: str
    target_url: str


def conclusion_to_state(conclusion: str) -> str:
    """Map a Bernstein conclusion string to a GitLab commit-state.

    Unknown conclusions fall back to ``"failed"`` (fail-closed).
    """
    return _CONCLUSION_TO_STATE.get(conclusion, "failed")


def build_status_body(
    state: str,
    description: str,
    target_url: str = "",
    ref: str = "",
    name: str = PIPELINE_STATUS_NAME,
) -> dict[str, Any]:
    """Build the JSON body for a commit-status POST.

    Args:
        state: GitLab state (``pending``, ``running``, ``success`` …).
        description: Short, human-readable status text.
        target_url: Optional URL pointing back to Bernstein.
        ref: Optional branch / tag name; if set GitLab posts the status
            against this ref.
        name: Display name in the GitLab UI.

    Returns:
        Dict suitable for JSON-serialisation as the request body.
    """
    body: dict[str, Any] = {
        "state": state,
        "description": description[:140] if description else "",
        "name": name,
    }
    if target_url:
        body["target_url"] = target_url
    if ref:
        body["ref"] = ref
    return body


class PipelineStatusClient:
    """Thin client around the GitLab commit-status API.

    Args:
        project_id: Numeric project ID or URL-encoded path slug.
        token: PAT / project access token.  When empty all calls are
            no-ops (returns ``None``).
        base_url: GitLab base URL.  ``None`` defers to env at call time.
    """

    def __init__(
        self,
        project_id: str | int,
        token: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self._project_id = project_id
        self._token = token or ""
        self._base_url = base_url

    @property
    def configured(self) -> bool:
        """True when there's a non-empty project ID + token."""
        return bool(self._project_id) and bool(self._token)

    def create(
        self,
        sha: str,
        state: str = "running",
        description: str = "Agent verification in progress",
        target_url: str = "",
        ref: str = "",
    ) -> PipelineStatusResult | None:
        """Post an initial status (running / pending) on *sha*.

        Args:
            sha: Commit SHA the status should attach to.
            state: GitLab state — defaults to ``running``.
            description: Short status text.
            target_url: Bernstein details URL.
            ref: Optional branch/tag for the status.

        Returns:
            :class:`PipelineStatusResult` on success, ``None`` otherwise.
        """
        if not self.configured:
            logger.debug("PipelineStatusClient not configured — skipping create")
            return None
        if state not in _VALID_STATES:
            state = "running"
        body = build_status_body(state, description, target_url, ref)
        return self._post(sha, body)

    def update(
        self,
        sha: str,
        conclusion: str,
        summary: str,
        target_url: str = "",
        ref: str = "",
    ) -> PipelineStatusResult | None:
        """Post a terminal status (success / failed / canceled) on *sha*.

        Args:
            sha: Commit SHA.
            conclusion: Bernstein conclusion (mapped to GitLab state).
            summary: Markdown summary — truncated to 140 chars.
            target_url: Bernstein details URL.
            ref: Optional branch/tag.

        Returns:
            :class:`PipelineStatusResult` on success, ``None`` otherwise.
        """
        if not self.configured:
            logger.debug("PipelineStatusClient not configured — skipping update")
            return None
        state = conclusion_to_state(conclusion)
        body = build_status_body(state, summary, target_url, ref)
        return self._post(sha, body)

    def _post(self, sha: str, body: dict[str, Any]) -> PipelineStatusResult | None:
        """POST the body and parse the result, returning ``None`` on error."""
        try:
            import httpx
        except ImportError:
            logger.debug("httpx not available — cannot post GitLab status")
            return None

        url = build_api_url(f"/projects/{self._project_id}/statuses/{sha}", self._base_url)
        try:
            response: Any = httpx.post(
                url,
                headers={**build_auth_headers(self._token), "Content-Type": "application/json"},
                json=body,
                timeout=30.0,
            )
        except Exception as exc:  # pragma: no cover - depends on env
            logger.debug("commit-status POST failed: %s", exc)
            return None

        if response.status_code not in {200, 201}:
            logger.warning(
                "commit-status POST returned HTTP %d for sha %s",
                response.status_code,
                sha[:12],
            )
            return None

        try:
            data: dict[str, Any] = response.json()
        except Exception:  # pragma: no cover - defensive
            return None

        return PipelineStatusResult(
            status_id=int(data.get("id", 0)),
            state=str(data.get("status", body["state"])),
            target_url=str(data.get("target_url", body.get("target_url", ""))),
        )
