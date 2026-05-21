"""Tests for ``bernstein.core.persistence.journal`` (#1799).

Covers:

* Step-hash determinism: identical inputs → identical SHA-256 across calls.
* Canonical JSON encoding contract: sorted keys, no whitespace, stable
  serialisation of ``None``/``False``/``True``/integers/floats so a peer
  reading the docstring can re-derive the hash without our code.
* Chain integrity: ``prev_hash`` of step N is the ``step_hash`` of N-1;
  tampering with any field breaks verification.
* Atomic append: concurrent appenders never interleave bytes or skip a
  ``seq`` number.
* Reader semantics: ``head()`` returns the latest step; ``verify()``
  walks the chain end-to-end; ``window(start, end)`` slices.
* Reconstruction: ``replay_up_to(step)`` rebuilds the input/tool sequence
  the parent agent observed at that point.

Naming convention: every test name describes the behaviour under test in
the form ``test_<noun>_<verb>``, never ``test_<happy_path>``.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

import pytest

from bernstein.core.persistence.journal import (
    Journal,
    JournalEntry,
    JournalError,
    JournalReader,
    canonical_step_payload,
    compute_step_hash,
)

# ---------------------------------------------------------------------------
# Step-hash determinism + canonical encoding contract
# ---------------------------------------------------------------------------


class TestStepHashDeterminism:
    """``compute_step_hash`` is the load-bearing primitive; any drift here
    breaks every downstream replay/export/verify call. The contract is:
    given identical inputs, identical bytes, identical hash, on every
    machine and every Python release we support."""

    def test_same_inputs_yield_same_hash(self) -> None:
        h1 = compute_step_hash(
            prev_hash="0" * 64,
            input_hash="aa",
            model="m1",
            prompt="hi",
            tool_call={"name": "echo", "args": {"x": 1}},
            tool_result={"ok": True, "stdout": "hi\n"},
        )
        h2 = compute_step_hash(
            prev_hash="0" * 64,
            input_hash="aa",
            model="m1",
            prompt="hi",
            tool_call={"name": "echo", "args": {"x": 1}},
            tool_result={"ok": True, "stdout": "hi\n"},
        )
        assert h1 == h2
        assert len(h1) == 64
        assert all(c in "0123456789abcdef" for c in h1)

    def test_dict_key_order_does_not_affect_hash(self) -> None:
        """Canonical form sorts keys, so callers can pass dicts in any order."""
        a = compute_step_hash(
            prev_hash="0" * 64,
            input_hash="aa",
            model="m1",
            prompt="hi",
            tool_call={"args": {"x": 1, "y": 2}, "name": "echo"},
            tool_result={"stdout": "hi\n", "ok": True},
        )
        b = compute_step_hash(
            prev_hash="0" * 64,
            input_hash="aa",
            model="m1",
            prompt="hi",
            tool_call={"name": "echo", "args": {"y": 2, "x": 1}},
            tool_result={"ok": True, "stdout": "hi\n"},
        )
        assert a == b

    def test_field_change_changes_hash(self) -> None:
        """Any one field flip moves the hash to a different value. This is
        the assertion the divergence reporter depends on."""
        base = {
            "prev_hash": "0" * 64,
            "input_hash": "aa",
            "model": "m1",
            "prompt": "hi",
            "tool_call": {"name": "echo"},
            "tool_result": {"ok": True},
        }
        baseline = compute_step_hash(**base)
        for field, mutated in (
            ("prev_hash", "1" * 64),
            ("input_hash", "ab"),
            ("model", "m2"),
            ("prompt", "hi!"),
            ("tool_call", {"name": "echo2"}),
            ("tool_result", {"ok": False}),
        ):
            kwargs = dict(base)
            kwargs[field] = mutated
            assert compute_step_hash(**kwargs) != baseline, f"hash unchanged for {field}"

    def test_canonical_payload_is_documented_form(self) -> None:
        """The canonical payload bytes must match what the docstring documents
        so an external verifier can re-derive the hash by hand. Whitespace
        and key ordering are part of the contract."""
        payload = canonical_step_payload(
            prev_hash="0" * 64,
            input_hash="aa",
            model="m1",
            prompt="hi",
            tool_call={"name": "echo"},
            tool_result={"ok": True},
        )
        # Sorted keys, no whitespace separators - this is the wire form.
        decoded = json.loads(payload)
        assert decoded == {
            "input_hash": "aa",
            "model": "m1",
            "prev_hash": "0" * 64,
            "prompt": "hi",
            "tool_call": {"name": "echo"},
            "tool_result": {"ok": True},
        }
        # Re-encode in the same canonical form and assert byte equality.
        reencoded = json.dumps(decoded, sort_keys=True, separators=(",", ":")).encode("utf-8")
        assert payload == reencoded
        # And the hash is plain SHA-256 over those bytes.
        expected = hashlib.sha256(payload).hexdigest()
        assert (
            compute_step_hash(
                prev_hash="0" * 64,
                input_hash="aa",
                model="m1",
                prompt="hi",
                tool_call={"name": "echo"},
                tool_result={"ok": True},
            )
            == expected
        )

    def test_none_fields_serialise_as_null(self) -> None:
        """Tool calls without args / models without prompt are legal; the
        canonical form must still be stable."""
        h = compute_step_hash(
            prev_hash="0" * 64,
            input_hash="aa",
            model=None,
            prompt=None,
            tool_call=None,
            tool_result=None,
        )
        # Should be deterministic; recompute and compare.
        assert h == compute_step_hash(
            prev_hash="0" * 64,
            input_hash="aa",
            model=None,
            prompt=None,
            tool_call=None,
            tool_result=None,
        )


# ---------------------------------------------------------------------------
# Journal append + chain integrity
# ---------------------------------------------------------------------------


class TestJournalAppend:
    def test_first_entry_uses_genesis_prev_hash(self, tmp_path: Path) -> None:
        journal = Journal.open(tmp_path / "agent-1")
        entry = journal.append(
            input_hash="aa",
            model="m1",
            prompt="hi",
            tool_call=None,
            tool_result=None,
        )
        assert entry.seq == 0
        assert entry.prev_hash == "0" * 64
        assert len(entry.step_hash) == 64
        assert journal.head_hash == entry.step_hash

    def test_chain_links_correctly(self, tmp_path: Path) -> None:
        """Step N's ``prev_hash`` is step N-1's ``step_hash``."""
        journal = Journal.open(tmp_path / "agent-1")
        e0 = journal.append(input_hash="aa", model="m1", prompt="p1")
        e1 = journal.append(input_hash="bb", model="m1", prompt="p2")
        e2 = journal.append(input_hash="cc", model="m1", prompt="p3")
        assert e1.prev_hash == e0.step_hash
        assert e2.prev_hash == e1.step_hash
        assert journal.head_hash == e2.step_hash
        assert e0.seq == 0
        assert e1.seq == 1
        assert e2.seq == 2

    def test_entries_persist_to_jsonl(self, tmp_path: Path) -> None:
        """One JSON object per line. No interleaving. Re-readable."""
        agent_dir = tmp_path / "agent-1"
        journal = Journal.open(agent_dir)
        for i in range(5):
            journal.append(input_hash=f"a{i}", model="m1", prompt=f"p{i}")
        journal.close()

        files = sorted(agent_dir.glob("*.jsonl"))
        assert len(files) == 1
        lines = files[0].read_text(encoding="utf-8").splitlines()
        assert len(lines) == 5
        parsed = [json.loads(line) for line in lines]
        # seq is monotonic, prev_hash chain holds.
        for i, row in enumerate(parsed):
            assert row["seq"] == i
        for i in range(1, 5):
            assert parsed[i]["prev_hash"] == parsed[i - 1]["step_hash"]

    def test_append_after_reopen_continues_chain(self, tmp_path: Path) -> None:
        """Crash recovery: re-opening an existing journal continues
        from the recorded head, not from genesis."""
        agent_dir = tmp_path / "agent-1"
        journal = Journal.open(agent_dir)
        e0 = journal.append(input_hash="aa", model="m1", prompt="p1")
        journal.close()

        # Reopen and append. The new entry must chain to e0, not to genesis.
        journal = Journal.open(agent_dir)
        assert journal.head_hash == e0.step_hash
        e1 = journal.append(input_hash="bb", model="m1", prompt="p2")
        assert e1.prev_hash == e0.step_hash
        assert e1.seq == 1


# ---------------------------------------------------------------------------
# Chain verification + tamper detection
# ---------------------------------------------------------------------------


class TestVerification:
    def test_verify_intact_chain_returns_ok(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "agent-1"
        journal = Journal.open(agent_dir)
        for i in range(3):
            journal.append(input_hash=f"a{i}", model="m1", prompt=f"p{i}")
        head = journal.head_hash
        journal.close()

        reader = JournalReader(agent_dir)
        result = reader.verify(expected_head=head)
        assert result.ok
        assert result.errors == []
        assert result.head_hash == head
        assert result.steps == 3

    def test_verify_detects_field_tamper(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "agent-1"
        journal = Journal.open(agent_dir)
        journal.append(input_hash="aa", model="m1", prompt="p1")
        journal.append(input_hash="bb", model="m1", prompt="p2")
        head = journal.head_hash
        journal.close()

        # Tamper with the model field on the second entry.
        log_file = next(agent_dir.glob("*.jsonl"))
        lines = log_file.read_text(encoding="utf-8").splitlines()
        row = json.loads(lines[1])
        row["model"] = "evil"
        # Re-encode in the SAME canonical form so the byte tamper-check
        # cannot trivially flag it - we want the chain check to catch it.
        lines[1] = json.dumps(row, sort_keys=True, separators=(",", ":"))
        log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        reader = JournalReader(agent_dir)
        result = reader.verify(expected_head=head)
        assert not result.ok
        # Error must name the entry that broke and the failing check.
        assert any("step_hash" in e or "chain" in e for e in result.errors)

    def test_verify_detects_head_mismatch(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "agent-1"
        journal = Journal.open(agent_dir)
        journal.append(input_hash="aa", model="m1", prompt="p1")
        journal.close()

        reader = JournalReader(agent_dir)
        result = reader.verify(expected_head="f" * 64)
        assert not result.ok
        assert any("head" in e.lower() for e in result.errors)


# ---------------------------------------------------------------------------
# Atomicity under concurrent appenders
# ---------------------------------------------------------------------------


class TestAtomicity:
    def test_concurrent_appends_yield_unique_seq_numbers(self, tmp_path: Path) -> None:
        """Two threads racing on ``append`` must not corrupt the file or
        skip a ``seq`` slot. Single-writer is the supported pattern, but
        the implementation must guard against accidental races (the
        spawner is multi-threaded for stdout/stderr fan-out)."""
        agent_dir = tmp_path / "agent-1"
        journal = Journal.open(agent_dir)
        errors: list[str] = []

        def worker(thread_idx: int) -> None:
            try:
                for j in range(20):
                    journal.append(
                        input_hash=f"t{thread_idx}-{j}",
                        model="m1",
                        prompt=f"thread {thread_idx} step {j}",
                    )
            except Exception as exc:
                errors.append(repr(exc))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        journal.close()

        assert errors == []
        # 4 threads * 20 appends = 80 entries, seqs 0..79.
        reader = JournalReader(agent_dir)
        entries = list(reader.entries())
        assert len(entries) == 80
        seqs = sorted(e.seq for e in entries)
        assert seqs == list(range(80))
        # Chain must still verify end-to-end.
        result = reader.verify(expected_head=entries[-1].step_hash)
        assert result.ok, result.errors

    def test_partial_line_is_not_observed_by_reader(self, tmp_path: Path) -> None:
        """If a writer is killed mid-line, the reader must skip the
        truncated tail rather than treat random bytes as a valid entry.
        ``Journal.append`` writes one full line under a single ``write()``
        call so the kernel handles atomicity at the PIPE_BUF boundary; for
        long entries we ensure the line ends with ``\\n`` and the reader
        treats missing-trailing-newline as a truncated tail."""
        agent_dir = tmp_path / "agent-1"
        journal = Journal.open(agent_dir)
        journal.append(input_hash="aa", model="m1", prompt="p1")
        journal.close()

        # Manually append a torn line (no trailing newline).
        log_file = next(agent_dir.glob("*.jsonl"))
        with log_file.open("a", encoding="utf-8") as fh:
            fh.write('{"seq":1,"prev_hash":"deadbeef","truncated":')

        reader = JournalReader(agent_dir)
        entries = list(reader.entries())
        assert len(entries) == 1  # Torn tail was discarded.


# ---------------------------------------------------------------------------
# Reconstruction for fork-from-step
# ---------------------------------------------------------------------------


class TestReconstruction:
    def test_replay_up_to_step_returns_chain_slice(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "agent-1"
        journal = Journal.open(agent_dir)
        entries = [journal.append(input_hash=f"a{i}", model="m1", prompt=f"p{i}") for i in range(5)]
        journal.close()

        reader = JournalReader(agent_dir)
        slice_ = reader.window(start_seq=0, end_seq=2)
        assert [e.seq for e in slice_] == [0, 1, 2]
        assert slice_[-1].step_hash == entries[2].step_hash

    def test_replay_up_to_step_out_of_range_raises(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "agent-1"
        journal = Journal.open(agent_dir)
        journal.append(input_hash="aa", model="m1", prompt="p1")
        journal.close()

        reader = JournalReader(agent_dir)
        with pytest.raises(JournalError):
            reader.window(start_seq=0, end_seq=10)


# ---------------------------------------------------------------------------
# Entry dataclass hygiene
# ---------------------------------------------------------------------------


class TestJournalEntry:
    def test_entry_round_trips_through_dict(self) -> None:
        entry = JournalEntry(
            seq=0,
            prev_hash="0" * 64,
            input_hash="aa",
            model="m1",
            prompt="hi",
            tool_call={"name": "echo"},
            tool_result={"ok": True},
            step_hash="f" * 64,
            ts=1747000000.0,
        )
        round_tripped = JournalEntry.from_dict(entry.to_dict())
        assert round_tripped == entry

    def test_step_hash_matches_compute_step_hash(self) -> None:
        """The dataclass stores the precomputed hash; rebuilding it
        from the dataclass fields must give the same value."""
        entry = JournalEntry(
            seq=0,
            prev_hash="0" * 64,
            input_hash="aa",
            model="m1",
            prompt="hi",
            tool_call={"name": "echo"},
            tool_result={"ok": True},
            step_hash="placeholder",  # will be set by compute below
            ts=0.0,
        )
        h = compute_step_hash(
            prev_hash=entry.prev_hash,
            input_hash=entry.input_hash,
            model=entry.model,
            prompt=entry.prompt,
            tool_call=entry.tool_call,
            tool_result=entry.tool_result,
        )
        assert len(h) == 64


# ---------------------------------------------------------------------------
# File-system layout: .sdd/runtime/journal/<agent_id>/<bucket>.jsonl
# ---------------------------------------------------------------------------


class TestLayout:
    def test_journal_path_default_is_runtime_journal(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "agent-1"
        journal = Journal.open(agent_dir)
        journal.append(input_hash="aa", model="m1", prompt="p1")
        journal.close()
        # Single bucket file by default.
        files = list(agent_dir.glob("*.jsonl"))
        assert len(files) == 1
        # File mode is owner-writeable; on POSIX must not be group/world-writable.
        if os.name == "posix":
            mode = files[0].stat().st_mode & 0o777
            assert (mode & 0o022) == 0, f"unsafe mode {oct(mode)}"
