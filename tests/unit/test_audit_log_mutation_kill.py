"""Targeted tests that kill mutmut survivors in audit.py (audit_log).

Each test pins one or more specific invariants identified by
``scripts/mutmut_critical.py --only audit_log``. Tests live in their own
file to keep the surviving-mutant map readable; the existing
``test_audit.py`` / ``test_audit_key.py`` / ``test_audit_chain_byteflip_regression.py``
are left untouched.
"""

from __future__ import annotations

import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from bernstein.core.security.audit import (
    _GENESIS_HMAC,  # pyright: ignore[reportPrivateUsage]
    AUDIT_KEY_ENV,
    AuditEvent,
    AuditKeyPermissionError,
    AuditLog,
    RetentionPolicy,
    _matches_query_filters,  # pyright: ignore[reportPrivateUsage]
    _split_jsonl_bytes,  # pyright: ignore[reportPrivateUsage]
)

# ---------------------------------------------------------------------------
# Common fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def audit_dir(tmp_path: Path) -> Path:
    d = tmp_path / "audit"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# Line 62: docstring of AuditKeyPermissionError mentions 0600. The mutation
# script flips ' 0' -> ' 1' inside the docstring, which changes the
# documented threshold. Pin the docstring so the mutation is killed.
# ---------------------------------------------------------------------------


def test_audit_key_permission_error_docstring_mentions_0600() -> None:
    """The docstring of AuditKeyPermissionError documents the 0600 threshold."""
    doc = AuditKeyPermissionError.__doc__
    assert doc is not None
    assert "0600" in doc, f"expected '0600' in docstring, got: {doc!r}"
    # And the mutated value must NOT be present.
    assert "1600" not in doc


# ---------------------------------------------------------------------------
# Line 102: error message for stat failure begins with 'Cannot stat'. The
# mutation removes 'not ' anywhere on the line; the first match flips
# 'Cannot' -> 'Cant' inside the f-string. Pin the exact wording.
# ---------------------------------------------------------------------------


def test_stat_failure_error_message_uses_cannot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When _enforce_key_permissions cannot stat the key, the error reads 'Cannot stat ...'."""
    import os as _os

    from bernstein.core.security.audit import _enforce_key_permissions  # pyright: ignore[reportPrivateUsage]

    # Bypass on Windows where _enforce_key_permissions is a no-op.
    if _os.name == "nt":
        pytest.skip("Windows skips POSIX permission enforcement")

    # Force stat() to raise OSError.
    def _boom(*_args: object, **_kw: object) -> None:
        raise OSError("simulated stat failure")

    monkeypatch.setattr(Path, "stat", _boom)
    with pytest.raises(AuditKeyPermissionError) as exc:
        _enforce_key_permissions(tmp_path / "key")
    msg = str(exc.value)
    assert "Cannot stat" in msg, f"expected 'Cannot stat' in: {msg!r}"
    # The mutated 'Cant stat' must not appear.
    assert "Cant stat" not in msg


# ---------------------------------------------------------------------------
# Line 141: parent.mkdir(parents=True, exist_ok=True). Mutant 'True'->'False'
# on `parents=True` would crash when a nested parent directory is missing.
# We pin: load_or_create_audit_key succeeds when key path is multiple
# levels deep below an empty tmpdir.
# ---------------------------------------------------------------------------


def test_load_or_create_audit_key_creates_nested_parents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nested missing parent directories are created (parents=True is honoured)."""
    monkeypatch.delenv(AUDIT_KEY_ENV, raising=False)
    deep = tmp_path / "a" / "b" / "c" / "audit.key"
    # Sanity: parents really are absent.
    assert not deep.parent.exists()
    assert not deep.parent.parent.exists()
    assert not deep.parent.parent.parent.exists()

    from bernstein.core.security.audit import load_or_create_audit_key

    load_or_create_audit_key(key_path=deep)
    assert deep.exists()
    assert deep.parent.is_dir()
    assert deep.parent.parent.is_dir()


