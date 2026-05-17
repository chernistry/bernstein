"""Oscillation guard for prompt-evolution proposals.

Synapse port of the patch-engine *2-consecutive-cycle* rule.

Problem
-------
The evolution loop can flip a prompt back and forth across cycles
(A → B → A → B), wasting compute and leaving the operator without a
signal about whether each change was net-positive.

Defense
-------
Two checks operate on a sliding window of accepted patches keyed by
content hash:

1. **Two-cycle confirmation** — a patch must be *proposed* in two
   consecutive cycles (same content hash, same target ``prompt_name``)
   before it is applied. Single-cycle proposals are stored as
   "pending" and not applied yet.

2. **Flip-back veto** — if the proposed content hash equals a content
   hash that was *applied* within the last ``window_size`` patches
   *and* a different content hash was applied between, the proposal is
   vetoed as an oscillation (A → B → A).

Session cap
-----------
At most ``max_patches_per_session`` patches may be accepted in a single
session. The 4th accepted patch in a session of cap 3 is rejected with
``session_cap``.

State
-----
The guard is in-memory by default; callers can persist the history
themselves by serialising :meth:`OscillationGuard.snapshot` to JSON.
A dedicated audit file is *not* written by this module — the wiring
into :class:`bernstein.evolution.gate.ApprovalGate` owns audit-row
emission.
"""

from __future__ import annotations

import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bernstein.evolution.predicted_delta import PatchProposal

logger = logging.getLogger(__name__)

# Tunables. The defaults mirror the issue acceptance criteria.
DEFAULT_WINDOW_SIZE: int = 4
DEFAULT_MAX_PATCHES_PER_SESSION: int = 3
DEFAULT_MIN_CONFIRMATIONS: int = 2  # consecutive-cycle requirement


class OscillationVerdict(Enum):
    """Possible outcomes of an oscillation-guard evaluation."""

    ACCEPTED = "accepted"
    """Patch cleared the oscillation guard and may be applied."""

    PENDING_CONFIRMATION = "pending_confirmation"
    """First sighting of this patch — not yet applied."""

    REJECTED_FLIP_BACK = "flip_back"
    """Patch would revert a recent change (A → B → A)."""

    REJECTED_SESSION_CAP = "session_cap"
    """Session already has ``max_patches_per_session`` accepted patches."""


@dataclass(frozen=True)
class OscillationResult:
    """Verdict from :class:`OscillationGuard`.

    Attributes:
        verdict: Outcome of the check.
        proposal_id: Proposal that was evaluated.
        content_hash: Content hash of the candidate patch.
        confirmations: How many *consecutive* cycles this patch hash has
            been proposed (including the current one).
        applied_count: How many patches have been *applied* in the
            current session.
        reason: Human-readable explanation suitable for an audit row.
    """

    verdict: OscillationVerdict
    proposal_id: str
    content_hash: str
    confirmations: int
    applied_count: int
    reason: str

    @property
    def accepted(self) -> bool:
        """True iff the proposal cleared the guard and may apply."""
        return self.verdict == OscillationVerdict.ACCEPTED


@dataclass
class _AppliedRecord:
    """Internal record for the recent-applied ring buffer."""

    prompt_name: str
    content_hash: str
    proposal_id: str
    applied_at: float = field(default_factory=time.time)


@dataclass
class _PendingRecord:
    """Internal record for a single-cycle pending proposal."""

    prompt_name: str
    content_hash: str
    proposal_id: str
    consecutive_cycles: int = 1
    first_seen_at: float = field(default_factory=time.time)


def resolve_max_patches_per_session(default: int = DEFAULT_MAX_PATCHES_PER_SESSION) -> int:
    """Resolve the per-session patch cap from the environment."""
    raw = os.environ.get("BERNSTEIN_PROMPT_MAX_PATCHES_PER_SESSION")
    if raw is None or raw.strip() == "":
        return default
    try:
        parsed = int(raw)
    except ValueError:
        logger.warning(
            "Invalid BERNSTEIN_PROMPT_MAX_PATCHES_PER_SESSION=%r — falling back to %s",
            raw,
            default,
        )
        return default
    return max(parsed, 0)


