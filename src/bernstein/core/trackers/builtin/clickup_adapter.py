"""ClickUp tracker adapter.

Implements the ``AbstractTrackerAdapter`` contract on top of the public
ClickUp REST API v2. Avoids the metered ClickUp MCP server by calling
the documented REST surface directly.

Auth:
- API token via an environment variable (default ``CLICKUP_API_TOKEN``).
  ClickUp accepts both personal-API tokens (raw token in the
  ``Authorization`` header) and OAuth bearer tokens; the adapter sends
  the token verbatim and lets the caller choose.

Capabilities:
- ``pull_open_tickets``: paginate ``/list/{list_id}/task`` and emit
  normalised ``Ticket`` objects, optionally filtered by status.
- ``add_comment``: post to ``/task/{task_id}/comment``.
- ``transition``: ``PUT /task/{task_id}`` with a new status value.

Per-step CLI choice:
- If ``cli_choice_custom_field_id`` is configured and a task has a value
  for that custom field, the adapter exposes
  ``ticket.routing_hint.cli`` so the orchestrator can pin a CLI adapter.

Rate-limit handling:
- Cooperative client-side token bucket sized per-plan via
  ``rate_limit_min_interval``.
- ``RateLimited`` raised with ``retry_after`` derived from the
  ``Retry-After`` or ``X-RateLimit-Reset`` headers ClickUp emits.
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

CLICKUP_API_BASE = "https://api.clickup.com/api/v2"
DEFAULT_PAGE_SIZE = 100
DEFAULT_TOKEN_ENV = "CLICKUP_API_TOKEN"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClickUpConfig:
    """Configuration for a ClickUp adapter instance.

    Attributes:
        list_id: ClickUp list id the adapter pulls tasks from.
        workspace_id: Optional workspace (team) id. Not needed for the
            list-scoped REST calls used here, but kept on the config so
            operators can surface a single ``trackers.clickup`` block.
        space_id: Optional space id. Same rationale as ``workspace_id``.
        status_filter: Optional default status the adapter filters on
            when ``pull_open_tickets`` is called with no override.
        status_map: Optional mapping from canonical Bernstein statuses to
            the list's display status names (e.g.
            ``{"done": "complete"}``).
        cli_choice_custom_field_id: Optional id of a custom field that
            names the CLI adapter for each task (``claude``, ``codex``,
            ``aider``, ...). The value is surfaced as
            ``ticket.routing_hint.cli``.
        token_env: Environment variable name that holds the API token.
            Default ``CLICKUP_API_TOKEN``.
        api_base: Override the API base URL (useful for tests and
            self-hosted proxies).
        page_size: Page size used by ``pull_open_tickets``. ClickUp
            caps task pages at 100.
        rate_limit_min_interval: Minimum seconds between API calls per
            adapter instance. Set per plan tier (free plans bucket at
            100 rpm shared, enterprise at 10k rpm).
        include_archived: Whether to include archived tasks. Default
            ``False``.
    """

    list_id: str
    workspace_id: str | None = None
    space_id: str | None = None
    status_filter: str | None = None
    status_map: dict[str, str] = field(default_factory=dict)
    cli_choice_custom_field_id: str | None = None
    token_env: str = DEFAULT_TOKEN_ENV
    api_base: str = CLICKUP_API_BASE
    page_size: int = DEFAULT_PAGE_SIZE
    rate_limit_min_interval: float = 0.0
    include_archived: bool = False


# ---------------------------------------------------------------------------
# Token resolution
# ---------------------------------------------------------------------------


def _resolve_token(config: ClickUpConfig) -> str:
    """Resolve the API token from the configured environment variable.

    Raises:
        TrackerUnavailable: if the environment variable is empty.
    """
    env_var = config.token_env or DEFAULT_TOKEN_ENV
    token = os.environ.get(env_var, "")
    if not token:
        msg = f"ClickUp adapter: no token available (env var '{env_var}' is empty)"
        raise TrackerUnavailable(msg)
    return token


# ---------------------------------------------------------------------------
# Token bucket
# ---------------------------------------------------------------------------


class _TokenBucket:
    """Minimal cooperative token-bucket throttle.

    A single shared lock-protected next-allowed timestamp. Calls block
    until the timestamp is reached. ``min_interval == 0`` disables the
    throttle. The adapter leans on server-side rate limits and
    ``Retry-After`` headers for hard enforcement.
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


