"""Tracker webhook receiver.

Inbound-webhook ingestion mode for tracker adapters. Trackers that
support webhooks (Jira Cloud, GitHub, GitLab, Linear, Plane, ...) can
push events to ``POST /webhooks/trackers/<adapter_name>`` which produces
the same normalised ``Ticket`` objects the polling path emits.

Polling remains the default and stays available; webhook ingestion is
opt-in per adapter via ``bernstein.yaml: trackers.<name>.webhook``.

Design notes:

* Per-adapter handlers implement two pure functions:
  ``verify_signature(headers, body, secret) -> bool`` and
  ``parse_event(headers, payload) -> TrackerEvent | None``.  Both are
  small and free of orchestrator dependencies so they can be unit-tested
  without a live server.
* Replay protection is layered.  In-memory: a bounded ordered-set of
  recently seen delivery ids.  On-disk: an append-only JSONL ledger that
  survives restarts.  The receiver checks both layers; a delivery id
  found in either causes a 200 OK no-op response so trackers do not
  retry indefinitely.
* Recovery: ``replay_recent_via_poll`` runs a single poll at startup for
  events newer than ``last_processed_ts`` so missed deliveries during
  bernstein downtime are not lost.

The module deliberately exposes small primitives.  The FastAPI route is
in :mod:`bernstein.core.routes.tracker_webhooks` and is the only place
that touches the running task store.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from bernstein.core.security.sanitize import sanitize_log
from bernstein.core.trackers.contract import RoutingHint, Ticket
from bernstein.core.webhook_signatures import verify_hmac_sha256

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrackerEvent:
    """Normalised inbound tracker event.

    ``ticket`` is the same dataclass produced by the polling path so the
    orchestrator can feed events into the same downstream queue without
    branching on transport.

    Attributes:
        adapter: Short adapter name (``jira_cloud``, ``github``, ...).
        action: Event action (``created``, ``updated``, ``transitioned``).
        ticket: Normalised ticket payload.
        delivery_id: Stable per-delivery identifier used for replay
            protection.  Falls back to a hash of the raw body when the
            tracker does not send one.
        received_ts: Unix seconds the receiver accepted the event.
    """

    adapter: str
    action: str
    ticket: Ticket
    delivery_id: str
    received_ts: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Handler protocol + registry
# ---------------------------------------------------------------------------


VerifyFn = Callable[[dict[str, str], bytes, str], bool]
ParseFn = Callable[[dict[str, str], dict[str, Any]], "TrackerEvent | None"]
DeliveryIdFn = Callable[[dict[str, str], bytes], str]


@dataclass(frozen=True)
class WebhookHandler:
    """Per-adapter webhook handler bundle.

    Each entry is a tiny pure-function trio so adapters can stay in their
    own modules and unit-test their parsing without spinning up the
    server.
    """

    adapter: str
    verify_signature: VerifyFn
    parse_event: ParseFn
    delivery_id: DeliveryIdFn


_HANDLERS: dict[str, WebhookHandler] = {}


def register_handler(handler: WebhookHandler) -> None:
    """Register a per-adapter webhook handler.

    Re-registration with the same adapter name overwrites the previous
    entry; this is useful for tests and for plugin reload.
    """

    _HANDLERS[handler.adapter] = handler


def get_handler(adapter: str) -> WebhookHandler | None:
    """Return the handler registered for ``adapter`` or ``None``."""

    return _HANDLERS.get(adapter)


def list_handlers() -> list[str]:
    """Return the sorted list of currently registered adapter names."""

    return sorted(_HANDLERS)


# ---------------------------------------------------------------------------
# Replay protection
# ---------------------------------------------------------------------------


class ReplayLedger:
    """Bounded in-memory + on-disk ledger of seen delivery ids.

    The in-memory layer is an ``OrderedDict`` capped at ``max_entries``;
    on overflow the oldest delivery id is evicted.  The on-disk layer is
    an append-only JSONL file that is loaded on construction so restarts
    keep replay protection.  Writes are best-effort: a disk failure logs
    a warning and falls back to the in-memory layer rather than rejecting
    the inbound event (the alternative is losing legitimate webhooks).
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        max_entries: int = 4096,
    ) -> None:
        self._lock = threading.Lock()
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._max = max_entries
        self._path = path
        self._load_from_disk()

    def _load_from_disk(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    delivery_id = entry.get("delivery_id")
                    ts = entry.get("ts")
                    if not isinstance(delivery_id, str) or not isinstance(ts, (int, float)):
                        continue
                    self._seen[delivery_id] = float(ts)
                    self._seen.move_to_end(delivery_id)
                    while len(self._seen) > self._max:
                        self._seen.popitem(last=False)
        except OSError as exc:
            logger.warning("ReplayLedger: failed to load %s: %s", self._path, exc)

    def seen(self, delivery_id: str) -> bool:
        """Return ``True`` when ``delivery_id`` has already been recorded."""

        with self._lock:
            return delivery_id in self._seen

    def remember(self, delivery_id: str, *, ts: float | None = None) -> bool:
        """Record ``delivery_id``.  Return ``True`` if it was new.

        The on-disk append is best-effort; failures log a warning but do
        not raise so a transient disk error does not surface as a 5xx to
        the tracker.
        """

        ts_value = float(ts if ts is not None else time.time())
        with self._lock:
            if delivery_id in self._seen:
                # Refresh recency so the LRU keeps active deliveries hot.
                self._seen.move_to_end(delivery_id)
                return False
            self._seen[delivery_id] = ts_value
            while len(self._seen) > self._max:
                self._seen.popitem(last=False)
        if self._path is not None:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"delivery_id": delivery_id, "ts": ts_value}) + "\n")
            except OSError as exc:
                logger.warning("ReplayLedger: append failed for %s: %s", self._path, exc)
        return True


