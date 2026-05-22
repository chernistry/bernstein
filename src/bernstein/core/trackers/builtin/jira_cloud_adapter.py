"""Jira Cloud tracker adapter.

Implements the ``AbstractTrackerAdapter`` contract on top of the public
Jira Cloud REST v3 API.

Auth:
- API token + email + domain (Atlassian's standard Basic auth lane).
  The adapter reads ``JIRA_CLOUD_API_TOKEN``, ``JIRA_CLOUD_EMAIL`` and
  ``JIRA_CLOUD_DOMAIN`` from the environment by default; the config can
  override each env-var name and the literal domain.

Capabilities:
- ``pull_open_tickets``: run a JQL filter and paginate the search API,
  emitting normalised ``Ticket`` objects.
- ``add_comment``: post a comment on the underlying issue.
- ``transition``: move the issue to a configured transition id, with
  optional ``status_map`` indirection.

Per-step CLI choice:
- If ``cli_choice_field_id`` is configured (a Jira custom-field id) the
  adapter surfaces that field's selected value as
  ``ticket.routing_hint.cli`` so the orchestrator can route the work to
  a specific CLI adapter.

Rate-limit handling:
- Cooperative token-bucket per adapter instance.
- ``RateLimited`` raised with ``retry_after`` derived from the
  ``Retry-After`` header on 429 responses.
"""

from __future__ import annotations

import base64
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

DEFAULT_DOMAIN_ENV = "JIRA_CLOUD_DOMAIN"
DEFAULT_EMAIL_ENV = "JIRA_CLOUD_EMAIL"
DEFAULT_TOKEN_ENV = "JIRA_CLOUD_API_TOKEN"
DEFAULT_PAGE_SIZE = 50
DEFAULT_JQL = "statusCategory != Done ORDER BY updated DESC"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JiraCloudConfig:
    """Configuration for a Jira Cloud adapter instance.

    Attributes:
        domain: Atlassian site domain (e.g. ``acme.atlassian.net``). If
            empty, the adapter reads ``domain_env`` from the environment.
        jql: JQL filter used by ``pull_open_tickets`` when no filter
            override is provided. Defaults to "open" issues.
        status_map: Optional mapping from canonical status names to
            tracker-side transition ids or names. ``done`` -> ``41``.
        cli_choice_field_id: Optional Jira custom-field id (e.g.
            ``customfield_10010``) whose value selects a CLI adapter.
        domain_env: Environment variable name holding the Atlassian
            domain when ``domain`` is empty.
        email_env: Environment variable name holding the account email.
        token_env: Environment variable name holding the API token.
        page_size: REST search page size.
        rate_limit_min_interval: Minimum seconds between API calls per
            adapter instance (cheap client-side throttle).
    """

    domain: str = ""
    jql: str = DEFAULT_JQL
    status_map: dict[str, str] = field(default_factory=dict)
    cli_choice_field_id: str | None = None
    domain_env: str = DEFAULT_DOMAIN_ENV
    email_env: str = DEFAULT_EMAIL_ENV
    token_env: str = DEFAULT_TOKEN_ENV
    page_size: int = DEFAULT_PAGE_SIZE
    rate_limit_min_interval: float = 0.0


# ---------------------------------------------------------------------------
# Auth resolution
# ---------------------------------------------------------------------------


def _resolve_domain(config: JiraCloudConfig) -> str:
    """Return the Atlassian site domain for ``config``.

    Order of precedence:
        1. Explicit ``config.domain``.
        2. ``config.domain_env`` environment variable.

    Raises:
        TrackerUnavailable: if no domain source is configured.
    """
    if config.domain:
        return config.domain.strip().rstrip("/")
    domain = os.environ.get(config.domain_env, "").strip().rstrip("/")
    if not domain:
        msg = (
            f"Jira Cloud adapter: no domain available "
            f"(env var '{config.domain_env}' is empty and config.domain is unset)"
        )
        raise TrackerUnavailable(msg)
    return domain


def _resolve_basic_auth(config: JiraCloudConfig) -> str:
    """Return the ``Basic ...`` Authorization header value.

    Reads the email + API-token pair from the env vars named in
    ``config`` and base64-encodes them per RFC 7617.

    Raises:
        TrackerUnavailable: when either env var is empty.
    """
    email = os.environ.get(config.email_env, "")
    token = os.environ.get(config.token_env, "")
    if not email or not token:
        msg = (
            f"Jira Cloud adapter: missing credentials "
            f"(email env '{config.email_env}' or token env '{config.token_env}' is empty)"
        )
        raise TrackerUnavailable(msg)
    pair = f"{email}:{token}".encode()
    return "Basic " + base64.b64encode(pair).decode("ascii")


