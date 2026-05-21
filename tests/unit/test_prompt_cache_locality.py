"""Tests for prompt-cache prefix locality enforcement and drift counting.

Covers the three primary acceptance criteria of the
``feat/prompt-cache-locality`` ticket:

1. Two consecutive same-role spawns produce identical prefix bytes
   (drift counter stays at zero).
2. The drift counter increments when the prefix changes between
   consecutive same-role spawns and records a usable reason label.
3. The prefix is stable across reordering of stable header fields
   (caller insertion order does not break Anthropic's byte-equality
   contract).
"""

from __future__ import annotations

from bernstein.core.agents.prompt_cache_locality import (
    DriftSnapshot,
    PromptCacheLocality,
    build_stable_prefix,
    hash_prefix,
)

# ---------------------------------------------------------------------------
# build_stable_prefix - header sorting + body verbatim
# ---------------------------------------------------------------------------


class TestBuildStablePrefix:
    """Verify the canonicalisation contract used for cache hits."""

    def test_identical_inputs_produce_identical_bytes(self) -> None:
        """Two calls with the same logical inputs must be byte-equal."""
        header = {"role": "backend", "templates_hash": "abc123"}
        body = "## Role\nYou are a backend engineer.\n"
        first = build_stable_prefix(header=header, body=body)
        second = build_stable_prefix(header=header, body=body)
        assert first == second
        assert hash_prefix(first) == hash_prefix(second)

    def test_header_field_reordering_does_not_change_bytes(self) -> None:
        """Caller insertion order must not break the cache."""
        header_a = {"role": "backend", "templates_hash": "abc", "git_safety": "v1"}
        header_b = {"templates_hash": "abc", "git_safety": "v1", "role": "backend"}
        body = "static body"
        prefix_a = build_stable_prefix(header=header_a, body=body)
        prefix_b = build_stable_prefix(header=header_b, body=body)
        assert prefix_a == prefix_b

    def test_header_value_change_changes_bytes(self) -> None:
        """A real header-field edit must produce a different prefix."""
        body = "shared body"
        prefix_a = build_stable_prefix(header={"role": "backend"}, body=body)
        prefix_b = build_stable_prefix(header={"role": "qa"}, body=body)
        assert prefix_a != prefix_b

    def test_body_change_changes_bytes(self) -> None:
        """Body edits must register as drift (cache would break on Anthropic)."""
        header = {"role": "backend"}
        prefix_a = build_stable_prefix(header=header, body="body v1")
        prefix_b = build_stable_prefix(header=header, body="body v2")
        assert prefix_a != prefix_b

    def test_empty_header_is_safe(self) -> None:
        """Missing header is allowed and still produces a deterministic prefix."""
        prefix_a = build_stable_prefix(body="just a body")
        prefix_b = build_stable_prefix(header={}, body="just a body")
        prefix_c = build_stable_prefix(header=None, body="just a body")
        assert prefix_a == prefix_b == prefix_c

    def test_empty_string_value_preserved(self) -> None:
        """Empty values in headers must round-trip (carry meaning)."""
        prefix_with = build_stable_prefix(
            header={"role": "backend", "agent_protocol_prefix": ""},
            body="b",
        )
        prefix_without = build_stable_prefix(
            header={"role": "backend"},
            body="b",
        )
        # Both representations are *different* - the empty key is an
        # explicit declaration; dropping it would silently change the
        # cache key in production.
        assert prefix_with != prefix_without


# ---------------------------------------------------------------------------
# hash_prefix
# ---------------------------------------------------------------------------


def test_hash_prefix_is_deterministic() -> None:
    """SHA-256 over UTF-8 produces identical digests for identical input."""
    s = "## Role\nYou are a backend engineer.\n"
    assert hash_prefix(s) == hash_prefix(s)


def test_hash_prefix_differs_on_change() -> None:
    """A single-byte difference flips the digest."""
    assert hash_prefix("a") != hash_prefix("b")


# ---------------------------------------------------------------------------
# PromptCacheLocality.observe - drift accounting per role
# ---------------------------------------------------------------------------