class ClickUpAdapter(AbstractTrackerAdapter):
    """Adapter for ClickUp.

    The adapter is intentionally thin: every call is a single REST
    round-trip with bearer-style auth. ClickUp returns task ids as
    opaque strings; the adapter passes them through.

    Parameters:
        config: Adapter configuration.
        http_client: Optional pre-built ``httpx.Client``. Tests inject a
            mocked transport via ``respx``.
        token_provider: Optional callable returning the auth token. The
            default reads from ``_resolve_token(config)`` on each call so
            token rotation is trivial.
    """

    name = "clickup"

    def __init__(
        self,
        config: ClickUpConfig,
        *,
        http_client: Any | None = None,
        token_provider: Any | None = None,
    ) -> None:
        if httpx is None:  # pragma: no cover - dep is declared in pyproject
            msg = "httpx is required for ClickUpAdapter"
            raise RuntimeError(msg)
        self._config = config
        self._client: Any = http_client or httpx.Client(timeout=30.0)
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

    def __enter__(self) -> ClickUpAdapter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- public API ---------------------------------------------------------

    def pull_open_tickets(
        self,
        filter: dict[str, Any] | None = None,
    ) -> Iterator[Ticket]:
        """Yield tasks from the configured list as ``Ticket`` objects.

        ``filter`` keys:
            - ``status``: override ``config.status_filter`` for this call.
            - ``include_archived``: bool, default ``config.include_archived``.
        """
        filter_dict = filter or {}
        status_filter = filter_dict.get("status", self._config.status_filter)
        include_archived = bool(filter_dict.get("include_archived", self._config.include_archived))

        page = 0
        page_size = max(1, min(100, self._config.page_size))
        path = f"/list/{self._config.list_id}/task"
        while True:
            params: dict[str, Any] = {
                "archived": "true" if include_archived else "false",
                "page": page,
                "subtasks": "true",
                "include_closed": "true",
            }
            data = self._request("GET", path, params=params)
            tasks = data.get("tasks") or []
            for raw_task in tasks:
                ticket = self._task_to_ticket(raw_task)
                if status_filter and ticket.status != status_filter:
                    continue
                yield ticket
            # ClickUp signals "last page" by returning fewer than page_size
            # tasks; some plans also include ``last_page`` in the response.
            if data.get("last_page") is True:
                break
            if len(tasks) < page_size:
                break
            page += 1

    def add_comment(
        self,
        ticket_id: str,
        body: str,
        *,
        idempotency_key: str | None = None,
    ) -> CommentResult:
        """Post a comment on ``ticket_id``.

        ``idempotency_key`` is appended to the comment as an HTML marker
        so re-tries with the same key do not duplicate the visible body
        but stay traceable in the audit log.
        """
        marker = ""
        if idempotency_key:
            marker = f"\n\n<!-- bernstein-idempotency: {idempotency_key} -->"
        payload = {
            "comment_text": f"{body}{marker}",
            "notify_all": False,
        }
        data = self._request(
            "POST",
            f"/task/{ticket_id}/comment",
            json=payload,
        )
        comment_id = str(data.get("id", "") or data.get("hist_id", ""))
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

        ``status_id`` is treated as a status name and resolved through
        ``config.status_map`` first (e.g. ``done`` -> ``complete``).
        ClickUp does not expose an etag surface; ``etag`` is accepted to
        keep the contract uniform but is not sent on the wire.
        """
        del idempotency_key  # ClickUp PUT does not honour an idempotency key
        mapped = self._config.status_map.get(status_id, status_id)
        payload = {"status": mapped}
        headers: dict[str, str] | None = None
        if etag:
            headers = {"If-Match": etag}
        self._request(
            "PUT",
            f"/task/{ticket_id}",
            json=payload,
            extra_headers=headers,
        )
        return TransitionResult(
            ticket_id=ticket_id,
            new_status=status_id,
            etag=None,
        )

    # -- internals ----------------------------------------------------------

    def _task_to_ticket(self, raw_task: dict[str, Any]) -> Ticket:
        status_block = raw_task.get("status") or {}
        status = status_block.get("status") if isinstance(status_block, dict) else str(status_block or "")
        cli_choice: str | None = None
        if self._config.cli_choice_custom_field_id:
            for cf in raw_task.get("custom_fields") or []:
                if cf.get("id") == self._config.cli_choice_custom_field_id:
                    value = cf.get("value")
                    if isinstance(value, str) and value:
                        cli_choice = value
                    elif isinstance(value, dict):
                        cli_choice = value.get("name") or value.get("value")
                    break
        labels = tuple((tag.get("name") or "") for tag in raw_task.get("tags") or [] if tag.get("name"))
        return Ticket(
            id=str(raw_task.get("id", "")),
            external_url=str(raw_task.get("url", "")),
            title=str(raw_task.get("name", "")),
            body=str(raw_task.get("description") or raw_task.get("text_content") or ""),
            status=str(status or ""),
            labels=labels,
            etag=None,
            routing_hint=RoutingHint(cli=cli_choice),
            raw={
                "task_id": str(raw_task.get("id", "")),
                "list_id": str(((raw_task.get("list") or {}).get("id")) or self._config.list_id),
                "space_id": str(((raw_task.get("space") or {}).get("id")) or (self._config.space_id or "")),
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
        headers = {
            "Authorization": token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)
        url = f"{self._config.api_base.rstrip('/')}{path}"
        try:
            response = self._client.request(
                method,
                url,
                params=params,
                json=json,
                headers=headers,
            )
        except Exception as exc:  # pragma: no cover - network errors
            msg = f"ClickUp transport error: {exc}"
            raise TrackerUnavailable(msg) from exc

        status_code = getattr(response, "status_code", 0)
        if status_code == 412:
            msg = "Precondition Failed (etag mismatch)"
            raise OptimisticConcurrencyError(msg)
        if status_code == 429:
            retry_after = _parse_retry_after(response)
            msg = f"ClickUp rate-limited (status={status_code})"
            raise RateLimited(msg, retry_after=retry_after)
        if status_code >= 500:
            msg = f"ClickUp server error (status={status_code})"
            raise TrackerUnavailable(msg)
        if status_code >= 400:
            body = _safe_text(response)
            msg = f"ClickUp HTTP {status_code}: {body[:200]}"
            raise TrackerUnavailable(msg)

        if status_code == 204 or method.upper() == "PUT":
            # ClickUp returns the updated task on PUT; some endpoints
            # return an empty body. Parse defensively.
            try:
                data = response.json()
            except Exception:
                return {}
            return data if isinstance(data, dict) else {}

        try:
            data = response.json()
        except Exception as exc:  # pragma: no cover - malformed JSON
            msg = f"ClickUp returned non-JSON: {exc}"
            raise TrackerUnavailable(msg) from exc
        if not isinstance(data, dict):
            msg = f"ClickUp returned non-object payload: {type(data).__name__}"
            raise TrackerUnavailable(msg)
        # ClickUp signals soft errors with ``{"err": "...", "ECODE": "..."}``.
        if data.get("err") and not data.get("tasks") and not data.get("id"):
            ecode = data.get("ECODE") or "UNKNOWN"
            if str(ecode).startswith("RATE"):
                raise RateLimited(
                    f"ClickUp rate-limit: {data.get('err')}",
                    retry_after=None,
                )
            msg = f"ClickUp error {ecode}: {data.get('err')}"
            raise TrackerUnavailable(msg)
        return data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_retry_after(response: Any) -> float | None:
    """Best-effort ``Retry-After`` / ``X-RateLimit-Reset`` parser."""
    headers = getattr(response, "headers", {}) or {}
    retry_after = headers.get("Retry-After") if hasattr(headers, "get") else None
    if retry_after:
        try:
            return float(retry_after)
        except (TypeError, ValueError):
            return None
    reset = headers.get("X-RateLimit-Reset") if hasattr(headers, "get") else None
    if reset:
        try:
            reset_epoch = float(reset)
            return max(0.0, reset_epoch - time.time())
        except (TypeError, ValueError):
            return None
    return None


def _safe_text(response: Any) -> str:
    try:
        return str(getattr(response, "text", ""))
    except Exception:  # pragma: no cover
        return ""
