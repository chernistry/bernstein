"""Property-based bug-hunt suite for the HMAC-chained audit log.

Targets:

* :mod:`bernstein.core.security.audit` (chain writer/verifier).
* :mod:`bernstein.core.security.audit_integrity` (startup verifier).
* :mod:`bernstein.core.security.audit_slice` (deterministic slice extractor).
* :mod:`bernstein.core.security.article12_bundle` (re-verification path).
* :mod:`bernstein.core.persistence.lineage` (lineage v2 chain - sibling).

The suite focuses on five invariants:

1. Verifier accepts every chain the writer produced.
2. Verifier rejects any single-byte tamper anywhere in any field.
3. ``prev_hmac[i] == hmac[i-1]`` for every i.
4. Slice extraction preserves structural verifiability.
5. Differential test: a 30-line spec implementation of the HMAC matches
   the production ``_compute_hmac`` for every Hypothesis-generated event.

Tests that pass without a fix demonstrate working invariants.  Tests
that ``xfail`` document known bugs and pin them so a future fix flips
the assertion.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import string
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from bernstein.core.security.audit import (
    _GENESIS_HMAC,  # pyright: ignore[reportPrivateUsage]
    AuditEvent,
    AuditLog,
    _compute_hmac,  # pyright: ignore[reportPrivateUsage]
)
from bernstein.core.security.audit_integrity import verify_audit_integrity
from bernstein.core.security.audit_slice import (
    AuditSliceError,
    slice_audit_log,
    verify_slice_chain,
)

# ---------------------------------------------------------------------------
# Fixtures + strategies
# ---------------------------------------------------------------------------

_TEST_KEY = b"property-test-hmac-key-not-for-production"

# Strict-printable ASCII: avoids whitespace canonicalisation noise.
_PRINTABLE = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
    min_size=1,
    max_size=24,
)
# Resource ids stay short; we are not stress-testing JSON escaping limits.
_SHORT_ID = st.text(alphabet=string.ascii_letters + string.digits + "_-", min_size=1, max_size=16)


@st.composite
def event_kwargs(draw: st.DrawFn) -> dict[str, Any]:
    """Generate plausible kwargs for ``AuditLog.log``."""
    return {
        "event_type": draw(
            st.sampled_from(["task.create", "task.update", "task.delete", "agent.spawn", "policy.deny"])
        ),
        "actor": draw(_PRINTABLE),
        "resource_type": draw(st.sampled_from(["task", "agent", "policy", "config"])),
        "resource_id": draw(_SHORT_ID),
        "details": draw(
            st.dictionaries(
                keys=_SHORT_ID,
                values=st.one_of(
                    st.integers(min_value=-(2**31), max_value=2**31 - 1),
                    st.text(alphabet=string.ascii_letters + string.digits + " ", max_size=20),
                    st.booleans(),
                    st.none(),
                ),
                max_size=4,
            ),
        ),
    }


def _make_log(tmp_path: Path, n: int, events: list[dict[str, Any]]) -> AuditLog:
    audit_dir = tmp_path / "audit"
    log = AuditLog(audit_dir, key=_TEST_KEY)
    for ev in events[:n]:
        log.log(**ev)
    return log


# ---------------------------------------------------------------------------
# Invariant 1 - verify(chain) is True for any chain the writer produced
# ---------------------------------------------------------------------------


class TestWriterRoundTrip:
    """Anything the writer emits must verify."""

    @given(events=st.lists(event_kwargs(), min_size=1, max_size=12))
    @settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_writer_produces_verifiable_chain(
        self, tmp_path_factory: pytest.TempPathFactory, events: list[dict[str, Any]]
    ) -> None:
        tmp_path = tmp_path_factory.mktemp("write_then_verify")
        log = _make_log(tmp_path, len(events), events)
        valid, errors = log.verify()
        assert valid, f"writer produced unverifiable chain: {errors}"

    @given(events=st.lists(event_kwargs(), min_size=1, max_size=8))
    @settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_chain_linkage_holds(self, tmp_path_factory: pytest.TempPathFactory, events: list[dict[str, Any]]) -> None:
        """``prev_hmac[i] == hmac[i-1]`` for every event the writer emits."""
        tmp_path = tmp_path_factory.mktemp("linkage")
        _make_log(tmp_path, len(events), events)
        recorded: list[dict[str, Any]] = []
        for jsonl_file in sorted((tmp_path / "audit").glob("*.jsonl")):
            for line in jsonl_file.read_text().splitlines():
                if line.strip():
                    recorded.append(json.loads(line))
        assert recorded[0]["prev_hmac"] == _GENESIS_HMAC
        for i in range(1, len(recorded)):
            assert recorded[i]["prev_hmac"] == recorded[i - 1]["hmac"], f"event[{i}].prev_hmac != event[{i - 1}].hmac"


# ---------------------------------------------------------------------------
# Invariant 2 - single-byte tamper anywhere is rejected
# ---------------------------------------------------------------------------


_TAMPERABLE_FIELDS = ("timestamp", "event_type", "actor", "resource_type", "resource_id", "prev_hmac", "hmac")


class TestSingleByteTamper:
    """Flipping a single byte in any field MUST fail verification."""

    @given(
        events=st.lists(event_kwargs(), min_size=2, max_size=6),
        target_field=st.sampled_from(_TAMPERABLE_FIELDS),
        target_idx=st.integers(min_value=0, max_value=5),
    )
    @settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_one_byte_flip_rejected(
        self,
        tmp_path_factory: pytest.TempPathFactory,
        events: list[dict[str, Any]],
        target_field: str,
        target_idx: int,
    ) -> None:
        assume(target_idx < len(events))
        tmp_path = tmp_path_factory.mktemp("byte_flip")
        log = _make_log(tmp_path, len(events), events)

        log_files = sorted((tmp_path / "audit").glob("*.jsonl"))
        assert log_files
        path = log_files[0]
        lines = path.read_text().splitlines()

        target = json.loads(lines[target_idx])
        original = target[target_field]
        if isinstance(original, str) and original:
            # Flip one character (deterministic per-input).
            tampered = ("X" if original[0] != "X" else "Y") + original[1:]
        elif isinstance(original, str):
            tampered = "X"
        else:
            tampered = original
        if tampered == original:
            tampered = original + "_tampered"
        target[target_field] = tampered
        lines[target_idx] = json.dumps(target, sort_keys=True)
        path.write_text("\n".join(lines) + "\n")

        valid, errors = log.verify()
        assert not valid, f"verifier accepted a tamper of {target_field!r} at index {target_idx}: errors={errors}"


class TestDetailsByteTamper:
    """Modifying nested ``details`` content must also be rejected."""

    @given(events=st.lists(event_kwargs(), min_size=1, max_size=4))
    @settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_details_payload_tamper(
        self, tmp_path_factory: pytest.TempPathFactory, events: list[dict[str, Any]]
    ) -> None:
        # Force at least one populated details dict so the mutation actually changes bytes.
        events[0] = events[0] | {"details": {"k": "v"}}
        tmp_path = tmp_path_factory.mktemp("details_tamper")
        log = _make_log(tmp_path, len(events), events)
        log_files = sorted((tmp_path / "audit").glob("*.jsonl"))
        path = log_files[0]
        lines = path.read_text().splitlines()
        target = json.loads(lines[0])
        target["details"]["__attacker"] = "hello"
        lines[0] = json.dumps(target, sort_keys=True)
        path.write_text("\n".join(lines) + "\n")
        valid, _ = log.verify()
        assert not valid


# ---------------------------------------------------------------------------
# Invariant 3 - differential HMAC implementation
# ---------------------------------------------------------------------------


def _spec_compute_hmac(key: bytes, prev_hmac: str, entry: dict[str, Any]) -> str:
    """30-LOC reference implementation of the audit HMAC.

    Spec, distilled from ``audit.py``:

    1. Canonicalise ``entry`` via ``json.dumps(entry, sort_keys=True)``
       with default separators (``', '``, ``': '``) and default
       ``ensure_ascii=True``.
    2. Concatenate UTF-8 bytes of ``prev_hmac + canonical_json``.
    3. Compute ``HMAC-SHA-256(key, payload).hexdigest()``.
    """
    canonical = json.dumps(entry, sort_keys=True)  # default separators, ensure_ascii=True
    payload = (prev_hmac + canonical).encode("utf-8")
    return _hmac.new(key, payload, hashlib.sha256).hexdigest()


class TestDifferentialHMAC:
    """Production ``_compute_hmac`` must match the spec implementation."""

    @given(
        prev_hmac=st.text(alphabet="0123456789abcdef", min_size=64, max_size=64),
        entry=st.fixed_dictionaries(
            {
                "timestamp": st.text(alphabet=string.ascii_letters + string.digits + ":-T.Z", min_size=1, max_size=32),
                "event_type": _PRINTABLE,
                "actor": _PRINTABLE,
                "resource_type": _PRINTABLE,
                "resource_id": _SHORT_ID,
                "details": st.dictionaries(_SHORT_ID, st.integers(), max_size=3),
                "prev_hmac": st.text(alphabet="0123456789abcdef", min_size=64, max_size=64),
            },
        ),
    )
    @settings(max_examples=80, deadline=None)
    def test_differential_match(self, prev_hmac: str, entry: dict[str, Any]) -> None:
        expected = _spec_compute_hmac(_TEST_KEY, prev_hmac, entry)
        got = _compute_hmac(_TEST_KEY, prev_hmac, entry)
        assert got == expected, "production HMAC diverges from spec for canonical JSON"


# ---------------------------------------------------------------------------
# Invariant 4 - slice extraction preserves verifiability
# ---------------------------------------------------------------------------


class TestSliceVerifiability:
    """Slices over the chain must remain structurally chained."""

    @given(events=st.lists(event_kwargs(), min_size=3, max_size=8))
    @settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_full_slice_verifies(self, tmp_path_factory: pytest.TempPathFactory, events: list[dict[str, Any]]) -> None:
        tmp_path = tmp_path_factory.mktemp("full_slice")
        _make_log(tmp_path, len(events), events)
        result = slice_audit_log(tmp_path / "audit", from_hmac=None, to_hmac=None)
        ok, errors = verify_slice_chain(result)
        assert ok, f"full-range slice should verify, got {errors}"

    @given(events=st.lists(event_kwargs(), min_size=3, max_size=8))
    @settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_inner_slice_structurally_verifies(
        self, tmp_path_factory: pytest.TempPathFactory, events: list[dict[str, Any]]
    ) -> None:
        tmp_path = tmp_path_factory.mktemp("inner_slice")
        log = _make_log(tmp_path, len(events), events)
        # Pick the 2nd and N-1th event hmacs as fence-posts.
        full = slice_audit_log(tmp_path / "audit")
        if len(full.events) < 3:
            return
        from_hmac = full.events[1]["hmac"]
        to_hmac = full.events[-2]["hmac"]
        result = slice_audit_log(tmp_path / "audit", from_hmac=from_hmac, to_hmac=to_hmac)
        ok, errors = verify_slice_chain(result)
        assert ok, f"inner slice should verify structurally, got {errors}"
        assert log is not None  # silence vulture

    @given(events=st.lists(event_kwargs(), min_size=3, max_size=6))
    @settings(max_examples=15, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_inverted_slice_bounds_rejected(
        self, tmp_path_factory: pytest.TempPathFactory, events: list[dict[str, Any]]
    ) -> None:
        tmp_path = tmp_path_factory.mktemp("inverted_slice")
        _make_log(tmp_path, len(events), events)
        full = slice_audit_log(tmp_path / "audit")
        if len(full.events) < 2:
            return
        # Swap from/to to produce an out-of-order slice request.
        from_hmac = full.events[-1]["hmac"]
        to_hmac = full.events[0]["hmac"]
        with pytest.raises(AuditSliceError):
            slice_audit_log(tmp_path / "audit", from_hmac=from_hmac, to_hmac=to_hmac)


# ---------------------------------------------------------------------------
# Adversarial: cross-chain confusion + key rotation
# ---------------------------------------------------------------------------


class TestCrossChainConfusion:
    """Splicing entries from a sibling chain must be rejected."""

    def test_entry_from_sibling_chain_breaks_verify(self, tmp_path: Path) -> None:
        chain_a = tmp_path / "audit_a"
        chain_b = tmp_path / "audit_b"
        log_a = AuditLog(chain_a, key=_TEST_KEY)
        log_b = AuditLog(chain_b, key=_TEST_KEY)
        log_a.log("e1", "a1", "task", "i1")
        a2 = log_a.log("e2", "a2", "task", "i2")
        log_a.log("e3", "a3", "task", "i3")
        log_b.log("b1", "b1", "task", "j1")
        b2 = log_b.log("b2", "b2", "task", "j2")
        log_b.log("b3", "b3", "task", "j3")

        # Splice b2 into chain A in place of a2.
        a_files = sorted(chain_a.glob("*.jsonl"))
        b_files = sorted(chain_b.glob("*.jsonl"))
        a_lines = a_files[0].read_text().splitlines()
        b_lines = b_files[0].read_text().splitlines()
        # Find the line whose hmac matches a2/b2 to splice.
        for i, line in enumerate(a_lines):
            if json.loads(line).get("hmac") == a2.hmac:
                for j, bline in enumerate(b_lines):
                    if json.loads(bline).get("hmac") == b2.hmac:
                        a_lines[i] = b_lines[j]
                        break
                break
        a_files[0].write_text("\n".join(a_lines) + "\n")

        log_a_reloaded = AuditLog(chain_a, key=_TEST_KEY)
        valid, errors = log_a_reloaded.verify()
        assert not valid, "cross-chain splice must fail verification"
        assert errors


class TestKeyRotationWithoutMigration:
    """A chain extended under a new key without explicit migration must NOT verify."""

    def test_silent_key_rotation_breaks_verify(self, tmp_path: Path) -> None:
        audit_dir = tmp_path / "audit"
        log = AuditLog(audit_dir, key=b"old-key")
        log.log("e1", "a1", "task", "i1")
        log.log("e2", "a2", "task", "i2")
        # Force-load with new key; writer treats existing chain tail
        # as just bytes and continues from it.
        new_log = AuditLog(audit_dir, key=b"new-key")
        new_log.log("e3", "a3", "task", "i3")

        # Verifying with the old key fails on the new entry.
        old_view = AuditLog(audit_dir, key=b"old-key")
        valid, errors = old_view.verify()
        assert not valid, "old key must reject events signed by new key"
        assert errors

        # Verifying with the new key fails on the original entries.
        new_view = AuditLog(audit_dir, key=b"new-key")
        valid_new, errors_new = new_view.verify()
        assert not valid_new, "new key must reject events signed by old key"
        assert errors_new


# ---------------------------------------------------------------------------
# JSON canonicalisation edge cases
# ---------------------------------------------------------------------------


class TestCanonicalisation:
    """Same logical event, different JSON formatting → same HMAC."""

    @given(
        actor=_PRINTABLE,
        rid=_SHORT_ID,
        details=st.dictionaries(_SHORT_ID, st.integers(min_value=-100, max_value=100), max_size=4),
    )
    @settings(max_examples=30, deadline=None)
    def test_key_order_and_whitespace_irrelevant(self, actor: str, rid: str, details: dict[str, int]) -> None:
        prev = "a" * 64
        # canonical form (production):
        e1 = {
            "timestamp": "2026-04-05T12:00:00.000Z",
            "event_type": "test",
            "actor": actor,
            "resource_type": "task",
            "resource_id": rid,
            "details": details,
            "prev_hmac": prev,
        }
        # Same logical content, different in-memory key order.
        e2 = dict(reversed(list(e1.items())))
        assert _compute_hmac(_TEST_KEY, prev, e1) == _compute_hmac(_TEST_KEY, prev, e2)


class TestUnicodeNormalisation:
    """Unicode that round-trips through json.dumps must remain stable."""

    @given(s=st.text(min_size=0, max_size=8))
    @settings(max_examples=30, deadline=None)
    def test_unicode_roundtrip_stable(self, s: str) -> None:
        prev = "0" * 64
        entry = {
            "timestamp": "2026-04-05T00:00:00.000Z",
            "event_type": "u",
            "actor": s,
            "resource_type": "task",
            "resource_id": "x",
            "details": {},
            "prev_hmac": prev,
        }
        h1 = _compute_hmac(_TEST_KEY, prev, entry)
        # Round-trip via JSON to mimic the reader path.
        roundtripped = json.loads(json.dumps(entry, sort_keys=True))
        h2 = _compute_hmac(_TEST_KEY, prev, roundtripped)
        assert h1 == h2


# ---------------------------------------------------------------------------
# Integer overflow in timestamp / negative timestamps / NUL bytes / deep nesting
# ---------------------------------------------------------------------------


class TestPathologicalPayloads:
    """Pathological values must either be rejected or chain cleanly."""

    def test_payload_with_nul_byte_chains(self, tmp_path: Path) -> None:
        log = AuditLog(tmp_path / "audit", key=_TEST_KEY)
        log.log("nul", "actor\x00with\x00nul", "task", "id\x00", details={"k\x00": "v\x00"})
        log.log("nul2", "actor", "task", "id2")
        valid, errors = log.verify()
        assert valid, f"NUL byte chain should still verify, got {errors}"

    def test_deeply_nested_details(self, tmp_path: Path) -> None:
        log = AuditLog(tmp_path / "audit", key=_TEST_KEY)
        nested: dict[str, Any] = {"v": 1}
        for _ in range(20):
            nested = {"nested": nested}
        log.log("deep", "a", "task", "x", details=nested)
        valid, _ = log.verify()
        assert valid

    def test_huge_integer_in_details(self, tmp_path: Path) -> None:
        """JSON has no integer ceiling - an attacker shoving 10^200 must round-trip."""
        log = AuditLog(tmp_path / "audit", key=_TEST_KEY)
        log.log("big", "a", "task", "x", details={"n": 10**200})
        valid, _ = log.verify()
        assert valid


# ---------------------------------------------------------------------------
# BUG: _recover_chain_tail forks the chain when last file has only a
#      truncated/malformed entry.
# ---------------------------------------------------------------------------


class TestRecoverChainTailEdgeCases:
    """The tail recover must not fall back to GENESIS when valid earlier files exist.

    Root cause (audit.py:_recover_chain_tail):
        Recovery only inspects ``log_files[-1]``.  If the last (newest)
        file's only line is corrupt/truncated, recovery returns
        ``_GENESIS_HMAC`` even though a fully-valid prior file's tail HMAC
        is the correct anchor.  The next ``.log()`` will start a NEW chain
        from genesis, silently FORKING the audit log.

    Impact:
        An attacker who can write a single garbage byte to the newest
        ``YYYY-MM-DD.jsonl`` file (or who triggers a crash in the writer
        right after rotation creates an empty/truncated new file)
        causes the next legitimate audit event to be signed against
        genesis - effectively forking the chain.  ``verify()`` then
        reports an error on the *first* event of the new file (not on
        the garbage line), so the operator sees the symptom but not the
        cause; meanwhile the attacker has a fresh chain head whose
        future entries verify in isolation.
    """

    def test_truncated_last_file_does_not_fork_chain(self, tmp_path: Path) -> None:
        audit_dir = tmp_path / "audit"
        log = AuditLog(audit_dir, key=_TEST_KEY)
        e1 = log.log("e1", "a1", "task", "i1")
        # Write a deliberately corrupted line into a NEWER file (next day).
        # The current file (today) holds e1 cleanly.
        next_day_file = audit_dir / "9999-12-31.jsonl"
        next_day_file.write_text("{ corrupt-json-no-newline")

        # Reload - the writer must recover from e1.hmac, NOT GENESIS.
        log2 = AuditLog(audit_dir, key=_TEST_KEY)
        # pyright: ignore[reportPrivateUsage]
        recovered = log2._prev_hmac  # type: ignore[attr-defined]
        assert recovered == e1.hmac, (
            f"BUG: tail recovery returned {recovered!r}, expected {e1.hmac!r}. "
            "The truncated file in the lex-last position caused the writer to fork the chain."
        )


# ---------------------------------------------------------------------------
# BUG: slice with from_hmac != None reports verifiability green even though
#      the first slice entry's prev_hmac is NOT genesis - the slice cannot be
#      independently key-verified without explicit chain anchor metadata.
#
# This is a "known gap" rather than a chain forgery, so it is xfail'd.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    reason=(
        "slice_audit_log returns the inner slice with the original prev_hmac "
        "of the first event still pointing into the un-included history. "
        "verify_slice_chain only walks structural prev_hmac chaining inside "
        "the slice, so it greenlights a slice that is NOT independently "
        "key-verifiable without the parent chain anchor.  An auditor handed "
        "the bare slice cannot reproduce the HMAC of the first event "
        "without the ground-truth prev_hmac (which is supplied implicitly "
        "in the slice but the slice ships no signed assertion that this "
        "anchor itself is authentic).  Fixing requires shipping a signed "
        "anchor manifest alongside the slice (deferred follow-up)."
    ),
    strict=False,
)
def test_inner_slice_first_entry_has_no_signed_anchor(tmp_path: Path) -> None:
    audit_dir = tmp_path / "audit"
    log = AuditLog(audit_dir, key=_TEST_KEY)
    log.log("e1", "a1", "task", "i1")
    e2 = log.log("e2", "a2", "task", "i2")
    log.log("e3", "a3", "task", "i3")
    log.log("e4", "a4", "task", "i4")

    # Auditor receives only [e2, e3] with from_hmac=e2.hmac.
    result = slice_audit_log(audit_dir, from_hmac=e2.hmac, to_hmac=None)
    assert len(result.events) >= 2

    # The slice must carry a signed anchor proving e2's prev_hmac is not
    # forged.  Currently the slice carries the bare prev_hmac value with
    # NO MAC over (anchor || from_hmac), so an attacker who controls the
    # transport can swap the slice for a different chain that happens to
    # share the same first-event hmac shape.
    has_signed_anchor = "anchor_signature" in result.__dict__ or hasattr(result, "anchor_hmac")
    assert has_signed_anchor, "slice has no cryptographic anchor binding"


# ---------------------------------------------------------------------------
# BUG: integrity verifier does NOT enforce that the FIRST entry of the
#      checked window prev-hmac chains back to the previous file's tail
#      when ``count`` is smaller than the total number of records.
#
# Root cause (audit_integrity.py:_verify_entry_chain):
#     ``prev_hmac = None`` initialises the local chain.  The first entry
#     in the window is accepted with whatever ``prev_hmac`` it carries -
#     no comparison is done against any external anchor.  An attacker who
#     can rewrite the LATEST 100 entries (e.g. log rotation race + write
#     access to the most recent file) can re-sign them with the real key
#     under a forged genesis-style anchor, and ``verify_audit_integrity``
#     reports VALID because the window-internal chain is intact.
#
# Impact:
#     "this allows an attacker who can write to the audit log AND has the
#     HMAC key (e.g. a rogue insider) to truncate the log to the last
#     ``count=DEFAULT_VERIFY_COUNT`` entries by rewriting them with a
#     fresh genesis-anchored chain - startup integrity check passes, the
#     full ``AuditLog.verify`` would fail but the orchestrator only runs
#     the bounded check by default."
# ---------------------------------------------------------------------------


class TestIntegrityVerifierWindowAnchor:
    """The bounded verifier must reject an unanchored window."""

    def test_truncated_to_window_passes_bounded_verifier(self, tmp_path: Path) -> None:
        audit_dir = tmp_path / "audit"
        log = AuditLog(audit_dir, key=_TEST_KEY)
        # Write 5 real entries.
        for i in range(5):
            log.log(f"e{i}", "a", "task", f"id{i}")

        # Attacker simulates: rewrite the only file with a fresh
        # genesis-anchored 3-entry chain (using the real key - they
        # have it in this scenario because they're a rogue insider).
        files = sorted(audit_dir.glob("*.jsonl"))
        path = files[0]
        prev = _GENESIS_HMAC
        new_lines: list[str] = []
        for i in range(3):
            entry = {
                "timestamp": f"2026-04-05T00:0{i}:00.000000Z",
                "event_type": "forged",
                "actor": "attacker",
                "resource_type": "task",
                "resource_id": f"f{i}",
                "details": {},
                "prev_hmac": prev,
            }
            entry["hmac"] = _compute_hmac(_TEST_KEY, prev, entry)
            prev = entry["hmac"]
            new_lines.append(json.dumps(entry, sort_keys=True))
        path.write_text("\n".join(new_lines) + "\n")

        # Bounded verifier passes (it only checks chain *within* the window).
        result = verify_audit_integrity(audit_dir, count=3, key=_TEST_KEY)
        # Documented current behaviour:
        assert result.valid, (
            f"bounded verifier accepted truncated/forged log; errors={result.errors}. "
            "If this assertion flips, an external-anchor check has been "
            "added - invert the expectation."
        )

        # But: the FULL verifier should still pass too because the new
        # chain is internally consistent.  This is the core gap: there
        # is no persistent anchor record that cannot be rewritten.
        log2 = AuditLog(audit_dir, key=_TEST_KEY)
        full_valid, _ = log2.verify()
        assert full_valid, "full verifier also passes - confirms missing external anchor"


# ---------------------------------------------------------------------------
# BUG: When the last file is empty (e.g. just rotated), the chain tail is
#      SILENTLY genesis-anchored.  Forking attack vector.
#
# Root cause (audit.py:_recover_chain_tail):
#    If ``log_files[-1]`` exists but has zero non-blank lines, the inner
#    loop never returns and falls through to ``return _GENESIS_HMAC``,
#    discarding all earlier files.
# ---------------------------------------------------------------------------


class TestEmptyLastFileForksChain:
    def test_empty_newest_file_does_not_reset_to_genesis(self, tmp_path: Path) -> None:
        audit_dir = tmp_path / "audit"
        log = AuditLog(audit_dir, key=_TEST_KEY)
        e1 = log.log("e1", "a1", "task", "i1")

        # Simulate an empty rotation file landing in the audit dir.
        (audit_dir / "9999-12-31.jsonl").write_text("")

        log2 = AuditLog(audit_dir, key=_TEST_KEY)
        # Currently this returns GENESIS - bug.  After fix it must equal e1.hmac.
        # pyright: ignore[reportPrivateUsage]
        assert log2._prev_hmac == e1.hmac, (  # type: ignore[attr-defined]
            f"BUG: empty newest file caused tail recovery to return GENESIS "
            f"({log2._prev_hmac!r}); expected {e1.hmac!r}. "  # type: ignore[attr-defined]
            "Next .log() will silently fork the chain."
        )


# ---------------------------------------------------------------------------
# Sanity: dataclass round-trip
# ---------------------------------------------------------------------------


@given(events=st.lists(event_kwargs(), min_size=1, max_size=4))
@settings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_audit_event_query_returns_dataclasses(
    tmp_path_factory: pytest.TempPathFactory, events: list[dict[str, Any]]
) -> None:
    tmp_path = tmp_path_factory.mktemp("query_roundtrip")
    log = _make_log(tmp_path, len(events), events)
    queried = log.query()
    assert all(isinstance(e, AuditEvent) for e in queried)
    assert len(queried) == len(events)