# ---------------------------------------------------------------------------
# Receiver
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WebhookConfig:
    """Per-adapter webhook config loaded from ``bernstein.yaml``.

    Attributes:
        enabled: Whether the webhook endpoint accepts deliveries for this
            adapter.  When ``False`` the receiver returns 503 so a tracker
            stops retrying.
        secret_env: Environment variable that holds the shared HMAC
            secret.  The receiver looks the variable up at request time
            so secret rotation does not require a process restart.
        public_url_base: Reverse-proxy URL operators wire into the
            tracker (advisory; consumed by docs, not by the receiver).
    """

    enabled: bool = False
    secret_env: str = ""
    public_url_base: str = ""


class WebhookReceiver:
    """Stateful receiver that turns inbound HTTP requests into ``TrackerEvent``.

    The receiver owns a :class:`ReplayLedger` and a per-adapter
    :class:`WebhookConfig` map.  Handlers live in the module-level
    registry so adapter packages can register themselves at import time
    without holding a reference to a singleton.
    """

    def __init__(
        self,
        *,
        ledger: ReplayLedger | None = None,
        configs: dict[str, WebhookConfig] | None = None,
    ) -> None:
        self._ledger = ledger or ReplayLedger()
        self._configs: dict[str, WebhookConfig] = dict(configs or {})

    @property
    def ledger(self) -> ReplayLedger:
        return self._ledger

    def configure(self, adapter: str, config: WebhookConfig) -> None:
        """Register or replace the webhook config for ``adapter``."""

        self._configs[adapter] = config

    def get_config(self, adapter: str) -> WebhookConfig:
        """Return the webhook config for ``adapter`` (defaults to disabled)."""

        return self._configs.get(adapter, WebhookConfig())

    def receive(
        self,
        adapter: str,
        headers: dict[str, str],
        body: bytes,
    ) -> ReceiveResult:
        """Verify, parse, and replay-check a single inbound delivery.

        Returns a :class:`ReceiveResult`; the caller (FastAPI route)
        renders the appropriate HTTP status.  This method is sync because
        verification + parsing are CPU-only; the route is async so it can
        delegate any I/O elsewhere.
        """

        handler = get_handler(adapter)
        if handler is None:
            return ReceiveResult(status="unknown_adapter")

        config = self.get_config(adapter)
        if not config.enabled:
            return ReceiveResult(status="disabled")

        secret = os.environ.get(config.secret_env, "") if config.secret_env else ""
        if not secret:
            return ReceiveResult(status="not_configured")

        lower_headers = {k.lower(): v for k, v in headers.items()}

        if not handler.verify_signature(lower_headers, body, secret):
            return ReceiveResult(status="bad_signature")

        delivery_id = handler.delivery_id(lower_headers, body)
        if self._ledger.seen(delivery_id):
            return ReceiveResult(status="replay", delivery_id=delivery_id)

        try:
            payload = json.loads(body or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return ReceiveResult(status="bad_payload", delivery_id=delivery_id)

        if not isinstance(payload, dict):
            return ReceiveResult(status="bad_payload", delivery_id=delivery_id)

        try:
            event = handler.parse_event(lower_headers, payload)
        except Exception as exc:  # boundary
            # The exception text can echo payload fragments; sanitize so
            # an attacker-controlled body cannot inject CR/LF into log
            # lines via the parser-failure path.
            logger.debug(
                "Parser raised for adapter=%s: %s",
                sanitize_log(adapter),
                sanitize_log(str(exc)),
            )
            return ReceiveResult(status="bad_payload", delivery_id=delivery_id)

        # Record only after successful parse so a flaky parser does not
        # poison the ledger and silently swallow legitimate retries.
        self._ledger.remember(delivery_id)

        if event is None:
            return ReceiveResult(status="ignored", delivery_id=delivery_id)

        # Parsers populate a best-effort delivery_id from header lookups
        # but the receiver-side derivation is the source of truth.
        canonical_event = (
            event
            if event.delivery_id == delivery_id
            else TrackerEvent(
                adapter=event.adapter,
                action=event.action,
                ticket=event.ticket,
                delivery_id=delivery_id,
                received_ts=event.received_ts,
            )
        )
        return ReceiveResult(status="accepted", delivery_id=delivery_id, event=canonical_event)


@dataclass(frozen=True)
class ReceiveResult:
    """Outcome of :meth:`WebhookReceiver.receive`."""

    status: str
    delivery_id: str | None = None
    event: TrackerEvent | None = None


# ---------------------------------------------------------------------------
# Generic helpers reused by per-adapter handlers
# ---------------------------------------------------------------------------


def _body_hash(body: bytes) -> str:
    """Stable hash of the body used as a fallback delivery id."""

    return hashlib.sha256(body).hexdigest()


def _header(headers: dict[str, str], name: str) -> str:
    return headers.get(name.lower(), "")


# ---------------------------------------------------------------------------
# Adapter implementations
# ---------------------------------------------------------------------------


# --- Jira Cloud ------------------------------------------------------------

_JIRA_BROWSE_TMPL = "https://{domain}/browse/{key}"


def _jira_cloud_verify(headers: dict[str, str], body: bytes, secret: str) -> bool:
    # Jira Cloud's "Webhook secret" feature posts the secret in the
    # standard Atlassian ``x-hub-signature`` header (HMAC-SHA256, hex,
    # ``sha256=`` prefix), matching GitHub's convention.  Atlassian's
    # docs accept either ``X-Hub-Signature`` or ``X-Atlassian-Webhook-Identifier``
    # depending on integration kind; we accept both prefixes.
    signature = _header(headers, "x-hub-signature-256") or _header(headers, "x-hub-signature")
    if not signature:
        return False
    return verify_hmac_sha256(body, signature, secret, prefix="sha256=")


def _jira_cloud_delivery_id(headers: dict[str, str], body: bytes) -> str:
    delivery = _header(headers, "x-atlassian-webhook-identifier")
    if delivery:
        return f"jira_cloud:{delivery}"
    return f"jira_cloud:{_body_hash(body)}"


def _jira_cloud_parse(headers: dict[str, str], payload: dict[str, Any]) -> TrackerEvent | None:
    issue = payload.get("issue") or {}
    key = str(issue.get("key") or "")
    if not key:
        return None
    fields = issue.get("fields") or {}
    status_obj = fields.get("status") or {}
    status_name = str(status_obj.get("name") or "")
    labels_raw = fields.get("labels") or []
    labels = tuple(str(x) for x in labels_raw if x)
    summary = str(fields.get("summary") or "")
    description = fields.get("description")
    # ADF blob (dict) is dropped here; downstream renderers consume the raw
    # payload from ``ticket.raw`` if they need to surface formatted bodies.
    body_text = "" if isinstance(description, dict) else str(description or "")
    # Best-effort browse URL using the issue self link.
    self_link = str(issue.get("self") or "")
    domain = ""
    if "://" in self_link:
        domain = self_link.split("://", 1)[1].split("/", 1)[0]
    external_url = _JIRA_BROWSE_TMPL.format(domain=domain, key=key) if domain else ""
    ticket = Ticket(
        id=key,
        external_url=external_url,
        title=summary,
        body=body_text,
        status=status_name,
        labels=labels,
        etag=None,
        raw={"issue_id": str(issue.get("id") or ""), "key": key},
    )
    return TrackerEvent(
        adapter="jira_cloud",
        action=str(payload.get("webhookEvent") or "updated"),
        ticket=ticket,
        delivery_id=_jira_cloud_delivery_id(headers, b""),
    )


# --- GitHub ----------------------------------------------------------------


def _github_verify(headers: dict[str, str], body: bytes, secret: str) -> bool:
    signature = _header(headers, "x-hub-signature-256")
    if not signature:
        return False
    return verify_hmac_sha256(body, signature, secret, prefix="sha256=")


def _github_delivery_id(headers: dict[str, str], body: bytes) -> str:
    delivery = _header(headers, "x-github-delivery")
    if delivery:
        return f"github:{delivery}"
    return f"github:{_body_hash(body)}"


def _github_parse(headers: dict[str, str], payload: dict[str, Any]) -> TrackerEvent | None:
    event_type = _header(headers, "x-github-event") or "unknown"
    if event_type not in {"issues", "issue_comment", "pull_request"}:
        return None
    issue = payload.get("issue") or payload.get("pull_request") or {}
    if not issue:
        return None
    issue_id = str(issue.get("id") or issue.get("node_id") or "")
    number = issue.get("number")
    repo = payload.get("repository") or {}
    repo_full = str(repo.get("full_name") or "")
    if number is not None and repo_full:
        ticket_id = f"{repo_full}#{number}"
    elif issue_id:
        ticket_id = issue_id
    else:
        return None
    external_url = str(issue.get("html_url") or "")
    title = str(issue.get("title") or "")
    body_text = str(issue.get("body") or "")
    state = str(issue.get("state") or "")
    labels_raw = issue.get("labels") or []
    labels = tuple(str(x.get("name") if isinstance(x, dict) else x) for x in labels_raw if x)
    ticket = Ticket(
        id=ticket_id,
        external_url=external_url,
        title=title,
        body=body_text,
        status=state,
        labels=labels,
        raw={"repo": repo_full, "number": number, "issue_id": issue_id},
    )
    return TrackerEvent(
        adapter="github",
        action=str(payload.get("action") or event_type),
        ticket=ticket,
        delivery_id=_github_delivery_id(headers, b""),
    )


# --- GitLab ----------------------------------------------------------------


def _gitlab_verify(headers: dict[str, str], body: bytes, secret: str) -> bool:
    # GitLab posts the secret token verbatim in ``x-gitlab-token``.
    # constant-time compare to avoid early-exit timing leakage.
    del body
    provided = _header(headers, "x-gitlab-token")
    if not provided or not secret:
        return False
    return hmac.compare_digest(provided, secret)


def _gitlab_delivery_id(headers: dict[str, str], body: bytes) -> str:
    delivery = _header(headers, "x-gitlab-event-uuid")
    if delivery:
        return f"gitlab:{delivery}"
    return f"gitlab:{_body_hash(body)}"


def _gitlab_parse(headers: dict[str, str], payload: dict[str, Any]) -> TrackerEvent | None:
    kind = str(payload.get("object_kind") or "")
    if kind not in {"issue", "note"}:
        return None
    attrs = payload.get("object_attributes") or {}
    if kind == "note":
        issue_obj = payload.get("issue") or {}
        if not issue_obj:
            return None
        attrs = {**issue_obj, **{"action": attrs.get("action", "commented")}}
    iid = attrs.get("iid")
    project = payload.get("project") or {}
    project_path = str(project.get("path_with_namespace") or project.get("name") or "")
    if iid is None or not project_path:
        return None
    ticket_id = f"{project_path}#{iid}"
    external_url = str(attrs.get("url") or "")
    title = str(attrs.get("title") or "")
    body_text = str(attrs.get("description") or "")
    state = str(attrs.get("state") or attrs.get("status") or "")
    labels_raw = payload.get("labels") or []
    labels = tuple(str(x.get("title") if isinstance(x, dict) else x) for x in labels_raw if x)
    ticket = Ticket(
        id=ticket_id,
        external_url=external_url,
        title=title,
        body=body_text,
        status=state,
        labels=labels,
        raw={"project": project_path, "iid": iid},
    )
    return TrackerEvent(
        adapter="gitlab",
        action=str(attrs.get("action") or kind),
        ticket=ticket,
        delivery_id=_gitlab_delivery_id(headers, b""),
    )


# --- Linear ----------------------------------------------------------------


def _linear_verify(headers: dict[str, str], body: bytes, secret: str) -> bool:
    signature = _header(headers, "linear-signature")
    if not signature:
        return False
    # Linear sends raw hex without prefix.
    return verify_hmac_sha256(body, signature, secret, prefix="")


def _linear_delivery_id(headers: dict[str, str], body: bytes) -> str:
    # Linear sends ``linear-delivery`` on each event.
    delivery = _header(headers, "linear-delivery")
    if delivery:
        return f"linear:{delivery}"
    return f"linear:{_body_hash(body)}"


def _linear_parse(headers: dict[str, str], payload: dict[str, Any]) -> TrackerEvent | None:
    data = payload.get("data") or {}
    issue_id = str(data.get("id") or "")
    if not issue_id:
        return None
    identifier = str(data.get("identifier") or issue_id)
    title = str(data.get("title") or "")
    body_text = str(data.get("description") or "")
    state_obj = data.get("state") or {}
    state_name = str(state_obj.get("name") or "")
    labels_raw = data.get("labels") or []
    if isinstance(labels_raw, dict):
        # Linear webhook payloads sometimes wrap labels in ``{"nodes": [...]}``.
        labels_raw = labels_raw.get("nodes") or []
    labels = tuple(str(x.get("name") if isinstance(x, dict) else x) for x in labels_raw if x)
    external_url = str(data.get("url") or "")
    ticket = Ticket(
        id=identifier,
        external_url=external_url,
        title=title,
        body=body_text,
        status=state_name,
        labels=labels,
        routing_hint=RoutingHint(),
        raw={"id": issue_id, "identifier": identifier},
    )
    return TrackerEvent(
        adapter="linear",
        action=str(payload.get("action") or payload.get("type") or "updated"),
        ticket=ticket,
        delivery_id=_linear_delivery_id(headers, b""),
    )


# --- Plane -----------------------------------------------------------------


def _plane_verify(headers: dict[str, str], body: bytes, secret: str) -> bool:
    # Plane's webhook header is ``x-plane-signature`` carrying a hex
    # HMAC-SHA256 of the raw body.  No prefix.
    signature = _header(headers, "x-plane-signature")
    if not signature:
        return False
    return verify_hmac_sha256(body, signature, secret, prefix="")


def _plane_delivery_id(headers: dict[str, str], body: bytes) -> str:
    delivery = _header(headers, "x-plane-delivery")
    if delivery:
        return f"plane:{delivery}"
    return f"plane:{_body_hash(body)}"


def _plane_parse(headers: dict[str, str], payload: dict[str, Any]) -> TrackerEvent | None:
    data = payload.get("data") or payload.get("issue") or {}
    issue_id = str(data.get("id") or "")
    if not issue_id:
        return None
    sequence_id = data.get("sequence_id")
    project_id = str(data.get("project") or data.get("project_id") or "")
    ticket_id = f"{project_id}#{sequence_id}" if project_id and sequence_id is not None else issue_id
    title = str(data.get("name") or data.get("title") or "")
    body_text = str(data.get("description_stripped") or data.get("description") or "")
    state = str(data.get("state") or data.get("state_detail", {}).get("name") or "")
    labels_raw = data.get("labels") or []
    labels = tuple(str(x) for x in labels_raw if x)
    external_url = str(data.get("url") or "")
    ticket = Ticket(
        id=ticket_id,
        external_url=external_url,
        title=title,
        body=body_text,
        status=state,
        labels=labels,
        raw={"id": issue_id, "project": project_id, "sequence_id": sequence_id},
    )
    return TrackerEvent(
        adapter="plane",
        action=str(payload.get("action") or payload.get("event") or "updated"),
        ticket=ticket,
        delivery_id=_plane_delivery_id(headers, b""),
    )


# ---------------------------------------------------------------------------
# Default handler registration
# ---------------------------------------------------------------------------


def register_builtin_handlers() -> None:
    """Register the built-in tracker webhook handlers.

    Idempotent - safe to call multiple times.  Adapter implementations
    that ship outside the core package can register additional handlers
    via :func:`register_handler` from their own ``__init__`` hooks.
    """

    register_handler(
        WebhookHandler(
            adapter="jira_cloud",
            verify_signature=_jira_cloud_verify,
            parse_event=_jira_cloud_parse,
            delivery_id=_jira_cloud_delivery_id,
        )
    )
    register_handler(
        WebhookHandler(
            adapter="github",
            verify_signature=_github_verify,
            parse_event=_github_parse,
            delivery_id=_github_delivery_id,
        )
    )
    register_handler(
        WebhookHandler(
            adapter="gitlab",
            verify_signature=_gitlab_verify,
            parse_event=_gitlab_parse,
            delivery_id=_gitlab_delivery_id,
        )
    )
    register_handler(
        WebhookHandler(
            adapter="linear",
            verify_signature=_linear_verify,
            parse_event=_linear_parse,
            delivery_id=_linear_delivery_id,
        )
    )
    register_handler(
        WebhookHandler(
            adapter="plane",
            verify_signature=_plane_verify,
            parse_event=_plane_parse,
            delivery_id=_plane_delivery_id,
        )
    )


# Register defaults at import time so the FastAPI route does not need to
# coordinate ordering with the server bootstrap.
register_builtin_handlers()


# ---------------------------------------------------------------------------
# Startup-poll recovery
# ---------------------------------------------------------------------------


def replay_recent_via_poll(
    adapter: Any,
    *,
    last_processed_ts: float,
    sink: Callable[[Ticket], None],
    now: Callable[[], float] | None = None,
) -> int:
    """Poll once and feed any ticket newer than ``last_processed_ts`` to ``sink``.

    Used at process start to catch events that the tracker tried to
    deliver while bernstein was down.  The tracker adapter's
    ``pull_open_tickets`` already paginates the upstream API, so we just
    iterate and filter by an ``updated_at`` claim on the raw payload -
    adapters that do not populate that field simply replay all open
    tickets, which is the safe default.

    Returns the number of tickets handed to ``sink``.
    """

    del now  # reserved for clock injection in tests; currently unused
    count = 0
    pull = getattr(adapter, "pull_open_tickets", None)
    if pull is None:
        return 0
    for ticket in pull():
        raw = getattr(ticket, "raw", None) or {}
        ts_field = raw.get("updated_at") or raw.get("updatedAt")
        if isinstance(ts_field, (int, float)) and ts_field <= last_processed_ts:
            continue
        sink(ticket)
        count += 1
    return count


__all__ = [
    "ReceiveResult",
    "ReplayLedger",
    "TrackerEvent",
    "WebhookConfig",
    "WebhookHandler",
    "WebhookReceiver",
    "get_handler",
    "list_handlers",
    "register_builtin_handlers",
    "register_handler",
    "replay_recent_via_poll",
]
