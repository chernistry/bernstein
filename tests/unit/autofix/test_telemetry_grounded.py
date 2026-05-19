"""Unit tests for telemetry-grounded autofix.

Covers source adapters (Sentry, GHA failure, stub adapters), settings
loader, grounding retriever, goal builder, signature verification, and
the dispatch state machine.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from bernstein.core.autofix.telemetry_grounded import (
    DEFAULT_PER_EVENT_COST_CAP_USD,
    GhaFailureSource,
    GroundedDispatchResult,
    GroundingContext,
    RecentJsonlLogRetriever,
    SentrySource,
    StubSource,
    TelemetryDispatchRecord,
    TelemetryEvent,
    TelemetrySettings,
    TelemetrySourceConfig,
    build_default_sources,
    build_grounded_goal,
    dispatch_telemetry_event,
    iter_enabled_sources,
    load_telemetry_settings,
    parse_json_payload,
    verify_webhook_signature,
)

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class _DispatchRecorder:
    """Stub dispatch hook with a programmable result."""

    result: GroundedDispatchResult = field(
        default_factory=lambda: GroundedDispatchResult(success=True, commit_sha="sha", cost_usd=0.05)
    )
    raised: BaseException | None = None
    calls: list[tuple[str, TelemetryEvent, GroundingContext, float]] = field(default_factory=list)

    def __call__(
        self,
        *,
        goal: str,
        event: TelemetryEvent,
        context: GroundingContext,
        cost_cap_usd: float,
    ) -> GroundedDispatchResult:
        self.calls.append((goal, event, context, cost_cap_usd))
        if self.raised is not None:
            raise self.raised
        return self.result


@dataclass
class _StaticRetriever:
    """Retriever that returns a fixed context regardless of the event."""

    context: GroundingContext = field(
        default_factory=lambda: GroundingContext(log_excerpts=("line a", "line b"), retriever_id="static")
    )
    raised: BaseException | None = None
    calls: list[TelemetryEvent] = field(default_factory=list)

    @property
    def retriever_id(self) -> str:
        return self.context.retriever_id or "static"

    def retrieve(self, event: TelemetryEvent) -> GroundingContext:
        self.calls.append(event)
        if self.raised is not None:
            raise self.raised
        return self.context


@dataclass
class _AuditRecorder:
    """Audit-emitter test double."""

    events: list[tuple[str, str, str, str, dict[str, object] | None]] = field(default_factory=list)

    def log(
        self,
        event_type: str,
        actor: str,
        resource_type: str,
        resource_id: str,
        details: dict[str, object] | None = None,
    ) -> object:
        self.events.append((event_type, actor, resource_type, resource_id, details))
        return object()


def _settings_with(
    *,
    source: str = "sentry",
    enabled: bool = True,
    cost_cap_usd: float = 0.20,
    fingerprint_path: str = "",
) -> TelemetrySettings:
    return TelemetrySettings(
        sources=(
            TelemetrySourceConfig(
                source=source,  # type: ignore[arg-type]
                enabled=enabled,
                endpoint=f"/webhooks/telemetry/{source}/",
                cost_cap_usd=cost_cap_usd,
                fingerprint_path=fingerprint_path,
            ),
        )
    )


# ---------------------------------------------------------------------------
# Settings loader
# ---------------------------------------------------------------------------


def test_load_telemetry_settings_missing_file(tmp_path: Path) -> None:
    """Loader returns empty defaults when bernstein.yaml is absent."""
    target = tmp_path / "missing.yaml"
    settings = load_telemetry_settings(target)
    assert settings.sources == ()


def test_load_telemetry_settings_parses_sources(tmp_path: Path) -> None:
    """Loader builds typed configs for declared sources."""
    target = tmp_path / "bernstein.yaml"
    target.write_text(
        """
