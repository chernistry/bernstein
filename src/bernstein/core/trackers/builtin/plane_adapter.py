"""Plane (OSS PM tool) tracker adapter.

Implements the ``AbstractTrackerAdapter`` contract on top of the public
Plane REST API. Plane is an AGPL-3 self-hostable project tracker; the
adapter targets both Plane Cloud (``https://api.plane.so``) and any
self-hosted Plane deployment via the ``PLANE_URL`` environment variable
or ``instance_url`` config.

Auth:
- API key via ``X-API-Key`` header. The key is read from the
  environment variable named in ``config.api_token_env`` (default
  ``PLANE_API_KEY``).

Capabilities:
- ``pull_open_tickets``: paginate the project's issues, optionally
  filtered by state name, and emit normalised ``Ticket`` objects.
- ``add_comment``: post a comment to the issue.
- ``transition``: update the issue's ``state`` to the configured target
  state. Plane represents workflow status as a ``state`` (UUID) on the
  issue; the adapter discovers states once and caches the lookup.

Rate-limit handling:
- Token-bucket per-adapter (cooperative, advisory).
- ``RateLimited`` raised with ``retry_after`` derived from the
  ``Retry-After`` header. 5xx responses raise ``TrackerUnavailable``.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

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

DEFAULT_PLANE_URL = "https://api.plane.so"
DEFAULT_PAGE_SIZE = 100


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlaneConfig:
    """Configuration for a Plane adapter instance.

    Attributes:
        workspace_slug: Plane workspace slug (the ``acme`` in
            ``app.plane.so/acme``).
        project_id: Plane project UUID.
        instance_url: Base URL for the Plane API. Defaults to
            ``https://api.plane.so``; set to your self-hosted Plane URL
            (for example ``https://plane.acme.internal``). The
            ``PLANE_URL`` environment variable overrides this when set.
        api_token_env: Environment variable name holding the API key.
            Defaults to ``PLANE_API_KEY``.
        state_filter: Optional state name the adapter filters on when
            calling ``pull_open_tickets`` with no filter override.
        state_map: Optional mapping from canonical state names to the
            project's display values. ``done`` -> ``Completed``, etc.
        cli_choice_label_prefix: Optional label prefix that selects a
            CLI adapter. Labels matching ``<prefix><name>`` (for
            example ``cli:claude``) surface as
            ``ticket.routing_hint.cli``.
        page_size: REST page size (Plane defaults to 100).
        rate_limit_min_interval: Minimum seconds between calls per
            adapter instance.
    """

    workspace_slug: str
    project_id: str
    instance_url: str = DEFAULT_PLANE_URL
    api_token_env: str = "PLANE_API_KEY"
    state_filter: str | None = None
    state_map: dict[str, str] = field(default_factory=dict)
    cli_choice_label_prefix: str | None = None
    page_size: int = DEFAULT_PAGE_SIZE
    rate_limit_min_interval: float = 0.0


# ---------------------------------------------------------------------------
# Token + URL resolution
# ---------------------------------------------------------------------------


def _resolve_token(config: PlaneConfig) -> str:
    """Resolve the API key from ``config.api_token_env``.

    Raises:
        TrackerUnavailable: if the env var is empty or missing.
    """
    env_var = config.api_token_env or "PLANE_API_KEY"
    token = os.environ.get(env_var, "")
    if not token:
        msg = f"Plane adapter: no API key available (env var '{env_var}' is empty)"
        raise TrackerUnavailable(msg)
    return token


def _resolve_base_url(config: PlaneConfig) -> str:
    """Resolve the base URL, preferring ``PLANE_URL`` env override."""
    env_url = os.environ.get("PLANE_URL", "").strip()
    url = env_url or config.instance_url or DEFAULT_PLANE_URL
    return url.rstrip("/")


# ---------------------------------------------------------------------------
# Token bucket
# ---------------------------------------------------------------------------


class _TokenBucket:
    """Minimal cooperative token-bucket throttle.

    A single shared lock-protected next-allowed timestamp. Calls block
    until the timestamp is reached. ``min_interval == 0`` disables the
    throttle. This is intentionally simple: we lean on server-side rate
    limits + ``Retry-After`` headers for hard enforcement.
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


@dataclass
class _StateSchema:
    """Cached state lookup tables."""

    by_id: dict[str, dict[str, Any]]
    by_name: dict[str, dict[str, Any]]


class PlaneAdapter(AbstractTrackerAdapter):
    """Adapter for Plane (OSS PM tool).

    The adapter is intentionally thin: every call is a single REST
    round-trip. State schemas are discovered once and cached.

    Parameters:
        config: Adapter configuration.
        http_client: Optional pre-built ``httpx.Client`` (tests pass a
            mocked transport via ``respx``).
        token_provider: Optional callable returning the API key. The
            default reads from ``_resolve_token(config)`` on each call.
        base_url_provider: Optional callable returning the base URL.
            Defaults to ``_resolve_base_url(config)``.
    """

    name = "plane"

    def __init__(
        self,
        config: PlaneConfig,
        *,
        http_client: Any | None = None,
        token_provider: Any | None = None,
        base_url_provider: Any | None = None,
    ) -> None:
        if httpx is None:  # pragma: no cover - dep is declared in pyproject
            msg = "httpx is required for PlaneAdapter"
            raise RuntimeError(msg)
        self._config = config
        self._client: Any = http_client or httpx.Client(timeout=30.0)
        self._owns_client = http_client is None
        self._token_provider = token_provider or (lambda: _resolve_token(config))
        self._base_url_provider = base_url_provider or (lambda: _resolve_base_url(config))
        self._bucket = _TokenBucket(config.rate_limit_min_interval)
        self._states: _StateSchema | None = None

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        """Release the underlying HTTP client if we own it."""
        if self._owns_client:
            try:
                self._client.close()
            except Exception:  # pragma: no cover - best effort
                logger.debug("ignoring error while closing httpx client", exc_info=True)

    def __enter__(self) -> PlaneAdapter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- public API ---------------------------------------------------------

    def pull_open_tickets(
        self,
        filter: dict[str, Any] | None = None,
    ) -> Iterator[Ticket]:
        """Yield Plane issues as ``Ticket`` objects.

        ``filter`` keys:
            - ``state``: override ``config.state_filter`` for this call.
        """
        filter_dict = filter or {}
        state_filter = filter_dict.get("state", self._config.state_filter)

        states = self._ensure_states()
        cursor: int = 1
        page_size = max(1, min(100, self._config.page_size))

        while True:
            data = self._get(
                self._issues_path(),
                params={"per_page": page_size, "cursor": cursor},
            )
            results = data.get("results") or []
            for raw_issue in results:
                ticket = self._issue_to_ticket(raw_issue, states=states)
                if ticket is None:
                    continue
                if state_filter and ticket.status != state_filter:
                    continue
                yield ticket
            # Plane's REST cursor format: prefer ``next_cursor`` / ``next``
            # when present; fall back to incrementing the page index until
            # the result set is empty.
            next_cursor = data.get("next_cursor") or data.get("next_page")
            if next_cursor is not None and str(next_cursor) not in ("", "None"):
                try:
                    cursor = int(next_cursor)
                except (TypeError, ValueError):
                    break
                continue
            if not results or len(results) < page_size:
                break
            cursor += 1

    def add_comment(
        self,
        ticket_id: str,
        body: str,
        *,
        idempotency_key: str | None = None,
    ) -> CommentResult:
        """Post a comment on a Plane issue.

        ``idempotency_key`` is passed as the ``Idempotency-Key`` header
        so the server can dedupe retries.
        """
        path = self._comments_path(ticket_id)
        payload: dict[str, Any] = {"comment_html": body}
        headers: dict[str, str] = {}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        data = self._post(path, json=payload, extra_headers=headers)
        comment_id = str(data.get("id") or "")
        return CommentResult(comment_id=comment_id, ticket_id=ticket_id)

    def transition(
        self,
        ticket_id: str,
        status_id: str,
        *,
        idempotency_key: str | None = None,
        etag: str | None = None,
    ) -> TransitionResult:
        """Move ``ticket_id`` to ``status_id``.

        ``status_id`` can be either:
            - a Plane state UUID (preferred, opaque)
            - a state display name (resolved via the cached schema)
            - a key in ``config.state_map`` (resolved twice: through the
              map then through the schema)
        """
        states = self._ensure_states()
        state_id = self._resolve_state_id(states, status_id)

        path = self._issue_path(ticket_id)
        payload = {"state": state_id}
        headers: dict[str, str] = {}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        if etag:
            headers["If-Match"] = etag
        self._patch(path, json=payload, extra_headers=headers)
        return TransitionResult(
            ticket_id=ticket_id,
            new_status=status_id,
            etag=None,
        )

    # -- internals ----------------------------------------------------------

    def _workspace_root(self) -> str:
        slug = quote(self._config.workspace_slug, safe="")
        project = quote(self._config.project_id, safe="")
        return f"/api/v1/workspaces/{slug}/projects/{project}"

    def _issues_path(self) -> str:
        return f"{self._workspace_root()}/issues/"

    def _issue_path(self, issue_id: str) -> str:
        return f"{self._workspace_root()}/issues/{quote(issue_id, safe='')}/"

    def _comments_path(self, issue_id: str) -> str:
        return f"{self._workspace_root()}/issues/{quote(issue_id, safe='')}/comments/"

    def _states_path(self) -> str:
        return f"{self._workspace_root()}/states/"

    def _ensure_states(self) -> _StateSchema:
        if self._states is not None:
            return self._states
        data = self._get(self._states_path(), params={"per_page": 100})
        nodes: list[dict[str, Any]] = data.get("results") or data.get("states") or []
        if not isinstance(nodes, list):
            nodes = []
        # Some Plane deployments return a bare list rather than a wrapped object.
        if not nodes and isinstance(data, list):
            nodes = data
        by_id: dict[str, dict[str, Any]] = {}
        by_name: dict[str, dict[str, Any]] = {}
        for state in nodes:
            sid = str(state.get("id") or "")
            name = state.get("name") or ""
            if sid:
                by_id[sid] = state
            if name:
                by_name[name] = state
        if not by_id and not by_name:
            msg = (
                f"Plane states not found for workspace='{self._config.workspace_slug}', "
                f"project='{self._config.project_id}'"
            )
            raise TrackerUnavailable(msg)
        self._states = _StateSchema(by_id=by_id, by_name=by_name)
        return self._states

    def _resolve_state_id(self, states: _StateSchema, status_id: str) -> str:
        """Resolve ``status_id`` (id, name, or mapped name) to a state UUID."""
        if status_id in states.by_id:
            return status_id
        mapped = self._config.state_map.get(status_id, status_id)
        if mapped in states.by_id:
            return mapped
        if mapped in states.by_name:
            return str(states.by_name[mapped].get("id") or "")
        msg = f"State '{status_id}' (mapped='{mapped}') not found in project"
        raise TrackerUnavailable(msg)

    def _issue_to_ticket(
        self,
        raw_issue: dict[str, Any],
        *,
        states: _StateSchema,
    ) -> Ticket | None:
        issue_id = str(raw_issue.get("id") or "")
        if not issue_id:
            return None

        state_id = str(raw_issue.get("state") or "")
        state_name = ""
        state_node = states.by_id.get(state_id)
        if state_node:
            state_name = state_node.get("name") or ""

        labels_raw = raw_issue.get("labels") or raw_issue.get("label_details") or []
        labels: list[str] = []
        cli_choice: str | None = None
        prefix = self._config.cli_choice_label_prefix
        for label in labels_raw:
            name = _label_name(label)
            if not name:
                continue
            if prefix and name.startswith(prefix) and cli_choice is None:
                cli_choice = name[len(prefix) :]
                continue
            labels.append(name)

        base = self._base_url_provider().rstrip("/")
        external_url = (
            f"{base}/{quote(self._config.workspace_slug, safe='')}/projects/"
            f"{quote(self._config.project_id, safe='')}/issues/{quote(issue_id, safe='')}"
        )
        return Ticket(
            id=issue_id,
            external_url=external_url,
            title=str(raw_issue.get("name") or ""),
            body=str(raw_issue.get("description_html") or raw_issue.get("description") or ""),
            status=state_name,
            labels=tuple(labels),
            etag=None,
            routing_hint=RoutingHint(cli=cli_choice),
            raw={
                "sequence_id": raw_issue.get("sequence_id"),
                "state_id": state_id,
                "workspace": self._config.workspace_slug,
                "project_id": self._config.project_id,
            },
        )

    # -- HTTP --------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self._bucket.acquire()
        token = self._token_provider()
        base = self._base_url_provider()
        url = f"{base}{path}"
        headers = {
            "X-API-Key": token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)
        try:
            response = self._client.request(
                method,
                url,
                params=params,
                json=json,
                headers=headers,
            )
        except Exception as exc:  # pragma: no cover - network errors
            msg = f"Plane transport error: {exc}"
            raise TrackerUnavailable(msg) from exc

        status_code = getattr(response, "status_code", 0)
        if status_code == 412:
            msg = "Precondition Failed (etag mismatch)"
            raise OptimisticConcurrencyError(msg)
        if status_code == 429:
            retry_after = _parse_retry_after(response)
            msg = f"Plane rate-limited (status={status_code})"
            raise RateLimited(msg, retry_after=retry_after)
        if status_code >= 500:
            msg = f"Plane server error (status={status_code})"
            raise TrackerUnavailable(msg)
        if status_code >= 400:
            body = _safe_text(response)
            msg = f"Plane HTTP {status_code}: {body[:200]}"
            raise TrackerUnavailable(msg)

        if status_code == 204 or not getattr(response, "content", b""):
            return {}
        try:
            data = response.json()
        except Exception as exc:  # pragma: no cover - malformed JSON
            msg = f"Plane returned non-JSON: {exc}"
            raise TrackerUnavailable(msg) from exc
        if isinstance(data, list):
            return {"results": data}
        if not isinstance(data, dict):
            msg = f"Plane returned unexpected payload type: {type(data).__name__}"
            raise TrackerUnavailable(msg)
        return data

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("GET", path, params=params)

    def _post(
        self,
        path: str,
        *,
        json: dict[str, Any],
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return self._request("POST", path, json=json, extra_headers=extra_headers)

    def _patch(
        self,
        path: str,
        *,
        json: dict[str, Any],
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return self._request("PATCH", path, json=json, extra_headers=extra_headers)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _label_name(label: Any) -> str:
    """Extract a label name from Plane's heterogeneous label payloads."""
    if isinstance(label, dict):
        return str(label.get("name") or "")
    if label is None:
        return ""
    return str(label)


def _parse_retry_after(response: Any) -> float | None:
    """Best-effort ``Retry-After`` parser (seconds only)."""
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
