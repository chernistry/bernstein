"""Shared helpers for the ``bernstein lineage conflicts/resolve`` commands.

The two new operator-facing surfaces sit on top of the merge primitives
already in :mod:`bernstein.core.lineage.merge` and the fork detector in
:mod:`bernstein.core.lineage.tips`. The helpers below stay small and
side-effect free so they remain trivially unit-testable through Click's
``CliRunner``.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from bernstein.core.lineage.entry import LineageEntry, canonicalise, entry_hash
from bernstein.core.lineage.merge import (
    AgentPolicy,
    FirstWriterPolicy,
    HumanPolicy,
    LineageConflict,
    MergePolicy,
    resolve_policy,
)
from bernstein.core.lineage.tips import Fork, detect_forks

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "CandidateView",
    "ConflictView",
    "build_conflict_views",
    "compute_char_count_diff",
    "filter_forks",
    "format_unified_diff",
    "index_entries",
    "resolve_one",
    "resolve_policy_name",
]


@dataclass(frozen=True, slots=True)
class CandidateView:
    """Human-readable projection of one competing child entry."""

    entry_hash: str
    agent_id: str
    ts_ns: int
    content_hash: str
    tool_call_id: str
    span_id: str

    def to_dict(self) -> dict[str, object]:
        return {
            "entry_hash": self.entry_hash,
            "agent_id": self.agent_id,
            "ts_ns": self.ts_ns,
            "content_hash": self.content_hash,
            "tool_call_id": self.tool_call_id,
            "span_id": self.span_id,
        }


@dataclass(frozen=True, slots=True)
class ConflictView:
    """One unresolved fork enriched with operator-facing metadata."""

    artefact_path: str
    parent_hash: str
    candidates: tuple[CandidateView, ...]
    char_count_diff: int

    def to_dict(self) -> dict[str, object]:
        return {
            "artefact_path": self.artefact_path,
            "parent_hash": self.parent_hash,
            "candidates": [c.to_dict() for c in self.candidates],
            "char_count_diff": self.char_count_diff,
        }


def index_entries(entries: Iterable[LineageEntry]) -> dict[str, LineageEntry]:
    """Return a ``{entry_hash: LineageEntry}`` map for the supplied entries."""
    return {entry_hash(e): e for e in entries}


def filter_forks(forks: Iterable[Fork], artefact_path: str | None) -> list[Fork]:
    """Optionally narrow a list of forks to a single artefact path."""
    if artefact_path is None:
        return list(forks)
    return [f for f in forks if f.artefact_path == artefact_path]


def compute_char_count_diff(by_hash: dict[str, LineageEntry], fork: Fork) -> int:
    """Return the largest pairwise canonical-byte-length delta across siblings.

    Char-count is taken from the RFC 8785 JCS canonical bytes of each child
    entry. The metric is intentionally coarse: it shows the operator at a
    glance whether the competing siblings agree on most of the payload or
    diverge wildly.
    """
    sizes = [len(canonicalise(by_hash[h])) for h in fork.child_hashes if h in by_hash]
    if len(sizes) < 2:
        return 0
    return max(sizes) - min(sizes)


def build_conflict_views(entries: Iterable[LineageEntry], artefact_path: str | None) -> list[ConflictView]:
    """Detect forks and project them into operator-facing ``ConflictView``s."""
    entries_list = list(entries)
    by_hash = index_entries(entries_list)
    forks = filter_forks(detect_forks(entries_list), artefact_path)
    out: list[ConflictView] = []
    for fork in forks:
        candidates: list[CandidateView] = []
        for h in fork.child_hashes:
            child = by_hash.get(h)
            if child is None:
                continue
            candidates.append(
                CandidateView(
                    entry_hash=h,
                    agent_id=child.agent_id,
                    ts_ns=child.ts_ns,
                    content_hash=child.content_hash,
                    tool_call_id=child.tool_call_id,
                    span_id=child.span_id,
                )
            )
        out.append(
            ConflictView(
                artefact_path=fork.artefact_path,
                parent_hash=fork.parent_hash,
                candidates=tuple(candidates),
                char_count_diff=compute_char_count_diff(by_hash, fork),
            )
        )
    return out


def format_unified_diff(by_hash: dict[str, LineageEntry], fork: Fork) -> str:
    """Return a unified diff between the first two competing entries.

    The diff operates on the canonical JSON form of each entry so the
    operator sees exactly which fields disagree (timestamp, agent id,
    content hash, span id, ...). This avoids needing access to the
    out-of-band content payload while still surfacing actionable detail.
    """
    if len(fork.child_hashes) < 2:
        return ""
    a_hash, b_hash = fork.child_hashes[0], fork.child_hashes[1]
    a_entry = by_hash.get(a_hash)
    b_entry = by_hash.get(b_hash)
    if a_entry is None or b_entry is None:
        return ""
    a_lines = json.dumps(asdict(a_entry), sort_keys=True, indent=2).splitlines(keepends=True)
    b_lines = json.dumps(asdict(b_entry), sort_keys=True, indent=2).splitlines(keepends=True)
    diff_iter = difflib.unified_diff(
        a_lines,
        b_lines,
        fromfile=f"candidate-a ({a_hash[:24]}...)",
        tofile=f"candidate-b ({b_hash[:24]}...)",
        n=3,
    )
    return "".join(diff_iter)


def resolve_policy_name(policy_name: str) -> MergePolicy:
    """Construct a ``MergePolicy`` and surface a clean ``ValueError`` on bad input."""
    return resolve_policy(policy_name)


def resolve_one(
    fork: Fork,
    by_hash: dict[str, LineageEntry],
    policy: MergePolicy,
) -> LineageEntry:
    """Apply ``policy`` to ``fork`` and return the winning entry.

    Raises:
        LineageConflict: When the policy refuses to choose (HumanPolicy
            without an interactive override, or AgentPolicy without a
            matching candidate).
    """
    return policy.resolve(fork, by_hash)


# Re-exports so the CLI command module does not need to import twice.
__all__ = [*__all__, "AgentPolicy", "FirstWriterPolicy", "HumanPolicy", "LineageConflict"]
