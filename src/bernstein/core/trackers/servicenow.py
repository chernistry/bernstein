"""ServiceNow tracker adapter.

Implements ``AbstractTrackerAdapter`` on top of the ServiceNow Table API,
targeting the ``incident`` table by default. ``change_request``,
``problem``, and custom tables are also supported via the
``table_name`` config field.

Auth:
- HTTP Basic auth using ``SERVICENOW_INSTANCE_URL``,
  ``SERVICENOW_USERNAME``, and ``SERVICENOW_PASSWORD`` environment
  variables. Basic auth is the most common path for ServiceNow Table
  API tenants; OAuth client-credentials remains available as a future
  extension.

Capabilities:
- ``pull_open_tickets``: ``GET /api/now/table/<table>`` with a
  ``sysparm_query`` filter, paginated via ``sysparm_offset`` /
  ``sysparm_limit``.
- ``add_comment``: appends to the ``work_notes`` field on the record.
- ``transition``: updates the configured state field on the record.

Rate-limit handling:
- 429 responses are surfaced as ``RateLimited`` with ``retry_after``
  parsed from the ``Retry-After`` header.
- A cooperative per-adapter min-interval throttle is available via
  ``rate_limit_min_interval``.
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

DEFAULT_TABLE_NAME = "incident"
DEFAULT_STATE_FIELD = "state"
DEFAULT_PAGE_SIZE = 50
DEFAULT_OPEN_QUERY = "active=true"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ServiceNowConfig:
    """Configuration for a ServiceNow adapter instance.

    Attributes:
        instance_url: Base URL of the ServiceNow tenant (e.g.
            ``https://dev12345.service-now.com``). If empty, the
            adapter reads ``SERVICENOW_INSTANCE_URL``.
        username_env: Environment variable name holding the basic-auth
            username. Defaults to ``SERVICENOW_USERNAME``.
        password_env: Environment variable name holding the basic-auth
            password. Defaults to ``SERVICENOW_PASSWORD``.
        table_name: Table to operate against. Common values:
            ``incident`` (default), ``change_request``, ``problem``,
            ``sn_si_incident``, or any custom-application table.
        state_field: Single-choice field used as the workflow state.
            Defaults to ``state``.
        open_query: ``sysparm_query`` clause used by ``pull_open_tickets``
            when the caller does not override it. Defaults to
            ``active=true``.
        state_map: Optional mapping from canonical status names to the
            tenant's display values. ``done`` -> ``6`` (Resolved), etc.
        page_size: ``sysparm_limit`` page size.
        rate_limit_min_interval: Minimum seconds between HTTP calls per
            adapter instance.
    """

    instance_url: str = ""
    username_env: str = "SERVICENOW_USERNAME"
    password_env: str = "SERVICENOW_PASSWORD"
    table_name: str = DEFAULT_TABLE_NAME
    state_field: str = DEFAULT_STATE_FIELD
    open_query: str = DEFAULT_OPEN_QUERY
    state_map: dict[str, str] = field(default_factory=dict)
    page_size: int = DEFAULT_PAGE_SIZE
    rate_limit_min_interval: float = 0.0


# ---------------------------------------------------------------------------
# Credential resolution
# ---------------------------------------------------------------------------


def _resolve_instance_url(config: ServiceNowConfig) -> str:
    url = config.instance_url or os.environ.get("SERVICENOW_INSTANCE_URL", "")
    if not url:
        msg = "ServiceNow adapter: SERVICENOW_INSTANCE_URL is not set"
        raise TrackerUnavailable(msg)
    return url.rstrip("/")


def _resolve_credentials(config: ServiceNowConfig) -> tuple[str, str]:
    username = os.environ.get(config.username_env, "")
    password = os.environ.get(config.password_env, "")
    if not username or not password:
        msg = (
            f"ServiceNow adapter: credentials missing (env vars "
            f"'{config.username_env}' and '{config.password_env}' must both be set)"
        )
        raise TrackerUnavailable(msg)
    return username, password


# ---------------------------------------------------------------------------
# Token bucket
# ---------------------------------------------------------------------------


class _TokenBucket:
    """Minimal cooperative token-bucket throttle.

    A single shared lock-protected next-allowed timestamp. Calls block
    until the timestamp is reached. ``min_interval == 0`` disables the
    throttle.
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


class ServiceNowTracker(AbstractTrackerAdapter):
    """Adapter for ServiceNow Table API.

    Parameters:
        config: Adapter configuration.
        http_client: Optional pre-built ``httpx.Client``. Tests pass a
            mocked transport via ``respx``.
        credential_provider: Optional callable returning the
            ``(username, password)`` tuple. The default reads from
            ``_resolve_credentials(config)`` on each call.
        instance_url_provider: Optional callable returning the instance
            URL. The default reads ``_resolve_instance_url(config)``
            once at construction time.
    """

    name = "servicenow"

    def __init__(
        self,
        config: ServiceNowConfig,
        *,
        http_client: Any | None = None,
        credential_provider: Any | None = None,
        instance_url_provider: Any | None = None,
    ) -> None:
        if httpx is None:  # pragma: no cover - dep is declared in pyproject
            msg = "httpx is required for ServiceNowTracker"
            raise RuntimeError(msg)
        self._config = config
        self._client: Any = http_client or httpx.Client(timeout=30.0)
        self._owns_client = http_client is None
        self._credential_provider = credential_provider or (lambda: _resolve_credentials(config))
        url_provider = instance_url_provider or (lambda: _resolve_instance_url(config))
        self._instance_url = url_provider()
        self._bucket = _TokenBucket(config.rate_limit_min_interval)

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        """Release the underlying HTTP client if we own it."""
        if self._owns_client:
            try:
                self._client.close()
            except Exception:  # pragma: no cover - best effort
                logger.debug("ignoring error while closing httpx client", exc_info=True)

    def __enter__(self) -> ServiceNowTracker:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- public API ---------------------------------------------------------

    def pull_open_tickets(
        self,
        filter: dict[str, Any] | None = None,
    ) -> Iterator[Ticket]:
        """Yield records as ``Ticket`` objects.

        ``filter`` keys:
            - ``sysparm_query``: override ``config.open_query`` for this
              call.
            - ``fields``: comma-separated list passed to
              ``sysparm_fields``. Optional; if omitted ServiceNow
              returns every column on the record.
        """
        filter_dict = filter or {}
        sysparm_query = filter_dict.get("sysparm_query", self._config.open_query)
        sysparm_fields = filter_dict.get("fields")
        page_size = max(1, min(1000, self._config.page_size))
        offset = 0
        while True:
            params: dict[str, Any] = {
                "sysparm_query": sysparm_query,
                "sysparm_limit": page_size,
                "sysparm_offset": offset,
                "sysparm_display_value": "all",
            }
            if sysparm_fields:
                params["sysparm_fields"] = sysparm_fields
            data = self._request(
                "GET",
                f"/api/now/table/{self._config.table_name}",
                params=params,
            )
            records = data.get("result") or []
            if not records:
                break
            for raw in records:
                yield self._record_to_ticket(raw)
            if len(records) < page_size:
                break
            offset += page_size

    def add_comment(
        self,
        ticket_id: str,
        body: str,
        *,
        idempotency_key: str | None = None,
    ) -> CommentResult:
        """Append ``body`` to the ``work_notes`` field on ``ticket_id``.

        ``ticket_id`` is the record's ``sys_id``. ServiceNow has no
        first-class comment id, so we return the record ``sys_id`` as
        the comment id; callers that need a stable comment id can store
        the optional ``idempotency_key`` they passed in.
        """
        payload: dict[str, Any] = {"work_notes": body}
        if idempotency_key is not None:
            # Append a marker so re-runs with the same key can be spotted
            # by operators scanning the journal. ServiceNow ignores the
            # marker for ordering / lookup; it is informational only.
            payload["work_notes"] = f"{body}\n[idempotency:{idempotency_key}]"
        self._request(
            "PATCH",
            f"/api/now/table/{self._config.table_name}/{ticket_id}",
            json_body=payload,
        )
        return CommentResult(comment_id=ticket_id, ticket_id=ticket_id)

    def transition(
        self,
        ticket_id: str,
        status_id: str,
        *,
        idempotency_key: str | None = None,
        etag: str | None = None,
    ) -> TransitionResult:
        """Move ``ticket_id`` (record ``sys_id``) to ``status_id``.

        ``status_id`` is resolved through ``config.state_map`` first,
        then passed through to the state field as-is. ServiceNow state
        values are commonly small integers (e.g. ``1`` = New,
        ``6`` = Resolved) but the field can also be a string.
        """
        mapped = self._config.state_map.get(status_id, status_id)
        payload = {self._config.state_field: mapped}
        if idempotency_key is not None:
            payload["x_bernstein_idempotency"] = idempotency_key
        self._request(
            "PATCH",
            f"/api/now/table/{self._config.table_name}/{ticket_id}",
            json_body=payload,
            etag=etag,
        )
        return TransitionResult(
            ticket_id=ticket_id,
            new_status=status_id,
            etag=None,
        )

    # -- internals ----------------------------------------------------------

    def _record_to_ticket(self, raw: dict[str, Any]) -> Ticket:
        """Normalise a single Table API record into a ``Ticket``.

        ServiceNow returns each field as either a bare string or an
        object with ``value`` / ``display_value`` keys when
        ``sysparm_display_value=all`` is set. We try both shapes.
        """

        def _get(name: str, *, display: bool = False) -> str:
            value = raw.get(name)
            if isinstance(value, dict):
                key = "display_value" if display else "value"
                return str(value.get(key) or value.get("value") or "")
            return "" if value is None else str(value)

        sys_id = _get("sys_id")
        number = _get("number")
        short_description = _get("short_description")
        description = _get("description")
        state = _get(self._config.state_field, display=True) or _get(self._config.state_field)
        external_url = f"{self._instance_url}/nav_to.do?uri={self._config.table_name}.do?sys_id={sys_id}"
        return Ticket(
            id=sys_id,
            external_url=external_url,
            title=short_description or number,
            body=description,
            status=state,
            labels=(),
            etag=None,
            routing_hint=RoutingHint(),
            raw={
                "number": number,
                "table": self._config.table_name,
                "sys_id": sys_id,
            },
        )

    # -- HTTP --------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        etag: str | None = None,
    ) -> dict[str, Any]:
        self._bucket.acquire()
        username, password = self._credential_provider()
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if etag:
            headers["If-Match"] = etag
        url = f"{self._instance_url}{path}"
        try:
            response = self._client.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=headers,
                auth=(username, password),
            )
        except Exception as exc:  # pragma: no cover - network errors
            msg = f"ServiceNow transport error: {exc}"
            raise TrackerUnavailable(msg) from exc

        status_code = getattr(response, "status_code", 0)
        if status_code == 412:
            msg = "Precondition Failed (etag mismatch)"
            raise OptimisticConcurrencyError(msg)
        if status_code == 429:
            retry_after = _parse_retry_after(response)
            msg = f"ServiceNow rate-limited (status={status_code})"
            raise RateLimited(msg, retry_after=retry_after)
        if status_code in {401, 403}:
            body = _safe_text(response)
            msg = f"ServiceNow auth/permission error (status={status_code}): {body[:200]}"
            raise TrackerUnavailable(msg)
        if status_code >= 500:
            msg = f"ServiceNow server error (status={status_code})"
            raise TrackerUnavailable(msg)
        if status_code >= 400:
            body = _safe_text(response)
            msg = f"ServiceNow HTTP {status_code}: {body[:200]}"
            raise TrackerUnavailable(msg)

        if status_code == 204 or not getattr(response, "content", b""):
            return {}
        try:
            data = response.json()
        except Exception as exc:  # pragma: no cover - malformed JSON
            msg = f"ServiceNow returned non-JSON: {exc}"
            raise TrackerUnavailable(msg) from exc
        if not isinstance(data, dict):
            msg = "ServiceNow returned a non-object JSON body"
            raise TrackerUnavailable(msg)
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
