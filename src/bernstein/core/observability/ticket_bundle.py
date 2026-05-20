"""Per-ticket transcript bundle with full audit correlation.

For every tracker ticket Bernstein has touched, this module produces a
single archive that ties together the verbatim per-turn transcript of
every agent that ran, the lineage entries, the trace JSONL, the resulting
commits and diffs, the resulting PR, and the failure-taxonomy structured
comments. The archive is the canonical artefact for an auditor reviewing
a single ticket's full agent activity.

Design notes:

- The bundle is a packaging primitive layered over existing on-disk state
  under ``.sdd/``. It does not own its inputs; it indexes them.
- Bundle archive format is ``tar.gz`` (stdlib only, no extra deps).
  Manifest is a versioned ``manifest.json`` at archive root listing every
  bundled file with its sha256.
- Signing reuses ``bernstein.core.lineage.identity.sign_detached`` so the
  same Ed25519 keypair stewards both per-artefact lineage entries and
  ticket bundles. The detached JWS goes in ``signature.jws`` next to the
  archive.
- Filter strategy is conservative: a file is included when its filename
  contains the ticket id OR any JSONL record within it carries the
  ``(tracker, ticket_id)`` pair. Callers can pass an explicit
  ``BundleSelector`` to override.
- Companion modules ``tracker_audit`` and ``trace_store`` are tracked by
  separate tickets; this module degrades gracefully when those sources
  are absent.

Public surface:

- :class:`BundleManifest` -- versioned manifest dataclass.
- :class:`BundleSelector` -- per-section file selectors.
- :class:`TicketBundle` -- ``assemble`` / ``sign`` / ``verify``.
- :data:`MANIFEST_SCHEMA_VERSION` -- bump on incompatible changes.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import operator
import subprocess
import tarfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import bernstein
from bernstein.core.lineage.identity import (
    AgentCard,
    sign_detached,
    verify_detached,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    "BundleManifest",
    "BundleSelector",
    "ManifestEntry",
    "TicketBundle",
    "default_selector",
]

MANIFEST_SCHEMA_VERSION = 1
"""Bump on any backwards-incompatible manifest field change."""

SUPPORTED_SCHEMA_VERSIONS: frozenset[int] = frozenset({1})
"""Manifest schema versions :meth:`TicketBundle.verify` accepts.

