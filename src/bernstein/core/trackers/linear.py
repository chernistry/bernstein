"""Linear tracker adapter.

Implements the ``AbstractTrackerAdapter`` contract on top of Linear's
public GraphQL API at ``https://api.linear.app/graphql``.

Auth:
- Personal API key (Linear OAuth Personal API Key) via an environment
  variable. ``LINEAR_API_KEY`` is read by default; an alternative env
  variable name can be set via ``LinearConfig.api_key_env``.

Capabilities:
- ``pull_open_tickets``: paginate the configured team's issues, filter
  by an optional workflow-state name, and emit normalised ``Ticket``
  objects.
- ``add_comment``: write a comment to the underlying issue via the
  ``commentCreate`` mutation.
- ``transition``: update the issue's workflow state via the
  ``issueUpdate`` mutation. The target state can be supplied as an
  opaque state id, a state name, or a key in ``LinearConfig.state_map``.

Rate-limit handling:
- Cooperative client-side token-bucket via ``min_interval`` is supported
  through ``LinearConfig.rate_limit_min_interval``.
- HTTP 429 is translated into ``RateLimited(retry_after=...)`` using the
  ``Retry-After`` header.
- Linear's secondary rate-limit errors are returned inside a 200 with a
  GraphQL error whose ``extensions.code`` mentions ``RATELIMIT``; those
  are surfaced as ``RateLimited`` as well.
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

LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"
DEFAULT_API_KEY_ENV = "LINEAR_API_KEY"
DEFAULT_PAGE_SIZE = 50


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LinearConfig:
    """Configuration for a Linear adapter instance.

    Attributes:
        team_key: Linear team key (e.g. ``ENG``) the adapter scopes to.
            Used to look up the team's workflow states for ``transition``
            and to filter ``pull_open_tickets``.
        state_filter: Optional workflow-state name the adapter filters
            on when calling ``pull_open_tickets`` with no filter
            override (e.g. ``Todo``, ``In Progress``).
        state_map: Optional mapping from canonical Bernstein status
            names to Linear workflow-state names. ``done`` -> ``Done``,
            ``in_progress`` -> ``In Progress``, etc.
        label_routing_field: Optional Linear label prefix used to route
            tickets to a CLI adapter. When set to e.g. ``cli/``, an
            issue carrying the label ``cli/claude`` exposes
            ``ticket.routing_hint.cli = "claude"``.
        api_key_env: Environment variable name holding the Linear
            Personal API key. Defaults to ``LINEAR_API_KEY``.
        include_archived: Include archived issues in ``pull_open_tickets``.
        page_size: GraphQL issues page size. Linear caps this at 250 per
            page; the adapter clamps the configured value to that.
        rate_limit_min_interval: Minimum seconds between GraphQL calls
            per adapter instance.
    """

    team_key: str
    state_filter: str | None = None
    state_map: dict[str, str] = field(default_factory=dict)
    label_routing_field: str | None = None
    api_key_env: str = DEFAULT_API_KEY_ENV
    include_archived: bool = False
    page_size: int = DEFAULT_PAGE_SIZE
    rate_limit_min_interval: float = 0.0


# ---------------------------------------------------------------------------
# Token resolution
# ---------------------------------------------------------------------------


def _resolve_token(config: LinearConfig) -> str:
    """Resolve the Linear API key from the configured environment variable.

    Raises:
        TrackerUnavailable: if the environment variable is unset or empty.
    """
    env_var = config.api_key_env or DEFAULT_API_KEY_ENV
    token = os.environ.get(env_var, "")
    if not token:
        msg = f"Linear adapter: no API key available (env var '{env_var}' is empty)"
        raise TrackerUnavailable(msg)
    return token


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
# GraphQL queries
# ---------------------------------------------------------------------------


_QUERY_TEAM = """\
query($key: String!) {
  teams(filter: { key: { eq: $key } }, first: 1) {
    nodes {
      id
      key
      name
      states(first: 100) {
        nodes { id name type }
      }
    }
  }
}\
"""


_QUERY_ISSUES = """\
query($teamId: String!, $first: Int!, $after: String, $includeArchived: Boolean) {
  issues(
    filter: { team: { id: { eq: $teamId } } }
    first: $first
    after: $after
    includeArchived: $includeArchived
    orderBy: updatedAt
  ) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      identifier
      title
      description
      url
      updatedAt
      state { id name }
      labels(first: 20) { nodes { name } }
    }
  }
}\
"""


_MUTATION_ADD_COMMENT = """\
mutation($issueId: String!, $body: String!) {
  commentCreate(input: { issueId: $issueId, body: $body }) {
    success
    comment { id }
  }
}\
"""


_MUTATION_UPDATE_STATE = """\
mutation($issueId: String!, $stateId: String!) {
  issueUpdate(id: $issueId, input: { stateId: $stateId }) {
    success
    issue { id updatedAt }
  }
}\
"""


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


@dataclass
class _TeamSchema:
    team_id: str
    states_by_name: dict[str, dict[str, Any]]


class LinearTracker(AbstractTrackerAdapter):
    """Adapter for Linear.

    The adapter is intentionally thin: every call is a single GraphQL
    round-trip. The team schema (workflow states) is discovered once and
    cached.

    Parameters:
        config: Adapter configuration.
        http_client: Optional pre-built ``httpx.Client`` (tests pass a
            mocked transport via ``respx``).
        token_provider: Optional callable returning the auth token. The
            default reads from ``_resolve_token(config)`` on each call,
            which makes credential rotation trivial.
    """

    name = "linear"

    def __init__(
        self,
        config: LinearConfig,
        *,
        http_client: Any | None = None,
        token_provider: Any | None = None,
    ) -> None:
        if httpx is None:  # pragma: no cover - dep is declared in pyproject
            msg = "httpx is required for LinearTracker"
            raise RuntimeError(msg)
        self._config = config
        self._client: Any = http_client or httpx.Client(timeout=30.0)
        self._owns_client = http_client is None
        self._token_provider = token_provider or (lambda: _resolve_token(config))
        self._bucket = _TokenBucket(config.rate_limit_min_interval)
        self._schema: _TeamSchema | None = None

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        """Release the underlying HTTP client if we own it."""
        if self._owns_client:
            try:
                self._client.close()
            except Exception:  # pragma: no cover - best effort
                logger.debug("ignoring error while closing httpx client", exc_info=True)

    def __enter__(self) -> LinearTracker:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- public API ---------------------------------------------------------

    def pull_open_tickets(
        self,
        filter: dict[str, Any] | None = None,
    ) -> Iterator[Ticket]:
        """Yield Linear issues as ``Ticket`` objects.

        ``filter`` keys:
            - ``state``: override ``config.state_filter`` for this call.
            - ``include_archived``: bool, default
              ``config.include_archived``.
        """
        filter_dict = filter or {}
        state_filter = filter_dict.get("state", self._config.state_filter)
        include_archived = bool(filter_dict.get("include_archived", self._config.include_archived))

        schema = self._ensure_schema()
        cursor: str | None = None
        page_size = max(1, min(250, self._config.page_size))

        while True:
            data = self._graphql(
                _QUERY_ISSUES,
                {
                    "teamId": schema.team_id,
                    "first": page_size,
                    "after": cursor,
                    "includeArchived": include_archived,
                },
            )
            issues_block = (data.get("data") or {}).get("issues") or {}
            for raw_issue in issues_block.get("nodes") or []:
                ticket = self._issue_to_ticket(raw_issue)
                if ticket is None:
                    continue
                if state_filter and ticket.status != state_filter:
                    continue
                yield ticket
            page_info = issues_block.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

    def add_comment(
        self,
        ticket_id: str,
        body: str,
        *,
        idempotency_key: str | None = None,
    ) -> CommentResult:
        """Post a comment on a Linear issue.

        ``ticket_id`` is the Linear issue UUID (the ``id`` field on
        ``Ticket``). Linear's GraphQL mutation does not surface a
        ``clientMutationId`` parameter, so ``idempotency_key`` is
        appended to the body as a hidden HTML-comment marker so
        operators can de-duplicate posts client-side.
        """
        payload_body = body
        if idempotency_key:
            payload_body = f"{body}\n\n<!-- bernstein-idempotency:{idempotency_key} -->"
        variables = {"issueId": ticket_id, "body": payload_body}
        result = self._graphql(_MUTATION_ADD_COMMENT, variables)
        block = (result.get("data") or {}).get("commentCreate") or {}
        if not block.get("success"):
            msg = f"Linear commentCreate did not succeed: {block!r}"
            raise TrackerUnavailable(msg)
        comment = block.get("comment") or {}
        return CommentResult(comment_id=comment.get("id", ""), ticket_id=ticket_id)

    def transition(
        self,
        ticket_id: str,
        status_id: str,
        *,
        idempotency_key: str | None = None,
        etag: str | None = None,
    ) -> TransitionResult:
        """Move ``ticket_id`` (Linear issue id) to ``status_id``.

        ``status_id`` can be either:
            - the workflow state id (preferred, opaque)
            - the workflow state name (resolved via the cached schema)
            - a key in ``config.state_map`` (resolved twice: through the
              map then through the schema)

        ``idempotency_key`` is accepted for ``AbstractTrackerAdapter``
        contract compatibility but intentionally not forwarded: Linear's
        ``issueUpdate`` mutation is naturally idempotent on
        ``(issueId, stateId)`` and exposes no client-side key field.
        Adding a body annotation here (as ``comment`` does) would not
        bind the dedup to the transition itself, so we drop the key.
        """
        _ = idempotency_key  # contract-compat; see docstring
        schema = self._ensure_schema()
        state_id = self._resolve_state_id(schema, status_id)
        variables = {"issueId": ticket_id, "stateId": state_id}
        data = self._graphql(_MUTATION_UPDATE_STATE, variables, etag=etag)
        block = (data.get("data") or {}).get("issueUpdate") or {}
        if not block.get("success"):
            msg = f"Linear issueUpdate did not succeed: {block!r}"
            raise TrackerUnavailable(msg)
        new_etag = ((block.get("issue") or {}).get("updatedAt")) or None
        return TransitionResult(
            ticket_id=ticket_id,
            new_status=status_id,
            etag=new_etag,
        )

    # -- internals ----------------------------------------------------------

    def _ensure_schema(self) -> _TeamSchema:
        if self._schema is not None:
            return self._schema
        data = self._graphql(_QUERY_TEAM, {"key": self._config.team_key})
        teams = ((data.get("data") or {}).get("teams") or {}).get("nodes") or []
        if not teams:
            msg = f"Linear team not found: key='{self._config.team_key}'"
            raise TrackerUnavailable(msg)
        team = teams[0]
        states_by_name: dict[str, dict[str, Any]] = {}
        for state in (team.get("states") or {}).get("nodes") or []:
            name = state.get("name")
            if name:
                states_by_name[name] = state
        self._schema = _TeamSchema(
            team_id=team["id"],
            states_by_name=states_by_name,
        )
        return self._schema

    def _resolve_state_id(self, schema: _TeamSchema, status_id: str) -> str:
        """Resolve ``status_id`` (id, name, or mapped name) to a state id."""
        # Direct id match (states_by_name only has names, so fall through).
        for state in schema.states_by_name.values():
            if state.get("id") == status_id:
                return str(state["id"])
        mapped = self._config.state_map.get(status_id, status_id)
        mapped_state = schema.states_by_name.get(mapped)
        if mapped_state:
            return str(mapped_state["id"])
        msg = f"Linear state '{status_id}' (mapped='{mapped}') not found for team '{self._config.team_key}'"
        raise TrackerUnavailable(msg)

    def _issue_to_ticket(self, raw_issue: dict[str, Any]) -> Ticket | None:
        state = (raw_issue.get("state") or {}).get("name") or ""
        labels_nodes = (raw_issue.get("labels") or {}).get("nodes") or []
        labels = tuple((lab.get("name") or "") for lab in labels_nodes if lab.get("name"))
        cli_choice = self._cli_choice_from_labels(labels)
        return Ticket(
            id=raw_issue.get("id", ""),
            external_url=raw_issue.get("url", ""),
            title=raw_issue.get("title", "") or "",
            body=raw_issue.get("description", "") or "",
            status=state,
            labels=labels,
            etag=raw_issue.get("updatedAt"),
            routing_hint=RoutingHint(cli=cli_choice),
            raw={
                "identifier": raw_issue.get("identifier", ""),
                "team_key": self._config.team_key,
                "state_id": (raw_issue.get("state") or {}).get("id", ""),
            },
        )

    def _cli_choice_from_labels(self, labels: tuple[str, ...]) -> str | None:
        """Pick a CLI choice from a labelled-prefix routing field."""
        prefix = self._config.label_routing_field
        if not prefix:
            return None
        for label in labels:
            if label.startswith(prefix):
                value = label[len(prefix) :].strip()
                if value:
                    return value
        return None

    # -- HTTP --------------------------------------------------------------

    def _graphql(
        self,
        query: str,
        variables: dict[str, Any],
        *,
        etag: str | None = None,
    ) -> dict[str, Any]:
        self._bucket.acquire()
        token = self._token_provider()
        headers = {
            "Authorization": token,
            "Content-Type": "application/json",
        }
        if etag:
            headers["If-Match"] = etag
        payload = {"query": query, "variables": variables}
        try:
            response = self._client.post(
                LINEAR_GRAPHQL_URL,
                json=payload,
                headers=headers,
            )
        except Exception as exc:  # pragma: no cover - network errors
            msg = f"Linear GraphQL transport error: {exc}"
            raise TrackerUnavailable(msg) from exc

        status_code = getattr(response, "status_code", 0)
        if status_code == 412:
            msg = "Precondition Failed (etag mismatch)"
            raise OptimisticConcurrencyError(msg)
        if status_code == 429:
            retry_after = _parse_retry_after(response)
            msg = f"Linear GraphQL rate-limited (status={status_code})"
            raise RateLimited(msg, retry_after=retry_after)
        if status_code in {401, 403}:
            body = _safe_text(response)
            msg = f"Linear GraphQL auth error (status={status_code}): {body[:200]}"
            raise TrackerUnavailable(msg)
        if status_code >= 500:
            msg = f"Linear GraphQL server error (status={status_code})"
            raise TrackerUnavailable(msg)
        if status_code >= 400:
            body = _safe_text(response)
            msg = f"Linear GraphQL HTTP {status_code}: {body[:200]}"
            raise TrackerUnavailable(msg)

        try:
            data = response.json()
        except Exception as exc:  # pragma: no cover - malformed JSON
            msg = f"Linear GraphQL returned non-JSON: {exc}"
            raise TrackerUnavailable(msg) from exc

        errors = data.get("errors") if isinstance(data, dict) else None
        if errors:
            for err in errors:
                code = ""
                extensions = err.get("extensions") if isinstance(err, dict) else None
                if isinstance(extensions, dict):
                    code = str(extensions.get("code") or "").upper()
                err_type = (err.get("type") or "").upper() if isinstance(err, dict) else ""
                if "RATELIMIT" in code or "RATE_LIMIT" in code or "RATELIMIT" in err_type:
                    raise RateLimited(
                        f"Linear GraphQL rate-limit: {err.get('message')}",
                        retry_after=None,
                    )
            msg = f"Linear GraphQL errors: {errors}"
            raise TrackerUnavailable(msg)
        return data  # type: ignore[no-any-return]


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