# ---------------------------------------------------------------------------
# Line 233: `if parts and parts[-1] == b"":  parts.pop()`. The function
# drops the trailing empty element produced by ``split(b"\n")`` on a file
# that ends with a newline. Two mutants survived:
#   - '==' -> '!=' inverts the condition.
#   - 'and' -> 'or' triggers an IndexError on the empty-parts case.
# ---------------------------------------------------------------------------


def test_split_jsonl_bytes_drops_trailing_empty_after_newline() -> None:
    """Trailing newline produces N entries, not N+1 with an empty tail."""
    raw = b'{"a": 1}\n{"b": 2}\n'
    parts = _split_jsonl_bytes(raw)
    assert parts == [b'{"a": 1}', b'{"b": 2}']
    # Specifically: there must be no empty bytes element at the end.
    assert b"" not in parts


def test_split_jsonl_bytes_handles_empty_input() -> None:
    """Empty bytes input returns an empty list (not an IndexError)."""
    # If the 'and' mutation flips to 'or', the function would try to
    # access parts[-1] on an empty list and raise IndexError.
    assert _split_jsonl_bytes(b"") == []


def test_split_jsonl_bytes_preserves_lines_without_trailing_newline() -> None:
    """Without a trailing newline, the final byte sequence is kept verbatim."""
    raw = b'{"a": 1}\n{"b": 2}'
    parts = _split_jsonl_bytes(raw)
    assert parts == [b'{"a": 1}', b'{"b": 2}']


# ---------------------------------------------------------------------------
# Line 258: `if not isinstance(entry, dict):` in the per-line verify loop.
# Mutant drops the 'not', so non-dict entries pass through and corrupt
# the chain check. We craft a JSONL line whose payload is a JSON array
# (a list, not a dict) and verify the loop reports the correct error.
# ---------------------------------------------------------------------------


def test_verify_rejects_non_dict_entry(audit_dir: Path) -> None:
    """A JSON value that parses to a list (not a dict) is rejected explicitly."""
    log = AuditLog(audit_dir, key=b"k")
    # Write one valid event then overwrite with a non-dict line.
    log.log("e", "a", "r", "i")
    log_files = sorted(audit_dir.glob("*.jsonl"))
    p = log_files[0]
    # Replace the body with a JSON array (top-level list, parses but is not a dict).
    p.write_bytes(b"[1, 2, 3]\n")

    valid, errors = log.verify()
    assert valid is False
    assert any("entry is not a JSON object" in e for e in errors), errors


# ---------------------------------------------------------------------------
# Line 267: canonical = json.dumps(entry, sort_keys=True).encode().
# Mutant flips sort_keys to False. We force a divergence by writing a
# JSON line with non-sorted key order (legal JSON) and checking the
# verifier catches the canonical-form drift.
# ---------------------------------------------------------------------------


def test_verify_re_canonicalises_with_sort_keys(audit_dir: Path) -> None:
    """A line with valid JSON but non-sorted keys must fail re-canonicalisation."""
    # Build a real event then overwrite the on-disk line with a re-ordered version.
    log = AuditLog(audit_dir, key=b"k")
    evt = log.log("e", "a", "r", "i", {"x": 1})
    p = sorted(audit_dir.glob("*.jsonl"))[0]
    # Re-serialise the event dict WITHOUT sort_keys to flip key order.
    body = {
        "timestamp": evt.timestamp,
        "event_type": evt.event_type,
        "actor": evt.actor,
        "resource_type": evt.resource_type,
        "resource_id": evt.resource_id,
        "details": evt.details,
        "prev_hmac": evt.prev_hmac,
        "hmac": evt.hmac,
    }
    # Force a key order that is NOT lexicographic (sort_keys=False keeps
    # this insertion order in the JSON output).
    reordered = {
        "hmac": body["hmac"],
        "timestamp": body["timestamp"],
        "event_type": body["event_type"],
        "actor": body["actor"],
        "resource_type": body["resource_type"],
        "resource_id": body["resource_id"],
        "details": body["details"],
        "prev_hmac": body["prev_hmac"],
    }
    non_canonical = json.dumps(reordered, sort_keys=False).encode()
    # Make sure we actually produced a different byte sequence.
    canonical = json.dumps(body, sort_keys=True).encode()
    assert non_canonical != canonical
    p.write_bytes(non_canonical + b"\n")

    valid, errors = log.verify()
    assert valid is False
    assert any("non-canonical line bytes" in e for e in errors), errors


