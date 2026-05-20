"""Jira Data Center / Server tracker adapter.

Implements the ``AbstractTrackerAdapter`` contract on top of Jira Data
Center's REST API v2. The adapter targets self-hosted Jira deployments
where the tenant cannot or will not route data through Atlassian
Cloud. The shape mirrors the GitHub Projects v2 sibling adapter: thin
HTTP wrappers, a small token-bucket throttle, and the same error
taxonomy (``RateLimited``, ``OptimisticConcurrencyError``,
``TrackerUnavailable``).

Auth:
- Personal Access Token via ``JIRA_DC_PAT`` (or a configurable env
  variable). Data Center uses bearer-token auth directly with no
  email/password pair.

Capabilities:
- ``pull_open_tickets``: page over a JQL query and emit normalised
  ``Ticket`` objects.
- ``add_comment``: POST to ``/rest/api/2/issue/<key>/comment``.
- ``transition``: GET the available transitions, resolve the target id
  or name, then POST to ``/rest/api/2/issue/<key>/transitions``.

TLS:
- Honours the ``BERNSTEIN_CA_BUNDLE`` environment variable so operators
  can pin self-signed certificates without rebuilding the wheel.

Rate-limit handling:
- Cooperative client-side throttle (token bucket).
- ``RateLimited`` raised with ``retry_after`` derived from the
  ``Retry-After`` header when the server returns 429 or a 503 with the
  same hint header.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

try:  # pragma: no cover - optional dep already present in project deps
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

from bernstein.core.trackers.contract import (
    AbstractTrackerAdapter,
    CommentResult,
    OptimisticConcurrencyError,
    RateLimited,
    RoutingHint,
    Ticket,
    TrackerUnavailable,
    TransitionResult,
)

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 50
DEFAULT_JQL = "resolution = Unresolved ORDER BY priority DESC, created ASC"
CA_BUNDLE_ENV = "BERNSTEIN_CA_BUNDLE"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JiraDataCenterConfig:
    """Configuration for a Jira Data Center adapter instance.

    Attributes:
        base_url: Operator's Data Center URL, for example
            ``https://jira.acme.internal``. Trailing slash optional.
        pat_env: Environment variable name holding the PAT. Defaults to
            ``JIRA_DC_PAT``.
        project_key: Optional Jira project key filter (e.g. ``ENG``).
        default_jql: JQL used by ``pull_open_tickets`` when the caller
            does not pass an override.
        status_map: Optional mapping from canonical workflow names to
            the target tracker's transition name. ``done`` -> ``Done``.
        cli_choice_field_id: Optional Jira custom-field id whose value
            selects a CLI adapter (``claude``, ``codex``, ...).
        page_size: REST API page size.
        rate_limit_min_interval: Minimum seconds between calls per
            adapter instance.
        verify_tls: TLS verification mode. ``True`` (default) verifies
            against the system trust store; pass a path to pin a custom
            bundle. ``BERNSTEIN_CA_BUNDLE`` overrides this when set.
    """

    base_url: str
    pat_env: str = "JIRA_DC_PAT"
    project_key: str | None = None
    default_jql: str = DEFAULT_JQL
    status_map: dict[str, str] = field(default_factory=dict)
    cli_choice_field_id: str | None = None
    page_size: int = DEFAULT_PAGE_SIZE
    rate_limit_min_interval: float = 0.0
    verify_tls: bool | str = True


# ---------------------------------------------------------------------------
# Token resolution
# ---------------------------------------------------------------------------


def _resolve_token(config: JiraDataCenterConfig) -> str:
    """Resolve the PAT for a Jira DC request.

    Raises:
        TrackerUnavailable: if the configured env var is empty.
    """
    env_var = config.pat_env or "JIRA_DC_PAT"
    token = os.environ.get(env_var, "")
    if not token:
        msg = f"Jira Data Center adapter: no PAT available (env var '{env_var}' is empty)"
        raise TrackerUnavailable(msg)
    return token


def _resolve_verify(config: JiraDataCenterConfig) -> bool | str:
    """Resolve TLS verify argument.

    ``BERNSTEIN_CA_BUNDLE`` env override wins so operators can pin a
    self-signed bundle without rebuilding configuration.
    """
    env_bundle = os.environ.get(CA_BUNDLE_ENV, "")
    if env_bundle:
        return env_bundle
    return config.verify_tls


def _normalise_base_url(base_url: str) -> str:
    """Strip trailing slashes from the configured base URL."""
    return base_url.rstrip("/")


# ---------------------------------------------------------------------------
# Token bucket
# ---------------------------------------------------------------------------


class _TokenBucket:
    """Minimal cooperative token-bucket throttle.

    Mirrors the GitHub Projects v2 adapter so the two trackers share
    operational characteristics. ``min_interval == 0`` disables it.
    """

    def __init__(self, min_interval: float) -> None:
        self._min_interval = max(0.0, min_interval)
        self._next_allowed = 0.0
        self._lock = threading.Lock()

    def acquire(self, *, now: float | None = None, sleep: Any = time.sleep) -> None:
        if self._min_interval <= 0.0:
            return
        now = time.monotonic() if now is None else now
        with self._lock:
            wait = self._next_allowed - now
            if wait > 0:
                sleep(wait)
                now = now + wait
            self._next_allowed = now + self._min_interval


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class JiraDataCenterAdapter(AbstractTrackerAdapter):
    """Adapter for Jira Data Center / Server.

    The adapter is intentionally thin: every method maps to a single
    REST round-trip (``add_comment``, ``transition``) or a paginated
    search (``pull_open_tickets``). Field schemas are not cached
    because Jira already returns enough metadata on each issue payload.

    Parameters:
        config: Adapter configuration.
        http_client: Optional pre-built ``httpx.Client`` (tests pass a
            mocked transport via ``respx``).
        token_provider: Optional callable returning the bearer token.
            The default reads ``_resolve_token(config)`` on every call
            so operators can rotate PATs without restarting Bernstein.
    """

    name = "jira_data_center"

    def __init__(
        self,
        config: JiraDataCenterConfig,
        *,
        http_client: Any | None = None,
        token_provider: Any | None = None,
    ) -> None:
        if httpx is None:  # pragma: no cover - dep is declared in pyproject
            msg = "httpx is required for JiraDataCenterAdapter"
            raise RuntimeError(msg)
        self._config = config
        self._base_url = _normalise_base_url(config.base_url)
        self._client: Any = http_client or httpx.Client(
            timeout=30.0,
            verify=_resolve_verify(config),
        )
        self._owns_client = http_client is None
        self._token_provider = token_provider or (lambda: _resolve_token(config))
        self._bucket = _TokenBucket(config.rate_limit_min_interval)

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        """Release the underlying HTTP client if we own it."""
        if self._owns_client:
            try:
                self._client.close()
            except Exception:  # pragma: no cover - best effort
                logger.debug("ignoring error while closing httpx client", exc_info=True)

    def __enter__(self) -> JiraDataCenterAdapter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- public API ---------------------------------------------------------

    def pull_open_tickets(
        self,
        filter: dict[str, Any] | None = None,
    ) -> Iterator[Ticket]:
        """Yield matching issues as ``Ticket`` objects.

        ``filter`` keys:
            - ``jql``: override the default JQL for this call.
            - ``status``: shorthand that becomes ``status = "<value>"``
              when no ``jql`` override is supplied.
            - ``project``: shorthand that overrides
              ``config.project_key`` for this call.
        """
        filter_dict = filter or {}
        jql = self._build_jql(filter_dict)
        start_at = 0
        page_size = max(1, min(100, self._config.page_size))

        while True:
            payload = self._search(jql, start_at=start_at, max_results=page_size)
            issues = payload.get("issues") or []
            for raw in issues:
                yield self._issue_to_ticket(raw)
            total = int(payload.get("total") or 0)
            start_at += len(issues)
            if not issues or start_at >= total:
                break

    def add_comment(
        self,
        ticket_id: str,
        body: str,
        *,
        idempotency_key: str | None = None,
    ) -> CommentResult:
        """Post a comment on ``ticket_id`` (the Jira issue key).

        Jira DC has no native idempotency key. We pass
        ``idempotency_key`` as a custom header so operators auditing
        webhook traffic can reconcile retries.
        """
        url = f"{self._base_url}/rest/api/2/issue/{ticket_id}/comment"
        headers = self._headers(idempotency_key=idempotency_key)
        response = self._request("POST", url, headers=headers, json={"body": body})
        data = self._parse_json(response)
        return CommentResult(
            comment_id=str(data.get("id") or ""),
            ticket_id=ticket_id,
        )

    def transition(
        self,
        ticket_id: str,
        status_id: str,
        *,
        idempotency_key: str | None = None,
        etag: str | None = None,
    ) -> TransitionResult:
        """Move ``ticket_id`` to the workflow state ``status_id``.

        ``status_id`` can be either:
            - the transition id (preferred, opaque).
            - the transition name (resolved by querying the available
              transitions on the issue).
            - a key in ``config.status_map`` (resolved twice: through
              the map first, then by id/name).
        """
        mapped = self._config.status_map.get(status_id, status_id)
        transition_id = self._resolve_transition_id(ticket_id, mapped)

        url = f"{self._base_url}/rest/api/2/issue/{ticket_id}/transitions"
        headers = self._headers(idempotency_key=idempotency_key, etag=etag)
        body = {"transition": {"id": transition_id}}
        self._request("POST", url, headers=headers, json=body)
        return TransitionResult(
            ticket_id=ticket_id,
            new_status=status_id,
            etag=None,
        )

    # -- internals ----------------------------------------------------------

    def _build_jql(self, filter_dict: dict[str, Any]) -> str:
        override = filter_dict.get("jql")
        if override:
            return str(override)
        project = filter_dict.get("project", self._config.project_key)
        status = filter_dict.get("status")
        clauses: list[str] = []
        if project:
            clauses.append(f'project = "{project}"')
        if status:
            clauses.append(f'status = "{status}"')
        if clauses:
            return " AND ".join(clauses) + " ORDER BY priority DESC, created ASC"
        return self._config.default_jql

    def _search(self, jql: str, *, start_at: int, max_results: int) -> dict[str, Any]:
        url = f"{self._base_url}/rest/api/2/search"
        fields = ["summary", "description", "status", "labels", "issuetype"]
        if self._config.cli_choice_field_id:
            fields.append(self._config.cli_choice_field_id)
        params = {
            "jql": jql,
            "startAt": start_at,
            "maxResults": max_results,
            "fields": ",".join(fields),
        }
        response = self._request("GET", url, headers=self._headers(), params=params)
        return self._parse_json(response)

    def _resolve_transition_id(self, ticket_id: str, status_id: str) -> str:
        url = f"{self._base_url}/rest/api/2/issue/{ticket_id}/transitions"
        response = self._request("GET", url, headers=self._headers())
        data = self._parse_json(response)
        for transition in data.get("transitions") or []:
            tid = str(transition.get("id") or "")
            name = transition.get("name") or ""
            to_name = (transition.get("to") or {}).get("name") or ""
            if status_id in {tid, name, to_name}:
                return tid
        msg = f"Transition '{status_id}' not found on issue '{ticket_id}'"
        raise TrackerUnavailable(msg)

    def _issue_to_ticket(self, raw: dict[str, Any]) -> Ticket:
        key = str(raw.get("key") or "")
        fields_block: dict[str, Any] = raw.get("fields") or {}
        status_block: dict[str, Any] = fields_block.get("status") or {}
        labels_raw = fields_block.get("labels") or []
        labels = tuple(str(lab) for lab in labels_raw if lab)

        cli_choice: str | None = None
        if self._config.cli_choice_field_id:
            field_value = fields_block.get(self._config.cli_choice_field_id)
            if isinstance(field_value, dict):
                value = field_value.get("value") or field_value.get("name")
                cli_choice = str(value) if value else None
            elif isinstance(field_value, str) and field_value:
                cli_choice = field_value

        external_url = f"{self._base_url}/browse/{key}" if key else ""
        return Ticket(
            id=key,
            external_url=external_url,
            title=str(fields_block.get("summary") or ""),
            body=str(fields_block.get("description") or ""),
            status=str(status_block.get("name") or ""),
            labels=labels,
            etag=None,
            routing_hint=RoutingHint(cli=cli_choice),
            raw={
                "issue_id": str(raw.get("id") or ""),
                "issue_type": str(((fields_block.get("issuetype") or {}).get("name")) or ""),
                "project_key": key.split("-", 1)[0] if "-" in key else "",
            },
        )

    # -- HTTP --------------------------------------------------------------

    def _headers(
        self,
        *,
        idempotency_key: str | None = None,
        etag: str | None = None,
    ) -> dict[str, str]:
        token = self._token_provider()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if idempotency_key:
            headers["X-Bernstein-Idempotency-Key"] = idempotency_key
        if etag:
            headers["If-Match"] = etag
        return headers

    def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        self._bucket.acquire()
        try:
            response = self._client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json,
            )
        except Exception as exc:  # pragma: no cover - network errors
            msg = f"Jira DC transport error: {exc}"
            raise TrackerUnavailable(msg) from exc

        status_code = getattr(response, "status_code", 0)
        if status_code == 412:
            msg = "Precondition Failed (etag mismatch)"
            raise OptimisticConcurrencyError(msg)
        if status_code in (429, 503):
            retry_after = _parse_retry_after(response)
            if status_code == 429 or retry_after is not None:
                msg = f"Jira DC rate-limited (status={status_code})"
                raise RateLimited(msg, retry_after=retry_after)
        if status_code >= 500:
            msg = f"Jira DC server error (status={status_code})"
            raise TrackerUnavailable(msg)
        if status_code >= 400:
            body = _safe_text(response)
            msg = f"Jira DC HTTP {status_code}: {body[:200]}"
            raise TrackerUnavailable(msg)
        return response

    def _parse_json(self, response: Any) -> dict[str, Any]:
        try:
            data = response.json()
        except Exception as exc:  # pragma: no cover - malformed JSON
            msg = f"Jira DC returned non-JSON: {exc}"
            raise TrackerUnavailable(msg) from exc
        if not isinstance(data, dict):
            return {}
        return data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_retry_after(response: Any) -> float | None:
    """Best-effort ``Retry-After`` parser."""
    headers = getattr(response, "headers", {}) or {}
    retry_after = headers.get("Retry-After") if hasattr(headers, "get") else None
    if retry_after:
        try:
            return float(retry_after)
        except (TypeError, ValueError):
            return None
    return None


def _safe_text(response: Any) -> str:
    try:
        return str(getattr(response, "text", ""))
    except Exception:  # pragma: no cover
        return ""
