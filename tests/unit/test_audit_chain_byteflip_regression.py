"""Regression: ``\\n`` line-terminator flips must not slip past ``verify()``.

The Hypothesis property
``tests/property/test_audit_chain_properties.py::
test_single_byte_flip_breaks_verification`` enforces that any single-byte
flip in any persisted entry surfaces as a verification error. Hypothesis
shrunk the failing case to byte positions occupied by the line terminator
``\\n`` (``0x0A``) - flipping them to ``\\v`` (``0x0B``) survived the
previous verifier because ``str.splitlines()`` treats the two bytes as
equivalent line separators.

This file pins the specific shrunk cases as regular unit tests so the fix
sticks even if the property test takes time to find the regression on a
faster random seed.

Two flip sites are exercised:

* The newline **between** two log lines (changes line layout for
  ``splitlines()``-based parsers but is invisible at the bytes layer).
* The trailing newline **at end of file** (truncates the file's terminator
  in a way that historically left the last line readable).

Both must be reported as verification errors after the fix.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from bernstein.core.security.audit import (
    _GENESIS_HMAC,  # pyright: ignore[reportPrivateUsage]
    AuditLog,
)


def _create_audit_log(prefix: str = "bernstein-byteflip-") -> AuditLog:
    """Return a fresh AuditLog over an isolated tempdir with a 0600 key.

    Shared by the ``audit_log`` fixture and the recovery tests that need a
    fresh log outside fixture scope, so the tempdir/key-permission setup
    lives in one place.
    """
    tmpdir = Path(tempfile.mkdtemp(prefix=prefix))
    audit_dir = tmpdir / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    key_path = tmpdir / "audit.key"
    key_path.write_bytes(b"regression-key-32-bytes-padding-pad")
    key_path.chmod(0o600)
    return AuditLog(audit_dir=audit_dir, key_path=key_path)


@pytest.fixture(name="audit_log")
def _audit_log() -> AuditLog:
    """Return a fresh AuditLog inside an isolated tempdir."""
    return _create_audit_log()


def _flip_byte(path: Path, offset: int) -> None:
    """XOR byte at ``offset`` with ``0x01`` (newline ↔ vertical tab)."""
    raw = path.read_bytes()
    mutated = bytearray(raw)
    mutated[offset] ^= 0x01
    path.write_bytes(bytes(mutated))


def test_interline_newline_flip_is_detected(audit_log: AuditLog) -> None:
    """Flipping the ``\\n`` between two log lines must trip ``verify()``.

    Pre-fix path: ``str.splitlines()`` accepted ``\\v`` as a line
    separator, so the two entries still parsed cleanly and the chain
    verified. Post-fix path: ``read_bytes().split(b"\\n")`` glues the
    mutated lines into a single malformed entry, surfaced as ``invalid
    JSON`` (or, when shape happens to round-trip, as ``non-canonical
    line bytes``).
    """
    audit_log.log("evt1", "actor", "task", "rid", {"k": 1})
    audit_log.log("evt2", "actor", "task", "rid", {"k": 2})

    target = sorted(audit_log._audit_dir.glob("*.jsonl"))[0]  # pyright: ignore[reportPrivateUsage]
    raw = target.read_bytes()
    interline_offsets = [i for i, b in enumerate(raw[:-1]) if b == 0x0A]
    assert interline_offsets, "test setup: no inter-line newline found"

    _flip_byte(target, interline_offsets[0])
    valid, errors = audit_log.verify()
    assert valid is False, "interline newline flip slipped past verify()"
    assert errors, "verify() returned invalid=True with empty errors list"


def test_trailing_newline_flip_is_detected(audit_log: AuditLog) -> None:
    """Flipping the file's final ``\\n`` must trip ``verify()``.

    Pre-fix path: the trailing terminator was treated as boilerplate and
    a ``\\n`` → ``\\v`` flip went unnoticed because ``splitlines()``
    consumed both bytes as separators. Post-fix path: the verifier
    requires the file to end with ``b"\\n"`` and surfaces the missing
    terminator as a hard error.
    """
    audit_log.log("evt1", "actor", "task", "rid", {})
    audit_log.log("evt2", "actor", "task", "rid", {})

    target = sorted(audit_log._audit_dir.glob("*.jsonl"))[0]  # pyright: ignore[reportPrivateUsage]
    raw = target.read_bytes()
    assert raw.endswith(b"\n"), "test setup: file does not end with newline"

    _flip_byte(target, len(raw) - 1)
    valid, errors = audit_log.verify()
    assert valid is False, "trailing newline flip slipped past verify()"
    assert errors, "verify() returned invalid=True with empty errors list"


def test_canonical_form_drift_is_detected(audit_log: AuditLog) -> None:
    """Whitespace tamper that survives ``json.loads`` must still fail.

    JSON tolerates incidental whitespace around values (e.g. a stray
    ``\\t`` or extra spaces). The verifier guards against this by
    re-canonicalising each entry via ``json.dumps(..., sort_keys=True)``
    and comparing the bytes to the on-disk line. This test simulates an
    attacker injecting a single space inside a JSON object literal - the
    resulting entry still parses cleanly, but the canonical form check
    catches the drift.
    """
    audit_log.log("evt", "actor", "task", "rid", {})
    target = sorted(audit_log._audit_dir.glob("*.jsonl"))[0]  # pyright: ignore[reportPrivateUsage]
    raw = target.read_bytes()
    # Insert an extra space after the opening ``{`` - survives json.loads
    # but breaks bytewise equality with the canonical re-serialisation.
    mutated = raw.replace(b'{"', b'{ "', 1)
    assert mutated != raw, "test setup: replacement did not change bytes"
    target.write_bytes(mutated)

    valid, errors = audit_log.verify()
    assert valid is False, "non-canonical whitespace slipped past verify()"
    assert errors, "verify() returned invalid=True with empty errors list"


def test_writer_uses_lf_only_terminator(audit_log: AuditLog) -> None:
    """Writer must emit ``b"\\n"`` even on Windows (no CRLF translation).

    Text-mode ``open("a")`` triggers Python's universal-newline translation
    on Windows, replacing ``\\n`` with ``\\r\\n`` on disk. The strict
    ``b"\\n"``-only verifier then sees ``}\\r\\n`` as the line, surfaces a
    trailing ``\\r`` as ``non-canonical line bytes``, and fresh logs fail
    verification on the very next read. Pinning binary append mode here so
    a future refactor cannot silently regress to text mode.
    """
    audit_log.log("evt1", "actor", "task", "rid", {})
    audit_log.log("evt2", "actor", "task", "rid", {})

    target = sorted(audit_log._audit_dir.glob("*.jsonl"))[0]  # pyright: ignore[reportPrivateUsage]
    raw = target.read_bytes()
    assert b"\r\n" not in raw, f"writer emitted CRLF terminators (text-mode open?): {raw!r}"
    # Two events → two LF terminators, the file must end with exactly one.
    assert raw.count(b"\n") == 2, f"unexpected newline count: {raw!r}"
    assert raw.endswith(b"\n") and not raw.endswith(b"\r\n"), f"file must end with bare LF, got: {raw[-4:]!r}"

    valid, errors = audit_log.verify()
    assert valid is True, f"freshly written log failed verify: {errors}"
    assert errors == [], f"unexpected errors on fresh log: {errors}"


# ---------------------------------------------------------------------------
# Recovery must agree with verify() on record framing.
#
# ``verify()`` splits strictly on ``b"\n"`` and rejects an inter-line
# ``\n`` -> ``\v`` flip (the mutation pinned by
# test_interline_newline_flip_is_detected). Chain-tail recovery
# (``AuditLog.__init__`` -> ``_recover_chain_tail``) historically used
# ``read_text().splitlines()``, which treats ``\v`` as a line separator.
# Recovery therefore split the tampered file the way the writer's ``\n``
# would have, recovered the last entry's ``hmac`` as if untouched, and a
# fresh ``AuditLog`` would keep appending valid-HMAC events on top of a log
# that ``verify()`` already considers broken - with no signal at recovery
# time. These tests pin recovery to the same byte-strict framing.
# ---------------------------------------------------------------------------


def _make_audit_log() -> AuditLog:
    """Return a fresh AuditLog over an isolated tempdir with a 0600 key."""
    return _create_audit_log(prefix="bernstein-recover-")


def test_recovery_does_not_adopt_tail_that_verify_rejects(audit_log: AuditLog) -> None:
    """An inter-line ``\\n`` -> ``\\v`` flip must not be silently absorbed by recovery.

    Pre-fix path: ``splitlines()`` split the tampered file into two clean
    records and recovery returned the last record's ``hmac`` - the exact
    tail ``verify()`` rejects. Post-fix path: byte-strict ``b"\\n"`` framing
    glues the two records into one malformed line, so recovery cannot adopt
    the mis-framed tail and instead falls back (here, to genesis, since the
    only file is the tampered one).
    """
    audit_log.log("evt1", "actor", "task", "rid", {"k": 1})
    last_record_hmac = audit_log.log("evt2", "actor", "task", "rid", {"k": 2}).hmac

    target = sorted(audit_log._audit_dir.glob("*.jsonl"))[0]  # pyright: ignore[reportPrivateUsage]
    raw = target.read_bytes()
    # The inter-line newline is the boundary that decides where the LAST
    # record begins; flipping it is what desyncs recovery from the tail.
    interline_offsets = [i for i, b in enumerate(raw[:-1]) if b == 0x0A]
    assert interline_offsets, "test setup: no inter-line newline found"

    mutated = bytearray(raw)
    mutated[interline_offsets[-1]] ^= 0x01  # \n -> \v, between the two records
    target.write_bytes(bytes(mutated))

    # verify() still rejects the tampered file (sanity: the tamper is real).
    valid, errors = audit_log.verify()
    assert valid is False, "interline flip slipped past verify()"
    assert errors

    # A fresh AuditLog must NOT recover the tail verify() rejects.
    reopened = AuditLog(audit_dir=audit_log._audit_dir, key=audit_log._key)  # pyright: ignore[reportPrivateUsage]
    recovered = reopened._prev_hmac  # pyright: ignore[reportPrivateUsage]
    assert recovered != last_record_hmac, (
        "recovery adopted the last record's hmac from a file verify() rejects; "
        "splitlines() over-split the tampered tail"
    )

    # A subsequent append must not chain onto the mis-framed record.
    appended = reopened.log("evt-after", "actor", "task", "rid", {})
    assert appended.prev_hmac != last_record_hmac, "append chained onto the mis-framed record's hmac"


def test_clean_reopen_recovers_same_tail_hmac() -> None:
    """A clean log recovers the same tail HMAC across a reopen (no regression).

    Mirrors the contract behind test_append_after_reopen_continues_chain:
    byte-strict recovery must not change the recovered tip for an untampered
    log, and a subsequent append must chain onto the genuine last ``hmac``.
    """
    log = _make_audit_log()
    log.log("evt1", "actor", "task", "rid", {"k": 1})
    last = log.log("evt2", "actor", "task", "rid", {"k": 2})

    reopened = AuditLog(audit_dir=log._audit_dir, key=log._key)  # pyright: ignore[reportPrivateUsage]
    assert reopened._prev_hmac == last.hmac  # pyright: ignore[reportPrivateUsage]

    appended = reopened.log("evt3", "actor", "task", "rid", {})
    assert appended.prev_hmac == last.hmac
    valid, errors = reopened.verify()
    assert valid is True, f"reopened+appended chain failed verify: {errors}"


def test_truncated_final_line_still_recovers_last_well_formed_record() -> None:
    """A genuinely truncated final line (crash mid-write) still recovers.

    A writer crash can leave a partial final record with no trailing ``\\n``.
    Byte-strict recovery must skip that malformed tail and resume from the
    last well-formed record, exactly as the legitimate truncation path does
    today (test_truncated_last_file_does_not_fork_chain).
    """
    log = _make_audit_log()
    last = log.log("evt1", "actor", "task", "rid", {"k": 1})

    target = sorted(log._audit_dir.glob("*.jsonl"))[0]  # pyright: ignore[reportPrivateUsage]
    # Append a truncated partial record (no trailing newline), as a crash
    # mid-write would leave behind.
    with target.open("ab") as fh:
        fh.write(b'{"timestamp": "2099-01-01T00:00:00.0Z", "event_typ')

    reopened = AuditLog(audit_dir=log._audit_dir, key=log._key)  # pyright: ignore[reportPrivateUsage]
    assert reopened._prev_hmac == last.hmac, (  # pyright: ignore[reportPrivateUsage]
        "recovery did not skip the truncated final line back to the last well-formed record"
    )
    # Genesis fallback is the wrong answer here - there IS a valid record.
    assert reopened._prev_hmac != _GENESIS_HMAC  # pyright: ignore[reportPrivateUsage]


def test_recovery_tolerates_invalid_utf8_in_tampered_tail() -> None:
    """A non-UTF-8 byte in the tail must not crash ``AuditLog`` construction.

    Byte-strict recovery decodes each record via ``json.loads`` on raw
    bytes. A flipped byte can produce invalid UTF-8 (e.g. a lone ``0x80``),
    which raises ``UnicodeDecodeError`` rather than ``json.JSONDecodeError``.
    Recovery must treat such a record as corrupt - skip it and resume from
    the last well-formed record - instead of letting the decode error
    propagate out of the constructor and wedge startup.
    """
    log = _make_audit_log()
    last = log.log("evt1", "actor", "task", "rid", {"k": 1})

    target = sorted(log._audit_dir.glob("*.jsonl"))[0]  # pyright: ignore[reportPrivateUsage]
    # Append a final record carrying a lone continuation byte (invalid UTF-8).
    with target.open("ab") as fh:
        fh.write(b'{"x": "\x80"}\n')

    # Construction must not raise; recovery skips the undecodable tail.
    reopened = AuditLog(audit_dir=log._audit_dir, key=log._key)  # pyright: ignore[reportPrivateUsage]
    assert reopened._prev_hmac == last.hmac  # pyright: ignore[reportPrivateUsage]
