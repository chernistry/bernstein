"""GitLab App authentication: token-based auth (PAT + project-scoped tokens).

GitLab does not (publicly) ship an Apps + JWT flow comparable to GitHub
Apps, so this module focuses on the two supported auth mechanisms:

* **Personal Access Tokens (PAT)** — read from ``GITLAB_TOKEN`` /
  ``GITLAB_PAT``; used as the global fallback.
* **Project / group access tokens** — passed in explicitly per-call so
  each repo can use a least-privilege token.

The base URL is configurable via ``BERNSTEIN_GITLAB_URL`` to support
self-managed installs (defaults to ``https://gitlab.com``).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# Default public GitLab.com base URL.  Overridable via env for self-managed.
DEFAULT_GITLAB_URL = "https://gitlab.com"

# Allowed URL schemes.  Strictly HTTPS for production; HTTP is permitted
# only for localhost / loopback (so unit tests can spin up a fake server)
# and self-signed dev installs.
_ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})


@dataclass(frozen=True)
class GitLabAppConfig:
    """Configuration for a GitLab App / integration.

    Attributes:
        base_url: GitLab instance base URL (``https://gitlab.com`` by
            default).  Self-managed installs override via
            ``BERNSTEIN_GITLAB_URL``.
        token: Personal / project access token used for API calls.
        webhook_token: Shared secret returned in the ``X-Gitlab-Token``
            header by GitLab when delivering webhooks.
    """

    base_url: str
    token: str
    webhook_token: str

    @classmethod
    def from_env(cls) -> GitLabAppConfig:
        """Read configuration from environment variables.

        Reads (in order):

        * ``BERNSTEIN_GITLAB_URL`` — base URL (default
          ``https://gitlab.com``).
        * ``GITLAB_TOKEN`` (or fallback ``GITLAB_PAT``) — API token.
        * ``GITLAB_WEBHOOK_TOKEN`` — webhook shared secret.

        Returns:
            Populated :class:`GitLabAppConfig`.

        Raises:
            ValueError: If a required environment variable is missing.
        """
        base_url = get_gitlab_base_url()

        token = os.environ.get("GITLAB_TOKEN") or os.environ.get("GITLAB_PAT", "")
        if not token:
            msg = "GITLAB_TOKEN (or GITLAB_PAT) environment variable is required"
            raise ValueError(msg)

        webhook_token = os.environ.get("GITLAB_WEBHOOK_TOKEN", "")
        if not webhook_token:
            msg = "GITLAB_WEBHOOK_TOKEN environment variable is required"
            raise ValueError(msg)

        return cls(base_url=base_url, token=token, webhook_token=webhook_token)


def get_gitlab_base_url() -> str:
    """Return the configured GitLab base URL.

    Honours ``BERNSTEIN_GITLAB_URL``.  The value is validated: scheme
    must be ``http`` or ``https`` and host must be non-empty.  Invalid
    values fall back to :data:`DEFAULT_GITLAB_URL` with a log warning.

    Returns:
        Canonical base URL with no trailing slash.
    """
    raw = os.environ.get("BERNSTEIN_GITLAB_URL", "").strip()
    if not raw:
        return DEFAULT_GITLAB_URL

    parsed = urlparse(raw)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES or not parsed.netloc:
        logger.warning(
            "Invalid BERNSTEIN_GITLAB_URL %r — falling back to %s",
            raw,
            DEFAULT_GITLAB_URL,
        )
        return DEFAULT_GITLAB_URL

    return raw.rstrip("/")


def build_api_url(path: str, base_url: str | None = None) -> str:
    """Build a fully-qualified GitLab API URL.

    Args:
        path: API path beginning with ``/`` — e.g. ``/projects/42/jobs``.
            A leading slash is added if missing.  The path is *not*
            URL-encoded; the caller is expected to encode project IDs
            (e.g. ``namespace%2Fproject``) where required.
        base_url: Override base URL.  When ``None``, reads from env.

    Returns:
        Absolute API URL such as
        ``https://gitlab.com/api/v4/projects/42/jobs``.
    """
    if base_url is None:
        base_url = get_gitlab_base_url()
    if not path.startswith("/"):
        path = "/" + path
    return f"{base_url.rstrip('/')}/api/v4{path}"


def build_auth_headers(token: str) -> dict[str, str]:
    """Build the standard GitLab API auth headers.

    Args:
        token: PAT or project access token.

    Returns:
        Header dict with ``PRIVATE-TOKEN`` set.  Returns an empty dict
        when *token* is empty so callers can short-circuit unauth flows.
    """
    if not token:
        return {}
    return {"PRIVATE-TOKEN": token}


def fetch_job_trace(
    project_id: str | int,
    job_id: int,
    token: str,
    base_url: str | None = None,
    *,
    timeout: float = 30.0,
) -> str:
    """Fetch the raw plain-text trace for a failed CI job.

    Calls ``GET /projects/:id/jobs/:job_id/trace``.  Returns the raw
    text body (which GitLab serves as ``text/plain``).  Returns an
    empty string on any error or when the HTTP client is unavailable;
    callers are expected to handle the empty case as "no trace
    available".

    Args:
        project_id: Numeric project ID *or* URL-encoded
            ``namespace%2Fproject`` slug.
        job_id: Numeric GitLab CI job ID.
        token: API token used for the ``PRIVATE-TOKEN`` header.
        base_url: GitLab base URL.  ``None`` reads from env.
        timeout: HTTP timeout in seconds.

    Returns:
        Trace text or ``""`` on failure.
    """
    if not token:
        logger.debug("fetch_job_trace: no token — skipping")
        return ""

    url = build_api_url(f"/projects/{project_id}/jobs/{job_id}/trace", base_url)
    try:
        import httpx
    except ImportError:
        logger.debug("httpx not available — cannot fetch GitLab job trace")
        return ""

    try:
        response: Any = httpx.get(url, headers=build_auth_headers(token), timeout=timeout)
        if response.status_code != 200:
            logger.info(
                "fetch_job_trace: %s returned HTTP %d",
                url,
                response.status_code,
            )
            return ""
        return str(response.text or "")
    except Exception as exc:  # pragma: no cover - depends on env
        logger.debug("fetch_job_trace failed: %s", exc)
        return ""
