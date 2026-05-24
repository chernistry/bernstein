"""Per-task diary entries distilled from task transcripts.

After a task closes, an offline pass extracts a structured ``DiaryEntry``
from its transcript: what was tried, what worked, what failed, the
rationale, and a list of tags. Entries land under
``.sdd/runtime/diaries/<task_id>.json`` via atomic write so a crash mid-
flush never leaves a torn payload on disk.

The diary writer is read-only over task state and never blocks task
close. A redaction hash captures the entry shape so downstream
verification can detect tampering without re-reading the redacted body.

This module is self-contained: it depends only on stdlib plus the
existing ``persistence.atomic_write`` helper. The synthesiser
(:mod:`bernstein.core.knowledge.synthesizer`) consumes diaries through
``load_diaries`` and the CLI surface lives in
``bernstein.cli.commands.knowledge_cmd``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bernstein.core.persistence.atomic_write import write_atomic_json

logger = logging.getLogger(__name__)


DIARY_SCHEMA_VERSION = 1
DEFAULT_DIARY_SUBPATH = Path(".sdd") / "runtime" / "diaries"

# Patterns redacted from transcripts before extraction. The redaction is
# intentionally conservative: it strips the most obvious credential
# shapes so a leaked diary file cannot serve as a credential vector. It
# is NOT a substitute for the audit-pack sanitiser; it is a guardrail.
_REDACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "[REDACTED:openai-key]"),
    (re.compile(r"ghp_[A-Za-z0-9]{20,}"), "[REDACTED:github-token]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED:aws-access-key]"),
    (re.compile(r"-----BEGIN[^-]+PRIVATE KEY-----"), "[REDACTED:private-key]"),
    # Generic 32+ hex-like high-entropy strings (bearer tokens).
    (re.compile(r"\b[A-Fa-f0-9]{40,}\b"), "[REDACTED:token]"),
)

# Section markers an agent transcript may emit. Lower-cased before
# matching so the writer is resilient to capitalisation drift.
_SECTION_MARKERS: tuple[tuple[str, str], ...] = (
    ("tried:", "tried"),
    ("attempted:", "tried"),
    ("worked:", "worked"),
    ("succeeded:", "worked"),
    ("failed:", "failed"),
    ("did not work:", "failed"),
    ("rationale:", "rationale"),
    ("why:", "rationale"),
    ("lesson:", "rationale"),
    ("surprising:", "rationale"),
)


class DiaryError(Exception):
    """Raised on malformed diary input or storage failures."""


@dataclass(frozen=True)
class DiaryEntry:
    """Structured per-task reflection record.

    Attributes mirror the acceptance-criteria fields from the issue:
    ``tried`` / ``worked`` / ``failed`` are short bullet-style strings,
    ``rationale`` is a single free-text paragraph, and ``tags`` is a
    deduplicated, lower-cased list used for clustering by the
    synthesiser.

    ``redaction_hash`` is computed from the redacted transcript so any
    tampered diary fails verification when ``verify_diary`` is run.
    """

    task_id: str
    tried: tuple[str, ...]
    worked: tuple[str, ...]
    failed: tuple[str, ...]
    rationale: str
    tags: tuple[str, ...]
    redaction_hash: str
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    schema_version: int = DIARY_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable plain dict."""
        payload = asdict(self)
        for key in ("tried", "worked", "failed", "tags"):
            payload[key] = list(payload[key])
        return payload


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def redact(text: str) -> str:
    """Apply the conservative redaction patterns to *text*.

    The output preserves structure so the section parser still finds
    headings; only sensitive substrings are replaced. The function is
    pure and safe for use on untrusted transcripts.
    """
    if not text:
        return ""
    redacted = text
    for pattern, replacement in _REDACTION_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def compute_redaction_hash(text: str) -> str:
    """Return a stable SHA-256 hash of the redacted *text*.

    Used to fingerprint diary inputs so external systems can detect
    tampering without re-reading the original transcript.
    """
    digest = hashlib.sha256(redact(text).encode("utf-8")).hexdigest()
    return digest


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def _normalise_bullet(line: str) -> str:
    """Strip leading bullet markers and surrounding whitespace."""
    stripped = line.strip()
    for marker in ("- ", "* ", "+ ", "  - "):
        if stripped.startswith(marker):
            return stripped[len(marker) :].strip()
    return stripped