# ---------------------------------------------------------------------------
# Line 300: docstring of _matches_query_filters says "Return True if entry
# passes all query filters." Pin the docstring word "True" so the mutation
# 'True' -> 'False' inside the docstring is killed.
# ---------------------------------------------------------------------------


def test_matches_query_filters_docstring_describes_true_path() -> None:
    """The helper's docstring uses 'True' (not 'False') for the pass case."""
    doc = _matches_query_filters.__doc__
    assert doc is not None
    assert "True" in doc, f"expected 'True' in docstring, got: {doc!r}"


# ---------------------------------------------------------------------------
# Lines 301-308: _matches_query_filters branches. We test each branch
# directly: pass-by-event_type, fail-by-event_type, pass-by-actor, etc.
# These also kill the 'False'->'True' / 'return False'->'return True' /
# 'and'->'or' / '!='/'<'/'>' mutations.
# ---------------------------------------------------------------------------


def _entry(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "event_type": "task.created",
        "actor": "system",
        "timestamp": "2026-01-01T00:00:00.000000Z",
    }
    base.update(kw)
    return base


def test_query_filter_event_type_mismatch_returns_false() -> None:
    """Different event_type -> filter returns False."""
    assert _matches_query_filters(_entry(event_type="other"), "task.created", None, None, None) is False


def test_query_filter_event_type_match_returns_true() -> None:
    """Matching event_type with no other filters -> True."""
    assert _matches_query_filters(_entry(event_type="task.created"), "task.created", None, None, None) is True


def test_query_filter_event_type_none_disabled() -> None:
    """event_type=None disables that filter (does not implicitly match anything)."""
    # Mutating ' and ' to ' or ' in the event_type branch would activate the
    # negative branch even when the filter arg is None/empty.
    assert _matches_query_filters(_entry(event_type="x"), None, None, None, None) is True


def test_query_filter_actor_mismatch_returns_false() -> None:
    assert _matches_query_filters(_entry(actor="bob"), None, "alice", None, None) is False


def test_query_filter_actor_match_returns_true() -> None:
    assert _matches_query_filters(_entry(actor="alice"), None, "alice", None, None) is True


def test_query_filter_since_strictly_earlier_excluded() -> None:
    """ts < since -> False (strict)."""
    # An event at 2026-01-01 must be excluded by since=2026-06-01.
    e = _entry(timestamp="2026-01-01T00:00:00.000000Z")
    assert _matches_query_filters(e, None, None, "2026-06-01T00:00:00.000000Z", None) is False


def test_query_filter_since_equal_included() -> None:
    """ts == since must be INCLUDED (boundary; '<' not '<=')."""
    e = _entry(timestamp="2026-01-01T00:00:00.000000Z")
    assert _matches_query_filters(e, None, None, "2026-01-01T00:00:00.000000Z", None) is True


def test_query_filter_until_strictly_later_excluded() -> None:
    """ts > until -> excluded (return not (until and ts > until))."""
    e = _entry(timestamp="2026-12-31T00:00:00.000000Z")
    assert _matches_query_filters(e, None, None, None, "2026-06-01T00:00:00.000000Z") is False


def test_query_filter_until_equal_included() -> None:
    """ts == until must be INCLUDED (boundary; '>' not '>=')."""
    e = _entry(timestamp="2026-01-01T00:00:00.000000Z")
    assert _matches_query_filters(e, None, None, None, "2026-01-01T00:00:00.000000Z") is True


def test_query_filter_until_none_disabled() -> None:
    """until=None disables the upper-bound filter (kills 'and'->'or' on line 308)."""
    e = _entry(timestamp="2026-12-31T00:00:00.000000Z")
    assert _matches_query_filters(e, None, None, None, None) is True


# ---------------------------------------------------------------------------
# AuditLog.query() integration - exercises lines 528/534/541.
# ---------------------------------------------------------------------------


