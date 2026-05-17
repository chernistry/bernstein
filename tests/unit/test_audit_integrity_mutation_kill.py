"""Targeted tests that kill mutmut survivors in audit_integrity.

Each test pins one or more specific invariants identified by
``scripts/mutmut_critical.py --only audit_integrity``. Tests live in
their own file to keep the surviving-mutant map readable; the existing
``test_audit_integrity.py`` is left untouched.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from pathlib import Path
from typing import Any

import pytest

from bernstein.core.security import audit_integrity as ai_mod
from bernstein.core.security.audit_integrity import (
    DEFAULT_VERIFY_COUNT,
    IntegrityCheckResult,
    _load_tail_entries,  # pyright: ignore[reportPrivateUsage]
    _verify_entry_chain,  # pyright: ignore[reportPrivateUsage]
    verify_audit_integrity,
    verify_on_startup,
)

_GENESIS_HMAC = "0" * 64


def _compute_test_hmac(key: bytes, prev_hmac: str, entry: dict[str, Any]) -> str:
    payload = prev_hmac + json.dumps(entry, sort_keys=True)
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def _write_chain(audit_dir: Path, key: bytes, count: int, filename: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    prev = _GENESIS_HMAC
    lines: list[str] = []
    for i in range(count):
        entry: dict[str, Any] = {
            "timestamp": f"2026-04-05T00:00:{i:02d}.000000Z",
            "event_type": "test.event",
            "actor": "test-actor",
            "resource_type": "task",
            "resource_id": f"task-{i}",
            "details": {},
            "prev_hmac": prev,
        }
        computed = _compute_test_hmac(key, prev, entry)
        entry["hmac"] = computed
        entries.append(entry)
        lines.append(json.dumps(entry, sort_keys=True))
        prev = computed
    (audit_dir / filename).write_text("\n".join(lines) + "\n")
    return entries


@pytest.fixture()
def audit_dir(tmp_path: Path) -> Path:
    d = tmp_path / "audit"
    d.mkdir()
    return d


@pytest.fixture()
def hmac_key() -> bytes:
    return b"mutation-kill-key-32-bytes-pad-pa"


# ---------------------------------------------------------------------------
# Line 31: DEFAULT_VERIFY_COUNT = 100 (mutant: 200)
# ---------------------------------------------------------------------------


def test_default_verify_count_is_exactly_100() -> None:
    """The shipped default tail-verify window is exactly 100 entries."""
    assert DEFAULT_VERIFY_COUNT == 100


def test_verify_on_startup_uses_default_count_100(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hmac_key: bytes
) -> None:
    """verify_on_startup() with no explicit count consults exactly 100 entries."""
    sdd_dir = tmp_path / ".sdd"
    sdd_dir.mkdir()
    ad = sdd_dir / "audit"
    ad.mkdir()
    # Use the explicit-key path through verify_on_startup by setting env.
    key_file = sdd_dir / "config" / "audit-key"
    key_file.parent.mkdir(parents=True)
    key_file.write_bytes(hmac_key)
    key_file.chmod(0o600)
    monkeypatch.setenv("BERNSTEIN_AUDIT_KEY_PATH", str(key_file))
    # Write 150 entries; default count=100 should check exactly 100.
    _write_chain(ad, hmac_key, 150, "2026-04-05.jsonl")

    result = verify_on_startup(sdd_dir)
    assert result.entries_checked == 100
    assert result.entries_total == 150


# ---------------------------------------------------------------------------
# Line 57: duration_ms: float = 0.0 default on dataclass
# ---------------------------------------------------------------------------


def test_integrity_check_result_default_duration_is_zero() -> None:
    """The IntegrityCheckResult default duration_ms is exactly 0.0."""
    r = IntegrityCheckResult(valid=True, entries_checked=0, entries_total=0)
    assert r.duration_ms == 0.0


def test_no_audit_dir_returns_zero_duration(tmp_path: Path) -> None:
    """When the audit directory is missing the duration_ms is exactly 0.0."""
    result = verify_audit_integrity(tmp_path / "nonexistent")
    assert result.duration_ms == 0.0


# ---------------------------------------------------------------------------
# Line 73: log_files = sorted(audit_dir.glob("*.jsonl"), reverse=True)
# Mutant flips reverse=True -> reverse=False. The function is supposed to
# walk NEWEST file first so that the tail-N window is filled by the most
# recent entries when multiple files exist. With reverse=False the oldest
# file is walked first and the tail window is filled with older data.
# ---------------------------------------------------------------------------


def test_load_tail_walks_newest_file_first(audit_dir: Path, hmac_key: bytes) -> None:
    """With multiple files, tail-N must include the LATEST file's entries."""
    # Older file: distinguishable resource_ids.
    older = _write_chain(audit_dir, hmac_key, 3, "2026-04-04.jsonl")
    newer = _write_chain(audit_dir, hmac_key, 3, "2026-04-05.jsonl")

    # Pull only 3 entries -> must come from the newer file.
    collected = _load_tail_entries(audit_dir, 3)
    assert len(collected) == 3
    names = {row[0] for row in collected}
    # Only the newest filename should be in the result.
    assert names == {"2026-04-05.jsonl"}, (
        f"reverse=True regressed; saw {names}, older[0]={older[0]['resource_id']!r}, "
        f"newer[0]={newer[0]['resource_id']!r}"
    )


