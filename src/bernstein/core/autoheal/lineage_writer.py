"""Lineage v2 attestation for auto-heal.

Each heal action writes one ``ChildBody`` under
``.sdd/lineage/v2/children/`` carrying the failure / classification /
strategy / patch / cost provenance. This makes auto-heal traceable in
the same audit chain as every other Bernstein actor.

The writer is decoupled from the in-process ``LineageV2Store`` to keep
this module hermetic for tests: we render the canonical JSON bytes and
let the workflow append them via the store's public API (or, in
tests, snapshot the bytes directly).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Final

LINEAGE_KIND: Final[str] = "autoheal.action"


@dataclass(frozen=True, slots=True)
class AutohealLineagePayload:
    """The structured payload embedded in a lineage child body.

    All fields are required so downstream parsers can rely on shape.
    """

    failed_run_id: str
    head_sha: str
    classification: str
    strategy: str
    patch_sha: str
    llm_calls: int
    cost_usd: float
    outcome: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "failed_run_id": self.failed_run_id,
            "head_sha": self.head_sha,
            "classification": self.classification,
            "strategy": self.strategy,
            "patch_sha": self.patch_sha,
            "llm_calls": self.llm_calls,
            "cost_usd": self.cost_usd,
            "outcome": self.outcome,
            "confidence": self.confidence,
        }


def render_payload(payload: AutohealLineagePayload) -> dict[str, Any]:
    """Return the dict to pass as ``payload=`` when constructing a
    ``ChildBody`` for an autoheal action.

    The matching ``kind`` is :data:`LINEAGE_KIND`.
    """
    return payload.to_dict()


def render_canonical_bytes(payload: AutohealLineagePayload) -> bytes:
    """Render the payload as canonical JSON bytes (sorted, compact).

    Useful for hashing in tests or for shipping the body without
    instantiating ``ChildBody`` directly.
    """
    return json.dumps(payload.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")


__all__ = [
    "LINEAGE_KIND",
    "AutohealLineagePayload",
    "render_canonical_bytes",
    "render_payload",
]
