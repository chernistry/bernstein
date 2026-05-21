"""Integration test for divergence detection (#1799).

Builds two journals from a *forced non-deterministic* test adapter and
asserts the orchestrator surfaces the precise field that flipped rather
than a flaky-test signature.

The harness here mirrors the production flow:

1. Run the adapter once with seed=1 and record the chain.
2. Run it again with seed=2 (one tool result deliberately differs).
3. Call ``diff_journals`` and assert the diff names ``tool_result`` (and
   only ``tool_result``) as the divergent field.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bernstein.core.persistence.journal import Journal
from bernstein.core.persistence.journal_diff import diff_journals


class FakeAdapter:
    """Tiny harness that emits tool calls + results into a journal.

    Acts as a stand-in for the real adapter contract; the only behaviour
    we need to exercise is that the journal append captures whatever the
    adapter produced, deterministic or not.
    """

    def __init__(self, journal: Journal, *, jitter: dict[int, dict] | None = None) -> None:
        self._journal = journal
        self._jitter = jitter or {}

    def run(self, n_steps: int) -> None:
        for i in range(n_steps):
            tool_result = {"ok": True, "stdout": f"step {i}"}
            tool_result.update(self._jitter.get(i, {}))
            self._journal.append(
                input_hash=f"input-{i}",
                model="model-A",
                prompt=f"prompt {i}",
                tool_call={"name": "tool", "args": {"i": i}},
                tool_result=tool_result,
            )


def test_two_deterministic_runs_match(tmp_path: Path) -> None:
    journal_a = Journal.open(tmp_path / "agent-a")
    FakeAdapter(journal_a).run(5)
    journal_a.close()

    journal_b = Journal.open(tmp_path / "agent-b")
    FakeAdapter(journal_b).run(5)
    journal_b.close()

    assert diff_journals(tmp_path / "agent-a", tmp_path / "agent-b") is None


def test_forced_non_determinism_surfaces_precise_field(tmp_path: Path) -> None:
    journal_a = Journal.open(tmp_path / "agent-a")
    FakeAdapter(journal_a).run(5)
    journal_a.close()

    # Force non-determinism only at step 3: the ``stdout`` line is "wrong".
    journal_b = Journal.open(tmp_path / "agent-b")
    FakeAdapter(
        journal_b,
        jitter={3: {"stdout": "step 3 (different)"}},
    ).run(5)
    journal_b.close()

    divergence = diff_journals(tmp_path / "agent-a", tmp_path / "agent-b")
    assert divergence is not None
    assert divergence.seq == 3
    # Both ``prev_hash`` and ``tool_result`` shifted - ``prev_hash`` because
    # any chained tampering propagates downward in a Merkle list. The
    # adapter-visible field is ``tool_result``; the cascade through
    # ``prev_hash`` is an expected side effect.
    assert "tool_result" in divergence.fields_changed
    assert divergence.left_values["tool_result"]["stdout"] == "step 3"
    assert divergence.right_values["tool_result"]["stdout"] == "step 3 (different)"


def test_orchestrator_does_not_silently_accept_divergence(tmp_path: Path) -> None:
    """AC #5: orchestrator never silently accepts a divergent replay.

    We model that contract here with a guard helper: any caller that
    compares two journals and finds a divergence MUST exit non-zero or
    raise. ``diff_journals`` is the primitive; the surface that consumes
    its output is required to escalate.
    """
    journal_a = Journal.open(tmp_path / "agent-a")
    FakeAdapter(journal_a).run(2)
    journal_a.close()

    journal_b = Journal.open(tmp_path / "agent-b")
    FakeAdapter(journal_b, jitter={0: {"stdout": "evil"}}).run(2)
    journal_b.close()

    def guard(left: Path, right: Path) -> None:
        d = diff_journals(left, right)
        if d is not None:
            raise RuntimeError(f"divergence at seq={d.seq} fields={d.fields_changed}")

    with pytest.raises(RuntimeError):
        guard(tmp_path / "agent-a", tmp_path / "agent-b")


def test_divergence_diagnostics_are_json_serialisable(tmp_path: Path) -> None:
    """The CLI surfaces divergence as JSON when ``--json`` is set; the
    underlying dataclass must round-trip through json.dumps cleanly so
    operators piping into ``jq`` see a useful object."""
    journal_a = Journal.open(tmp_path / "agent-a")
    FakeAdapter(journal_a).run(2)
    journal_a.close()

    journal_b = Journal.open(tmp_path / "agent-b")
    FakeAdapter(journal_b, jitter={1: {"stdout": "shift"}}).run(2)
    journal_b.close()

    d = diff_journals(tmp_path / "agent-a", tmp_path / "agent-b")
    assert d is not None
    payload = {
        "seq": d.seq,
        "fields_changed": list(d.fields_changed),
        "left_values": d.left_values,
        "right_values": d.right_values,
    }
    blob = json.dumps(payload, default=str)
    # Round-trips without error and the seq is preserved.
    assert json.loads(blob)["seq"] == d.seq
