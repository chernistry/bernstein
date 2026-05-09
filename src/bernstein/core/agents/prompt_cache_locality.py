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
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)


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


def build_stable_prefix(
    *,
    header: Mapping[str, str] | None = None,
    body: str = "",
) -> str:
    """Build a byte-stable cache prefix from a header dict and a body string.

    The header is canonicalised by sorting keys lexicographically and
    rendering each entry as ``"<key>: <value>"`` on its own line.  This
    means callers cannot break the cache by reordering header fields.
    The body is appended verbatim after a fixed separator.

    Args:
        header: Mapping of stable header field names to values
            (e.g. ``{"role": "backend", "templates_hash": "abc..."}``).
            Keys and values are coerced to ``str``.  Empty-string values
            are kept (they may carry meaning, e.g. an empty agent
            protocol prefix).  ``None`` is treated as an empty mapping.
        body: Free-form prefix body (role template, project context,
            git safety protocol, etc.).  Appended verbatim.

    Returns:
        A deterministic prefix string.  Two calls with the same logical
        inputs return byte-identical strings regardless of header
        insertion order.
    """
    items = sorted((str(k), str(v)) for k, v in (header or {}).items())
    header_str = "\n".join(f"{k}: {v}" for k, v in items)
    return f"{header_str}{_HEADER_SEPARATOR}{body}"


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
    "build_stable_prefix",
    "default_locality",
    "hash_prefix",
    "observe_prefix",
]