def _classify_marker(line: str) -> str | None:
    """Map a heading line to a canonical section name."""
    lowered = line.strip().lower()
    for prefix, canonical in _SECTION_MARKERS:
        if lowered.startswith(prefix):
            return canonical
    return None


def extract_sections(transcript: str) -> dict[str, list[str]]:
    """Split *transcript* into the four canonical diary sections.

    Headings are matched case-insensitively against ``_SECTION_MARKERS``.
    Bullet lines under a heading land in the matching bucket; everything
    before the first heading is ignored so leading agent banter does not
    pollute the diary.
    """
    buckets: dict[str, list[str]] = {
        "tried": [],
        "worked": [],
        "failed": [],
        "rationale": [],
    }
    if not transcript:
        return buckets
    current: str | None = None
    for raw_line in transcript.splitlines():
        marker = _classify_marker(raw_line)
        if marker is not None:
            current = marker
            # Capture inline content after the colon, if any.
            _, _, tail = raw_line.partition(":")
            tail = tail.strip()
            if tail:
                buckets[current].append(tail)
            continue
        if current is None:
            continue
        bullet = _normalise_bullet(raw_line)
        if bullet:
            buckets[current].append(bullet)
    return buckets


def extract_tags(transcript: str, *, limit: int = 16) -> tuple[str, ...]:
    """Derive deduplicated, lower-cased tags from a transcript.

    Tags are token-like words: alphanumerics plus dashes, length 3-32,
    excluding a small English stop-list. The tag order is the first-seen
    order so the output is deterministic.
    """
    if not transcript:
        return ()
    stop = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "into",
        "than",
        "then",
        "have",
        "has",
        "had",
        "are",
        "was",
        "were",
        "but",
        "not",
        "all",
        "any",
        "use",
        "used",
        "uses",
        "via",
        "per",
        "out",
        "off",
        "its",
        "lol",
        "yes",
        "now",
        "tbd",
    }
    seen: list[str] = []
    seen_set: set[str] = set()
    for raw in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,31}", transcript):
        token = raw.lower()
        if token in stop or token in seen_set:
            continue
        seen.append(token)
        seen_set.add(token)
        if len(seen) >= limit:
            break
    return tuple(seen)