Bundles written under an unknown schema version are rejected by
:func:`_manifest_from_dict` so an older verifier never silently treats a
newer manifest as v1.
"""

_JSONL_PROBE_BYTE_BUDGET = 2 * 1024 * 1024
"""Skip JSONL content probing for files larger than this. Filename match
still applies."""


# ---------------------------------------------------------------------------
# Manifest schema
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    """One row inside :class:`BundleManifest.files`.

    Attributes:
        arcname: Path inside the archive (POSIX-style, no leading slash).
        size_bytes: Uncompressed size on disk.
        sha256: Hex sha-256 over the file contents as packed.
        section: Logical section (``transcripts``, ``traces``, ``git`` ...).
    """

    arcname: str
    size_bytes: int
    sha256: str
    section: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "arcname": self.arcname,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "section": self.section,
        }


@dataclass(frozen=True, slots=True)
class BundleManifest:
    """Versioned manifest written as ``manifest.json`` at archive root.

    Attributes:
        schema_version: Format version of this manifest (bump on
            backwards-incompatible field changes).
        created_at: ISO-8601 UTC timestamp of bundle creation.
        bernstein_version: Producer's ``bernstein.__version__``.
        tracker: Tracker identifier (e.g. ``github``, ``jira``).
        ticket_id: Ticket id within the tracker.
        files: Sorted list of bundled files with sizes and sha256s.
        pr_number: Resulting PR number, when known.
        commits: Resulting commit SHAs, when known.
    """

    schema_version: int
    created_at: str
    bernstein_version: str
    tracker: str
    ticket_id: str
    files: list[ManifestEntry] = field(default_factory=list)
    pr_number: int | None = None
    commits: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "bernstein_version": self.bernstein_version,
            "tracker": self.tracker,
            "ticket_id": self.ticket_id,
            "files": [entry.to_dict() for entry in self.files],
            "pr_number": self.pr_number,
            "commits": self.commits.copy(),
        }

    def canonical_bytes(self) -> bytes:
        """Return the JCS-like canonical encoding used for signing.

        We sort keys and separate without whitespace so the bytes are
        byte-stable across producers. The detached JWS in
        ``signature.jws`` covers exactly these bytes.
        """
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")


# ---------------------------------------------------------------------------
# Selector strategy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BundleSelector:
    """File-collection strategy for one ticket.

    Attributes:
        transcripts: callable returning per-agent transcript files.
        traces: callable returning trace JSONL files.
        lineage: callable returning lineage JSONL files (filtered).
        tracker_audit: callable returning the tracker audit JSONL slice.
        commits: callable returning the resolved commit SHA list.
        pr_payload: callable returning the PR JSON payload, when known.
    """

    transcripts: Callable[[Path, str, str], list[Path]]
    traces: Callable[[Path, str, str], list[Path]]
    lineage: Callable[[Path, str, str], list[Path]]
    tracker_audit: Callable[[Path, str, str], list[Path]]
    commits: Callable[[Path, str, str], list[str]]
    pr_payload: Callable[[Path, str, str], dict[str, Any] | None]


# ---------------------------------------------------------------------------
# Default selectors -- pure-stdlib scans of ``.sdd/``
# ---------------------------------------------------------------------------


def _safe_iter(path: Path) -> Iterable[Path]:
    """Yield regular files under *path* one level deep, sorted, safely.

    Returns nothing if *path* is missing or not a directory.
    """
    if not path.is_dir():
        return
    for child in sorted(path.iterdir()):
        if child.is_file():
            yield child


def _record_matches(record: Any, tracker: str, ticket_id: str) -> bool:
    """Return True when one parsed JSON *record* carries both keys.

    A record matches when:

    - Any value in the record (recursed into nested dicts/lists) equals
      *ticket_id*, AND
    - either *tracker* is empty or any value equals *tracker*.

    Cross-record matches do not count: each record is judged on its own.
    """
    if not ticket_id:
        return False
    ticket_hit = False
    tracker_hit = not tracker

    def _walk(node: Any) -> None:
        nonlocal ticket_hit, tracker_hit
        if isinstance(node, dict):
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)
        else:
            text = node if isinstance(node, str) else None
            if text is None:
                return
            if text == ticket_id:
                ticket_hit = True
            if not tracker_hit and text == tracker:
                tracker_hit = True

    _walk(record)
    return ticket_hit and tracker_hit


def _file_mentions_ticket(path: Path, tracker: str, ticket_id: str) -> bool:
    """Return True when *path* is in scope for the (tracker, ticket).

    Match rules (any one suffices):

    1. Filename contains the ticket id verbatim.
    2. File is JSONL/JSON and at least one parsed record carries both
       the tracker string and the ticket id within the SAME record.
       Cross-record matches do not qualify.
    """
    name = path.name
    if ticket_id and ticket_id in name:
        return True
    if path.suffix.lower() not in {".jsonl", ".json"}:
        return False
    try:
        if path.stat().st_size > _JSONL_PROBE_BYTE_BUDGET:
            return False
    except OSError:
        return False
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if path.suffix.lower() == ".json":
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            return False
        return _record_matches(record, tracker, ticket_id)
    # JSONL: one record per line; a single record must satisfy both keys.
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if _record_matches(record, tracker, ticket_id):
            return True
    return False


def _collect_matching(base: Path, tracker: str, ticket_id: str) -> list[Path]:
    return [p for p in _safe_iter(base) if _file_mentions_ticket(p, tracker, ticket_id)]


def _select_transcripts(workdir: Path, tracker: str, ticket_id: str) -> list[Path]:
    """Default transcript selector -- scan ``.sdd/traces/`` and ``.sdd/transcripts/``."""
    candidates: list[Path] = []
    candidates.extend(_collect_matching(workdir / ".sdd" / "transcripts", tracker, ticket_id))
    return sorted(candidates)


def _select_traces(workdir: Path, tracker: str, ticket_id: str) -> list[Path]:
    """Default trace selector -- scan ``.sdd/traces/`` for ticket-tagged files."""
    return sorted(_collect_matching(workdir / ".sdd" / "traces", tracker, ticket_id))


def _select_lineage(workdir: Path, tracker: str, ticket_id: str) -> list[Path]:
    """Default lineage selector -- scan ``.sdd/lineage/`` for ticket-tagged files."""
    return sorted(_collect_matching(workdir / ".sdd" / "lineage", tracker, ticket_id))


def _select_tracker_audit(workdir: Path, tracker: str, ticket_id: str) -> list[Path]:
    """Default tracker-audit selector -- scan ``.sdd/audit/``.

    Falls back to an empty list when the audit subsystem is not present.
    """
    return sorted(_collect_matching(workdir / ".sdd" / "audit", tracker, ticket_id))


def _select_commits(workdir: Path, tracker: str, ticket_id: str) -> list[str]:
    """Default commit selector -- ``git log --grep=<ticket_id>``.

    Returns commits in chronological order (oldest first, newest last)
    so downstream consumers can treat ``commits[0]`` as the first commit
    of the ticket and ``commits[-1]`` as the latest. Returns an empty
    list when git is unavailable or the lookup fails.
    """
    if not ticket_id:
        return []
    try:
        proc = subprocess.run(
            ["git", "-C", str(workdir), "log", "--format=%H", f"--grep={ticket_id}", "--all"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    # git log emits newest-first; flip to oldest-first.
    newest_first = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    return list(reversed(newest_first))


def _select_pr_payload(workdir: Path, tracker: str, ticket_id: str) -> dict[str, Any] | None:
    """Default PR-payload selector -- look for ``.sdd/pr/pr_*.json``.

    Returns the first JSON file whose body mentions the ticket id, or
    ``None`` when no such file exists.
    """
    pr_dir = workdir / ".sdd" / "pr"
    if not pr_dir.is_dir():
        return None
    for child in sorted(pr_dir.iterdir()):
        if not child.is_file() or child.suffix.lower() != ".json":
            continue
        try:
            text = child.read_text(encoding="utf-8")
        except OSError:
            continue
        if ticket_id and ticket_id not in text:
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            continue
    return None


def default_selector() -> BundleSelector:
    """Return the on-disk default :class:`BundleSelector`.

    Each callable scans the conventional ``.sdd/`` subtree for its
    section. Missing directories yield empty results so the bundle can
    still be produced when subsystems are partially deployed.
    """
    return BundleSelector(
        transcripts=_select_transcripts,
        traces=_select_traces,
        lineage=_select_lineage,
        tracker_audit=_select_tracker_audit,
        commits=_select_commits,
        pr_payload=_select_pr_payload,
    )


# ---------------------------------------------------------------------------
# Bundle assembly
# ---------------------------------------------------------------------------


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_diff(workdir: Path, commits: list[str]) -> str:
    """Return a unified patch covering *commits*.

    For a single commit, this is ``git show``. For a multi-commit list,
    it is ``git diff <first>^..<last>``. Returns the empty string on
    failure or when the commit list is empty.
    """
    if not commits:
        return ""
    try:
        if len(commits) == 1:
            proc = subprocess.run(
                ["git", "-C", str(workdir), "show", "--patch", commits[0]],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        else:
            first, last = commits[0], commits[-1]
            proc = subprocess.run(
                ["git", "-C", str(workdir), "diff", f"{first}^..{last}"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout


@dataclass
class TicketBundle:
    """Per-ticket bundle assembler / signer / verifier.

    Typical usage::

        bundle = TicketBundle(
            workdir=Path("."),
            tracker="github",
            ticket_id="ENG-42",
        )
        manifest = bundle.assemble(out=Path("ENG-42.tar.gz"))
        bundle.sign(private_key_pem=priv, kid="lineage-kid-1")
        # later, on auditor host:
        ok = TicketBundle.verify(
            Path("ENG-42.tar.gz"),
            Path("ENG-42.tar.gz.jws"),
            card,
        )

    Attributes:
        workdir: Project root directory holding ``.sdd/``.
        tracker: Tracker identifier (e.g. ``github``).
        ticket_id: Ticket id within the tracker.
        selector: File-selection strategy. Defaults to
            :func:`default_selector`.
        output_path: Set after :meth:`assemble` succeeds.
        manifest: Set after :meth:`assemble` succeeds.
    """

    workdir: Path
    tracker: str
    ticket_id: str
    selector: BundleSelector = field(default_factory=default_selector)
    output_path: Path | None = None
    manifest: BundleManifest | None = None

    def assemble(self, out: Path) -> BundleManifest:
        """Assemble the bundle archive at *out* and return the manifest.

        Args:
            out: Destination path for the ``tar.gz`` archive. Parents
                are created if missing.

        Returns:
            The :class:`BundleManifest` written into the archive.
        """
        entries: list[tuple[str, str, bytes]] = []  # (arcname, section, data)

        def _pack_files(section: str, files: list[Path], rel_prefix: str) -> None:
            for src in files:
                try:
                    data = src.read_bytes()
                except OSError:
                    continue
                entries.append((f"{rel_prefix}/{src.name}", section, data))

        _pack_files("transcripts", self.selector.transcripts(self.workdir, self.tracker, self.ticket_id), "transcripts")
        _pack_files("traces", self.selector.traces(self.workdir, self.tracker, self.ticket_id), "traces")
        _pack_files("lineage", self.selector.lineage(self.workdir, self.tracker, self.ticket_id), "lineage")
        _pack_files("audit", self.selector.tracker_audit(self.workdir, self.tracker, self.ticket_id), "audit")

        commits = self.selector.commits(self.workdir, self.tracker, self.ticket_id)
        commits_payload = json.dumps(
            {"tracker": self.tracker, "ticket_id": self.ticket_id, "commits": commits},
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        entries.append(("git/commits.json", "git", commits_payload))

        diff_text = _git_diff(self.workdir, commits)
        if diff_text:
            entries.append(("git/diff.patch", "git", diff_text.encode("utf-8")))

        pr_payload = self.selector.pr_payload(self.workdir, self.tracker, self.ticket_id)
        pr_number: int | None = None
        if pr_payload is not None:
            raw_number = pr_payload.get("number")
            if isinstance(raw_number, int):
                pr_number = raw_number
            pr_bytes = json.dumps(pr_payload, indent=2, sort_keys=True).encode("utf-8")
            arc = f"pr/pr_{pr_number if pr_number is not None else 'unknown'}.json"
            entries.append((arc, "pr", pr_bytes))

        manifest_entries: list[ManifestEntry] = [
            ManifestEntry(
                arcname=arcname,
                size_bytes=len(data),
                sha256=_sha256_bytes(data),
                section=section,
            )
            for arcname, section, data in entries
        ]
        manifest_entries.sort(key=lambda e: e.arcname)

        manifest = BundleManifest(
            schema_version=MANIFEST_SCHEMA_VERSION,
            created_at=datetime.now(tz=UTC).isoformat(),
            bernstein_version=bernstein.__version__,
            tracker=self.tracker,
            ticket_id=self.ticket_id,
            files=manifest_entries,
            pr_number=pr_number,
            commits=commits.copy(),
        )

        out.parent.mkdir(parents=True, exist_ok=True)
        manifest_bytes = json.dumps(manifest.to_dict(), indent=2, sort_keys=True).encode("utf-8") + b"\n"

        # Use an explicit GzipFile with mtime=0 so the gzip header is
        # deterministic; the default tarfile.open(..., "w:gz") embeds
        # the current time and breaks bit-for-bit reproducibility.
        with (
            out.open("wb") as raw,
            gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz,
            tarfile.open(fileobj=gz, mode="w") as tf,
        ):
            self._add_bytes(tf, "manifest.json", manifest_bytes)
            for arcname, _section, data in sorted(entries, key=operator.itemgetter(0)):
                self._add_bytes(tf, arcname, data)

        self.output_path = out
        self.manifest = manifest
        return manifest

    @staticmethod
    def _add_bytes(tf: tarfile.TarFile, arcname: str, data: bytes) -> None:
        """Add *data* to *tf* under *arcname* with a deterministic header."""
        info = tarfile.TarInfo(name=arcname)
        info.size = len(data)
        info.mtime = 0  # deterministic
        info.mode = 0o644
        info.type = tarfile.REGTYPE
        info.uid = 0
        info.gid = 0
        info.uname = ""
        info.gname = ""
        from io import BytesIO

        tf.addfile(info, BytesIO(data))

    def sign(self, *, private_key_pem: str, kid: str, out: Path | None = None) -> Path:
        """Produce a detached Ed25519 JWS over the manifest.

        The signature covers :meth:`BundleManifest.canonical_bytes`, not
        the archive bytes themselves. Because every bundled file has its
        sha256 in the manifest, the signature transitively covers the
        archive contents -- tampering with any included file changes
        that file's sha256, which changes the canonical manifest bytes.

        Args:
            private_key_pem: PEM-encoded Ed25519 private key.
            kid: Key id; must match the verifier's :class:`AgentCard`.
            out: Optional path for the ``.jws`` file. Defaults to
                ``<archive>.jws``.

        Returns:
            Path to the written JWS file.
        """
        if self.manifest is None or self.output_path is None:
            raise RuntimeError("sign() requires a prior assemble() call")
        jws = sign_detached(self.manifest.canonical_bytes(), private_key_pem, kid=kid)
        jws_path = out if out is not None else Path(str(self.output_path) + ".jws")
        jws_path.parent.mkdir(parents=True, exist_ok=True)
        jws_path.write_text(jws, encoding="utf-8")
        return jws_path

    @classmethod
    def verify(cls, archive_path: Path, jws_path: Path, card: AgentCard) -> bool:
        """Verify a bundle's detached JWS against *card*.

        Returns True iff:

        1. The archive opens and contains a valid ``manifest.json``.
        2. Every file recorded in the manifest is present and its bytes
           sha256 to the recorded digest.
        3. The detached JWS verifies against the manifest's canonical
           bytes under *card*'s public key.

        Never raises on malformed input -- returns False instead.
        """
        try:
            with tarfile.open(archive_path, mode="r:*") as tf:
                manifest_member = tf.getmember("manifest.json")
                manifest_fp = tf.extractfile(manifest_member)
                if manifest_fp is None:
                    return False
                manifest_bytes_raw = manifest_fp.read()
                try:
                    manifest_dict = json.loads(manifest_bytes_raw)
                except json.JSONDecodeError:
                    return False
                manifest = _manifest_from_dict(manifest_dict)
                if manifest is None:
                    return False

                # Reject archives that contain members not listed in the
                # manifest. Without this, an attacker could smuggle
                # extra payload files past verification.
                allowed = {entry.arcname for entry in manifest.files}
                allowed.add("manifest.json")
                for member in tf.getmembers():
                    if not member.isfile():
                        # Skip directory/symlink/hardlink markers; we
                        # only ever pack regular files in assemble().
                        continue
                    if member.name not in allowed:
                        return False

                # Re-check each file's sha256 against the manifest.
                for entry in manifest.files:
                    try:
                        member = tf.getmember(entry.arcname)
                    except KeyError:
                        return False
                    file_fp = tf.extractfile(member)
                    if file_fp is None:
                        return False
                    data = file_fp.read()
                    if len(data) != entry.size_bytes:
                        return False
                    if _sha256_bytes(data) != entry.sha256:
                        return False
        except (tarfile.TarError, OSError, KeyError):
            return False

        try:
            jws = jws_path.read_text(encoding="utf-8").strip()
        except OSError:
            return False
        return verify_detached(manifest.canonical_bytes(), jws, card)


def _manifest_from_dict(payload: dict[str, Any]) -> BundleManifest | None:
    """Reconstruct a :class:`BundleManifest` from a raw JSON dict.

    Returns None on schema mismatch -- callers treat that as a verify
    failure rather than raising.
    """
    try:
        schema = int(payload["schema_version"])
        if schema not in SUPPORTED_SCHEMA_VERSIONS:
            # Reject unknown / future schema versions so an older
            # verifier never treats a newer manifest as v1 by accident.
            return None
        files_raw = payload.get("files", [])
        if not isinstance(files_raw, list):
            return None
        files: list[ManifestEntry] = []
        for row in files_raw:
            if not isinstance(row, dict):
                return None
            files.append(
                ManifestEntry(
                    arcname=str(row["arcname"]),
                    size_bytes=int(row["size_bytes"]),
                    sha256=str(row["sha256"]),
                    section=str(row.get("section", "")),
                ),
            )
        commits_raw = payload.get("commits", []) or []
        if not isinstance(commits_raw, list):
            return None
        return BundleManifest(
            schema_version=schema,
            created_at=str(payload["created_at"]),
            bernstein_version=str(payload["bernstein_version"]),
            tracker=str(payload["tracker"]),
            ticket_id=str(payload["ticket_id"]),
            files=files,
            pr_number=payload.get("pr_number") if isinstance(payload.get("pr_number"), int) else None,
            commits=[str(c) for c in commits_raw],
        )
    except (KeyError, TypeError, ValueError):
        return None