autofix:
  telemetry_sources:
    - source: sentry
      enabled: true
      endpoint: /webhooks/telemetry/sentry/
      secret_env: SENTRY_SECRET
      fingerprint_path: data.issue.id
      cost_cap_usd: 0.10
    - source: gha_failure
      enabled: false
      endpoint: /webhooks/telemetry/gha/
    - source: unknown_source
      enabled: true
        """.strip(),
        encoding="utf-8",
    )
    settings = load_telemetry_settings(target)
    assert len(settings.sources) == 2
    sentry = settings.for_source("sentry")
    assert sentry is not None
    assert sentry.enabled is True
    assert sentry.secret_env == "SENTRY_SECRET"
    assert sentry.cost_cap_usd == pytest.approx(0.10)
    assert sentry.fingerprint_path == "data.issue.id"
    gha = settings.for_source("gha_failure")
    assert gha is not None
    assert gha.enabled is False
    assert gha.cost_cap_usd == pytest.approx(DEFAULT_PER_EVENT_COST_CAP_USD)


def test_load_telemetry_settings_rejects_negative_cap(tmp_path: Path) -> None:
    """Negative cost caps are clamped to zero."""
    target = tmp_path / "bernstein.yaml"
    target.write_text(
        """
autofix:
  telemetry_sources:
    - source: sentry
      enabled: true
      cost_cap_usd: -2.0
        """.strip(),
        encoding="utf-8",
    )
    sentry = load_telemetry_settings(target).for_source("sentry")
    assert sentry is not None
    assert sentry.cost_cap_usd == 0.0


def test_iter_enabled_sources_filters_disabled() -> None:
    """iter_enabled_sources yields only enabled entries in order."""
    settings = TelemetrySettings(
        sources=(
            TelemetrySourceConfig(source="sentry", enabled=False),
            TelemetrySourceConfig(source="gha_failure", enabled=True),
        )
    )
    enabled = list(iter_enabled_sources(settings))
    assert len(enabled) == 1
    assert enabled[0].source == "gha_failure"


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


def test_sentry_source_parses_issue_alert() -> None:
    """SentrySource extracts fingerprint / title / message from the alert."""
    payload: dict[str, Any] = {
        "action": "created",
        "data": {
            "issue": {
                "id": "12345",
                "title": "ZeroDivisionError: division by zero",
                "culprit": "app.handlers.divide in src/app.py",
                "shortId": "BACKEND-1A",
                "permalink": "https://sentry.example/issues/12345/",
                "metadata": {"value": "Specific arithmetic failure"},
            }
        },
        "project_slug": "backend",
    }
    event = SentrySource().parse(payload)
    assert event.source == "sentry"
    assert event.fingerprint == "12345"
    assert "ZeroDivisionError" in event.title
    assert "Specific arithmetic failure" in event.message
    assert event.environment == "backend"
    assert event.url == "https://sentry.example/issues/12345/"
    assert event.raw is payload


def test_sentry_source_honours_fingerprint_path() -> None:
    """Operator-supplied fingerprint_path overrides the default extractor."""
    payload: dict[str, Any] = {
        "data": {"issue": {"id": "ignored"}},
        "fingerprint_override": "custom-finger",
    }
    event = SentrySource(fingerprint_path="fingerprint_override").parse(payload)
    assert event.fingerprint == "custom-finger"


def test_sentry_source_falls_back_to_hashed_title() -> None:
    """Missing id falls back to a stable hashed-title fingerprint."""
    payload: dict[str, Any] = {"data": {"issue": {"title": "OOPS"}}}
    event = SentrySource().parse(payload)
    assert event.fingerprint.startswith("sentry:")
    expected = "sentry:" + hashlib.sha256(b"OOPS").hexdigest()[:16]
    assert event.fingerprint == expected


def test_gha_failure_source_parses_failure() -> None:
    """GhaFailureSource accepts a failure conclusion."""
    payload: dict[str, Any] = {
        "action": "completed",
        "workflow_run": {
            "id": 987,
            "conclusion": "failure",
            "head_sha": "abc123def456",
            "head_branch": "feat/x",
            "name": "ci",
            "html_url": "https://github.com/owner/name/actions/runs/987",
        },
        "repository": {"full_name": "owner/name"},
    }
    event = GhaFailureSource().parse(payload)
    assert event.source == "gha_failure"
    assert event.fingerprint == "owner/name#run-987"
    assert event.repo == "owner/name"
    assert "abc123def456" in event.message
    assert event.environment == "ci"


def test_gha_failure_source_skips_success() -> None:
    """Non-failure conclusions produce an empty fingerprint (skip)."""
    payload: dict[str, Any] = {
        "workflow_run": {"id": 1, "conclusion": "success"},
        "repository": {"full_name": "owner/name"},
    }
    event = GhaFailureSource().parse(payload)
    assert event.fingerprint == ""


def test_gha_failure_source_accepts_timed_out() -> None:
    """timed_out conclusion is treated as a failure."""
    payload: dict[str, Any] = {
        "workflow_run": {
            "id": 2,
            "conclusion": "timed_out",
            "head_sha": "deadbeef",
            "head_branch": "main",
        },
        "repository": {"full_name": "owner/name"},
    }
    event = GhaFailureSource().parse(payload)
    assert event.fingerprint == "owner/name#run-2"


def test_stub_source_normalises_basic_fields() -> None:
    """StubSource copies top-level fields when present."""
    payload: dict[str, Any] = {
        "fingerprint": "fp-1",
        "title": "T",
        "message": "M",
        "repo": "owner/name",
        "environment": "prod",
        "url": "https://x.example",
    }
    event = StubSource("datadog").parse(payload)
    assert event.source == "datadog"
    assert event.fingerprint == "fp-1"
    assert event.title == "T"
    assert event.url == "https://x.example"


def test_build_default_sources_includes_all_builtins() -> None:
    """build_default_sources wires every documented adapter."""
    sources = build_default_sources(None)
    assert set(sources.keys()) == {"sentry", "gha_failure", "datadog", "loki", "custom_jsonl"}


def test_build_default_sources_respects_fingerprint_path() -> None:
    """Operator-supplied path propagates to the Sentry adapter."""
    settings = _settings_with(fingerprint_path="raw.id")
    sources = build_default_sources(settings)
    adapter = sources["sentry"]
    event = adapter.parse({"raw": {"id": "from-path"}})
    assert event.fingerprint == "from-path"


# ---------------------------------------------------------------------------
# Grounding retriever
# ---------------------------------------------------------------------------


def test_recent_jsonl_retriever_returns_matching_lines(tmp_path: Path) -> None:
    """The retriever returns lines whose body contains the fingerprint."""
    log_path = tmp_path / "app.jsonl"
    lines = [
        json.dumps({"ts": 1, "fp": "OTHER", "msg": "unrelated"}),
        json.dumps({"ts": 2, "fp": "fp-1", "msg": "first hit"}),
        json.dumps({"ts": 3, "fp": "fp-1", "msg": "second hit"}),
    ]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    retriever = RecentJsonlLogRetriever(log_path, max_lines=5)
    event = TelemetryEvent(source="sentry", fingerprint="fp-1")
    context = retriever.retrieve(event)
    assert len(context.log_excerpts) == 2
    assert "first hit" in context.log_excerpts[0]
    assert "second hit" in context.log_excerpts[1]
    assert context.retriever_id == "recent_jsonl_log"


def test_recent_jsonl_retriever_caps_lines(tmp_path: Path) -> None:
    """max_lines is honoured even when many matches exist."""
    log_path = tmp_path / "app.jsonl"
    lines = [json.dumps({"fp": "fp", "i": i}) for i in range(10)]
    log_path.write_text("\n".join(lines), encoding="utf-8")

    retriever = RecentJsonlLogRetriever(log_path, max_lines=3)
    context = retriever.retrieve(TelemetryEvent(source="sentry", fingerprint="fp"))
    assert len(context.log_excerpts) == 3
    # Most-recent-last ordering.
    assert '"i": 9' in context.log_excerpts[-1]


def test_recent_jsonl_retriever_handles_missing_file(tmp_path: Path) -> None:
    """A missing log file yields an empty context."""
    retriever = RecentJsonlLogRetriever(tmp_path / "nope.jsonl")
    context = retriever.retrieve(TelemetryEvent(source="sentry", fingerprint="fp"))
    assert context.log_excerpts == ()


def test_recent_jsonl_retriever_skips_empty_fingerprint(tmp_path: Path) -> None:
    """Empty fingerprint short-circuits the retriever."""
    log_path = tmp_path / "log.jsonl"
    log_path.write_text("{}\n", encoding="utf-8")
    retriever = RecentJsonlLogRetriever(log_path)
    context = retriever.retrieve(TelemetryEvent(source="sentry", fingerprint=""))
    assert context.log_excerpts == ()


# ---------------------------------------------------------------------------
# Goal builder
# ---------------------------------------------------------------------------


def test_build_grounded_goal_is_deterministic() -> None:
    """Identical inputs produce identical goal bodies."""
    event = TelemetryEvent(
        source="sentry",
        fingerprint="fp-1",
        title="T",
        message="m",
        environment="prod",
        url="https://x",
    )
    context = GroundingContext(
        log_excerpts=("line-a", "line-b"),
        commits=(("abcdef", "subject"),),
        related_tests=("tests/x.py::test_y",),
        retriever_id="static",
    )
    a = build_grounded_goal(event, context)
    b = build_grounded_goal(event, context)
    assert a == b
    assert "fp-1" in a
    assert "line-a" in a
    assert "tests/x.py::test_y" in a


def test_build_grounded_goal_handles_empty_context() -> None:
    """Empty context renders the placeholders, not blank sections."""
    event = TelemetryEvent(source="gha_failure", fingerprint="fp")
    text = build_grounded_goal(event, GroundingContext())
    assert "(no log lines captured)" in text
    assert "(no commit context)" in text


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


def test_verify_webhook_signature_accepts_hex() -> None:
    """Bare hex signatures match constant-time."""
    secret = "topsecret"
    body = b'{"a": 1}'
    sig = hmac.new(secret.encode("utf-8"), body, "sha256").hexdigest()
    assert verify_webhook_signature(body=body, signature_header=sig, secret=secret) is True


def test_verify_webhook_signature_accepts_prefixed() -> None:
    """sha256=<hex> prefixed form also matches."""
    secret = "topsecret"
    body = b"{}"
    sig = hmac.new(secret.encode("utf-8"), body, "sha256").hexdigest()
    assert verify_webhook_signature(body=body, signature_header=f"sha256={sig}", secret=secret) is True


def test_verify_webhook_signature_rejects_tampered_body() -> None:
    """Modifying the body invalidates the signature."""
    secret = "s"
    sig = hmac.new(secret.encode("utf-8"), b"good", "sha256").hexdigest()
    assert verify_webhook_signature(body=b"bad", signature_header=sig, secret=secret) is False


def test_verify_webhook_signature_rejects_empty_secret() -> None:
    """Empty secret is fail-closed."""
    assert verify_webhook_signature(body=b"x", signature_header="abc", secret="") is False


# ---------------------------------------------------------------------------
# parse_json_payload
# ---------------------------------------------------------------------------


def test_parse_json_payload_round_trip() -> None:
    """Valid JSON object survives the round-trip."""
    body = b'{"x": 1}'
    assert parse_json_payload(body) == {"x": 1}


def test_parse_json_payload_rejects_array() -> None:
    """Non-object top-level is rejected."""
    with pytest.raises(ValueError):
        parse_json_payload(b"[1, 2]")


def test_parse_json_payload_rejects_bad_json() -> None:
    """Malformed JSON raises ValueError."""
    with pytest.raises(ValueError):
        parse_json_payload(b"not-json")


# ---------------------------------------------------------------------------
# dispatch_telemetry_event
# ---------------------------------------------------------------------------


def test_dispatch_returns_skipped_when_source_disabled() -> None:
    """Disabled source short-circuits before retrieval."""
    settings = _settings_with(enabled=False)
    retriever = _StaticRetriever()
    hook = _DispatchRecorder()
    event = TelemetryEvent(source="sentry", fingerprint="fp")
    record = dispatch_telemetry_event(event, settings=settings, retriever=retriever, dispatch_hook=hook)
    assert record.outcome == "skipped"
    assert hook.calls == []
    assert retriever.calls == []


def test_dispatch_returns_skipped_when_fingerprint_empty() -> None:
    """Empty fingerprint refuses to ground/dispatch."""
    settings = _settings_with()
    record = dispatch_telemetry_event(
        TelemetryEvent(source="sentry", fingerprint=""),
        settings=settings,
        retriever=_StaticRetriever(),
        dispatch_hook=_DispatchRecorder(),
    )
    assert record.outcome == "skipped"


def test_dispatch_stubbed_for_deferred_sources() -> None:
    """Datadog / Loki / custom_jsonl record stubbed without spawning."""
    settings = _settings_with(source="datadog")
    hook = _DispatchRecorder()
    record = dispatch_telemetry_event(
        TelemetryEvent(source="datadog", fingerprint="fp"),
        settings=settings,
        retriever=_StaticRetriever(),
        dispatch_hook=hook,
    )
    assert record.outcome == "stubbed"
    assert hook.calls == []
    assert record.log_lines == 2


def test_dispatch_spawns_for_sentry_event() -> None:
    """Enabled Sentry event makes it through to the dispatch hook."""
    settings = _settings_with()
    hook = _DispatchRecorder(result=GroundedDispatchResult(success=True, commit_sha="sha-1", cost_usd=0.05))
    audit = _AuditRecorder()
    record = dispatch_telemetry_event(
        TelemetryEvent(source="sentry", fingerprint="fp", title="t"),
        settings=settings,
        retriever=_StaticRetriever(),
        dispatch_hook=hook,
        audit=audit,
    )
    assert record.outcome == "dispatched"
    assert record.commit_sha == "sha-1"
    assert hook.calls and hook.calls[0][0].startswith("fix(sentry):")
    assert audit.events and audit.events[0][0] == "autofix.telemetry.dispatch"


def test_dispatch_cost_capped_when_hook_overspends() -> None:
    """Hook spending past the per-event cap flips to cost_capped."""
    settings = _settings_with(cost_cap_usd=0.10)
    hook = _DispatchRecorder(result=GroundedDispatchResult(success=True, cost_usd=0.50))
    record = dispatch_telemetry_event(
        TelemetryEvent(source="sentry", fingerprint="fp"),
        settings=settings,
        retriever=_StaticRetriever(),
        dispatch_hook=hook,
    )
    assert record.outcome == "cost_capped"
    assert record.cost_usd == pytest.approx(0.50)


def test_dispatch_cost_capped_when_cap_is_zero() -> None:
    """Zero cap refuses to spawn even before invoking the hook."""
    settings = _settings_with(cost_cap_usd=0.0)
    hook = _DispatchRecorder()
    record = dispatch_telemetry_event(
        TelemetryEvent(source="sentry", fingerprint="fp"),
        settings=settings,
        retriever=_StaticRetriever(),
        dispatch_hook=hook,
    )
    assert record.outcome == "cost_capped"
    assert hook.calls == []


def test_dispatch_errored_when_hook_raises() -> None:
    """Hook exceptions are caught and surfaced as ``errored``."""
    settings = _settings_with()
    hook = _DispatchRecorder(raised=RuntimeError("boom"))
    record = dispatch_telemetry_event(
        TelemetryEvent(source="sentry", fingerprint="fp"),
        settings=settings,
        retriever=_StaticRetriever(),
        dispatch_hook=hook,
    )
    assert record.outcome == "errored"
    assert "boom" in record.reason


def test_dispatch_swallows_retriever_failure() -> None:
    """A failing retriever does not crash the dispatcher."""
    settings = _settings_with()
    retriever = _StaticRetriever(raised=RuntimeError("retr"))
    hook = _DispatchRecorder()
    record = dispatch_telemetry_event(
        TelemetryEvent(source="sentry", fingerprint="fp"),
        settings=settings,
        retriever=retriever,
        dispatch_hook=hook,
    )
    # Retriever raised, but dispatch continues (with empty context).
    assert record.outcome == "dispatched"
    assert record.log_lines == 0


def test_dispatch_record_is_immutable() -> None:
    """TelemetryDispatchRecord is a frozen dataclass."""
    import dataclasses

    record = TelemetryDispatchRecord(outcome="dispatched", source="sentry", fingerprint="fp")
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.outcome = "errored"  # type: ignore[misc]