def build_entry(task_id: str, transcript: str) -> DiaryEntry:
    """Compose a :class:`DiaryEntry` from a raw transcript.

    The transcript is redacted before extraction so any sensitive
    substring is gone from the resulting bullet bodies. A clean
    transcript with no recognised section headings still yields a
    well-formed entry: empty section tuples plus a rationale built from
    the first non-blank line so analysts always see *something*.
    """
    if not task_id or not task_id.strip():
        raise DiaryError("task_id must be a non-empty string")
    cleaned = redact(transcript)
    sections = extract_sections(cleaned)
    rationale = " ".join(sections["rationale"]).strip()
    if not rationale:
        # Fallback rationale: first non-empty stripped line of the cleaned
        # transcript. Keeps the diary useful even for noisy logs.
        for line in cleaned.splitlines():
            candidate = line.strip()
            if candidate:
                rationale = candidate[:240]
                break
    tags = extract_tags(cleaned)
    return DiaryEntry(
        task_id=task_id.strip(),
        tried=tuple(sections["tried"]),
        worked=tuple(sections["worked"]),
        failed=tuple(sections["failed"]),
        rationale=rationale,
        tags=tags,
        redaction_hash=compute_redaction_hash(transcript),
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _diary_dir(sdd_dir: Path) -> Path:
    """Return the diary directory under *sdd_dir*, creating it if needed."""
    target = sdd_dir / "runtime" / "diaries"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _safe_filename(task_id: str) -> str:
    """Return a filesystem-safe filename for *task_id*.

    Restricts characters to ``[A-Za-z0-9._-]`` so a malicious task id
    cannot escape the diary directory via path traversal.
    """
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", task_id.strip())
    if not cleaned:
        raise DiaryError(f"invalid task_id for filename: {task_id!r}")
    if cleaned in {".", ".."}:
        cleaned = cleaned.replace(".", "_")
    return f"{cleaned}.json"


def write_diary(entry: DiaryEntry, sdd_dir: Path) -> Path:
    """Persist *entry* under ``<sdd_dir>/runtime/diaries/<task_id>.json``.

    Uses :func:`write_atomic_json` so a crash mid-flush never leaves a
    half-written file. Returns the final path on success.
    """
    target = _diary_dir(sdd_dir) / _safe_filename(entry.task_id)
    write_atomic_json(target, entry.to_dict(), indent=2, sort_keys=True)
    logger.debug("diary.write task_id=%s path=%s", entry.task_id, target)
    return target


def write_diary_from_transcript(task_id: str, transcript: str, sdd_dir: Path) -> Path:
    """Convenience: build an entry from *transcript* and persist it.

    Failures are propagated as :class:`DiaryError`; callers wiring this
    into the task-close hook should catch broadly so a diary write never
    blocks task close.
    """
    entry = build_entry(task_id, transcript)
    return write_diary(entry, sdd_dir)


def load_diary(path: Path) -> DiaryEntry:
    """Load a single diary JSON file into a :class:`DiaryEntry`."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DiaryError(f"cannot load diary {path}: {exc}") from exc
    return _entry_from_payload(payload)


def load_diaries(sdd_dir: Path) -> list[DiaryEntry]:
    """Load every diary under ``<sdd_dir>/runtime/diaries/``.

    Files that fail to parse are skipped with a warning so a single
    corrupt entry does not break the synthesiser. The result is sorted
    by ``created_at`` ascending so downstream consumers see a stable
    time-ordered stream.
    """
    diary_dir = sdd_dir / "runtime" / "diaries"
    if not diary_dir.is_dir():
        return []
    entries: list[DiaryEntry] = []
    for path in sorted(diary_dir.glob("*.json")):
        try:
            entries.append(load_diary(path))
        except DiaryError as exc:
            logger.warning("diary.skip path=%s err=%s", path, exc)
    entries.sort(key=lambda e: e.created_at)
    return entries


def _entry_from_payload(payload: dict[str, Any]) -> DiaryEntry:
    """Construct a :class:`DiaryEntry` from a parsed dict payload."""
    required = ("task_id", "tried", "worked", "failed", "rationale", "tags", "redaction_hash")
    for key in required:
        if key not in payload:
            raise DiaryError(f"diary payload missing key: {key}")
    try:
        return DiaryEntry(
            task_id=str(payload["task_id"]),
            tried=tuple(str(x) for x in payload["tried"]),
            worked=tuple(str(x) for x in payload["worked"]),
            failed=tuple(str(x) for x in payload["failed"]),
            rationale=str(payload["rationale"]),
            tags=tuple(str(x) for x in payload["tags"]),
            redaction_hash=str(payload["redaction_hash"]),
            created_at=str(payload.get("created_at") or datetime.now(UTC).isoformat(timespec="seconds")),
            schema_version=int(payload.get("schema_version") or DIARY_SCHEMA_VERSION),
        )
    except (TypeError, ValueError) as exc:
        raise DiaryError(f"invalid diary payload: {exc}") from exc


def verify_diary(entry: DiaryEntry, transcript: str) -> bool:
    """Return True iff *entry*'s redaction hash matches the *transcript*."""
    return entry.redaction_hash == compute_redaction_hash(transcript)


__all__ = [
    "DEFAULT_DIARY_SUBPATH",
    "DIARY_SCHEMA_VERSION",
    "DiaryEntry",
    "DiaryError",
    "build_entry",
    "compute_redaction_hash",
    "extract_sections",
    "extract_tags",
    "load_diaries",
    "load_diary",
    "redact",
    "verify_diary",
    "write_diary",
    "write_diary_from_transcript",
]
