"""Unit tests for :mod:`bernstein.core.orchestration.federation`.

The suite covers the three default link detectors, the
two-tracker and three-tracker graph assemblies, the per-role write
allow-list, the audit-log shape on success and on read-only failure,
and the YAML config view.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from bernstein.core.orchestration.federation import (
    DEFAULT_AUDIT_RELPATH,
    CommentMentionDetector,
    CrossTrackerAuditRecord,
    CustomFieldDetector,
    FederatedTicketGraph,
    FederationBuilder,
    FederationConfig,
    FederationDispatcher,
    FederationError,
    FederationPermissionError,
    GraphNode,
    LinkRef,
    URLDetector,
    write_audit_record,
)
from bernstein.core.orchestration.federation_contract import (
    Comment,
    Ticket,
    TrackerReadOnlyError,
    TrackerUnknownError,
)

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class FakeAdapter:
    """Minimal in-memory adapter satisfying :class:`TrackerAdapter`."""

    name: str
    tracker_uri_base: str = ""
    tickets: dict[str, Ticket] = field(default_factory=dict)
    read_only: bool = False
    comments_written: list[tuple[str, str]] = field(default_factory=list)
    transitions: list[tuple[str, str]] = field(default_factory=list)

    def fetch_ticket(self, ticket_id: str) -> Ticket:
        try:
            return self.tickets[ticket_id]
        except KeyError as exc:
            raise TrackerUnknownError(ticket_id) from exc

    def list_tickets(self) -> Iterable[Ticket]:
        return list(self.tickets.values())

    def add_comment(self, ticket_id: str, body: str) -> None:
        if self.read_only:
            raise TrackerReadOnlyError(self.name)
        if ticket_id not in self.tickets:
            raise TrackerUnknownError(ticket_id)
        self.comments_written.append((ticket_id, body))

    def transition(self, ticket_id: str, target_state: str) -> None:
        if self.read_only:
            raise TrackerReadOnlyError(self.name)
        if ticket_id not in self.tickets:
            raise TrackerUnknownError(ticket_id)
        self.transitions.append((ticket_id, target_state))


def _ticket(
    tracker: str,
    ticket_id: str,
    *,
    body: str = "",
    comments: tuple[Comment, ...] = (),
    custom_fields: dict[str, str] | None = None,
) -> Ticket:
    return Ticket(
        tracker=tracker,
        ticket_id=ticket_id,
        title=f"{tracker}:{ticket_id}",
        body=body,
        comments=comments,
        custom_fields=custom_fields or {},
        url=f"https://example.test/{tracker}/{ticket_id}",
    )


# ---------------------------------------------------------------------------
# URL detector
# ---------------------------------------------------------------------------


def test_url_detector_finds_cross_tracker_link_in_body() -> None:
    linear = FakeAdapter(name="linear", tracker_uri_base="https://linear.app/acme/issue/")
    github = FakeAdapter(
        name="github_projects",
        tracker_uri_base="https://github.com/acme/repo/issues/",
    )
    source = _ticket(
        "linear",
        "LIN-1",
        body="ref https://github.com/acme/repo/issues/42 fixes this",
    )
    refs = list(URLDetector().detect(source, [linear, github]))
    assert refs == [
        LinkRef(
            ref_kind="url",
            target_tracker="github_projects",
            target_id="42",
            source_text="https://github.com/acme/repo/issues/42",
        )
    ]


def test_url_detector_ignores_self_tracker_urls() -> None:
    linear = FakeAdapter(name="linear", tracker_uri_base="https://linear.app/acme/issue/")
    source = _ticket("linear", "LIN-1", body="see https://linear.app/acme/issue/LIN-2")
    assert list(URLDetector().detect(source, [linear])) == []


def test_url_detector_scans_comment_bodies() -> None:
    linear = FakeAdapter(name="linear", tracker_uri_base="https://linear.app/acme/issue/")
    github = FakeAdapter(
        name="github_projects",
        tracker_uri_base="https://github.com/acme/repo/issues/",
    )
    source = _ticket(
        "linear",
        "LIN-1",
        comments=(Comment(author="op", body="see https://github.com/acme/repo/issues/9"),),
    )
    refs = list(URLDetector().detect(source, [linear, github]))
    assert [r.target_id for r in refs] == ["9"]


def test_url_detector_skips_adapter_with_empty_uri_base() -> None:
    linear = FakeAdapter(name="linear", tracker_uri_base="https://linear.app/acme/issue/")
    notion = FakeAdapter(name="notion", tracker_uri_base="")
    source = _ticket("linear", "LIN-1", body="https://notion.so/p/xyz")
    assert list(URLDetector().detect(source, [linear, notion])) == []


# ---------------------------------------------------------------------------
# Custom-field detector
# ---------------------------------------------------------------------------


def test_custom_field_detector_picks_up_external_link_field() -> None:
    linear = FakeAdapter(name="linear", tracker_uri_base="https://linear.app/acme/issue/")
    github = FakeAdapter(
        name="github_projects",
        tracker_uri_base="https://github.com/acme/repo/issues/",
    )
    source = _ticket(
        "linear",
        "LIN-1",
        custom_fields={"External-Link": "https://github.com/acme/repo/issues/77"},
    )
    refs = list(CustomFieldDetector().detect(source, [linear, github]))
    assert [(r.ref_kind, r.target_tracker, r.target_id) for r in refs] == [("custom_field", "github_projects", "77")]


def test_custom_field_detector_is_case_insensitive_on_field_name() -> None:
    linear = FakeAdapter(name="linear", tracker_uri_base="https://linear.app/acme/issue/")
    github = FakeAdapter(
        name="github_projects",
        tracker_uri_base="https://github.com/acme/repo/issues/",
    )
    source = _ticket(
        "linear",
        "LIN-1",
        custom_fields={"links": "https://github.com/acme/repo/issues/5"},
    )
    refs = list(CustomFieldDetector().detect(source, [linear, github]))
    assert len(refs) == 1


def test_custom_field_detector_yields_nothing_when_unknown_field() -> None:
    linear = FakeAdapter(name="linear", tracker_uri_base="https://linear.app/acme/issue/")
    github = FakeAdapter(
        name="github_projects",
        tracker_uri_base="https://github.com/acme/repo/issues/",
    )
    source = _ticket(
        "linear",
        "LIN-1",
        custom_fields={"random": "https://github.com/acme/repo/issues/5"},
    )
    assert list(CustomFieldDetector().detect(source, [linear, github])) == []


# ---------------------------------------------------------------------------
# Comment-mention detector
# ---------------------------------------------------------------------------


def test_comment_mention_detector_matches_prefixed_ids() -> None:
    linear = FakeAdapter(name="linear")
    jira = FakeAdapter(name="jira")
    source = _ticket(
        "linear",
        "LIN-9",
        body="follows JIRA-1234 and JIRA-2222 changes",
    )
    refs = list(CommentMentionDetector().detect(source, [linear, jira]))
    assert sorted((r.target_tracker, r.target_id) for r in refs) == [
        ("jira", "JIRA-1234"),
        ("jira", "JIRA-2222"),
    ]


def test_comment_mention_detector_ignores_self_tracker_prefix() -> None:
    linear = FakeAdapter(name="linear")
    jira = FakeAdapter(name="jira")
    source = _ticket("linear", "LIN-9", body="links to LIN-10 should not fire")
    detector = CommentMentionDetector(mention_prefixes={"LIN": "linear", "JIRA": "jira"})
    assert list(detector.detect(source, [linear, jira])) == []


def test_comment_mention_detector_hash_default_tracker() -> None:
    linear = FakeAdapter(name="linear")
    github = FakeAdapter(name="github_projects")
    source = _ticket("linear", "LIN-9", body="closes #42")
    detector = CommentMentionDetector(
        mention_prefixes={},
        hash_default_tracker="github_projects",
    )
    refs = list(detector.detect(source, [linear, github]))
    assert [(r.target_tracker, r.target_id) for r in refs] == [("github_projects", "42")]


# ---------------------------------------------------------------------------
# Graph rendering
# ---------------------------------------------------------------------------


def test_graph_render_context_is_deterministic_and_sorted() -> None:
    graph = FederatedTicketGraph()
    n_b = graph.add_ticket(_ticket("b", "2"))
    n_a = graph.add_ticket(_ticket("a", "1"))
    graph.add_edge(n_a, n_b, LinkRef(ref_kind="url", target_tracker="b", target_id="2", source_text="x"))
    rendered = graph.render_context()
    assert "node: a:1" in rendered
    assert "node: b:2" in rendered
    assert rendered.index("node: a:1") < rendered.index("node: b:2")
    assert "-> b:2 (url: x)" in rendered


def test_graph_neighbours_deduplicates_and_sorts() -> None:
    graph = FederatedTicketGraph()
    src = graph.add_ticket(_ticket("a", "1"))
    other = graph.add_ticket(_ticket("b", "2"))
    third = graph.add_ticket(_ticket("c", "3"))
    graph.add_edge(
        src,
        third,
        LinkRef("url", "c", "3", "x"),
    )
    graph.add_edge(
        src,
        other,
        LinkRef("url", "b", "2", "y"),
    )
    graph.add_edge(
        src,
        other,
        LinkRef("custom_field", "b", "2", "z"),
    )
    keys = [n.key() for n in graph.neighbours(src)]
    assert keys == [("b", "2"), ("c", "3")]


# ---------------------------------------------------------------------------
# Two-tracker build
# ---------------------------------------------------------------------------


def test_two_tracker_setup_with_one_url_link_builds_one_edge() -> None:
    linear = FakeAdapter(
        name="linear",
        tracker_uri_base="https://linear.app/acme/issue/",
        tickets={
            "LIN-1": _ticket(
                "linear",
                "LIN-1",
                body="see https://github.com/acme/repo/issues/42",
            )
        },
    )
    github = FakeAdapter(
        name="github_projects",
        tracker_uri_base="https://github.com/acme/repo/issues/",
        tickets={"42": _ticket("github_projects", "42")},
    )
    graph = FederationBuilder(adapters=[linear, github]).build()
    assert sorted(graph.nodes) == [
        ("github_projects", "42"),
        ("linear", "LIN-1"),
    ]
    assert len(graph.edges) == 1
    edge = graph.edges[0]
    assert edge.source.key() == ("linear", "LIN-1")
    assert edge.target.key() == ("github_projects", "42")
    assert edge.ref_kind == "url"


def test_builder_drops_refs_to_unknown_trackers() -> None:
    linear = FakeAdapter(
        name="linear",
        tracker_uri_base="https://linear.app/acme/issue/",
        tickets={
            "LIN-1": _ticket(
                "linear",
                "LIN-1",
                body="https://notion.so/p/abc",
            )
        },
    )
    graph = FederationBuilder(adapters=[linear]).build()
    assert graph.edges == []


# ---------------------------------------------------------------------------
# Three-tracker chain
# ---------------------------------------------------------------------------


def test_three_tracker_chain_yields_chained_edges() -> None:
    linear = FakeAdapter(
        name="linear",
        tracker_uri_base="https://linear.app/acme/issue/",
        tickets={
            "LIN-1": _ticket(
                "linear",
                "LIN-1",
                body="ref https://github.com/acme/repo/issues/42",
            ),
        },
    )
    github = FakeAdapter(
        name="github_projects",
        tracker_uri_base="https://github.com/acme/repo/issues/",
        tickets={
            "42": _ticket(
                "github_projects",
                "42",
                custom_fields={"External-Link": "https://notion.so/p/abc"},
            )
        },
    )
    notion = FakeAdapter(
        name="notion",
        tracker_uri_base="https://notion.so/p/",
        tickets={"abc": _ticket("notion", "abc")},
    )
    graph = FederationBuilder(adapters=[linear, github, notion]).build()
    edge_keys = sorted((e.source.key(), e.target.key(), e.ref_kind) for e in graph.edges)
    assert edge_keys == [
        (("github_projects", "42"), ("notion", "abc"), "custom_field"),
        (("linear", "LIN-1"), ("github_projects", "42"), "url"),
    ]
    rendered = graph.render_context()
    assert "github_projects:42" in rendered
    assert "notion:abc" in rendered


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_federation_config_from_dict_parses_allow_list() -> None:
    raw = {
        "linked_trackers": ["linear", "jira", "github_projects"],
        "link_detectors": ["url", "comment_mention"],
        "cross_tracker_dispatch": {
            "allow": {
                "backend": ["jira", "github_projects"],
                "reviewer": "*",
            }
        },
    }
    cfg = FederationConfig.from_dict(raw)
    assert cfg.linked_trackers == ("linear", "jira", "github_projects")
    assert cfg.link_detector_names == ("url", "comment_mention")
    assert cfg.is_allowed("backend", "jira") is True
    assert cfg.is_allowed("backend", "linear") is False
    assert cfg.is_allowed("reviewer", "linear") is True
    assert cfg.is_allowed("docs", "jira") is False


def test_federation_config_defaults_when_block_missing() -> None:
    cfg = FederationConfig.from_dict({})
    assert cfg.linked_trackers == ()
    assert cfg.link_detector_names == ("url", "custom_field", "comment_mention")
    assert cfg.is_allowed("backend", "anything") is False


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def _dispatcher(
    *,
    role: str,
    allow: dict[str, frozenset[str]],
    adapters: dict[str, FakeAdapter],
    graph: FederatedTicketGraph | None = None,
) -> tuple[FederationDispatcher, list[CrossTrackerAuditRecord]]:
    sink: list[CrossTrackerAuditRecord] = []
    cfg = FederationConfig(
        linked_trackers=tuple(adapters),
        cross_tracker_dispatch_allow=allow,
    )
    dispatcher = FederationDispatcher(
        graph=graph or FederatedTicketGraph(),
        adapters=adapters,
        config=cfg,
        role=role,
        audit_sink=sink.append,
        _clock=lambda: 1700000000.0,
    )
    return dispatcher, sink


def test_dispatcher_read_emits_audit_and_returns_ticket() -> None:
    linear = FakeAdapter(name="linear", tickets={"LIN-1": _ticket("linear", "LIN-1")})
    dispatcher, sink = _dispatcher(
        role="backend",
        allow={"backend": frozenset({"linear"})},
        adapters={"linear": linear},
    )
    ticket = dispatcher.read("linear", "LIN-1")
    assert ticket.ticket_id == "LIN-1"
    assert len(sink) == 1
    record = sink[0]
    assert record.event == "cross_tracker_read"
    assert record.role == "backend"
    assert record.tracker_name_to == "linear"
    assert record.ticket_id_to == "LIN-1"
    assert record.action == "read"


def test_dispatcher_comment_records_link_kind_from_graph() -> None:
    linear = FakeAdapter(name="linear", tickets={"LIN-1": _ticket("linear", "LIN-1")})
    jira = FakeAdapter(name="jira", tickets={"JIRA-9": _ticket("jira", "JIRA-9")})
    graph = FederatedTicketGraph()
    src = graph.add_ticket(linear.tickets["LIN-1"])
    target = graph.add_ticket(jira.tickets["JIRA-9"])
    graph.add_edge(
        src,
        target,
        LinkRef(ref_kind="comment_mention", target_tracker="jira", target_id="JIRA-9", source_text="JIRA-9"),
    )
    dispatcher, sink = _dispatcher(
        role="backend",
        allow={"backend": frozenset({"jira"})},
        adapters={"linear": linear, "jira": jira},
        graph=graph,
    )
    dispatcher.comment("jira", "JIRA-9", "ack", from_tracker="linear")
    assert jira.comments_written == [("JIRA-9", "ack")]
    record = sink[-1]
    assert record.event == "cross_tracker_comment"
    assert record.tracker_name_from == "linear"
    assert record.tracker_name_to == "jira"
    assert record.link_kind == "comment_mention"
    assert record.action == "comment"


def test_dispatcher_transition_routed_and_audited() -> None:
    jira = FakeAdapter(name="jira", tickets={"JIRA-9": _ticket("jira", "JIRA-9")})
    dispatcher, sink = _dispatcher(
        role="backend",
        allow={"backend": frozenset({"jira"})},
        adapters={"jira": jira},
    )
    dispatcher.transition("jira", "JIRA-9", "Done", from_tracker="linear")
    assert jira.transitions == [("JIRA-9", "Done")]
    assert sink[-1].event == "cross_tracker_transition"
    assert sink[-1].action == "transition:Done"


def test_dispatcher_blocks_write_when_role_not_in_allow() -> None:
    jira = FakeAdapter(name="jira", tickets={"JIRA-1": _ticket("jira", "JIRA-1")})
    dispatcher, sink = _dispatcher(
        role="docs",
        allow={"backend": frozenset({"jira"})},
        adapters={"jira": jira},
    )
    with pytest.raises(FederationPermissionError):
        dispatcher.comment("jira", "JIRA-1", "noop")
    assert jira.comments_written == []
    assert sink == []


def test_dispatcher_blocks_write_when_tracker_not_in_role_scope() -> None:
    jira = FakeAdapter(name="jira", tickets={"JIRA-1": _ticket("jira", "JIRA-1")})
    linear = FakeAdapter(name="linear", tickets={"LIN-1": _ticket("linear", "LIN-1")})
    dispatcher, _sink = _dispatcher(
        role="backend",
        allow={"backend": frozenset({"linear"})},
        adapters={"jira": jira, "linear": linear},
    )
    with pytest.raises(FederationPermissionError):
        dispatcher.transition("jira", "JIRA-1", "Done")
    assert jira.transitions == []


def test_dispatcher_read_only_adapter_raises_and_audits_block() -> None:
    jira = FakeAdapter(
        name="jira",
        tickets={"JIRA-1": _ticket("jira", "JIRA-1")},
        read_only=True,
    )
    dispatcher, sink = _dispatcher(
        role="backend",
        allow={"backend": frozenset({"jira"})},
        adapters={"jira": jira},
    )
    with pytest.raises(TrackerReadOnlyError):
        dispatcher.comment("jira", "JIRA-1", "ack")
    assert sink[-1].event == "cross_tracker_write_blocked"
    assert sink[-1].detail == "adapter read-only"


def test_dispatcher_unknown_adapter_raises_federation_error() -> None:
    dispatcher, _sink = _dispatcher(
        role="backend",
        allow={"backend": frozenset({"jira"})},
        adapters={},
    )
    with pytest.raises(FederationError):
        dispatcher.read("jira", "JIRA-1")


def test_dispatcher_read_miss_audits_missing_ticket() -> None:
    jira = FakeAdapter(name="jira")
    dispatcher, sink = _dispatcher(
        role="backend",
        allow={"backend": frozenset({"jira"})},
        adapters={"jira": jira},
    )
    with pytest.raises(TrackerUnknownError):
        dispatcher.read("jira", "missing")
    assert sink[-1].event == "cross_tracker_read_miss"
    assert sink[-1].detail == "not found"


# ---------------------------------------------------------------------------
# Audit sink
# ---------------------------------------------------------------------------


def test_write_audit_record_appends_json_line(tmp_path: Path) -> None:
    record = CrossTrackerAuditRecord(
        event="cross_tracker_comment",
        timestamp=1700000000.0,
        role="backend",
        tracker_name_from="linear",
        tracker_name_to="jira",
        ticket_id_from="LIN-1",
        ticket_id_to="JIRA-9",
        link_kind="url",
        action="comment",
        detail="",
    )
    path = write_audit_record(record, tmp_path)
    assert path == tmp_path / DEFAULT_AUDIT_RELPATH
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["event"] == "cross_tracker_comment"
    assert parsed["tracker_name_from"] == "linear"
    assert parsed["tracker_name_to"] == "jira"
    assert parsed["link_kind"] == "url"


def test_write_audit_record_appends_multiple_lines(tmp_path: Path) -> None:
    record = CrossTrackerAuditRecord(
        event="cross_tracker_read",
        timestamp=1.0,
        role="r",
        tracker_name_from="",
        tracker_name_to="jira",
        ticket_id_from="",
        ticket_id_to="JIRA-1",
        link_kind="",
        action="read",
    )
    write_audit_record(record, tmp_path)
    write_audit_record(record, tmp_path)
    path = tmp_path / DEFAULT_AUDIT_RELPATH
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


# ---------------------------------------------------------------------------
# Permission boundary integration
# ---------------------------------------------------------------------------


def test_permission_boundary_role_not_in_allow_list_cannot_write_to_other_tracker() -> None:
    """End-to-end check that mirrors the acceptance-criteria scenario."""

    linear = FakeAdapter(
        name="linear",
        tracker_uri_base="https://linear.app/acme/issue/",
        tickets={
            "LIN-1": _ticket(
                "linear",
                "LIN-1",
                body="see https://github.com/acme/repo/issues/42",
            )
        },
    )
    github = FakeAdapter(
        name="github_projects",
        tracker_uri_base="https://github.com/acme/repo/issues/",
        tickets={"42": _ticket("github_projects", "42")},
    )
    graph = FederationBuilder(adapters=[linear, github]).build()
    cfg = FederationConfig.from_dict(
        {
            "linked_trackers": ["linear", "github_projects"],
            "cross_tracker_dispatch": {
                "allow": {"backend": ["linear"]},
            },
        }
    )
    sink: list[CrossTrackerAuditRecord] = []
    dispatcher = FederationDispatcher(
        graph=graph,
        adapters={"linear": linear, "github_projects": github},
        config=cfg,
        role="backend",
        audit_sink=sink.append,
    )
    with pytest.raises(FederationPermissionError):
        dispatcher.comment("github_projects", "42", "blocked")
    assert github.comments_written == []
    assert sink == []
    dispatcher.read("github_projects", "42")
    assert sink[-1].event == "cross_tracker_read"
    assert sink[-1].link_kind == "url"


# ---------------------------------------------------------------------------
# GraphNode helpers
# ---------------------------------------------------------------------------


def test_graph_node_key_round_trip() -> None:
    node = GraphNode(tracker="x", ticket_id="1")
    assert node.key() == ("x", "1")


def test_empty_graph_render() -> None:
    assert FederatedTicketGraph().render_context() == "federation: empty\n"
