"""Slice extractor - deterministic subset of the HMAC-chained audit log.

First slice of the time-travel/replay epic.  Given a ``--from`` and ``--to``
HMAC fence-post, return the contiguous run of audit events whose HMACs fall
within the range.  Output is byte-deterministic JSONL (sorted keys, trailing
newline) so the slice can be hashed, signed, or shipped to a downstream
replayer with no ambiguity.

Design notes:
    * Read-only.  The source ``.sdd/audit/*.jsonl`` is never mutated.
    * Chain still verifies inside the slice when re-anchored at
      ``from_hmac``: every event's ``prev_hmac`` chains to its predecessor,
      and the final event's HMAC equals ``to_hmac`` (when supplied).
    * "from" / "to" semantics: each is the HMAC OF an event already in the
      log (not its ``prev_hmac``).  Both bounds are inclusive - so a slice
      with ``from == to`` yields exactly one event.  Pass ``from_hmac=None``
      to start at the genesis event; pass ``to_hmac=None`` to run to the
      last recorded event.
    * No PII redaction in this slice - that is deferred to the
      ``replay publish`` flow.  Operators slicing locally already trust
      the audit dir.

This module is intentionally CLI-agnostic so future ``replay`` and ``fork``
commands can reuse the same primitives.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

_JSONL_GLOB = "*.jsonl"


class AuditSliceError(ValueError):
    """Raised when a slice request is malformed or unresolvable."""


@dataclass(frozen=True)
class AuditSliceResult:
    """Outcome of a slice extraction.

    Attributes:
        events: The matched JSONL entries in chronological order.  Each
            element is the raw dict that was on disk (HMAC + prev_hmac
            included) so consumers can re-verify the chain offline.
        from_hmac: The lower-bound HMAC actually used.  ``None`` means
            "from genesis".
        to_hmac: The upper-bound HMAC actually used.  ``None`` means
            "through end of log".
        source_files: Sorted log file names that contributed events.
    """

    events: list[dict] = field(default_factory=list)
    from_hmac: str | None = None
    to_hmac: str | None = None
    source_files: list[str] = field(default_factory=list)

    @property
    def event_count(self) -> int:
        """Number of events in the slice."""
        return len(self.events)


def _iter_audit_entries(audit_dir: Path) -> list[tuple[str, dict]]:
    """Walk every JSONL file in chronological order, yielding ``(file, entry)``.

    Malformed lines are skipped with a debug log so a corrupted tail does
    not abort an otherwise-valid slice.
    """
    out: list[tuple[str, dict]] = []
    for log_path in sorted(audit_dir.glob(_JSONL_GLOB)):
        try:
            text = log_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.debug("Skipping unreadable audit file %s: %s", log_path, exc)
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.debug("Skipping malformed line in %s: %s", log_path, exc)
                continue
            if isinstance(entry, dict) and "hmac" in entry:
                out.append((log_path.name, entry))
    return out


def slice_audit_log(
    audit_dir: Path,
    *,
    from_hmac: str | None = None,
    to_hmac: str | None = None,
) -> AuditSliceResult:
    """Extract the contiguous run of audit events between two HMAC fence-posts.

    Args:
        audit_dir: Directory containing daily ``YYYY-MM-DD.jsonl`` audit files.
        from_hmac: Lower bound - inclusive.  Must be the HMAC of an event in
            the log.  Pass ``None`` to start at the first recorded event.
        to_hmac: Upper bound - inclusive.  Must be the HMAC of an event in
            the log and must occur at or after ``from_hmac``.  Pass ``None``
            to run to the final event.

    Returns:
        ``AuditSliceResult`` with the matched entries and the bounds that
        were actually applied.

    Raises:
        AuditSliceError: ``audit_dir`` does not exist, either bound was
            specified but does not match any event, or the bounds are
            out of order.
    """
    if not audit_dir.is_dir():
        raise AuditSliceError(f"Audit directory not found: {audit_dir}")

    entries = _iter_audit_entries(audit_dir)
    if not entries:
        raise AuditSliceError(f"Audit directory is empty: {audit_dir}")

    # Build hmac -> index map for O(1) bound resolution.  The chain is
    # supposed to be unique-by-HMAC; if a duplicate ever appears we keep
    # the first occurrence so behaviour is deterministic.
    hmac_to_index: dict[str, int] = {}
    for idx, (_, entry) in enumerate(entries):
        h = str(entry.get("hmac", ""))
        hmac_to_index.setdefault(h, idx)

    start = 0
    if from_hmac is not None:
        if from_hmac not in hmac_to_index:
            raise AuditSliceError(f"--from hash not found in audit log: {from_hmac}")
        start = hmac_to_index[from_hmac]

    end = len(entries) - 1
    if to_hmac is not None:
        if to_hmac not in hmac_to_index:
            raise AuditSliceError(f"--to hash not found in audit log: {to_hmac}")
        end = hmac_to_index[to_hmac]

    if start > end:
        raise AuditSliceError(f"--from must precede --to in the chain (from index {start}, to index {end})")

    sliced = entries[start : end + 1]
    out_events = [entry for _, entry in sliced]
    source_files = sorted({fname for fname, _ in sliced})

    return AuditSliceResult(
        events=out_events,
        from_hmac=from_hmac,
        to_hmac=to_hmac,
        source_files=source_files,
    )


def write_slice_jsonl(result: AuditSliceResult, out_path: Path) -> Path:
    """Write a slice result to a deterministic JSONL file.

    Output bytes are stable across runs: each line uses ``sort_keys=True``
    and ends with a single ``\\n``.  No trailing whitespace, no header
    comments - so downstream consumers can hash the file directly.

    Args:
        result: Slice produced by :func:`slice_audit_log`.
        out_path: Target file.  Parent directories are created.

    Returns:
        The resolved path that was written.
    """
    out_path = out_path.expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        fh.writelines(json.dumps(entry, sort_keys=True) + "\n" for entry in result.events)
    return out_path


def verify_slice_chain(result: AuditSliceResult) -> tuple[bool, list[str]]:
    """Confirm that ``prev_hmac`` linkage is intact across the slice.

    This is a structural check only - it does NOT recompute HMACs against
    the signing key (that requires ``AuditLog.verify`` against the source
    files).  The check ensures the slice itself is contiguous, i.e. each
    event's ``prev_hmac`` matches the previous event's ``hmac``.

    Args:
        result: Slice to validate.

    Returns:
        ``(valid, errors)`` - ``errors`` lists every gap or mismatch.
    """
    errors: list[str] = []
    prev_hmac: str | None = None
    for idx, entry in enumerate(result.events):
        cur_prev = str(entry.get("prev_hmac", ""))
        cur_hmac = str(entry.get("hmac", ""))
        if prev_hmac is not None and cur_prev != prev_hmac:
            errors.append(f"slice[{idx}]: prev_hmac mismatch (expected {prev_hmac[:16]}…, got {cur_prev[:16]}…)")
        prev_hmac = cur_hmac
    return len(errors) == 0, errors
