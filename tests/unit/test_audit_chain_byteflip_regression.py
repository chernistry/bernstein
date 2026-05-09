"""Regression: ``\\n`` line-terminator flips must not slip past ``verify()``.

The Hypothesis property
``tests/property/test_audit_chain_properties.py::
test_single_byte_flip_breaks_verification`` enforces that any single-byte
flip in any persisted entry surfaces as a verification error. Hypothesis
shrunk the failing case to byte positions occupied by the line terminator
``\\n`` (``0x0A``) — flipping them to ``\\v`` (``0x0B``) survived the
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

from bernstein.core.security.audit import AuditLog


@pytest.fixture(name="audit_log")
def _audit_log() -> AuditLog:
    """Return a fresh AuditLog inside an isolated tempdir."""
    tmpdir = Path(tempfile.mkdtemp(prefix="bernstein-byteflip-"))
    audit_dir = tmpdir / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    key_path = tmpdir / "audit.key"
    key_path.write_bytes(b"regression-key-32-bytes-padding-pad")
    key_path.chmod(0o600)
    return AuditLog(audit_dir=audit_dir, key_path=key_path)


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
    attacker injecting a single space inside a JSON object literal — the
    resulting entry still parses cleanly, but the canonical form check
    catches the drift.
    """
    audit_log.log("evt", "actor", "task", "rid", {})
    target = sorted(audit_log._audit_dir.glob("*.jsonl"))[0]  # pyright: ignore[reportPrivateUsage]
    raw = target.read_bytes()
    # Insert an extra space after the opening ``{`` — survives json.loads
    # but breaks bytewise equality with the canonical re-serialisation.
    mutated = raw.replace(b'{"', b'{ "', 1)
    assert mutated != raw, "test setup: replacement did not change bytes"
    target.write_bytes(mutated)

    valid, errors = audit_log.verify()
    assert valid is False, "non-canonical whitespace slipped past verify()"
    assert errors, "verify() returned invalid=True with empty errors list"