class TestPromptCacheLocalityDriftCounter:
    """Verify per-role drift counting against the ticket's primary AC."""

    def test_two_consecutive_identical_spawns_no_drift(self) -> None:
        """Same role, same prefix bytes → drift_count stays at zero."""
        loc = PromptCacheLocality()
        prefix = build_stable_prefix(
            header={"role": "backend", "templates_hash": "h1"},
            body="static body",
        )
        first = loc.observe(role="backend", prefix=prefix)
        second = loc.observe(role="backend", prefix=prefix)
        assert first.drift_count == 0
        assert second.drift_count == 0
        assert second.spawn_count == 2
        assert first.last_hash == second.last_hash

    def test_drift_counter_increments_on_prefix_change(self) -> None:
        """Same role, different prefix bytes → drift_count == 1."""
        loc = PromptCacheLocality()
        body_v1 = "static body version 1"
        body_v2 = "static body version 2"
        loc.observe(
            role="backend",
            prefix=build_stable_prefix(header={"role": "backend"}, body=body_v1),
        )
        snap = loc.observe(
            role="backend",
            prefix=build_stable_prefix(header={"role": "backend"}, body=body_v2),
        )
        assert snap.drift_count == 1
        assert snap.last_reason == "body_changed"
        assert snap.spawn_count == 2

    def test_drift_counter_does_not_increment_across_reordering(self) -> None:
        """Reordering stable header fields is *not* drift."""
        loc = PromptCacheLocality()
        body = "shared body"
        prefix_a = build_stable_prefix(
            header={"role": "backend", "templates_hash": "h1", "git_safety": "v1"},
            body=body,
        )
        prefix_b = build_stable_prefix(
            header={"git_safety": "v1", "templates_hash": "h1", "role": "backend"},
            body=body,
        )
        loc.observe(role="backend", prefix=prefix_a)
        snap = loc.observe(role="backend", prefix=prefix_b)
        assert snap.drift_count == 0, "header field reordering must not break the cache"

    def test_first_spawn_never_counts_as_drift(self) -> None:
        """The very first observation establishes the baseline."""
        loc = PromptCacheLocality()
        snap = loc.observe(
            role="backend",
            prefix=build_stable_prefix(header={"role": "backend"}, body="b"),
        )
        assert snap.drift_count == 0
        assert snap.spawn_count == 1
        assert snap.last_hash  # baseline recorded

    def test_drift_per_role_is_isolated(self) -> None:
        """Drift in one role must not pollute the count for another role."""
        loc = PromptCacheLocality()
        loc.observe(
            role="backend",
            prefix=build_stable_prefix(header={"role": "backend"}, body="v1"),
        )
        loc.observe(
            role="backend",
            prefix=build_stable_prefix(header={"role": "backend"}, body="v2"),
        )
        loc.observe(
            role="qa",
            prefix=build_stable_prefix(header={"role": "qa"}, body="v1"),
        )

        assert loc.snapshot("backend").drift_count == 1
        assert loc.snapshot("qa").drift_count == 0

    def test_reason_hint_is_recorded_on_drift(self) -> None:
        """A caller-supplied reason hint must propagate to the snapshot."""
        loc = PromptCacheLocality()
        loc.observe(
            role="backend",
            prefix=build_stable_prefix(header={"role": "backend"}, body="v1"),
        )
        snap = loc.observe(
            role="backend",
            prefix=build_stable_prefix(header={"role": "backend"}, body="v2"),
            reason_hint="role_template_edited",
        )
        assert snap.last_reason == "role_template_edited"

    def test_unknown_reason_buckets_under_unknown(self) -> None:
        """Reason labels outside the closed taxonomy bucket under ``unknown``."""
        loc = PromptCacheLocality()
        loc.observe(
            role="backend",
            prefix=build_stable_prefix(header={"role": "backend"}, body="v1"),
        )
        snap = loc.observe(
            role="backend",
            prefix=build_stable_prefix(header={"role": "backend"}, body="v2"),
            reason_hint="something_invented_by_caller",
        )
        # An invalid hint normalises to "unknown"; the locality module
        # records that as the reason on the snapshot.
        assert snap.last_reason == "unknown"


# ---------------------------------------------------------------------------
# Prometheus integration - ensure the counter is incremented
# ---------------------------------------------------------------------------


class _RecordingCounter:
    """Minimal stub matching the prometheus_client Counter API."""

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []
        self._last: dict[str, str] = {}

    def labels(self, **kwargs: str) -> _RecordingCounter:
        self._last = kwargs.copy()
        return self

    def inc(self, value: float = 1.0) -> None:
        self.calls.append(self._last)


def test_prometheus_counter_incremented_on_drift() -> None:
    """Drift events must call ``counter.labels(role, reason).inc()``."""
    counter = _RecordingCounter()
    loc = PromptCacheLocality(prometheus_counter=counter)
    loc.observe(
        role="backend",
        prefix=build_stable_prefix(header={"role": "backend"}, body="v1"),
    )
    loc.observe(
        role="backend",
        prefix=build_stable_prefix(header={"role": "backend"}, body="v2"),
        reason_hint="tool_set_changed",
    )
    assert len(counter.calls) == 1
    assert counter.calls[0] == {"role": "backend", "reason": "tool_set_changed"}


def test_prometheus_counter_silent_on_no_drift() -> None:
    """No drift → no counter increments."""
    counter = _RecordingCounter()
    loc = PromptCacheLocality(prometheus_counter=counter)
    prefix = build_stable_prefix(header={"role": "backend"}, body="b")
    loc.observe(role="backend", prefix=prefix)
    loc.observe(role="backend", prefix=prefix)
    assert counter.calls == []


# ---------------------------------------------------------------------------
# DriftSnapshot is a copy - internal mutation is not exposed
# ---------------------------------------------------------------------------


def test_snapshot_is_independent_copy() -> None:
    """Mutating a returned snapshot must not affect tracker state."""
    loc = PromptCacheLocality()
    loc.observe(
        role="backend",
        prefix=build_stable_prefix(header={"role": "backend"}, body="b"),
    )
    snap = loc.snapshot("backend")
    snap.drift_count = 999
    assert loc.snapshot("backend").drift_count == 0


def test_unknown_role_returns_zero_snapshot() -> None:
    """Querying a role with no observations returns a fresh empty snapshot."""
    loc = PromptCacheLocality()
    snap = loc.snapshot("never-spawned")
    assert snap == DriftSnapshot()


def test_reset_clears_state() -> None:
    """``reset()`` drops all tracked roles."""
    loc = PromptCacheLocality()
    prefix = build_stable_prefix(header={"role": "backend"}, body="b")
    loc.observe(role="backend", prefix=prefix)
    loc.reset()
    assert loc.snapshot("backend") == DriftSnapshot()
