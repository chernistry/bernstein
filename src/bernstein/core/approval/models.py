"""Data models for interactive tool-call approval.

Defines the payload that the security-layer gate pushes onto the queue
when a tool invocation misses the always-allow rules and interactive
approvals are enabled, plus the decision primitives used by resolvers
(TUI, web, CLI).
"""

from __future__ import annotations

import secrets
import time
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class ApprovalTimeoutError(RuntimeError):
    """Raised when a pending approval expires before a decision arrives.

    Callers MUST treat this as an implicit reject: the tool call did not
    receive explicit operator consent, so the agent is not permitted to
    proceed. The error message is user-facing and should include the
    approval id and the tool name that timed out.
    """


class ApprovalDecision(StrEnum):
    """Operator verdict on a pending tool-call approval.

    Attributes:
        ALLOW: One-shot allow for this specific invocation.
        REJECT: Deny the tool call; the agent surfaces a permission error.
        ALWAYS: Allow and promote the tool+args pattern into the user's
            always-allow rules so future matches short-circuit the queue.
    """

    ALLOW = "allow"
    REJECT = "reject"
    ALWAYS = "always"


def _new_id() -> str:
    """Return a short, URL-safe unique approval id."""
    return f"ap-{secrets.token_hex(6)}"


@dataclass(frozen=True)
class PendingApproval:
    """A tool call awaiting an operator decision.

    Attributes:
        id: Unique approval identifier used in URLs and filenames.
        session_id: Bernstein session / run identifier.
        agent_role: Role of the agent that issued the tool call
            (``backend``, ``architect``, etc.).
        tool_name: Name of the tool being invoked.
        tool_args: Arguments passed to the tool. ``path``/``command``/
            ``file_path``/``query`` fields are used for pattern matching
            when the operator chooses "always allow".
        created_at: Unix epoch seconds at which the approval was queued.
        ttl_seconds: Time-to-live in seconds. After ``created_at +
            ttl_seconds`` the approval is considered expired and the
            default resolver rejects it.
    """

    id: str = field(default_factory=_new_id)
    session_id: str = ""
    agent_role: str = ""
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=lambda: time.time())
    ttl_seconds: int = 600

    @property
    def expires_at(self) -> float:
        """Unix epoch seconds at which this approval times out."""
        return self.created_at + float(self.ttl_seconds)

    def is_expired(self, *, now: float | None = None) -> bool:
        """Return ``True`` when the approval has passed its TTL.

        Args:
            now: Optional injected timestamp for deterministic tests.
        """
        current = time.time() if now is None else now
        return current >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PendingApproval:
        """Build a :class:`PendingApproval` from a JSON-decoded mapping.

        Unknown keys are ignored so the on-disk format can grow without
        breaking older readers.
        """
        known = {
            "id": str(data.get("id", _new_id())),
            "session_id": str(data.get("session_id", "")),
            "agent_role": str(data.get("agent_role", "")),
            "tool_name": str(data.get("tool_name", "")),
            "tool_args": dict(data.get("tool_args", {}) or {}),
            "created_at": float(data.get("created_at", time.time())),
            "ttl_seconds": int(data.get("ttl_seconds", 600)),
        }
        return cls(**known)


@dataclass(frozen=True)
class ResolvedApproval:
    """The outcome of resolving a :class:`PendingApproval`.

    Attributes:
        approval_id: Id of the pending approval this resolution refers to.
        decision: The operator verdict.
        reason: Optional free-form note supplied by the operator.
        resolved_at: Unix epoch seconds at which the decision was made.
    """

    approval_id: str
    decision: ApprovalDecision
    reason: str = ""
    resolved_at: float = field(default_factory=lambda: time.time())
