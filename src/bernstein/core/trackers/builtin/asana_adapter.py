"""Asana tracker adapter.

Implements the ``AbstractTrackerAdapter`` contract on top of the public
Asana REST API (https://app.asana.com/api/1.0).

Auth:
- Personal Access Token (PAT) read from an environment variable.

Capabilities:
- ``pull_open_tickets``: paginate the configured project's tasks,
  optionally filtered by section, and emit normalised ``Ticket``
  objects.
- ``add_comment``: post a story (comment) on the task.
- ``transition``: move the task between sections. Asana sections are
  per-project workflow buckets and act as the status surface for this
  adapter. An operator-supplied ``section_map`` maps canonical status
  names to section gids.

Per-step CLI choice:
- If ``cli_choice_custom_field_gid`` is configured and a task has an
  enum value for that custom field, the adapter exposes
  ``ticket.routing_hint.cli`` so the orchestrator can route the work to
  a specific CLI adapter.

Rate-limit handling:
- Token-bucket per-instance (cooperative, advisory).
- ``RateLimited`` raised with ``retry_after`` derived from the
  ``Retry-After`` header on a 429 response.
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
    RateLimited,
    RoutingHint,
    Ticket,
    TrackerUnavailable,
    TransitionResult,
)

logger = logging.getLogger(__name__)

ASANA_API_BASE = "https://app.asana.com/api/1.0"
DEFAULT_PAGE_SIZE = 50
DEFAULT_PAT_ENV = "ASANA_PERSONAL_ACCESS_TOKEN"

# Comma-separated list of task fields requested from the API. Keeps the
# payload small while exposing everything the adapter needs to populate a
# ``Ticket``.
_TASK_OPT_FIELDS = (
    "gid,name,notes,permalink_url,completed,"
    "memberships.section.gid,memberships.section.name,"
    "tags.name,"
    "custom_fields.gid,custom_fields.name,"
    "custom_fields.enum_value.name,custom_fields.text_value,"
    "custom_fields.display_value"
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AsanaConfig:
    """Configuration for an Asana adapter instance.

    Attributes:
        workspace_gid: Asana workspace gid the project lives in.
        project_gid: Numeric/string gid of the project to pull tasks from.
        section_filter_gid: Optional section gid the adapter filters on
            when ``pull_open_tickets`` is called with no filter override.
        section_map: Optional mapping from canonical status names to
            section gids. ``done`` -> ``1201234567890123``, etc.
        cli_choice_custom_field_gid: Optional custom-field gid whose enum
            value selects a CLI adapter (``claude``, ``codex``, ...).
        include_completed: Whether to yield completed tasks. Default
            ``False`` (matches the ``completed_since=now`` semantics on
            the upstream API).
        pat_env: Environment variable name holding the Personal Access
            Token. Defaults to ``ASANA_PERSONAL_ACCESS_TOKEN``.
        page_size: REST pagination ``limit``.
        rate_limit_min_interval: Minimum seconds between API calls per
            adapter instance (cheap client-side throttle).
    """

    workspace_gid: str
    project_gid: str
    section_filter_gid: str | None = None
    section_map: dict[str, str] = field(default_factory=dict)
    cli_choice_custom_field_gid: str | None = None
    include_completed: bool = False
    pat_env: str | None = None
    page_size: int = DEFAULT_PAGE_SIZE
    rate_limit_min_interval: float = 0.0


# ---------------------------------------------------------------------------
# Token resolution
# ---------------------------------------------------------------------------


def _resolve_token(config: AsanaConfig) -> str:
    """Resolve the PAT from the configured environment variable.

    Order of precedence:
        1. ``config.pat_env`` if set.
        2. ``ASANA_PERSONAL_ACCESS_TOKEN``.

    Raises:
        TrackerUnavailable: if no token is available.
    """
    env_var = config.pat_env or DEFAULT_PAT_ENV
    token = os.environ.get(env_var, "")
    if not token:
        msg = f"Asana adapter: no token available (env var '{env_var}' is empty)"
        raise TrackerUnavailable(msg)
    return token


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


class AsanaAdapter(AbstractTrackerAdapter):
    """Adapter for Asana.

    The adapter is intentionally thin: every call is a single REST
    round-trip. Section gids are operator-supplied because Asana
    sections are per-project (not workspace-global).

    Parameters:
        config: Adapter configuration.
        http_client: Optional pre-built ``httpx.Client`` (tests pass a
            mocked transport via ``respx``).
        token_provider: Optional callable returning the PAT. The default
            reads from ``_resolve_token(config)`` on each call.
    """

    name = "asana"

    def __init__(
        self,
        config: AsanaConfig,
        *,
        http_client: Any | None = None,
        token_provider: Any | None = None,
    ) -> None:
        if httpx is None:  # pragma: no cover - dep is declared in pyproject
            msg = "httpx is required for AsanaAdapter"
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

    def __enter__(self) -> AsanaAdapter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- public API ---------------------------------------------------------

    def pull_open_tickets(
        self,
        filter: dict[str, Any] | None = None,
    ) -> Iterator[Ticket]:
        """Yield project tasks as ``Ticket`` objects.

        ``filter`` keys:
            - ``section``: override ``config.section_filter_gid`` for
              this call. Pass a section gid.
            - ``include_completed``: bool, default
              ``config.include_completed``.
        """
        filter_dict = filter or {}
        section_filter = filter_dict.get("section", self._config.section_filter_gid)
        include_completed = bool(filter_dict.get("include_completed", self._config.include_completed))
        page_size = max(1, min(100, self._config.page_size))

        # Asana exposes a ``/projects/{gid}/tasks`` endpoint that yields
        # every task in the project; we filter by section client-side so
        # operator configs that omit a section still work.
        params: dict[str, Any] = {
            "limit": page_size,
            "opt_fields": _TASK_OPT_FIELDS,
        }
        if not include_completed:
            # ``completed_since=now`` asks Asana for tasks completed
            # after "now", which effectively returns only open tasks.
            params["completed_since"] = "now"

        path = f"/projects/{self._config.project_gid}/tasks"
        next_offset: str | None = None
        while True:
            if next_offset:
                params["offset"] = next_offset
            data = self._request("GET", path, params=params)
            for raw_task in data.get("data") or []:
                ticket = self._task_to_ticket(raw_task)
                if ticket is None:
                    continue
                if section_filter and ticket.raw.get("section_gid") != section_filter:
                    continue
                yield ticket
            next_page = (data.get("next_page") or {}) if isinstance(data, dict) else {}
            next_offset = next_page.get("offset") if isinstance(next_page, dict) else None
            if not next_offset:
                break

    def add_comment(
        self,
        ticket_id: str,
        body: str,
        *,
        idempotency_key: str | None = None,
    ) -> CommentResult:
        """Post a story (comment) on ``ticket_id``.

        Asana does not have a native idempotency-key header, so
        ``idempotency_key`` is forwarded as an ``X-Idempotency-Key``
        request header. The server ignores unknown headers; the value is
        kept so that operator-side logging can correlate retries.
        """
        path = f"/tasks/{ticket_id}/stories"
        headers: dict[str, str] = {}
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        payload = {"data": {"text": body}}
        result = self._request("POST", path, json_body=payload, headers=headers)
        story = result.get("data") or {}
        return CommentResult(
            comment_id=str(story.get("gid", "")),
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
        """Move ``ticket_id`` (task gid) into the section ``status_id``.

        ``status_id`` may be either:
            - a section gid (preferred, opaque)
            - a key in ``config.section_map`` that resolves to a gid.

        Asana's REST API ignores ``etag`` on this endpoint; the
        parameter is accepted for contract conformance and discarded.
        """
        del etag
        section_gid = self._config.section_map.get(status_id, status_id)
        if not section_gid:
            msg = f"Asana adapter: empty section gid for status '{status_id}'"
            raise TrackerUnavailable(msg)
        path = f"/sections/{section_gid}/addTask"
        headers: dict[str, str] = {}
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        payload = {"data": {"task": ticket_id}}
        self._request("POST", path, json_body=payload, headers=headers)
        return TransitionResult(
            ticket_id=ticket_id,
            new_status=status_id,
            etag=None,
        )

    def update_custom_field(
        self,
        ticket_id: str,
        custom_field_gid: str,
        value: Any,
        *,
        idempotency_key: str | None = None,
    ) -> None:
        """Write ``value`` to ``custom_field_gid`` on ``ticket_id``.

        Asana custom-field writes go through ``PUT /tasks/{gid}`` with a
        ``custom_fields`` body keyed by gid. The accepted shape of
        ``value`` depends on the custom-field type (text, number, enum
        option gid, etc.); the caller is responsible for matching it.
        """
        path = f"/tasks/{ticket_id}"
        headers: dict[str, str] = {}
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        payload = {"data": {"custom_fields": {custom_field_gid: value}}}
        self._request("PUT", path, json_body=payload, headers=headers)

    # -- internals ----------------------------------------------------------

    def _task_to_ticket(self, raw_task: dict[str, Any]) -> Ticket | None:
        if not isinstance(raw_task, dict):
            return None

        section_gid = ""
        section_name = ""
        for membership in raw_task.get("memberships") or []:
            section = membership.get("section") if isinstance(membership, dict) else None
            if isinstance(section, dict) and section.get("gid"):
                section_gid = str(section.get("gid") or "")
                section_name = str(section.get("name") or "")
                break

        cli_choice: str | None = None
        if self._config.cli_choice_custom_field_gid:
            for cf in raw_task.get("custom_fields") or []:
                if not isinstance(cf, dict):
                    continue
                if str(cf.get("gid") or "") != self._config.cli_choice_custom_field_gid:
                    continue
                enum_value = cf.get("enum_value")
                if isinstance(enum_value, dict) and enum_value.get("name"):
                    cli_choice = str(enum_value["name"])
                    break
                text_value = cf.get("text_value")
                if isinstance(text_value, str) and text_value:
                    cli_choice = text_value
                    break

        labels = tuple(
            str(tag.get("name") or "")
            for tag in (raw_task.get("tags") or [])
            if isinstance(tag, dict) and tag.get("name")
        )
        return Ticket(
            id=str(raw_task.get("gid", "")),
            external_url=str(raw_task.get("permalink_url", "") or ""),
            title=str(raw_task.get("name", "") or ""),
            body=str(raw_task.get("notes", "") or ""),
            status=section_name,
            labels=labels,
            etag=None,
            routing_hint=RoutingHint(cli=cli_choice),
            raw={
                "gid": str(raw_task.get("gid", "")),
                "section_gid": section_gid,
                "section_name": section_name,
                "completed": bool(raw_task.get("completed", False)),
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
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self._bucket.acquire()
        token = self._token_provider()
        merged_headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if headers:
            merged_headers.update(headers)
        url = f"{ASANA_API_BASE}{path}"
        try:
            response = self._client.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=merged_headers,
            )
        except Exception as exc:  # pragma: no cover - network errors
            msg = f"Asana transport error: {exc}"
            raise TrackerUnavailable(msg) from exc

        status_code = getattr(response, "status_code", 0)
        if status_code == 429:
            retry_after = _parse_retry_after(response)
            raise RateLimited(
                f"Asana rate-limited (status={status_code})",
                retry_after=retry_after,
            )
        if status_code >= 500:
            msg = f"Asana server error (status={status_code})"
            raise TrackerUnavailable(msg)
        if status_code >= 400:
            body = _safe_text(response)
            msg = f"Asana HTTP {status_code}: {body[:200]}"
            raise TrackerUnavailable(msg)

        try:
            data = response.json()
        except Exception as exc:  # pragma: no cover - malformed JSON
            msg = f"Asana returned non-JSON: {exc}"
            raise TrackerUnavailable(msg) from exc
        if not isinstance(data, dict):
            msg = f"Asana returned non-object payload: {type(data).__name__}"
            raise TrackerUnavailable(msg)
        errors = data.get("errors")
        if errors:
            msg = f"Asana errors: {errors}"
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
