"""GitHub Projects v2 tracker adapter.

Implements the ``AbstractTrackerAdapter`` contract on top of the public
GitHub Projects v2 GraphQL API.

Auth:
- GitHub App installation token (reuses ``bernstein.github_app.app``).
- Personal Access Token via an environment variable.

Capabilities:
- ``pull_open_tickets``: paginate the project's items, optionally filtered
  by ``status_field`` value, and emit normalised ``Ticket`` objects.
- ``add_comment``: write the comment to the underlying Issue or PR
  (project items themselves have no comment surface).
- ``transition``: update the project item's single-select ``status_field``
  to the configured target value.

Per-step CLI choice:
- If ``cli_choice_field_name`` is configured and an item has a value for
  that field, the adapter exposes ``ticket.routing_hint.cli`` so the
  orchestrator can route the work to a specific CLI adapter.

Rate-limit handling:
- Token-bucket per-installation (cooperative, advisory).
- ``RateLimited`` raised with ``retry_after`` derived from the
  ``Retry-After`` header or ``X-RateLimit-Reset`` epoch.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
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

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
DEFAULT_STATUS_FIELD = "Status"
DEFAULT_PAGE_SIZE = 50


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GitHubProjectsV2Config:
    """Configuration for a Projects v2 adapter instance.

    Attributes:
        project_owner: Org or user login that owns the project.
        project_number: Numeric project id (the ``#42`` in the URL).
        status_field_name: Name of the single-select project field used as
            the workflow status. Default ``Status``.
        status_filter: Optional status value the adapter filters on when
            calling ``pull_open_tickets`` with no filter override.
        status_map: Optional mapping from canonical status names to the
            project's display values. ``done`` -> ``Closed``, etc.
        cli_choice_field_name: Optional name of a single-select field that
            selects a CLI adapter (``claude``, ``codex``, ``aider``, ...).
        app_id: GitHub App id (for installation-token auth).
        private_key_path: Path to the App's PEM private key.
        installation_id: Numeric installation id for the target org/user.
        pat_env: Environment variable name holding a Personal Access
            Token (``GITHUB_TOKEN`` is read by default at adapter
            construction time when no other auth is configured).
        page_size: GraphQL items page size.
        rate_limit_min_interval: Minimum seconds between GraphQL calls per
            adapter instance (cheap client-side throttle).
    """

    project_owner: str
    project_number: int
    status_field_name: str = DEFAULT_STATUS_FIELD
    status_filter: str | None = None
    status_map: dict[str, str] = field(default_factory=dict)
    cli_choice_field_name: str | None = None
    app_id: str | None = None
    private_key_path: str | None = None
    installation_id: int | None = None
    pat_env: str | None = None
    page_size: int = DEFAULT_PAGE_SIZE
    rate_limit_min_interval: float = 0.0


# ---------------------------------------------------------------------------
# Token resolution
# ---------------------------------------------------------------------------


def _resolve_token(config: GitHubProjectsV2Config) -> str:
    """Resolve the auth token for a GraphQL call.

    Order of precedence:
        1. GitHub App installation token (if ``app_id`` and
           ``installation_id`` are set).
        2. Personal Access Token from ``config.pat_env``.
        3. ``GITHUB_TOKEN`` environment variable.

    Raises:
        TrackerUnavailable: if no token source is configured.
    """
    if config.app_id and config.installation_id is not None:
        from bernstein.github_app.app import (
            GitHubAppConfig,
            create_installation_token,
        )

        private_key = ""
        if config.private_key_path:
            try:
                private_key = Path(config.private_key_path).read_text()
            except OSError as exc:
                # Normalise file IO failures to the adapter's typed error
                # surface so callers never see raw FileNotFoundError /
                # PermissionError from the auth path.
                msg = f"GitHub App private key could not be read: {config.private_key_path}"
                raise TrackerUnavailable(msg) from exc
        else:
            env_pk = os.environ.get("GITHUB_APP_PRIVATE_KEY", "")
            if env_pk and Path(env_pk).is_file():
                try:
                    private_key = Path(env_pk).read_text()
                except OSError as exc:
                    msg = f"GitHub App private key could not be read: {env_pk}"
                    raise TrackerUnavailable(msg) from exc
            else:
                private_key = env_pk
        if not private_key:
            msg = "GitHub App private key not found for tracker auth"
            raise TrackerUnavailable(msg)
        app_config = GitHubAppConfig(
            app_id=config.app_id,
            private_key=private_key,
            webhook_secret=os.environ.get("GITHUB_WEBHOOK_SECRET", "unused"),
        )
        return create_installation_token(app_config, config.installation_id)

    env_var = config.pat_env or "GITHUB_TOKEN"
    token = os.environ.get(env_var, "")
    if not token:
        msg = (
            f"GitHub Projects v2 adapter: no token available (env var '{env_var}' is empty and no App auth configured)"
        )
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
# GraphQL queries
# ---------------------------------------------------------------------------


_QUERY_PROJECT_ID = """\
query($login: String!, $number: Int!) {
  organization(login: $login) {
    projectV2(number: $number) {
      id
      fields(first: 50) {
        nodes {
          ... on ProjectV2FieldCommon { id name dataType }
          ... on ProjectV2SingleSelectField {
            id name dataType
            options { id name }
          }
        }
      }
    }
  }
  user(login: $login) {
    projectV2(number: $number) {
      id
      fields(first: 50) {
        nodes {
          ... on ProjectV2FieldCommon { id name dataType }
          ... on ProjectV2SingleSelectField {
            id name dataType
            options { id name }
          }
        }
      }
    }
  }
}\
"""


_QUERY_ITEMS = """\
query($projectId: ID!, $first: Int!, $after: String) {
  node(id: $projectId) {
    ... on ProjectV2 {
      items(first: $first, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          content {
            __typename
            ... on Issue {
              id number title body url
              repository { nameWithOwner }
              labels(first: 20) { nodes { name } }
            }
            ... on PullRequest {
              id number title body url
              repository { nameWithOwner }
              labels(first: 20) { nodes { name } }
            }
            ... on DraftIssue { id title body }
          }
          fieldValues(first: 20) {
            nodes {
              __typename
              ... on ProjectV2ItemFieldSingleSelectValue {
                name
                field { ... on ProjectV2FieldCommon { name } }
              }
              ... on ProjectV2ItemFieldTextValue {
                text
                field { ... on ProjectV2FieldCommon { name } }
              }
            }
          }
        }
      }
    }
  }
}\
"""


_MUTATION_ADD_COMMENT = """\
mutation($subjectId: ID!, $body: String!, $clientMutationId: String) {
  addComment(input: { subjectId: $subjectId, body: $body, clientMutationId: $clientMutationId }) {
    commentEdge { node { id } }
  }
}\
"""


_MUTATION_UPDATE_STATUS = """\
mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!, $clientMutationId: String) {
  updateProjectV2ItemFieldValue(input: {
    projectId: $projectId,
    itemId: $itemId,
    fieldId: $fieldId,
    value: { singleSelectOptionId: $optionId },
    clientMutationId: $clientMutationId
  }) {
    projectV2Item { id }
  }
}\
"""


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


@dataclass
class _FieldSchema:
    project_id: str
    fields_by_name: dict[str, dict[str, Any]]


class GitHubProjectsV2Adapter(AbstractTrackerAdapter):
    """Adapter for GitHub Projects v2.

    The adapter is intentionally thin: every call is a single GraphQL
    round-trip. Field schemas are discovered once and cached.

    Parameters:
        config: Adapter configuration.
        http_client: Optional pre-built ``httpx.Client`` (tests pass a
            mocked transport via ``respx``).
        token_provider: Optional callable returning the auth token. The
            default reads from ``_resolve_token(config)`` on each call,
            which makes installation-token rotation trivial.
    """

    name = "github_projects_v2"

    def __init__(
        self,
        config: GitHubProjectsV2Config,
        *,
        http_client: Any | None = None,
        token_provider: Any | None = None,
    ) -> None:
        if httpx is None:  # pragma: no cover - dep is declared in pyproject
            msg = "httpx is required for GitHubProjectsV2Adapter"
            raise RuntimeError(msg)
        self._config = config
        self._client: Any = http_client or httpx.Client(timeout=30.0)
        self._owns_client = http_client is None
        self._token_provider = token_provider or (lambda: _resolve_token(config))
        self._bucket = _TokenBucket(config.rate_limit_min_interval)
        self._schema: _FieldSchema | None = None

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        """Release the underlying HTTP client if we own it."""
        if self._owns_client:
            try:
                self._client.close()
            except Exception:  # pragma: no cover - best effort
                logger.debug("ignoring error while closing httpx client", exc_info=True)

    def __enter__(self) -> GitHubProjectsV2Adapter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- public API ---------------------------------------------------------

    def pull_open_tickets(
        self,
        filter: dict[str, Any] | None = None,
    ) -> Iterator[Ticket]:
        """Yield project items as ``Ticket`` objects.

        ``filter`` keys:
            - ``status``: override ``config.status_filter`` for this call.
            - ``include_drafts``: bool, default False.
        """
        filter_dict = filter or {}
        status_filter = filter_dict.get("status", self._config.status_filter)
        include_drafts = bool(filter_dict.get("include_drafts", False))

        schema = self._ensure_schema()
        cursor: str | None = None
        page_size = max(1, min(100, self._config.page_size))

        while True:
            data = self._graphql(
                _QUERY_ITEMS,
                {
                    "projectId": schema.project_id,
                    "first": page_size,
                    "after": cursor,
                },
            )
            node = (data.get("data") or {}).get("node") or {}
            items_block = node.get("items") or {}
            for raw_item in items_block.get("nodes") or []:
                ticket = self._item_to_ticket(raw_item, include_drafts=include_drafts)
                if ticket is None:
                    continue
                if status_filter and ticket.status != status_filter:
                    continue
                yield ticket
            page_info = items_block.get("pageInfo") or {}
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
        """Post a comment on the underlying Issue or PR.

        ``ticket_id`` must be the *content* node id (an Issue or PR id),
        not the project item id. The orchestrator passes
        ``ticket.raw["content_id"]`` for this. We also accept the project
        item id when the raw content is cached on this adapter instance.
        """
        subject_id = ticket_id
        variables = {
            "subjectId": subject_id,
            "body": body,
            "clientMutationId": idempotency_key,
        }
        result = self._graphql(_MUTATION_ADD_COMMENT, variables)
        edge = (result.get("data") or {}).get("addComment", {}).get("commentEdge", {})
        comment_id = ((edge or {}).get("node") or {}).get("id", "")
        return CommentResult(comment_id=comment_id, ticket_id=ticket_id)

    def transition(
        self,
        ticket_id: str,
        status_id: str,
        *,
        idempotency_key: str | None = None,
        etag: str | None = None,
    ) -> TransitionResult:
        """Move ``ticket_id`` (project item id) to ``status_id``.

        ``status_id`` can be either:
            - the option id (preferred, opaque)
            - the option name (resolved via the cached schema)
            - a key in ``config.status_map`` (resolved twice: through the
              map then through the schema)
        """
        schema = self._ensure_schema()
        status_field = schema.fields_by_name.get(self._config.status_field_name)
        if not status_field:
            msg = f"Status field '{self._config.status_field_name}' not found in project schema"
            raise TrackerUnavailable(msg)

        option_id = self._resolve_option_id(status_field, status_id)
        variables = {
            "projectId": schema.project_id,
            "itemId": ticket_id,
            "fieldId": status_field["id"],
            "optionId": option_id,
            "clientMutationId": idempotency_key,
        }
        self._graphql(_MUTATION_UPDATE_STATUS, variables, etag=etag)
        return TransitionResult(
            ticket_id=ticket_id,
            new_status=status_id,
            etag=None,
        )

    # -- internals ----------------------------------------------------------

    def _ensure_schema(self) -> _FieldSchema:
        if self._schema is not None:
            return self._schema
        data = self._graphql(
            _QUERY_PROJECT_ID,
            {
                "login": self._config.project_owner,
                "number": self._config.project_number,
            },
        )
        block = data.get("data") or {}
        project = (block.get("organization") or {}).get("projectV2") or (block.get("user") or {}).get("projectV2")
        if not project:
            msg = f"Project not found: owner='{self._config.project_owner}', number={self._config.project_number}"
            raise TrackerUnavailable(msg)
        fields_by_name: dict[str, dict[str, Any]] = {}
        for field_node in (project.get("fields") or {}).get("nodes") or []:
            name = field_node.get("name")
            if name:
                fields_by_name[name] = field_node
        self._schema = _FieldSchema(
            project_id=project["id"],
            fields_by_name=fields_by_name,
        )
        return self._schema

    def _resolve_option_id(self, field_node: dict[str, Any], status_id: str) -> str:
        """Resolve ``status_id`` (id, name, or mapped name) to an option id."""
        mapped = self._config.status_map.get(status_id, status_id)
        for option in field_node.get("options") or []:
            if mapped in (option.get("id"), option.get("name")):
                return str(option["id"])
        msg = f"Status '{status_id}' (mapped='{mapped}') not found in field '{field_node.get('name')}'"
        raise TrackerUnavailable(msg)

    def _item_to_ticket(
        self,
        raw_item: dict[str, Any],
        *,
        include_drafts: bool,
    ) -> Ticket | None:
        content = raw_item.get("content") or {}
        kind = content.get("__typename")
        # Skip items whose content is null or of an unknown typename. We
        # only know how to materialise issues, PRs, and draft issues; any
        # other shape produces tickets with empty title/body/content ids
        # and pollutes downstream consumers.
        if kind not in {"Issue", "PullRequest", "DraftIssue"}:
            return None
        if kind == "DraftIssue" and not include_drafts:
            return None

        status = ""
        cli_choice: str | None = None
        for fv in (raw_item.get("fieldValues") or {}).get("nodes") or []:
            field_meta = fv.get("field") or {}
            field_name = field_meta.get("name")
            if field_name == self._config.status_field_name:
                status = fv.get("name") or fv.get("text") or ""
            elif self._config.cli_choice_field_name and field_name == self._config.cli_choice_field_name:
                cli_choice = fv.get("name") or fv.get("text")

        labels = tuple(
            (lab.get("name") or "") for lab in ((content.get("labels") or {}).get("nodes") or []) if lab.get("name")
        )
        return Ticket(
            id=raw_item.get("id", ""),
            external_url=content.get("url", ""),
            title=content.get("title", ""),
            body=content.get("body", "") or "",
            status=status,
            labels=labels,
            etag=None,
            routing_hint=RoutingHint(cli=cli_choice),
            raw={
                "content_id": content.get("id", ""),
                "content_type": kind or "",
                "repository": (content.get("repository") or {}).get("nameWithOwner", ""),
                "number": content.get("number"),
            },
        )

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
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if etag:
            headers["If-Match"] = etag
        payload = {"query": query, "variables": variables}
        try:
            response = self._client.post(
                GITHUB_GRAPHQL_URL,
                json=payload,
                headers=headers,
            )
        except Exception as exc:  # pragma: no cover - network errors
            msg = f"GitHub GraphQL transport error: {exc}"
            raise TrackerUnavailable(msg) from exc

        status_code = getattr(response, "status_code", 0)
        if status_code == 412:
            msg = "Precondition Failed (etag mismatch)"
            raise OptimisticConcurrencyError(msg)
        if status_code in {403, 429}:
            retry_after = _parse_retry_after(response)
            msg = f"GitHub GraphQL rate-limited (status={status_code})"
            raise RateLimited(msg, retry_after=retry_after)
        if status_code >= 500:
            msg = f"GitHub GraphQL server error (status={status_code})"
            raise TrackerUnavailable(msg)
        if status_code >= 400:
            body = _safe_text(response)
            msg = f"GitHub GraphQL HTTP {status_code}: {body[:200]}"
            raise TrackerUnavailable(msg)

        try:
            data = response.json()
        except Exception as exc:  # pragma: no cover - malformed JSON
            msg = f"GitHub GraphQL returned non-JSON: {exc}"
            raise TrackerUnavailable(msg) from exc

        errors = data.get("errors") if isinstance(data, dict) else None
        if errors:
            # Secondary-rate-limit errors come back as a 200 with a typed error.
            for err in errors:
                err_type = (err.get("type") or "").upper()
                if "RATE_LIMITED" in err_type or "ABUSE" in err_type:
                    raise RateLimited(
                        f"GitHub GraphQL secondary rate-limit: {err.get('message')}",
                        retry_after=None,
                    )
            msg = f"GitHub GraphQL errors: {errors}"
            raise TrackerUnavailable(msg)
        return data  # type: ignore[no-any-return]


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
