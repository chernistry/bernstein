"""Idempotency / content-hash dedupe for auto-heal patches.

If the same logical patch (same diff content) was attempted within the
last ``DEDUPE_WINDOW_SECONDS`` and failed, do not retry. This prevents
infinite-loop scenarios where the underlying autofixer is broken in
the same deterministic way.

State is the audit log itself - we read recent rows and check if any
entry with the same ``patch_sha`` and ``outcome != applied`` is within
the window. No extra state file is introduced.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from bernstein.core.autoheal.audit_log import HealRecord

DEDUPE_WINDOW_SECONDS: int = 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class IdempotencyDecision:
    """Outcome of the dedupe check."""

    allowed: bool
    reason: str
    matched_record: HealRecord | None


def patch_sha_for(diff_bytes: bytes) -> str:
    """Compute the content-hash key for a candidate patch.

    SHA-256 truncated to 16 hex chars is unique enough at this scale
    and short enough to fit cleanly in audit-log columns.
    """
    return hashlib.sha256(diff_bytes).hexdigest()[:16]


def check(
    candidate_sha: str,
    history: Iterable[HealRecord],
    *,
    now: float | None = None,
    window_seconds: int = DEDUPE_WINDOW_SECONDS,
) -> IdempotencyDecision:
    """Inspect history for a recent failed attempt with the same hash.

    Returns ``allowed=False`` if any prior record in the window has the
    same ``patch_sha`` and was not ``"applied"``.
    """
    if not candidate_sha:
        return IdempotencyDecision(allowed=False, reason="empty_patch_sha", matched_record=None)
    cutoff = (now if now is not None else time.time()) - float(window_seconds)
    for rec in history:
        if rec.patch_sha != candidate_sha:
            continue
        if rec.ts < cutoff:
            continue
        if rec.outcome == "applied":
            # Re-applying a successful patch is harmless from a dedupe
            # perspective - the *failure* state is what we want to
            # short-circuit. Allow it through; caller can decide.
            continue
        return IdempotencyDecision(
            allowed=False,
            reason=f"recent_failure_within_{window_seconds}s",
            matched_record=rec,
        )
    return IdempotencyDecision(allowed=True, reason="no_recent_match", matched_record=None)


__all__ = [
    "DEDUPE_WINDOW_SECONDS",
    "IdempotencyDecision",
    "check",
    "patch_sha_for",
]
