"""Tests for vendor-specific cache hints emitted by ``build_stable_prefix``.

The default ``vendor="generic"`` path is exercised in
``test_prompt_cache_locality.py``.  This module covers the three
vendor-aware paths added by the observability hardening wave:

* ``"openai"`` — pads the prefix to the next 256-token boundary.
* ``"anthropic"`` — emits a message structure tagged with
  ``cache_control: {"type": "ephemeral"}`` markers (≤4 segments).
* ``"gemini"`` — emits a candidate ``cachedContents/<sha256>`` handle.

At least 4 tests per vendor.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from bernstein.core.agents.prompt_cache_locality import (
    _ANTHROPIC_MAX_SEGMENTS,
    _OPENAI_BOUNDARY_TOKENS,
    StablePrefix,
    _count_openai_tokens,
    build_stable_prefix,
    hash_prefix,
)

# ---------------------------------------------------------------------------
# Generic path (back-compat sanity)
# ---------------------------------------------------------------------------


def test_generic_default_returns_str_for_back_compat() -> None:
    """Default behaviour unchanged — generic path returns bare ``str``."""
    out = build_stable_prefix(header={"role": "backend"}, body="b")
    assert isinstance(out, str)


def test_explicit_generic_returns_str() -> None:
    out = build_stable_prefix(header={"role": "backend"}, body="b", vendor="generic")
    assert isinstance(out, str)


# ---------------------------------------------------------------------------
# OpenAI path — 256-token boundary alignment
# ---------------------------------------------------------------------------


class TestOpenAIVendor:
    """``vendor="openai"`` pads to the next 256-token boundary."""

    def test_returns_stable_prefix_bundle(self) -> None:
        out = build_stable_prefix(header={"role": "backend"}, body="b", vendor="openai")
        assert isinstance(out, StablePrefix)
        assert out.vendor == "openai"
        # Canonical text must round-trip the generic output exactly.
        generic = build_stable_prefix(header={"role": "backend"}, body="b")
        assert out.text == generic

    def test_padded_text_is_at_least_canonical_length(self) -> None:
        out = build_stable_prefix(
            header={"role": "backend", "templates_hash": "h"},
            body="this is the body",
            vendor="openai",
        )
        assert len(out.padded_text) >= len(out.text)
        # Padded text starts with the canonical text — alignment never
        # mutates the original prefix bytes; padding goes to the right.
        assert out.padded_text.startswith(out.text)

    def test_padded_length_lands_on_boundary(self) -> None:
        """Token count of the padded text divides ``_OPENAI_BOUNDARY_TOKENS``."""
        out = build_stable_prefix(
            header={"role": "backend"},
            body="lorem ipsum dolor sit amet " * 10,
            vendor="openai",
        )
        # Use the same token-count helper the implementation uses so the
        # heuristic-vs-tiktoken environment stays internally consistent.
        token_count = _count_openai_tokens(out.padded_text)
        assert token_count % _OPENAI_BOUNDARY_TOKENS == 0

    def test_idempotent_for_already_aligned_input(self) -> None:
        """Reblowing through the OpenAI path on aligned text changes nothing."""
        out = build_stable_prefix(
            header={"role": "backend"},
            body="x" * (4 * _OPENAI_BOUNDARY_TOKENS),  # ~256 tokens via heuristic
            vendor="openai",
        )
        # The padded text must still be aligned.
        assert _count_openai_tokens(out.padded_text) % _OPENAI_BOUNDARY_TOKENS == 0
        # No extra padding beyond what the original text already needed
        # to reach a boundary — the padded text is shorter than two
        # boundaries' worth of characters.
        assert len(out.padded_text) <= len(out.text) + (_OPENAI_BOUNDARY_TOKENS * 4)

    def test_padded_text_aligns_short_canonical_input(self) -> None:
        """Even a short canonical text (just the header separator) aligns."""
        out = build_stable_prefix(header={}, body="", vendor="openai")
        # Canonical text contains the separator sentinel even when both
        # header and body are empty — alignment still applies.
        assert out.text  # non-empty due to the separator
        token_count = _count_openai_tokens(out.padded_text)
        assert token_count % _OPENAI_BOUNDARY_TOKENS == 0
        assert out.padded_text.startswith(out.text)


# ---------------------------------------------------------------------------
# Anthropic path — segmented message structure with cache_control
# ---------------------------------------------------------------------------


class TestAnthropicVendor:
    """``vendor="anthropic"`` emits ≤4 segments with ephemeral cache_control."""

    def test_returns_stable_prefix_bundle(self) -> None:
        out = build_stable_prefix(
            header={"role": "backend"},
            body="x",
            vendor="anthropic",
        )
        assert isinstance(out, StablePrefix)
        assert out.vendor == "anthropic"
        assert out.segments  # non-empty

    def test_segments_count_within_limit(self) -> None:
        out = build_stable_prefix(
            header={"role": "backend", "templates_hash": "h"},
            body="role body",
            vendor="anthropic",
        )
        assert len(out.segments) <= _ANTHROPIC_MAX_SEGMENTS

    def test_prefix_segments_carry_ephemeral_cache_control(self) -> None:
        out = build_stable_prefix(
            header={"role": "backend"},
            body="prefix body",
            vendor="anthropic",
        )
        cached_segments = [seg for seg in out.segments if "cache_control" in seg]
        assert cached_segments, "at least one segment must be cache-tagged"
        for seg in cached_segments:
            assert seg["cache_control"] == {"type": "ephemeral"}
            assert seg["type"] == "text"

    def test_segments_round_trip_to_canonical_text(self) -> None:
        """Concatenating segment text fields must reproduce the canonical text."""
        out = build_stable_prefix(
            header={"role": "backend", "templates_hash": "h"},
            body="role body",
            vendor="anthropic",
        )
        joined = "".join(str(seg["text"]) for seg in out.segments)
        assert joined == out.text

    def test_empty_body_drops_body_segment(self) -> None:
        """No body → no body segment (compactness)."""
        out = build_stable_prefix(
            header={"role": "backend"},
            body="",
            vendor="anthropic",
        )
        body_segments = [seg for seg in out.segments if "body" in str(seg.get("text", ""))]
        assert body_segments == []
        # Header segment must still exist and be cache-tagged.
        assert any("cache_control" in seg for seg in out.segments)


# ---------------------------------------------------------------------------
# Gemini path — cachedContents handle from canonical SHA-256
# ---------------------------------------------------------------------------


class TestGeminiVendor:
    """``vendor="gemini"`` emits a candidate cachedContents handle."""

    def test_returns_stable_prefix_bundle(self) -> None:
        out = build_stable_prefix(
            header={"role": "backend"},
            body="b",
            vendor="gemini",
        )
        assert isinstance(out, StablePrefix)
        assert out.vendor == "gemini"
        assert out.cached_content_handle.startswith("cachedContents/")

    def test_handle_uses_canonical_sha256(self) -> None:
        out = build_stable_prefix(
            header={"role": "backend"},
            body="canonical body",
            vendor="gemini",
        )
        expected = f"cachedContents/{hash_prefix(out.text)}"
        assert out.cached_content_handle == expected

    def test_handle_is_stable_across_calls(self) -> None:
        a = build_stable_prefix(header={"role": "backend"}, body="b", vendor="gemini")
        b = build_stable_prefix(header={"role": "backend"}, body="b", vendor="gemini")
        assert a.cached_content_handle == b.cached_content_handle

    def test_handle_changes_when_prefix_bytes_change(self) -> None:
        a = build_stable_prefix(header={"role": "backend"}, body="v1", vendor="gemini")
        b = build_stable_prefix(header={"role": "backend"}, body="v2", vendor="gemini")
        assert a.cached_content_handle != b.cached_content_handle

    def test_handle_invariant_under_header_reordering(self) -> None:
        """Caller insertion order must not change the Gemini handle."""
        a = build_stable_prefix(
            header={"role": "backend", "templates_hash": "h"},
            body="b",
            vendor="gemini",
        )
        b = build_stable_prefix(
            header={"templates_hash": "h", "role": "backend"},
            body="b",
            vendor="gemini",
        )
        assert a.cached_content_handle == b.cached_content_handle
