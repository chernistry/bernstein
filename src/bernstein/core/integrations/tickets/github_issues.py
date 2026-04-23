"""GitHub Issues ticket fetcher.

Prefers the local ``gh`` CLI when available (picks up the user's stored
credentials automatically). Falls back to the REST API with
``GITHUB_TOKEN`` if ``gh`` is missing.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any, cast

from bernstein.core.integrations.tickets import (
    TicketAuthError,
    TicketParseError,
    TicketPayload,
)

__all__ = ["fetch_github_issue"]


_GH_ENV = "GITHUB_TOKEN"
_TIMEOUT_S = 10.0
_ISSUE_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/issues/(?P<num>\d+)",
    re.IGNORECASE,
)


def _parse_url(url: str) -> tuple[str, str, int]:
    match = _ISSUE_RE.match(url.strip())
    if match is None:
        raise TicketParseError(f"Not a GitHub issue URL: {url!r}")
    return match["owner"], match["repo"], int(match["num"])


def _gh_available() -> bool:
    return shutil.which("gh") is not None


def _fetch_via_gh(owner: str, repo: str, number: int) -> dict[str, Any]:
    cmd = [
        "gh",
        "issue",
        "view",
        str(number),
        "--repo",
        f"{owner}/{repo}",
        "--json",
        "number,title,body,labels,assignees,url",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise TicketParseError(f"gh CLI invocation failed: {exc}") from exc
    if proc.returncode != 0:
        stderr = (proc.stderr or "").lower()
        if "auth" in stderr or "login" in stderr or "token" in stderr:
            raise TicketAuthError(
                "gh CLI is not authenticated. Run `gh auth login` or set the "
                f"{_GH_ENV} environment variable to a personal access token."
            )
        raise TicketParseError(f"gh CLI error: {proc.stderr.strip()[:200]}")
    try:
        return cast(dict[str, Any], json.loads(proc.stdout))
    except json.JSONDecodeError as exc:
        raise TicketParseError(f"gh CLI returned invalid JSON: {exc}") from exc


def _fetch_via_rest(owner: str, repo: str, number: int) -> dict[str, Any]:
    token = os.environ.get(_GH_ENV)
    if not token:
        raise TicketAuthError(
            f"gh CLI not found and {_GH_ENV} is unset. Install gh or set {_GH_ENV} to a personal access token."
        )
    endpoint = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        import httpx

        resp = httpx.get(endpoint, headers=headers, timeout=_TIMEOUT_S)
        if resp.status_code in (401, 403):
            raise TicketAuthError(
                f"GitHub rejected the request (HTTP {resp.status_code}). Check the {_GH_ENV} environment variable."
            )
        if resp.status_code >= 400:
            raise TicketParseError(f"GitHub API returned HTTP {resp.status_code}: {resp.text[:200]}")
        return cast(dict[str, Any], resp.json())
    except ImportError:  # pragma: no cover - httpx is a declared dependency
        import urllib.error
        import urllib.request

        req = urllib.request.Request(endpoint, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as handle:
                return cast(dict[str, Any], json.loads(handle.read().decode("utf-8")))
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise TicketAuthError(
                    f"GitHub rejected the request (HTTP {exc.code}). Check the {_GH_ENV} environment variable."
                ) from exc
            raise TicketParseError(f"GitHub API returned HTTP {exc.code}") from exc


def _normalize_gh_cli(
    owner: str,
    repo: str,
    raw: dict[str, Any],
) -> TicketPayload:
    labels_raw = raw.get("labels") or []
    labels = tuple(
        str(item.get("name", "")).strip() for item in labels_raw if isinstance(item, dict) and item.get("name")
    )
    assignees = raw.get("assignees") or []
    assignee = None
    if isinstance(assignees, list) and assignees and isinstance(assignees[0], dict):
        first = assignees[0]
        assignee = first.get("login") or first.get("name")
    number = raw.get("number")
    return TicketPayload(
        id=f"{owner}/{repo}#{number}",
        title=str(raw.get("title") or "").strip(),
        description=str(raw.get("body") or "").strip(),
        labels=labels,
        assignee=str(assignee) if assignee else None,
        url=str(raw.get("url") or ""),
        source="github",
    )


def _normalize_rest(owner: str, repo: str, raw: dict[str, Any]) -> TicketPayload:
    labels_raw = raw.get("labels") or []
    labels: tuple[str, ...] = ()
    collected: list[str] = []
    for item in labels_raw:
        if isinstance(item, str):
            collected.append(item)
        elif isinstance(item, dict):
            name = item.get("name")
            if isinstance(name, str):
                collected.append(name)
    labels = tuple(collected)

    assignee_obj = raw.get("assignee") or {}
    assignee: str | None = None
    if isinstance(assignee_obj, dict):
        login = assignee_obj.get("login")
        if isinstance(login, str):
            assignee = login

    number = raw.get("number")
    return TicketPayload(
        id=f"{owner}/{repo}#{number}",
        title=str(raw.get("title") or "").strip(),
        description=str(raw.get("body") or "").strip(),
        labels=labels,
        assignee=assignee,
        url=str(raw.get("html_url") or ""),
        source="github",
    )


def fetch_github_issue(url: str) -> TicketPayload:
    """Fetch a GitHub issue and return it as a :class:`TicketPayload`.

    Uses the ``gh`` CLI if available; otherwise calls the REST API with
    ``GITHUB_TOKEN``.
    """
    owner, repo, number = _parse_url(url)
    if _gh_available():
        raw = _fetch_via_gh(owner, repo, number)
        return _normalize_gh_cli(owner, repo, raw)
    raw = _fetch_via_rest(owner, repo, number)
    return _normalize_rest(owner, repo, raw)
