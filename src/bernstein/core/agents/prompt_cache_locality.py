"""Prompt-cache prefix locality enforcement and drift accounting.

This module provides a thin wrapper around prompt assembly that guarantees
byte-identical prefixes for cache hits across consecutive same-role spawns
and increments a drift counter (in-memory + Prometheus) when the prefix
changes between spawns.

Anthropic's prompt cache awards a 90% input-token discount on cache hits,
OpenAI's awards 50%, and Google's Gemini context cache charges per-hour
storage; all three contracts require the *prefix* to be byte-identical
between requests.  Bernstein already builds cacheable prefixes via
``mark_cacheable_prefix`` and ``extract_system_prefix``; this module is
the layer that *enforces* stability across spawns and surfaces drift so
the operator can see the cost-leak the moment a prefix change broke
the cache.

Design choices:

* The drift counter is per-role.  Different roles have different
  prefixes by definition; only same-role drift is interesting.
* The prefix is canonicalised before hashing: the stable header fields
  are sorted lexicographically by key so that callers do not break the
  cache by reordering ``role: backend\\ntemplates_hash: ...`` in their
  rendering code.
* The body of the prefix (role template, project context) is appended
  verbatim and not re-sorted: rearranging real prompt text *would*
  change the cache key on Anthropic's side, so a hash mismatch in that
  region is a true drift signal.
* Backed by a Prometheus counter on the shared registry so the metric
  shows up on ``/metrics`` automatically; in-memory snapshot is also
  kept for tests and the ``cache report`` CLI.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, overload

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

#: Supported vendor cache backends.  ``"generic"`` keeps the legacy
#: byte-string return shape so existing callers do not change.
Vendor = Literal["generic", "openai", "anthropic", "gemini"]

#: OpenAI prompt-cache requires the cacheable prefix to align to a
#: 256-token boundary (see Bernstein cache locality docs).  When
#: ``tiktoken`` is installed we use the model's tokeniser; otherwise we
#: fall back to a 4-chars/token heuristic that matches OpenAI's own
#: rule-of-thumb for English text.
_OPENAI_BOUNDARY_TOKENS = 256
_HEURISTIC_CHARS_PER_TOKEN = 4

#: Anthropic's prompt-cache API accepts up to 4 cache_control breakpoints
#: per request.  See https://docs.anthropic.com/en/docs/build-with-claude/
#: prompt-caching#structuring-your-prompt.
_ANTHROPIC_MAX_SEGMENTS = 4

#: Padding character used when zero-padding the OpenAI prefix to the
#: next 256-token boundary.  A space is benign for the model and does
#: not collide with the header-separator sentinel.
_OPENAI_PAD_CHAR = " "


# ---------------------------------------------------------------------------
# Drift reason taxonomy — keep this closed so the Prometheus label set is
# bounded.  Unknown values bucket under ``unknown``.
# ---------------------------------------------------------------------------

_KNOWN_DRIFT_REASONS: frozenset[str] = frozenset(
    {
        "tool_set_changed",
        "time_inserted",
        "role_template_edited",
        "dynamic_field_in_prefix",
        "header_field_changed",
        "body_changed",
        "unknown",
    },
)


def _normalise_reason(raw: str) -> str:
    """Normalise a drift reason against the closed taxonomy."""
    value = (raw or "").strip().lower()
    return value if value in _KNOWN_DRIFT_REASONS else "unknown"


# ---------------------------------------------------------------------------
# Stable prefix construction
# ---------------------------------------------------------------------------

# Sentinel separator inserted between header and body so callers can split
# the prefix back into its parts deterministically.  Chosen to be unlikely
# to collide with real prompt content.
_HEADER_SEPARATOR = "\n<!--bernstein:prefix-header-end-->\n"


@dataclass(frozen=True)
class StablePrefix:
    """Vendor-aware cache-prefix bundle.

    Returned by :func:`build_stable_prefix` when a vendor other than
    ``"generic"`` is requested.  The bundle always carries the
    canonical bytes (``text``) so the caller can still hash them, plus
    one or more vendor-specific cache hints:

    * ``segments`` — an ordered list of message-shaped dicts.  For
      Anthropic, each entry has the form
      ``{"type": "text", "text": "...", "cache_control": {...}}``
      with ``cache_control`` set to ``{"type": "ephemeral"}`` on the
      prefix segments.
    * ``cached_content_handle`` — Gemini's canonical handle name; it is
      the lowercase ``cachedContent/<sha256>`` form so the caller can
      use it to look up an existing entry on the API.
    * ``padded_text`` — OpenAI's text padded to the next 256-token
      boundary so consecutive prompts share a cache-aligned prefix.

    Empty fields denote "vendor doesn't expose this hint".
    """

    text: str
    vendor: Vendor = "generic"
    segments: list[dict[str, object]] = field(default_factory=list)
    cached_content_handle: str = ""
    padded_text: str = ""


@overload
def build_stable_prefix(
    *,
    header: Mapping[str, str] | None = ...,
    body: str = ...,
    vendor: Literal["generic"] = ...,
) -> str: ...


@overload
def build_stable_prefix(
    *,
    header: Mapping[str, str] | None = ...,
    body: str = ...,
    vendor: Literal["openai", "anthropic", "gemini"],
) -> StablePrefix: ...


def build_stable_prefix(
    *,
    header: Mapping[str, str] | None = None,
    body: str = "",
    vendor: Vendor = "generic",
) -> str | StablePrefix:
    """Build a byte-stable cache prefix from a header dict and a body string.

    The header is canonicalised by sorting keys lexicographically and
    rendering each entry as ``"<key>: <value>"`` on its own line.  This
    means callers cannot break the cache by reordering header fields.
    The body is appended verbatim after a fixed separator.

    Vendor selection
    ----------------
    The optional ``vendor`` argument selects one of three vendor-aware
    output paths.  ``"generic"`` (the default) keeps the legacy
    behaviour and returns a plain :class:`str` so existing callers do
    not need to change.  The other three return a :class:`StablePrefix`
    bundle that carries vendor-specific cache hints in addition to the
    canonical bytes:

    * ``"openai"`` — pads the prefix to the next 256-token boundary
      (rounded up via ``tiktoken`` when installed; otherwise via a
      4 chars/token heuristic).  OpenAI's prompt-cache hashes only the
      first 1024-token prefix in 256-token chunks; alignment maximises
      hit rate.
    * ``"anthropic"`` — splits the prompt into at most 4 segments and
      tags the prefix segments with ``cache_control: {"type": "ephemeral"}``
      so Anthropic's prompt cache treats them as a stable breakpoint.
    * ``"gemini"`` — emits a candidate ``cachedContent`` handle name
      derived from the canonical SHA-256 of the prefix bytes.  The
      caller can use the handle to look up an existing context-cache
      entry on Vertex AI / Gemini.

    Args:
        header: Mapping of stable header field names to values
            (e.g. ``{"role": "backend", "templates_hash": "abc..."}``).
            Keys and values are coerced to ``str``.  Empty-string values
            are kept (they may carry meaning, e.g. an empty agent
            protocol prefix).  ``None`` is treated as an empty mapping.
        body: Free-form prefix body (role template, project context,
            git safety protocol, etc.).  Appended verbatim.
        vendor: Cache backend selection.  Default ``"generic"`` keeps
            the legacy ``str`` return type for backward compatibility.

    Returns:
        For ``vendor == "generic"`` — the deterministic prefix string.
        Otherwise a :class:`StablePrefix` bundle with vendor-specific
        cache hints.
    """
    items = sorted((str(k), str(v)) for k, v in (header or {}).items())
    header_str = "\n".join(f"{k}: {v}" for k, v in items)
    text = f"{header_str}{_HEADER_SEPARATOR}{body}"

    if vendor == "generic":
        return text
    if vendor == "openai":
        return StablePrefix(
            text=text,
            vendor="openai",
            padded_text=_align_to_openai_boundary(text),
        )
    if vendor == "anthropic":
        return StablePrefix(
            text=text,
            vendor="anthropic",
            segments=_anthropic_cache_segments(header_str, body),
        )
    if vendor == "gemini":
        return StablePrefix(
            text=text,
            vendor="gemini",
            cached_content_handle=_gemini_cached_content_handle(text),
        )
    # Defensive: Literal narrowing should make this unreachable.
    msg = f"unsupported vendor: {vendor!r}"
    raise ValueError(msg)


# ---------------------------------------------------------------------------
# Vendor-specific helpers
# ---------------------------------------------------------------------------


def _count_openai_tokens(text: str) -> int:
    """Return token count for *text*, preferring tiktoken when available.

    Args:
        text: The input string.

    Returns:
        Token count.  Falls back to ``len(text) // 4`` (rounded up by
        the caller via ceiling division) when ``tiktoken`` is not
        installed in the environment.
    """
    try:
        import tiktoken  # type: ignore[import-not-found]
    except Exception:
        # Heuristic fallback — OpenAI's own rule of thumb is ~4 chars
        # per token for English text.  We round UP via ceiling division
        # so a partially-filled token still counts as one.
        return -(-len(text) // _HEURISTIC_CHARS_PER_TOKEN)
    try:
        # ``cl100k_base`` is the encoding used by all current GPT-4 /
        # GPT-4o / GPT-3.5 chat completions.
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:  # pragma: no cover — defensive
        return -(-len(text) // _HEURISTIC_CHARS_PER_TOKEN)


def _align_to_openai_boundary(text: str) -> str:
    """Right-pad *text* to the next ``_OPENAI_BOUNDARY_TOKENS`` boundary.

    Args:
        text: The canonical prefix text.

    Returns:
        ``text`` if already aligned, otherwise ``text`` followed by the
        smallest amount of padding needed to reach the next boundary.
    """
    if not text:
        return text
    tokens = _count_openai_tokens(text)
    remainder = tokens % _OPENAI_BOUNDARY_TOKENS
    if remainder == 0:
        return text
    pad_tokens = _OPENAI_BOUNDARY_TOKENS - remainder
    # Pad by character count using the chars/token heuristic so the
    # alignment remains deterministic in environments without tiktoken.
    pad_chars = pad_tokens * _HEURISTIC_CHARS_PER_TOKEN
    return f"{text}{_OPENAI_PAD_CHAR * pad_chars}"


def _anthropic_cache_segments(
    header_str: str,
    body: str,
) -> list[dict[str, object]]:
    """Build Anthropic message segments with ``cache_control`` markers.

    The result has at most :data:`_ANTHROPIC_MAX_SEGMENTS` entries:

    1. ``header`` — the canonicalised header block (always tagged
       ephemeral so the system prefix is cached).
    2. ``separator`` — the boundary sentinel.
    3. ``body`` — the role/project/git_safety body (tagged ephemeral).
    4. (reserved for caller-appended directive — no entry emitted by
       this builder so callers can attach a 4th cache_control block on
       a final user/assistant turn if desired).

    Empty header / body segments are dropped to keep the message list
    compact.

    Args:
        header_str: Canonicalised header text (already ``\\n``-joined
            ``key: value`` pairs).
        body: Verbatim prefix body.

    Returns:
        List of message-shaped dicts with ``cache_control`` markers on
        the prefix segments.
    """
    segments: list[dict[str, object]] = []
    if header_str:
        segments.append(
            {
                "type": "text",
                "text": header_str,
                "cache_control": {"type": "ephemeral"},
            },
        )
    # Always include the deterministic separator so the segment chain
    # round-trips back to the canonical text bytes.
    segments.append(
        {
            "type": "text",
            "text": _HEADER_SEPARATOR,
        },
    )
    if body:
        segments.append(
            {
                "type": "text",
                "text": body,
                "cache_control": {"type": "ephemeral"},
            },
        )
    # Anthropic's prompt-cache supports at most 4 cache_control
    # breakpoints; the splitter above never emits more than 3 prefix
    # segments so the caller can safely add one more on a downstream
    # user-turn block.
    assert len(segments) <= _ANTHROPIC_MAX_SEGMENTS
    return segments


def _gemini_cached_content_handle(text: str) -> str:
    """Return the canonical Gemini ``cachedContents`` resource name.

    Args:
        text: The prefix text.

    Returns:
        ``cachedContents/<sha256>`` — the candidate handle that the
        caller can pass to ``GenerativeModel.from_cached_content`` on
        Vertex AI / Gemini.  The hash is the same SHA-256 used by
        :func:`hash_prefix`, so two consecutive prefixes with the same
        bytes yield the same handle.
    """
    return f"cachedContents/{hash_prefix(text)}"


def hash_prefix(prefix: str) -> str:
    """Compute the SHA-256 hex digest of *prefix* (UTF-8 encoded).

    Args:
        prefix: The cache prefix string.

    Returns:
        Lowercase 64-char hex string.
    """
    return hashlib.sha256(prefix.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Drift tracker — process-local, per-role.
# ---------------------------------------------------------------------------


@dataclass
class DriftSnapshot:
    """Per-role drift tracking state.

    Attributes:
        last_hash: Hash of the most recently seen prefix for this role.
        drift_count: Number of drift events recorded for this role.
        last_reason: Reason classifier for the most recent drift event,
            or ``""`` if no drift has occurred.
        spawn_count: Number of spawns observed for this role (including
            the first one, which never counts as drift).
    """

    last_hash: str = ""
    drift_count: int = 0
    last_reason: str = ""
    spawn_count: int = 0


class PromptCacheLocality:
    """Track per-role prefix hashes and increment a drift counter on change.

    Thread-safe.  The first spawn for any role is *not* counted as drift —
    drift is by definition a *change* relative to a previous observation.

    Args:
        prometheus_counter: Optional Prometheus ``Counter`` for
            ``prompt_cache_drift_total{role,reason}``.  When ``None``,
            only in-memory state is updated.  Tests typically pass
            ``None`` to avoid global registry pollution.
    """

    def __init__(self, prometheus_counter: object | None = None) -> None:
        self._lock = threading.Lock()
        self._snapshots: dict[str, DriftSnapshot] = defaultdict(DriftSnapshot)
        self._counter = prometheus_counter

    def observe(
        self,
        *,
        role: str,
        prefix: str,
        reason_hint: str = "",
    ) -> DriftSnapshot:
        """Record a prefix observation for *role* and surface drift.

        Args:
            role: Stable role name (e.g. ``"backend"``, ``"qa"``).  Used
                as the Prometheus label and the in-memory key.
            prefix: The fully assembled cache prefix (typically the
                output of :func:`build_stable_prefix`).
            reason_hint: Optional pre-classified drift reason.  When the
                caller already knows *why* the prefix changed (e.g. the
                tool set was edited), pass it here so the Prometheus
                label is precise.  Falls back to ``body_changed`` when
                empty and a drift is detected.

        Returns:
            A snapshot copy of the role's tracking state *after* the
            observation has been applied.
        """
        digest = hash_prefix(prefix)
        normalised_reason = _normalise_reason(reason_hint) if reason_hint else ""

        with self._lock:
            snap = self._snapshots[role]
            snap.spawn_count += 1
            drifted = bool(snap.last_hash) and snap.last_hash != digest
            if drifted:
                reason = normalised_reason or "body_changed"
                snap.drift_count += 1
                snap.last_reason = reason
                self._record_metric(role=role, reason=reason)
                logger.warning(
                    "prompt cache drift role=%s reason=%s prev=%s new=%s",
                    role,
                    reason,
                    snap.last_hash[:12],
                    digest[:12],
                )
            snap.last_hash = digest
            # Return a copy so callers can't mutate internal state.
            return DriftSnapshot(
                last_hash=snap.last_hash,
                drift_count=snap.drift_count,
                last_reason=snap.last_reason,
                spawn_count=snap.spawn_count,
            )

    def snapshot(self, role: str) -> DriftSnapshot:
        """Return a copy of the current tracking state for *role*."""
        with self._lock:
            snap = self._snapshots.get(role, DriftSnapshot())
            return DriftSnapshot(
                last_hash=snap.last_hash,
                drift_count=snap.drift_count,
                last_reason=snap.last_reason,
                spawn_count=snap.spawn_count,
            )

    def reset(self) -> None:
        """Drop all per-role tracking state.  Test-only helper."""
        with self._lock:
            self._snapshots.clear()

    def _record_metric(self, *, role: str, reason: str) -> None:
        """Best-effort Prometheus increment; never raises."""
        if self._counter is None:
            return
        try:
            # ``labels(...).inc()`` is the canonical Counter API and is
            # supported by both real prometheus_client and Bernstein's
            # in-process stub.  Wrap in try/except so a misconfigured
            # registry never blocks the spawn path.
            self._counter.labels(role=role, reason=reason).inc()  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover — defensive
            logger.debug("prompt_cache_drift_total inc failed", exc_info=True)


# ---------------------------------------------------------------------------
# Module-level singleton wired to the shared Prometheus registry.
# ---------------------------------------------------------------------------


def _build_default_locality() -> PromptCacheLocality:
    """Construct the singleton, attaching the Prometheus counter when
    available.  The counter is registered on the shared Bernstein registry
    in :mod:`bernstein.core.observability.prometheus`.
    """
    try:
        from bernstein.core.observability.prometheus import (
            prompt_cache_drift_total,
        )
    except Exception:  # pragma: no cover — prometheus optional on Windows
        return PromptCacheLocality(prometheus_counter=None)
    return PromptCacheLocality(prometheus_counter=prompt_cache_drift_total)


_default_locality: PromptCacheLocality | None = None
_default_lock = threading.Lock()


def default_locality() -> PromptCacheLocality:
    """Return the lazily-initialised module singleton."""
    global _default_locality
    if _default_locality is None:
        with _default_lock:
            if _default_locality is None:
                _default_locality = _build_default_locality()
    return _default_locality


def observe_prefix(
    *,
    role: str,
    prefix: str,
    reason_hint: str = "",
) -> DriftSnapshot:
    """Shortcut: observe *prefix* on the module-level singleton.

    Args:
        role: Stable role name.
        prefix: Fully assembled cache prefix.
        reason_hint: Optional drift-reason classifier.

    Returns:
        The post-observation snapshot.
    """
    return default_locality().observe(role=role, prefix=prefix, reason_hint=reason_hint)


__all__ = [
    "DriftSnapshot",
    "PromptCacheLocality",
    "StablePrefix",
    "Vendor",
    "build_stable_prefix",
    "default_locality",
    "hash_prefix",
    "observe_prefix",
]