def test_query_empty_log_returns_empty_list(audit_dir: Path) -> None:
    """query() on an empty audit dir returns [] (not [None])."""
    log = AuditLog(audit_dir, key=b"k")
    result = log.query()
    assert result == []
    # Defensive against [None] mutation: every element must be an AuditEvent.
    assert all(isinstance(r, AuditEvent) for r in result)


def test_query_skips_blank_lines(audit_dir: Path) -> None:
    """Blank lines in the JSONL file are skipped (line 534 'not raw')."""
    log = AuditLog(audit_dir, key=b"k")
    log.log("e1", "a1", "r", "i1")
    # Insert blank lines into the log file.
    p = sorted(audit_dir.glob("*.jsonl"))[0]
    text = p.read_text()
    p.write_text("\n\n" + text + "\n\n")
    # Re-init so the chain reloads cleanly (we're just testing query()).
    log2 = AuditLog(audit_dir, key=b"k")
    result = log2.query()
    # Only one real event; no extra phantom events from blank lines.
    assert len(result) == 1
    assert result[0].event_type == "e1"


def test_query_event_type_filter_only_returns_matches(audit_dir: Path) -> None:
    """When a filter is set, non-matching entries must be excluded (line 541)."""
    log = AuditLog(audit_dir, key=b"k")
    log.log("type.A", "a1", "r", "i1")
    log.log("type.B", "a1", "r", "i2")
    log.log("type.A", "a1", "r", "i3")
    only_a = log.query(event_type="type.A")
    assert len(only_a) == 2
    assert {e.resource_id for e in only_a} == {"i1", "i3"}


# ---------------------------------------------------------------------------
# Line 361: in _recover_chain_tail, `if not line: continue` after strip().
# Mutant drops the 'not' so empty lines would be passed to json.loads.
# We construct a tail of blank-padded entries and check chain recovery.
# ---------------------------------------------------------------------------


def test_recover_chain_tail_skips_blank_lines(audit_dir: Path) -> None:
    """Blank lines in the tail must be skipped (not parsed)."""
    log = AuditLog(audit_dir, key=b"k")
    evt = log.log("e", "a", "r", "i")
    # Append a few blank lines to the file.
    p = sorted(audit_dir.glob("*.jsonl"))[0]
    p.write_text(p.read_text() + "\n\n\n")
    # Reload - recovery must find the real entry, not crash on blanks.
    log2 = AuditLog(audit_dir, key=b"k")
    assert log2._prev_hmac == evt.hmac  # pyright: ignore[reportPrivateUsage]


# ---------------------------------------------------------------------------
# Line 367: `if isinstance(entry, dict) and "hmac" in entry: return ...`
# Mutant flips 'and'->'or': a non-dict entry with no 'hmac' key would
# match (None case). We pin: a JSON entry that is a list does NOT trip
# chain recovery; it walks past and returns genesis.
# ---------------------------------------------------------------------------


def test_recover_chain_tail_ignores_non_dict_entries(audit_dir: Path) -> None:
    """A JSON list line in the audit dir does not poison _recover_chain_tail."""
    # Write a file that contains a top-level JSON array (parses, but is
    # not a dict and lacks 'hmac'). Recovery must continue past it and
    # fall back to genesis.
    p = audit_dir / "2026-01-01.jsonl"
    p.write_text("[1, 2, 3]\n")
    log = AuditLog(audit_dir, key=b"k")
    # No valid prior chain -> stays at genesis.
    assert log._prev_hmac == _GENESIS_HMAC  # pyright: ignore[reportPrivateUsage]


def test_recover_chain_tail_requires_hmac_field_in_dict(audit_dir: Path) -> None:
    """A dict-shaped entry without an 'hmac' field is ignored (kills 'and'->'or').

    If the condition were ``isinstance(entry, dict) or "hmac" in entry`` a
    dict line lacking the 'hmac' key would still match and trigger
    ``entry["hmac"]`` -> KeyError, which would crash AuditLog
    construction. The correct behaviour is to walk past such entries
    silently and continue searching for a valid prior hmac (or fall back
    to genesis if none is found).
    """
    p = audit_dir / "2026-01-01.jsonl"
    # Dict but without an 'hmac' key.
    p.write_text(json.dumps({"event_type": "x", "actor": "y"}) + "\n")
    # Construction must succeed and yield genesis.
    log = AuditLog(audit_dir, key=b"k")
    assert log._prev_hmac == _GENESIS_HMAC  # pyright: ignore[reportPrivateUsage]