# ---------------------------------------------------------------------------
# Token bucket
# ---------------------------------------------------------------------------


class _TokenBucket:
    """Minimal cooperative token-bucket throttle.

    ``min_interval == 0`` disables the throttle. Hard enforcement is
    delegated to the server-side ``Retry-After`` header.
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


class JiraCloudTracker(AbstractTrackerAdapter):
    """Adapter for Jira Cloud's REST v3 API.

    Each public method is a single HTTP round-trip; field metadata is
    cached only for the ``cli_choice_field_id`` lookup.

    Parameters:
        config: Adapter configuration.
        http_client: Optional pre-built ``httpx.Client`` (tests pass a
            mocked transport via ``respx``).
        auth_provider: Optional callable returning the
            ``Authorization`` header value. The default reads from
            ``_resolve_basic_auth(config)`` on each call, which makes
            credential rotation trivial.
        domain_provider: Optional callable returning the site domain.
    """

    name = "jira_cloud"

    def __init__(
        self,
        config: JiraCloudConfig,
        *,
        http_client: Any | None = None,
        auth_provider: Any | None = None,
        domain_provider: Any | None = None,
    ) -> None:
        if httpx is None:  # pragma: no cover - dep is declared in pyproject
            msg = "httpx is required for JiraCloudTracker"
            raise RuntimeError(msg)
        self._config = config
        self._client: Any = http_client or httpx.Client(timeout=30.0)
        self._owns_client = http_client is None
        self._auth_provider = auth_provider or (lambda: _resolve_basic_auth(config))
        self._domain_provider = domain_provider or (lambda: _resolve_domain(config))
        self._bucket = _TokenBucket(config.rate_limit_min_interval)

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        """Release the underlying HTTP client if we own it."""
        if self._owns_client:
            try:
                self._client.close()
            except Exception:  # pragma: no cover - best effort
                logger.debug("ignoring error while closing httpx client", exc_info=True)

    def __enter__(self) -> JiraCloudTracker:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- public API ---------------------------------------------------------

    def pull_open_tickets(
        self,
        filter: dict[str, Any] | None = None,
    ) -> Iterator[Ticket]:
        """Yield issues matched by the configured JQL filter.

        ``filter`` keys:
            - ``jql``: override ``config.jql`` for this call.
            - ``fields``: comma-separated list of fields to request
              (default: ``summary,description,status,labels`` plus
              ``cli_choice_field_id`` when configured).
        """
        filter_dict = filter or {}
        jql = str(filter_dict.get("jql") or self._config.jql)
        fields = self._fields_for_request(filter_dict.get("fields"))

        page_size = max(1, min(100, self._config.page_size))
        start_at = 0
        while True:
            payload = {
                "jql": jql,
                "startAt": start_at,
                "maxResults": page_size,
                "fields": fields,
            }
            data = self._request("POST", "/rest/api/3/search", json=payload)
            issues = data.get("issues") or []
            for raw in issues:
                yield self._issue_to_ticket(raw)
            total = int(data.get("total") or 0)
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
        """Post a comment on the issue ``ticket_id``.

        Jira's REST v3 expects an Atlassian Document Format payload, so
        the plain-text ``body`` is wrapped in a minimal ADF tree. When
        ``idempotency_key`` is provided it is prepended as a fenced
        metadata block so duplicate posts can be detected by readers.
        """
        text = body if not idempotency_key else f"```idempotency:{idempotency_key}```\n\n{body}"
        adf = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": text}],
                },
            ],
        }
        result = self._request(
            "POST",
            f"/rest/api/3/issue/{ticket_id}/comment",
            json={"body": adf},
        )
        comment_id = str(result.get("id") or "")
        return CommentResult(comment_id=comment_id, ticket_id=ticket_id)

    def transition(
        self,
        ticket_id: str,
        status_id: str,
        *,
        idempotency_key: str | None = None,
        etag: str | None = None,
    ) -> TransitionResult:
        """Move ``ticket_id`` to the transition identified by ``status_id``.

        ``status_id`` is resolved through ``config.status_map`` first
        (canonical -> Jira transition id), then passed straight through
        to the Jira API. ``etag`` is forwarded as ``If-Match`` for
        optimistic concurrency; Jira responds 409 on conflict.
        ``idempotency_key`` is forwarded as the
        ``X-Bernstein-Idempotency-Key`` header so operators can audit
        and de-duplicate retried transitions even though Jira Cloud
        does not natively honour an idempotency key on this endpoint.
        """
        mapped = self._config.status_map.get(status_id, status_id)
        self._request(
            "POST",
            f"/rest/api/3/issue/{ticket_id}/transitions",
            json={"transition": {"id": mapped}},
            etag=etag,
            idempotency_key=idempotency_key,
        )
        return TransitionResult(
            ticket_id=ticket_id,
            new_status=status_id,
            etag=None,
        )

    # -- internals ----------------------------------------------------------

    def _fields_for_request(self, requested: Any) -> list[str]:
        if isinstance(requested, list):
            base = [str(x) for x in requested]
        elif isinstance(requested, str):
            base = [chunk.strip() for chunk in requested.split(",") if chunk.strip()]
        else:
            base = ["summary", "description", "status", "labels"]
        if self._config.cli_choice_field_id and self._config.cli_choice_field_id not in base:
            base.append(self._config.cli_choice_field_id)
        return base

    def _issue_to_ticket(self, raw: dict[str, Any]) -> Ticket:
        fields = raw.get("fields") or {}
        status_block = fields.get("status") or {}
        status_name = str(status_block.get("name") or "")

        cli_choice: str | None = None
        if self._config.cli_choice_field_id:
            value = fields.get(self._config.cli_choice_field_id)
            if isinstance(value, dict):
                cli_choice = value.get("value") or value.get("name")
            elif isinstance(value, str):
                cli_choice = value

        labels_raw = fields.get("labels") or []
        labels = tuple(str(x) for x in labels_raw if x)

        body = fields.get("description") or ""
        if isinstance(body, dict):
            body = _adf_to_text(body)

        key = str(raw.get("key") or "")
        domain = self._domain_provider()
        external_url = f"https://{domain}/browse/{key}" if key else ""
        return Ticket(
            id=key,
            external_url=external_url,
            title=str(fields.get("summary") or ""),
            body=str(body or ""),
            status=status_name,
            labels=labels,
            etag=None,
            routing_hint=RoutingHint(cli=cli_choice),
            raw={
                "issue_id": str(raw.get("id") or ""),
                "key": key,
            },
        )

    # -- HTTP --------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        etag: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        self._bucket.acquire()
        domain = self._domain_provider()
        url = f"https://{domain}{path}"
        headers = {
            "Authorization": self._auth_provider(),
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if etag:
            headers["If-Match"] = etag
        if idempotency_key:
            headers["X-Bernstein-Idempotency-Key"] = idempotency_key
        try:
            response = self._client.request(method, url, headers=headers, json=json)
        except Exception as exc:  # pragma: no cover - network errors
            msg = f"Jira Cloud transport error: {exc}"
            raise TrackerUnavailable(msg) from exc

        status_code = getattr(response, "status_code", 0)
        if status_code in (412, 409):
            msg = f"Jira Cloud precondition/conflict (status={status_code})"
            raise OptimisticConcurrencyError(msg)
        if status_code == 429:
            retry_after = _parse_retry_after(response)
            msg = f"Jira Cloud rate-limited (status={status_code})"
            raise RateLimited(msg, retry_after=retry_after)
        if status_code >= 500:
            msg = f"Jira Cloud server error (status={status_code})"
            raise TrackerUnavailable(msg)
        if status_code >= 400:
            body = _safe_text(response)
            msg = f"Jira Cloud HTTP {status_code}: {body[:200]}"
            raise TrackerUnavailable(msg)
        if status_code == 204 or not _has_body(response):
            return {}
        try:
            data = response.json()
        except Exception as exc:  # pragma: no cover - malformed JSON
            msg = f"Jira Cloud returned non-JSON: {exc}"
            raise TrackerUnavailable(msg) from exc
        if not isinstance(data, dict):
            return {"value": data}
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


def _has_body(response: Any) -> bool:
    text = getattr(response, "text", "") or ""
    return bool(text.strip())


def _adf_to_text(node: dict[str, Any]) -> str:
    """Collapse a simple ADF tree to plain text (best effort)."""
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return str(node.get("text") or "")
    out: list[str] = [_adf_to_text(child) for child in node.get("content") or []]
    return "".join(out)