class OscillationGuard:
    """In-memory oscillation guard for prompt patches.

    Args:
        window_size: Number of recent *applied* patches retained per
            prompt for the flip-back check. Defaults to
            :data:`DEFAULT_WINDOW_SIZE`.
        min_confirmations: How many consecutive cycles a content hash
            must appear in before it may be applied. Defaults to
            :data:`DEFAULT_MIN_CONFIRMATIONS` (the 2-cycle rule).
        max_patches_per_session: Per-session accepted-patch cap. ``0``
            disables the cap.
    """

    def __init__(
        self,
        window_size: int | None = None,
        min_confirmations: int | None = None,
        max_patches_per_session: int | None = None,
    ) -> None:
        self.window_size: int = max(window_size if window_size is not None else DEFAULT_WINDOW_SIZE, 1)
        self.min_confirmations: int = max(
            min_confirmations if min_confirmations is not None else DEFAULT_MIN_CONFIRMATIONS,
            1,
        )
        self.max_patches_per_session: int = (
            max_patches_per_session if max_patches_per_session is not None else resolve_max_patches_per_session()
        )
        # Recent applied patches, keyed by prompt_name. Maintained as a
        # ring buffer so the flip-back check is O(window_size).
        self._applied: dict[str, deque[_AppliedRecord]] = {}
        # Pending (single-cycle) proposals, keyed by (prompt_name, content_hash).
        self._pending: dict[tuple[str, str], _PendingRecord] = {}
        # Total applied count for the session cap.
        self._session_applied_count: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(self, proposal: PatchProposal) -> OscillationResult:
        """Check whether ``proposal`` may be applied without oscillating.

        The method is *non-mutating with respect to applied state* — it
        records the sighting for the consecutive-cycle counter but does
        not mark the patch as applied. Callers must invoke
        :meth:`record_applied` once the patch has actually been written.
        That split matches the two-stage flow of
        :class:`bernstein.evolution.gate.ApprovalGate`: route → apply.

        Args:
            proposal: The candidate patch.

        Returns:
            :class:`OscillationResult` describing the verdict.
        """
        key = (proposal.prompt_name, proposal.content_hash)

        if self._is_flip_back(proposal):
            self._pending.pop(key, None)
            return OscillationResult(
                verdict=OscillationVerdict.REJECTED_FLIP_BACK,
                proposal_id=proposal.proposal_id,
                content_hash=proposal.content_hash,
                confirmations=0,
                applied_count=self._session_applied_count,
                reason=(
                    f"flip_back: content_hash={proposal.content_hash[:12]} "
                    f"would revert a recent change to prompt={proposal.prompt_name!r}"
                ),
            )

        if self.max_patches_per_session > 0 and self._session_applied_count >= self.max_patches_per_session:
            return OscillationResult(
                verdict=OscillationVerdict.REJECTED_SESSION_CAP,
                proposal_id=proposal.proposal_id,
                content_hash=proposal.content_hash,
                confirmations=0,
                applied_count=self._session_applied_count,
                reason=(
                    f"session_cap: already applied {self._session_applied_count} of "
                    f"{self.max_patches_per_session} allowed patches this session"
                ),
            )

        # Consecutive-cycle confirmation logic.
        existing = self._pending.get(key)
        if existing is None:
            self._pending[key] = _PendingRecord(
                prompt_name=proposal.prompt_name,
                content_hash=proposal.content_hash,
                proposal_id=proposal.proposal_id,
            )
            if self.min_confirmations <= 1:
                return OscillationResult(
                    verdict=OscillationVerdict.ACCEPTED,
                    proposal_id=proposal.proposal_id,
                    content_hash=proposal.content_hash,
                    confirmations=1,
                    applied_count=self._session_applied_count,
                    reason="accepted: min_confirmations=1, single sighting is sufficient",
                )
            return OscillationResult(
                verdict=OscillationVerdict.PENDING_CONFIRMATION,
                proposal_id=proposal.proposal_id,
                content_hash=proposal.content_hash,
                confirmations=1,
                applied_count=self._session_applied_count,
                reason=(f"pending_confirmation: 1/{self.min_confirmations} consecutive cycles seen"),
            )

        # Re-sighting — bump the consecutive counter.
        existing.consecutive_cycles += 1
        if existing.consecutive_cycles >= self.min_confirmations:
            return OscillationResult(
                verdict=OscillationVerdict.ACCEPTED,
                proposal_id=proposal.proposal_id,
                content_hash=proposal.content_hash,
                confirmations=existing.consecutive_cycles,
                applied_count=self._session_applied_count,
                reason=(
                    f"accepted: confirmed {existing.consecutive_cycles}/{self.min_confirmations} consecutive cycles"
                ),
            )

        return OscillationResult(
            verdict=OscillationVerdict.PENDING_CONFIRMATION,
            proposal_id=proposal.proposal_id,
            content_hash=proposal.content_hash,
            confirmations=existing.consecutive_cycles,
            applied_count=self._session_applied_count,
            reason=(
                f"pending_confirmation: {existing.consecutive_cycles}/{self.min_confirmations} consecutive cycles seen"
            ),
        )

    def record_applied(self, proposal: PatchProposal) -> None:
        """Mark ``proposal`` as applied. Updates the recent-applied window.

        Idempotent against repeated calls for the same proposal id — a
        proposal whose content_hash matches the most recent applied
        record for that prompt is treated as a no-op so callers can
        safely call this in a finally clause.
        """
        bucket = self._applied.setdefault(proposal.prompt_name, deque(maxlen=self.window_size))
        if bucket and bucket[-1].content_hash == proposal.content_hash:
            # Same content as the last applied — nothing to do.
            return
        bucket.append(
            _AppliedRecord(
                prompt_name=proposal.prompt_name,
                content_hash=proposal.content_hash,
                proposal_id=proposal.proposal_id,
            )
        )
        self._session_applied_count += 1
        # Clear pending state for this hash now that it's applied.
        self._pending.pop((proposal.prompt_name, proposal.content_hash), None)

    def reset_session(self) -> None:
        """Reset the per-session counters but keep the applied window.

        Useful when starting a new operator-managed session inside the
        same process (e.g. interactive CLI flow).
        """
        self._session_applied_count = 0
        self._pending.clear()

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def session_applied_count(self) -> int:
        """How many patches have been applied in the current session."""
        return self._session_applied_count

    def recent_applied_hashes(self, prompt_name: str) -> list[str]:
        """Return the recent-applied content hashes for ``prompt_name``."""
        bucket = self._applied.get(prompt_name)
        if bucket is None:
            return []
        return [r.content_hash for r in bucket]

    def pending_count(self) -> int:
        """How many proposals are currently in the "pending confirmation" state."""
        return len(self._pending)

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot of the guard's state."""
        return {
            "session_applied_count": self._session_applied_count,
            "window_size": self.window_size,
            "min_confirmations": self.min_confirmations,
            "max_patches_per_session": self.max_patches_per_session,
            "applied": {
                prompt: [
                    {
                        "content_hash": r.content_hash,
                        "proposal_id": r.proposal_id,
                        "applied_at": r.applied_at,
                    }
                    for r in bucket
                ]
                for prompt, bucket in self._applied.items()
            },
            "pending": [
                {
                    "prompt_name": r.prompt_name,
                    "content_hash": r.content_hash,
                    "proposal_id": r.proposal_id,
                    "consecutive_cycles": r.consecutive_cycles,
                    "first_seen_at": r.first_seen_at,
                }
                for r in self._pending.values()
            ],
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_flip_back(self, proposal: PatchProposal) -> bool:
        """Detect A → B → A within the recent-applied window.

        A flip-back is when the proposed content_hash equals an
        applied content_hash *and* there is at least one different
        applied content_hash *after* it in the window. The plain "same
        content as last applied" case is NOT a flip-back — it's a re-
        application or noop.
        """
        bucket = self._applied.get(proposal.prompt_name)
        if bucket is None or len(bucket) < 2:
            return False
        # Find the position of the proposed hash in the window.
        hashes = [r.content_hash for r in bucket]
        try:
            idx = hashes.index(proposal.content_hash)
        except ValueError:
            return False
        # Anything after idx that differs constitutes the "B" in A→B→A.
        return any(h != proposal.content_hash for h in hashes[idx + 1 :])