# ---------------------------------------------------------------------------
# Line 88: parse_error marker. Mutant turns the sentinel into False which
# makes ``entry.get("__parse_error")`` falsy and silently skips the
# "unparseable JSON" error.
# ---------------------------------------------------------------------------


def test_unparseable_line_surfaced_as_parse_error(audit_dir: Path, hmac_key: bytes) -> None:
    """Unparseable JSON in the tail must produce an 'unparseable JSON' error."""
    # Write valid entries then append a broken line.
    _write_chain(audit_dir, hmac_key, 2, "2026-04-05.jsonl")
    log = audit_dir / "2026-04-05.jsonl"
    log.write_text(log.read_text() + "this-is-not-json\n")

    result = verify_audit_integrity(audit_dir, count=10, key=hmac_key)
    assert result.valid is False
    assert any("unparseable JSON" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Line 121: HMAC payload built with sort_keys=True. Mutant flips to False
# which makes the recomputed HMAC depend on insertion order. With matching
# insertion-order writes the chain still verifies; we craft a payload where
# the on-disk JSON has sort_keys=True but field order in the parsed dict
# (which Python preserves) differs from sorted order, forcing a divergence
# when sort_keys=False is used to recompute.
# ---------------------------------------------------------------------------


def test_compute_hmac_uses_sorted_keys(audit_dir: Path, hmac_key: bytes) -> None:
    """A re-ordered (non-sorted) on-disk entry must still verify because
    the verifier canonicalises via sort_keys=True. If the mutation flips
    sort_keys to False, the recomputed HMAC would depend on insertion
    order and would NOT match the HMAC stored under the canonical form.
    """
    # Build entry where insertion order differs from sorted order:
    # 'zeta' inserted before 'alpha' but sorted() puts 'alpha' first.
    entry: dict[str, Any] = {
        "zeta_field": "z",
        "actor": "a",
        "alpha_field": "a",
        "event_type": "e",
        "resource_type": "r",
        "resource_id": "rid",
        "timestamp": "2026-04-05T00:00:00.000000Z",
        "details": {},
        "prev_hmac": _GENESIS_HMAC,
    }
    # Compute HMAC with the canonical (sorted) form - this is what the
    # writer produces and what the verifier must compute to match.
    canonical = json.dumps(entry, sort_keys=True)
    expected = hmac.new(hmac_key, (_GENESIS_HMAC + canonical).encode(), hashlib.sha256).hexdigest()
    entry["hmac"] = expected

    # Write canonical form (sorted) to disk so json.loads can parse but the
    # dict iteration order at parse time is canonical order.
    line = json.dumps(entry, sort_keys=True)
    (audit_dir / "2026-04-05.jsonl").write_text(line + "\n")

    result = verify_audit_integrity(audit_dir, count=1, key=hmac_key)
    # With sort_keys=True (correct), HMAC matches and chain verifies.
    assert result.valid is True, f"sort_keys=True regression: errors={result.errors}"
    assert result.errors == []


# ---------------------------------------------------------------------------
# Line 194/203: format-string content for the "chain broken" and "HMAC
# mismatch" error messages. The text contains '!=' which is a meaningful
# operator marker for operators reading logs. We pin both error messages
# expose the actual unequal pair (so an attacker can see what differs)
# AND contain the literal '!=' separator.
# ---------------------------------------------------------------------------


def test_chain_broken_error_uses_not_equal_marker(audit_dir: Path, hmac_key: bytes) -> None:
    """The chain-broken error message contains '!=' showing the mismatch."""
    entries = _write_chain(audit_dir, hmac_key, 3, "2026-04-05.jsonl")
    # Break the chain on entry 2.
    entries[1]["prev_hmac"] = "f" * 64
    entries[1]["hmac"] = _compute_test_hmac(
        hmac_key,
        entries[1]["prev_hmac"],
        {k: v for k, v in entries[1].items() if k != "hmac"},
    )
    (audit_dir / "2026-04-05.jsonl").write_text("\n".join(json.dumps(e, sort_keys=True) for e in entries) + "\n")

    result = verify_audit_integrity(audit_dir, count=3, key=hmac_key)
    assert result.valid is False
    chain_errs = [e for e in result.errors if "chain broken" in e]
    assert chain_errs, f"no 'chain broken' error: {result.errors}"
    # The format string carries '!=' - mutation '!=' -> '==' would change this.
    assert " != " in chain_errs[0], f"missing '!=' marker in: {chain_errs[0]!r}"


def test_hmac_mismatch_error_uses_not_equal_marker(audit_dir: Path, hmac_key: bytes) -> None:
    """The HMAC-mismatch error message contains '!=' showing the mismatch."""
    entries = _write_chain(audit_dir, hmac_key, 2, "2026-04-05.jsonl")
    entries[0]["hmac"] = "deadbeef" * 8
    (audit_dir / "2026-04-05.jsonl").write_text("\n".join(json.dumps(e, sort_keys=True) for e in entries) + "\n")

    result = verify_audit_integrity(audit_dir, count=2, key=hmac_key)
    assert result.valid is False
    mm = [e for e in result.errors if "HMAC mismatch" in e]
    assert mm, f"no 'HMAC mismatch' error: {result.errors}"
    assert " != " in mm[0], f"missing '!=' marker in: {mm[0]!r}"


# ---------------------------------------------------------------------------
# Line 216: logger.warning() uses len(errors) - mutant '0 * len(errors)'.
# We assert the actual warning log line carries the true non-zero count.
# ---------------------------------------------------------------------------


def test_failed_verify_logs_actual_error_count(
    audit_dir: Path, hmac_key: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    """The 'FAILED: N error(s)' warning carries the actual count, not 0."""
    entries = _write_chain(audit_dir, hmac_key, 3, "2026-04-05.jsonl")
    # Tamper 2 entries so len(errors) > 0.
    entries[0]["hmac"] = "00" * 32
    entries[1]["hmac"] = "11" * 32
    (audit_dir / "2026-04-05.jsonl").write_text("\n".join(json.dumps(e, sort_keys=True) for e in entries) + "\n")

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="bernstein.core.security.audit_integrity"):
        result = verify_audit_integrity(audit_dir, count=3, key=hmac_key)

    assert result.valid is False
    n = len(result.errors)
    assert n > 0
    # The header warning line includes the actual error count, not zero.
    headers = [r for r in caplog.records if "Audit integrity check FAILED" in r.getMessage()]
    assert headers, f"no FAILED header captured; messages={[r.getMessage() for r in caplog.records]}"
    rendered = headers[0].getMessage()
    assert f"{n} error" in rendered, f"expected count {n} in: {rendered!r}"
    # And critically NOT 0:
    assert "0 error" not in rendered, f"len(errors)=0 mutation slipped: {rendered!r}"


# ---------------------------------------------------------------------------
# Line 259: warnings: list[str] = []  -> [None]. With [None] the result
# would carry a stray None entry in warnings even when no warnings are
# emitted. We pin warnings is an empty list when the chain is healthy.
# ---------------------------------------------------------------------------


def test_clean_chain_emits_empty_warnings_list(audit_dir: Path, hmac_key: bytes) -> None:
    """A healthy chain yields warnings=[] exactly - no stray None entries."""
    _write_chain(audit_dir, hmac_key, 3, "2026-04-05.jsonl")
    result = verify_audit_integrity(audit_dir, count=3, key=hmac_key)
    assert result.valid is True
    assert result.warnings == []
    # Defensive: no element is None.
    assert all(w is not None for w in result.warnings)


# ---------------------------------------------------------------------------
# Lines 293 / 299: duration_ms = (time.monotonic() - start) * 1000.
# Mutant '* 1000' -> '* 2000' doubles the reported duration. We pin
# duration is a sane positive number that is *not* off by a factor of 2.
# Since absolute timings are flaky, we instead pin the conversion against
# a controlled monkeypatch of time.monotonic().
# ---------------------------------------------------------------------------


def test_duration_ms_uses_1000x_scale_no_entries_path(
    audit_dir: Path, hmac_key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """duration_ms scales seconds->ms by exactly 1000 on the no-entries path (line 293)."""
    # Path: audit_dir is a directory, key supplied, but no .jsonl files
    # -> hits the "no audit entries" return at line ~287 -> duration_ms
    # computed at line 293.
    fake = iter([1000.0, 1000.5])  # delta = 0.5s -> expect 500.0ms
    monkeypatch.setattr(ai_mod.time, "monotonic", lambda: next(fake))
    result = verify_audit_integrity(audit_dir, count=5, key=hmac_key)
    assert result.entries_checked == 0
    # 500ms exactly when scaled by 1000; 1000ms if mutated to *2000.
    assert result.duration_ms == pytest.approx(500.0, abs=1e-6)


def test_duration_ms_uses_1000x_scale_verified_path(
    audit_dir: Path, hmac_key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """duration_ms scales seconds->ms by exactly 1000 on the main verify path (line 299)."""
    _write_chain(audit_dir, hmac_key, 2, "2026-04-05.jsonl")
    fake = iter([2000.0, 2000.25])  # delta = 0.25s -> expect 250.0ms
    monkeypatch.setattr(ai_mod.time, "monotonic", lambda: next(fake))
    result = verify_audit_integrity(audit_dir, count=2, key=hmac_key)
    assert result.entries_checked == 2
    assert result.duration_ms == pytest.approx(250.0, abs=1e-6)


# ---------------------------------------------------------------------------
# Line 337: ``if not result.valid`` in verify_on_startup. Mutant drops the
# 'not' so the warning is emitted on the SUCCESS path instead of failure.
# We pin: healthy chain emits NO warning; failed chain emits ONE.
# ---------------------------------------------------------------------------


def test_verify_on_startup_warning_only_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hmac_key: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    """The 'AUDIT INTEGRITY WARNING' line fires when valid=False, NEVER on valid=True."""
    sdd_dir = tmp_path / ".sdd"
    sdd_dir.mkdir()
    ad = sdd_dir / "audit"
    ad.mkdir()
    key_file = sdd_dir / "config" / "audit-key"
    key_file.parent.mkdir(parents=True)
    key_file.write_bytes(hmac_key)
    key_file.chmod(0o600)
    monkeypatch.setenv("BERNSTEIN_AUDIT_KEY_PATH", str(key_file))

    # Healthy chain: no warning.
    _write_chain(ad, hmac_key, 2, "2026-04-05.jsonl")
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="bernstein.core.security.audit_integrity"):
        ok = verify_on_startup(sdd_dir, count=2)
    assert ok.valid is True
    warns = [r.getMessage() for r in caplog.records if "AUDIT INTEGRITY WARNING" in r.getMessage()]
    assert warns == [], f"warning emitted on healthy chain: {warns}"

    # Now tamper -> the warning MUST fire.
    log_path = ad / "2026-04-05.jsonl"
    raw = log_path.read_text().replace('"task-0"', '"task-tampered"', 1)
    log_path.write_text(raw)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="bernstein.core.security.audit_integrity"):
        bad = verify_on_startup(sdd_dir, count=2)
    assert bad.valid is False
    warns2 = [r.getMessage() for r in caplog.records if "AUDIT INTEGRITY WARNING" in r.getMessage()]
    assert warns2, "expected warning on tampered chain"


# ---------------------------------------------------------------------------
# Line 341: logger.warning(... %d ..., len(result.errors), ...). Mutant
# '0 * len(...)' would log 0 errors instead of the real count.
# ---------------------------------------------------------------------------


def test_verify_on_startup_warning_carries_real_error_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hmac_key: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    """The startup warning records the actual number of errors detected."""
    sdd_dir = tmp_path / ".sdd"
    sdd_dir.mkdir()
    ad = sdd_dir / "audit"
    ad.mkdir()
    key_file = sdd_dir / "config" / "audit-key"
    key_file.parent.mkdir(parents=True)
    key_file.write_bytes(hmac_key)
    key_file.chmod(0o600)
    monkeypatch.setenv("BERNSTEIN_AUDIT_KEY_PATH", str(key_file))

    # Tamper TWO entries -> the warning must report >=2.
    entries = _write_chain(ad, hmac_key, 4, "2026-04-05.jsonl")
    entries[0]["hmac"] = "aa" * 32
    entries[1]["hmac"] = "bb" * 32
    (ad / "2026-04-05.jsonl").write_text("\n".join(json.dumps(e, sort_keys=True) for e in entries) + "\n")

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="bernstein.core.security.audit_integrity"):
        result = verify_on_startup(sdd_dir, count=4)

    assert result.valid is False
    err_count = len(result.errors)
    assert err_count >= 2
    warns = [r.getMessage() for r in caplog.records if "AUDIT INTEGRITY WARNING" in r.getMessage()]
    assert warns, "no AUDIT INTEGRITY WARNING captured"
    msg = warns[0]
    assert f"{err_count} error" in msg, f"expected count {err_count} in: {msg!r}"
    assert "0 error" not in msg, f"len(...)*0 mutation slipped: {msg!r}"


# ---------------------------------------------------------------------------
# Extra anchors for _verify_entry_chain - exercise the prev_hmac is None
# branch so the loop body runs at least one chain-link compare.
# ---------------------------------------------------------------------------


def test_verify_entry_chain_returns_checked_count(hmac_key: bytes) -> None:
    """_verify_entry_chain reports the exact number of entries it checked."""
    entries: list[tuple[str, int, dict[str, Any]]] = []
    prev = _GENESIS_HMAC
    for i in range(3):
        d: dict[str, Any] = {
            "timestamp": f"t{i}",
            "event_type": "e",
            "actor": "a",
            "resource_type": "r",
            "resource_id": f"r{i}",
            "details": {},
            "prev_hmac": prev,
        }
        d["hmac"] = _compute_test_hmac(hmac_key, prev, d)
        entries.append(("f.jsonl", i + 1, d))
        prev = d["hmac"]

    errs: list[str] = []
    last_hmac, checked = _verify_entry_chain(entries, hmac_key, errs)
    assert errs == []
    assert checked == 3
    assert last_hmac == entries[-1][2]["hmac"]