def test_recover_chain_tail_walks_past_missing_hmac_to_earlier_real_entry(audit_dir: Path) -> None:
    """When the most recent dict line has no 'hmac', recovery walks earlier."""
    # Real chain first.
    log = AuditLog(audit_dir, key=b"k")
    evt = log.log("e1", "a", "r", "i")
    real_hmac = evt.hmac
    # Append a dict-shaped line WITHOUT an hmac field - recovery must skip it.
    p = sorted(audit_dir.glob("*.jsonl"))[0]
    p.write_text(p.read_text() + json.dumps({"junk": True}) + "\n")
    # Reload - recovery should walk past the junk dict and find the real hmac.
    log2 = AuditLog(audit_dir, key=b"k")
    assert log2._prev_hmac == real_hmac  # pyright: ignore[reportPrivateUsage]


# ---------------------------------------------------------------------------
# Line 400: ``details or {}``. Mutant flips 'or' -> 'and'. With 'and',
# details=None would propagate as None into the event/log payload.
# ---------------------------------------------------------------------------


def test_log_details_default_is_empty_dict_not_none(audit_dir: Path) -> None:
    """log(... details=None) stores an empty dict, not None (kills 'or'->'and')."""
    log = AuditLog(audit_dir, key=b"k")
    evt = log.log("e", "a", "r", "i", None)
    # Event dataclass receives {} not None.
    assert evt.details == {}
    assert isinstance(evt.details, dict)
    # The serialised JSON payload also carries {}.
    p = sorted(audit_dir.glob("*.jsonl"))[0]
    data = json.loads(p.read_text().strip())
    assert data["details"] == {}


def test_log_details_provided_is_preserved(audit_dir: Path) -> None:
    """A truthy details dict is stored as-is (no degradation under 'and')."""
    log = AuditLog(audit_dir, key=b"k")
    evt = log.log("e", "a", "r", "i", {"x": 7})
    assert evt.details == {"x": 7}


# ---------------------------------------------------------------------------
# Line 444: `return True, []` for the empty audit dir branch. Three
# mutants flip True/[]/return True. We pin the exact (True, []) tuple.
# ---------------------------------------------------------------------------


def test_verify_empty_directory_returns_true_and_empty_list(audit_dir: Path) -> None:
    """verify() on an audit dir with no log files returns (True, [])."""
    log = AuditLog(audit_dir, key=b"k")
    valid, errors = log.verify()
    assert valid is True
    assert errors == []
    # Defensive against [None] mutation on the errors list.
    assert all(e is not None for e in errors)


# ---------------------------------------------------------------------------
# Line 468: ``policy = policy or RetentionPolicy()``. Mutant flips 'or' ->
# 'and'. With 'and', archive(None) would try to access None.archive_subdir
# and crash.
# ---------------------------------------------------------------------------


def test_archive_with_none_policy_uses_default(audit_dir: Path) -> None:
    """archive(None) substitutes RetentionPolicy() (kills 'or'->'and')."""
    log = AuditLog(audit_dir, key=b"k")
    # No log files -> result is empty but the call must not crash.
    result = log.archive(None)
    assert result.archive_dir.endswith("archive")


# ---------------------------------------------------------------------------
# Line 470: archive_dir.mkdir(parents=True, exist_ok=True). Mutant
# 'True'->'False' on parents=True; we exercise a deep audit dir so a
# missing parent forces mkdir(parents=True).
# ---------------------------------------------------------------------------


def test_archive_creates_subdir_under_existing_audit_dir(audit_dir: Path) -> None:
    """archive() creates the archive subdirectory inside audit_dir."""
    log = AuditLog(audit_dir, key=b"k")
    sub = audit_dir / "archive"
    assert not sub.exists()
    log.archive()
    assert sub.is_dir()


