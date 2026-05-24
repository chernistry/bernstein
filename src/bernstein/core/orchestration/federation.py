"""Multi-tracker federation layer.

Vendor tracker AI today is tenant-locked: Linear Agent reads only
Linear, Jira Rovo reads only Jira, ClickUp Brain reads only ClickUp.
Real engineering teams typically run two to four trackers concurrently
(an issue tracker, a PM tool, a knowledge base, a CI/CD board) and the
useful work increasingly crosses those surfaces. The federation layer
lets one Bernstein orchestrator instance pull tickets from many
configured tracker adapters, detect cross-references between them, and
dispatch a single agent run that can act across the joined surface.

What this module ships
----------------------
* :class:`LinkDetector` protocol plus three default implementations:
  URL-based, custom-field-based, and comment-mention-based.
* :class:`FederatedTicketGraph` - a small per-run graph of
  ``(tracker, ticket_id)`` nodes and labelled directed edges.
* :class:`FederationBuilder` - assembles the graph by running detectors
  across the union of adapter outputs.
* :class:`FederationDispatcher` - the read/comment/transition entry
  point a single agent run uses to act across the linked surface,
  guarded by a per-role allow-list and emitting an audit record per
  cross-tracker action.
* :class:`FederationConfig` - typed view over ``bernstein.yaml``'s
  ``federation`` block.

What this module deliberately omits
-----------------------------------
* Auto-discovery of which trackers a team uses (separate UX ticket).
* Cross-tracker ticket creation - read/comment/transition only in v1.
* Bi-directional link sync - one-way detection only.
* Cross-tenant federation across organisations.

The audit hook
--------------
Each cross-tracker write produces a :class:`CrossTrackerAuditRecord`
containing ``tracker_name_from`` (the originating tracker context the
agent reasoned from), ``tracker_name_to`` (the target adapter that
received the write), the ``link_kind`` that justified the edge, and the
ticket ids on each side. The default sink appends JSONL to
``.sdd/lineage/cross-tracker-audit.jsonl``; tests inject an in-memory
sink instead.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Protocol, cast, runtime_checkable

from bernstein.core.orchestration.federation_contract import (
    Ticket,
    TrackerAdapter,
    TrackerReadOnlyError,
    TrackerUnknownError,
)

__all__ = [
    "DEFAULT_AUDIT_RELPATH",
    "CommentMentionDetector",
    "CrossTrackerAuditRecord",
    "CustomFieldDetector",
    "FederatedTicketGraph",
    "FederationBuilder",
    "FederationConfig",
    "FederationDispatcher",
    "FederationError",
    "FederationPermissionError",
    "GraphEdge",
    "GraphNode",
    "LinkDetector",
    "LinkRef",
    "URLDetector",
    "write_audit_record",
]


log = logging.getLogger(__name__)


type _StringTuple = tuple[str, ...]


_EMPTY_STRING_TUPLE: Final[_StringTuple] = ()

DEFAULT_AUDIT_RELPATH: Final[Path] = Path("lineage") / "cross-tracker-audit.jsonl"
"""Path under ``.sdd/`` where cross-tracker audit records land by default."""


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class FederationError(Exception):
    """Base class for federation-layer errors."""


class FederationPermissionError(FederationError):
    """Raised when ``cross_tracker_dispatch.allow`` denies a write."""


# ---------------------------------------------------------------------------
# Link detection
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LinkRef:
    """A single detected cross-reference.

    Attributes
    ----------
    ref_kind:
        Free-form label identifying which detector produced the ref
        (e.g. ``"url"``, ``"custom_field"``, ``"comment_mention"``).
    target_tracker:
        Name of the adapter the ref points at.
    target_id:
        Adapter-local ticket id on the destination side.
    source_text:
        Verbatim slice of input text that triggered the match. Used by
        the audit log and by debug rendering.
    """

    ref_kind: str
    target_tracker: str
    target_id: str
    source_text: str


@runtime_checkable
class LinkDetector(Protocol):
    """A pluggable link detector.

    Implementations receive the source ticket plus the iterable of all
    configured adapters and return zero or more :class:`LinkRef`
    instances. Detectors MUST be side-effect-free.
    """

    @property
    def name(self) -> str:
        """Stable detector name used in config."""
        ...

    def detect(self, source: Ticket, adapters: Sequence[TrackerAdapter]) -> Iterable[LinkRef]:
        """Yield detected refs from ``source`` to other tickets."""
        ...


@dataclass(frozen=True, slots=True)
class URLDetector:
    """Detect refs by matching adapter ``tracker_uri_base`` prefixes.

    The detector inspects the ticket body and every comment body for
    occurrences of each known adapter's ``tracker_uri_base`` and
    extracts the trailing id segment. URLs hosting the source adapter
    itself are ignored - they reference the ticket we already have.
    """

    name: str = "url"

    def detect(self, source: Ticket, adapters: Sequence[TrackerAdapter]) -> Iterable[LinkRef]:
        haystacks = [source.body, *(c.body for c in source.comments)]
        for adapter in adapters:
            base = getattr(adapter, "tracker_uri_base", "")
            if not base or adapter.name == source.tracker:
                continue
            for text in haystacks:
                for target_id in _extract_id_after_prefix(text, base):
                    yield LinkRef(
                        ref_kind="url",
                        target_tracker=adapter.name,
                        target_id=target_id,
                        source_text=f"{base}{target_id}",
                    )


@dataclass(frozen=True, slots=True)
class CustomFieldDetector:
    """Detect refs via ``External-Link``-shaped custom fields.

    Many trackers expose a "linked URL" custom field on tickets. The
    detector walks ``Ticket.custom_fields`` looking for keys that match
    ``field_names`` (case-insensitive) and parses each value as one or
    more cross-tracker URLs.
    """

    field_names: tuple[str, ...] = ("external-link", "external_link", "links")
    name: str = "custom_field"

    def detect(self, source: Ticket, adapters: Sequence[TrackerAdapter]) -> Iterable[LinkRef]:
        wanted = {name.lower() for name in self.field_names}
        candidates: list[str] = [value for key, value in source.custom_fields.items() if key.lower() in wanted]
        if not candidates:
            return
        for adapter in adapters:
            base = getattr(adapter, "tracker_uri_base", "")
            if not base or adapter.name == source.tracker:
                continue
            for value in candidates:
                for target_id in _extract_id_after_prefix(value, base):
                    yield LinkRef(
                        ref_kind="custom_field",
                        target_tracker=adapter.name,
                        target_id=target_id,
                        source_text=f"{base}{target_id}",
                    )


@dataclass(frozen=True, slots=True)
class CommentMentionDetector:
    """Detect refs via prefix-style comment mentions.

    The detector recognises adapter-prefixed ids such as ``JIRA-1234``
    or ``LIN-456``. Prefixes are configured per adapter via
    ``mention_prefixes``; a default map covers the most common cases.
    Plain ``#1234`` mentions can be opted in by passing
    ``hash_default_tracker`` so that the detector knows which adapter
    the bare ``#`` belongs to.
    """

    mention_prefixes: Mapping[str, str] = field(
        default_factory=lambda: {
            "LIN": "linear",
            "JIRA": "jira",
            "ENG": "linear",
            "PROJ": "github_projects",
        }
    )
    hash_default_tracker: str = ""
    name: str = "comment_mention"

    def detect(self, source: Ticket, adapters: Sequence[TrackerAdapter]) -> Iterable[LinkRef]:
        adapter_names = {a.name for a in adapters}
        haystacks = [source.body, *(c.body for c in source.comments)]
        for text in haystacks:
            for prefix, tracker_name in self.mention_prefixes.items():
                if tracker_name == source.tracker:
                    continue
                if tracker_name not in adapter_names:
                    continue
                pattern = rf"\b{re.escape(prefix)}-(\d+)\b"
                for match in re.finditer(pattern, text):
                    target_id = f"{prefix}-{match.group(1)}"
                    yield LinkRef(
                        ref_kind="comment_mention",
                        target_tracker=tracker_name,
                        target_id=target_id,
                        source_text=match.group(0),
                    )
            if (
                self.hash_default_tracker
                and self.hash_default_tracker != source.tracker
                and self.hash_default_tracker in adapter_names
            ):
                for match in re.finditer(r"(?<!\w)#(\d+)\b", text):
                    yield LinkRef(
                        ref_kind="comment_mention",
                        target_tracker=self.hash_default_tracker,
                        target_id=match.group(1),
                        source_text=match.group(0),
                    )


def _extract_id_after_prefix(text: str, prefix: str) -> Iterator[str]:
    """Yield ids that follow ``prefix`` in ``text``.

    The id segment is everything from the character after ``prefix`` up
    to the next non-id character (whitespace, slash, query separator).
    """

    if not prefix or not text:
        return
    start = 0
    while True:
        index = text.find(prefix, start)
        if index < 0:
            return
        tail = text[index + len(prefix) :]
        cleaned: list[str] = []
        for char in tail:
            if char.isalnum() or char in "-_":
                cleaned.append(char)
                continue
            break
        token = "".join(cleaned).strip("-_")
        if token:
            yield token
        start = index + len(prefix) + max(len(cleaned), 1)


DEFAULT_DETECTORS: Final[tuple[LinkDetector, ...]] = (
    URLDetector(),
    CustomFieldDetector(),
    CommentMentionDetector(),
)


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GraphNode:
    """A node in the federated ticket graph."""

    tracker: str
    ticket_id: str

    def key(self) -> tuple[str, str]:
        return (self.tracker, self.ticket_id)


@dataclass(frozen=True, slots=True)
class GraphEdge:
    """A directed labelled edge between two ticket nodes."""

    source: GraphNode
    target: GraphNode
    ref_kind: str
    source_text: str


@dataclass
class FederatedTicketGraph:
    """A small in-memory graph of nodes and edges for one agent run.

    The graph is intentionally lightweight: federations of more than a
    few hundred linked tickets per run are unusual, and the agent only
    needs to render a single block of context from it. The structure is
    therefore ``dict[node_key, GraphNode]`` plus a list of edges.
    """

    nodes: dict[tuple[str, str], GraphNode] = field(default_factory=dict[tuple[str, str], GraphNode])
    edges: list[GraphEdge] = field(default_factory=list[GraphEdge])

    def add_ticket(self, ticket: Ticket) -> GraphNode:
        """Insert a ticket as a node, returning the canonical instance."""

        node = GraphNode(tracker=ticket.tracker, ticket_id=ticket.ticket_id)
        self.nodes.setdefault(node.key(), node)
        return self.nodes[node.key()]

    def add_edge(self, source: GraphNode, target: GraphNode, ref: LinkRef) -> None:
        self.nodes.setdefault(source.key(), source)
        self.nodes.setdefault(target.key(), target)
        self.edges.append(
            GraphEdge(
                source=source,
                target=target,
                ref_kind=ref.ref_kind,
                source_text=ref.source_text,
            )
        )

    def neighbours(self, node: GraphNode) -> list[GraphNode]:
        """Return outbound neighbours of ``node`` in deterministic order."""

        seen: dict[tuple[str, str], GraphNode] = {}
        for edge in self.edges:
            if edge.source.key() == node.key():
                seen.setdefault(edge.target.key(), edge.target)
        return sorted(seen.values(), key=lambda n: n.key())

    def render_context(self) -> str:
        """Return a stable string the agent run consumes as context.

        The render is deterministic so trace fixtures can pin against
        it. One line per node with its outbound edges grouped beneath.
        """

        if not self.nodes:
            return "federation: empty\n"
        lines: list[str] = ["federation:"]
        for key in sorted(self.nodes):
            node = self.nodes[key]
            lines.append(f"  - node: {node.tracker}:{node.ticket_id}")
            outbound = [e for e in self.edges if e.source.key() == key]
            outbound.sort(key=lambda e: (e.target.tracker, e.target.ticket_id, e.ref_kind))
            for edge in outbound:
                lines.append(
                    f"    -> {edge.target.tracker}:{edge.target.ticket_id} ({edge.ref_kind}: {edge.source_text})"
                )
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FederationConfig:
    """Typed view over the ``federation`` block in ``bernstein.yaml``.

    ``cross_tracker_dispatch_allow`` maps an agent role name (e.g.
    ``"backend"``) to the set of tracker names that role is allowed to
    write to during a federated run. A missing role means deny-all.
    Wildcard ``"*"`` may be used to grant any-tracker write.
    """

    linked_trackers: tuple[str, ...] = ()
    link_detector_names: tuple[str, ...] = ("url", "custom_field", "comment_mention")
    cross_tracker_dispatch_allow: Mapping[str, frozenset[str]] = field(default_factory=dict[str, frozenset[str]])

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> FederationConfig:
        """Build from a parsed YAML mapping. Tolerant of missing keys."""

        linked = _to_string_tuple(raw.get("linked_trackers"))
        detectors_raw = raw.get("link_detectors")
        detectors: tuple[str, ...]
        if detectors_raw is None:
            detectors = ("url", "custom_field", "comment_mention")
        else:
            detectors = _to_string_tuple(detectors_raw)
        allow_raw = raw.get("cross_tracker_dispatch")
        allow_block: object = {}
        if isinstance(allow_raw, Mapping):
            allow_block = cast("Mapping[object, object]", allow_raw).get("allow", {})
        allow: dict[str, frozenset[str]] = {}
        if isinstance(allow_block, Mapping):
            for role, trackers in cast("Mapping[object, object]", allow_block).items():
                if not isinstance(role, str):
                    continue
                allow[role] = frozenset(_to_string_tuple(trackers))
        return cls(
            linked_trackers=linked,
            link_detector_names=detectors,
            cross_tracker_dispatch_allow=allow,
        )

    def is_allowed(self, role: str, tracker: str) -> bool:
        """Return True iff ``role`` may write to ``tracker``."""

        scopes = self.cross_tracker_dispatch_allow.get(role)
        if scopes is None:
            return False
        return "*" in scopes or tracker in scopes


def _to_string_tuple(value: object) -> _StringTuple:
    if value is None:
        return _EMPTY_STRING_TUPLE
    if isinstance(value, str):
        return (value,)
    try:
        values = iter(cast("Iterable[object]", value))
    except TypeError:
        return _EMPTY_STRING_TUPLE
    return tuple(item for item in values if isinstance(item, str))


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


@dataclass
class FederationBuilder:
    """Build a :class:`FederatedTicketGraph` from a set of adapters.

    The builder iterates each adapter's ticket list once, registers
    every ticket as a node, then runs every configured detector against
    every ticket. Detector output is normalised against the known
    adapter set so refs pointing at unknown trackers are dropped (with
    a debug log line).
    """

    adapters: Sequence[TrackerAdapter]
    detectors: Sequence[LinkDetector] = DEFAULT_DETECTORS

    def build(self) -> FederatedTicketGraph:
        graph = FederatedTicketGraph()
        adapter_map = {a.name: a for a in self.adapters}
        for adapter in self.adapters:
            for ticket in adapter.list_tickets():
                source_node = graph.add_ticket(ticket)
                for detector in self.detectors:
                    for ref in detector.detect(ticket, self.adapters):
                        if ref.target_tracker not in adapter_map:
                            log.debug(
                                "federation: dropping ref to unknown tracker %s",
                                ref.target_tracker,
                            )
                            continue
                        target_node = GraphNode(
                            tracker=ref.target_tracker,
                            ticket_id=ref.target_id,
                        )
                        graph.add_edge(source_node, target_node, ref)
        return graph


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CrossTrackerAuditRecord:
    """One JSONL row describing a cross-tracker action.

    The shape intentionally mirrors the lineage merge audit record so
    operators can grep both files with the same field names.
    """

    event: str
    timestamp: float
    role: str
    tracker_name_from: str
    tracker_name_to: str
    ticket_id_from: str
    ticket_id_to: str
    link_kind: str
    action: str
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "event": self.event,
            "timestamp": self.timestamp,
            "role": self.role,
            "tracker_name_from": self.tracker_name_from,
            "tracker_name_to": self.tracker_name_to,
            "ticket_id_from": self.ticket_id_from,
            "ticket_id_to": self.ticket_id_to,
            "link_kind": self.link_kind,
            "action": self.action,
            "detail": self.detail,
        }


AuditSink = Callable[[CrossTrackerAuditRecord], None]


def write_audit_record(record: CrossTrackerAuditRecord, root: Path) -> Path:
    """Append ``record`` to the default JSONL sink under ``root``.

    ``root`` is typically ``Path(".sdd")``. The audit file's parent is
    created if missing. Returns the path written to.
    """

    path = root / DEFAULT_AUDIT_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record.to_dict(), sort_keys=True, separators=(",", ":"))
    # Emit the trailing newline in the same write() so concurrent writers
    # cannot interleave a half-record into the JSONL file.
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return path


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


@dataclass
class FederationDispatcher:
    """Read/comment/transition entry point for federated agent runs.

    The dispatcher binds together the per-run :class:`FederatedTicketGraph`,
    the adapter map, the :class:`FederationConfig` allow-list, and an
    audit sink. Agents call into the dispatcher rather than into the
    raw adapters so every cross-tracker action lands in the audit log.
    """

    graph: FederatedTicketGraph
    adapters: Mapping[str, TrackerAdapter]
    config: FederationConfig
    role: str
    audit_sink: AuditSink
    _clock: Callable[[], float] = field(default=time.time, repr=False)

    def context_for_run(self) -> str:
        """Return the rendered graph view the agent run consumes."""

        return self.graph.render_context()

    def read(self, tracker_name: str, ticket_id: str) -> Ticket:
        """Read a ticket via the named adapter.

        Reads are never gated by ``cross_tracker_dispatch.allow`` but
        do produce an audit entry so cross-tracker information flow is
        traceable in post-hoc review.
        """

        adapter = self._require_adapter(tracker_name)
        try:
            ticket = adapter.fetch_ticket(ticket_id)
        except TrackerUnknownError:
            self._emit(
                "cross_tracker_read_miss",
                tracker_to=tracker_name,
                ticket_to=ticket_id,
                link_kind="",
                action="read",
                detail="not found",
            )
            raise
        self._emit(
            "cross_tracker_read",
            tracker_to=tracker_name,
            ticket_to=ticket_id,
            link_kind=self._infer_link_kind(tracker_name, ticket_id),
            action="read",
        )
        return ticket

    def comment(self, tracker_name: str, ticket_id: str, body: str, *, from_tracker: str = "") -> None:
        """Post a comment, gated by the per-role allow-list.

        ``from_tracker`` is the name of the tracker the agent is
        reasoning from at the moment of the action; it lands in the
        ``tracker_name_from`` audit field. Empty string means
        "untargeted" and is recorded verbatim.
        """

        self._require_allowed(tracker_name)
        adapter = self._require_adapter(tracker_name)
        try:
            adapter.add_comment(ticket_id, body)
        except TrackerReadOnlyError:
            self._emit(
                "cross_tracker_write_blocked",
                tracker_from=from_tracker,
                tracker_to=tracker_name,
                ticket_to=ticket_id,
                link_kind=self._infer_link_kind(tracker_name, ticket_id),
                action="comment",
                detail="adapter read-only",
            )
            raise
        self._emit(
            "cross_tracker_comment",
            tracker_from=from_tracker,
            tracker_to=tracker_name,
            ticket_to=ticket_id,
            link_kind=self._infer_link_kind(tracker_name, ticket_id),
            action="comment",
        )

    def transition(
        self,
        tracker_name: str,
        ticket_id: str,
        target_state: str,
        *,
        from_tracker: str = "",
    ) -> None:
        """Transition a ticket, gated by the per-role allow-list."""

        self._require_allowed(tracker_name)
        adapter = self._require_adapter(tracker_name)
        try:
            adapter.transition(ticket_id, target_state)
        except TrackerReadOnlyError:
            self._emit(
                "cross_tracker_write_blocked",
                tracker_from=from_tracker,
                tracker_to=tracker_name,
                ticket_to=ticket_id,
                link_kind=self._infer_link_kind(tracker_name, ticket_id),
                action=f"transition:{target_state}",
                detail="adapter read-only",
            )
            raise
        self._emit(
            "cross_tracker_transition",
            tracker_from=from_tracker,
            tracker_to=tracker_name,
            ticket_to=ticket_id,
            link_kind=self._infer_link_kind(tracker_name, ticket_id),
            action=f"transition:{target_state}",
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _require_allowed(self, tracker_name: str) -> None:
        if not self.config.is_allowed(self.role, tracker_name):
            raise FederationPermissionError(f"role {self.role!r} is not allowed to write to {tracker_name!r}")

    def _require_adapter(self, tracker_name: str) -> TrackerAdapter:
        try:
            return self.adapters[tracker_name]
        except KeyError as exc:
            raise FederationError(f"unknown tracker {tracker_name!r}") from exc

    def _infer_link_kind(self, tracker: str, ticket_id: str) -> str:
        """Look up the link kind in the graph by destination node.

        When the destination has multiple inbound edges the kinds are
        joined with ``+`` so the audit log preserves all justifications
        for the action.
        """

        target_key = (tracker, ticket_id)
        matching = [edge.ref_kind for edge in self.graph.edges if edge.target.key() == target_key]
        # Preserve first-seen order while deduplicating.
        return "+".join(dict.fromkeys(matching))

    def _emit(
        self,
        event: str,
        *,
        tracker_from: str = "",
        tracker_to: str,
        ticket_from: str = "",
        ticket_to: str,
        link_kind: str,
        action: str,
        detail: str = "",
    ) -> None:
        record = CrossTrackerAuditRecord(
            event=event,
            timestamp=self._clock(),
            role=self.role,
            tracker_name_from=tracker_from,
            tracker_name_to=tracker_to,
            ticket_id_from=ticket_from,
            ticket_id_to=ticket_to,
            link_kind=link_kind,
            action=action,
            detail=detail,
        )
        self.audit_sink(record)