def test_archive_creates_multi_level_subdir(audit_dir: Path) -> None:
    """archive_subdir containing '/' forces parents=True in mkdir (kills True->False)."""
    log = AuditLog(audit_dir, key=b"k")
    # Multi-level archive subdir: audit_dir/old/archive. The intermediate
    # 'old' directory does not exist; mkdir(parents=False) would crash.
    policy = RetentionPolicy(retention_days=90, archive_subdir="old/archive")
    log.archive(policy)
    assert (audit_dir / "old" / "archive").is_dir()


# ---------------------------------------------------------------------------
# Lines 474/475: archived: list[str] = [] and skipped: list[str] = [].
# Mutants flip [] -> [None]. We pin both lists do NOT carry a stray None
# when archive() runs in steady state.
# ---------------------------------------------------------------------------


def _make_dated_log(audit_dir: Path, days_ago: int) -> str:
    date = (datetime.now(tz=UTC) - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    log_path = audit_dir / f"{date}.jsonl"
    entry: dict[str, Any] = {
        "timestamp": f"{date}T00:00:00.000000Z",
        "event_type": "test",
        "actor": "test",
        "resource_type": "test",
        "resource_id": "id1",
        "details": {},
        "prev_hmac": _GENESIS_HMAC,
        "hmac": "a" * 64,
    }
    log_path.write_text(json.dumps(entry, sort_keys=True) + "\n")
    return log_path.name


def test_archive_lists_have_no_stray_none_when_empty(audit_dir: Path) -> None:
    """archived/skipped start as [] not [None]: an idle archive() leaves both empty."""
    # No source files at all -> both lists must be empty.
    log = AuditLog(audit_dir, key=b"k")
    result = log.archive()
    assert result.archived == []
    assert result.skipped == []
    assert all(x is not None for x in result.archived)
    assert all(x is not None for x in result.skipped)


def test_archive_skipped_only_contains_recent_filenames(audit_dir: Path) -> None:
    """skipped carries only the real recent filenames (no None mixed in)."""
    name = _make_dated_log(audit_dir, days_ago=10)
    log = AuditLog(audit_dir, key=b"k")
    result = log.archive(RetentionPolicy(retention_days=90))
    assert result.skipped == [name]
    assert result.archived == []


# ---------------------------------------------------------------------------
# Line 485: ``if file_date >= cutoff: skipped``. Mutant '>=' -> '>'.
# A file dated EXACTLY at cutoff must be skipped (not archived).
# ---------------------------------------------------------------------------


def test_archive_boundary_at_cutoff_is_skipped(audit_dir: Path) -> None:
    """A log file exactly at retention_days old is SKIPPED (>= cutoff)."""
    # Place a file exactly 90 days old.
    name = _make_dated_log(audit_dir, days_ago=90)
    log = AuditLog(audit_dir, key=b"k")
    # cutoff = today - 90 days. file_date = today - 90 days. >= cutoff -> skip.
    result = log.archive(RetentionPolicy(retention_days=90))
    assert name in result.skipped
    assert name not in result.archived
    # The original file is preserved.
    assert (audit_dir / name).exists()


def test_archive_one_day_past_cutoff_is_archived(audit_dir: Path) -> None:
    """A log file one day past retention IS archived (file_date < cutoff)."""
    name = _make_dated_log(audit_dir, days_ago=91)
    log = AuditLog(audit_dir, key=b"k")
    result = log.archive(RetentionPolicy(retention_days=90))
    assert name in result.archived
    assert name not in result.skipped


# ---------------------------------------------------------------------------
# Extra: load_or_create_audit_key writes file with 0600 (kills mutations
# on the chmod path on lines 142/154 if they reappear).
# ---------------------------------------------------------------------------


def test_load_or_create_audit_key_writes_0600(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The freshly created key file has mode exactly 0o600 (kills bit-flip mutations)."""
    import os as _os

    if _os.name == "nt":
        pytest.skip("POSIX permission bits not enforced on Windows")
    monkeypatch.delenv(AUDIT_KEY_ENV, raising=False)
    key_path = tmp_path / "state" / "audit.key"

    from bernstein.core.security.audit import load_or_create_audit_key

    load_or_create_audit_key(key_path=key_path)
    mode = stat.S_IMODE(key_path.stat().st_mode)
    assert mode == 0o600, f"expected 0600 mode on fresh key, got {mode:04o}"
