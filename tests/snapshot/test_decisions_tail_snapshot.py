"""Snapshot test for the ``bernstein decisions tail`` table.

The CLI's table layout is part of the operator UX contract: a silent
column reorder or width change would frustrate every script that
greps the output. The snapshot here pins the deterministic columns
(``kind``, ``chosen``, ``conf``, ``rationale``) and the row count.

Update workflow: when the layout intentionally changes, run
``uv run pytest tests/snapshot/test_decisions_tail_snapshot.py --snapshot-update``
and review the diff before committing.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
from syrupy.assertion import SnapshotAssertion

from bernstein.cli.commands.decisions_cmd import decisions_group
from bernstein.core.observability.decision_log import (
    SCHEMA_VERSION,
    DecisionRecord,
)


def _seed_ledger(path: Path) -> None:
    """Write a two-record ledger with deterministic ts/decision_id values."""
    records = [
        DecisionRecord(
            ts=1700000000.000,
            decision_id="dec-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            kind="model_route",
            chosen="claude-sonnet-4.7",
            alternatives=(),
            confidence=0.85,
            rationale="role=manager → premium model",
            schema_version=SCHEMA_VERSION,
        ),
        DecisionRecord(
            ts=1700000001.500,
            decision_id="dec-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            kind="mode_profile",
            chosen="surge",
            alternatives=(),
            confidence=0.70,
            rationale="queue depth high",
            schema_version=SCHEMA_VERSION,
        ),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r.to_dict(), separators=(",", ":")) + "\n")


def test_decisions_tail_table_snapshot(tmp_path: Path, snapshot: SnapshotAssertion) -> None:
    """The ``decisions tail`` table layout must match the stored snapshot.

    A diff here means the operator-facing format changed; review the
    snapshot diff before re-baselining.
    """
    ledger = tmp_path / "decisions.jsonl"
    _seed_ledger(ledger)

    runner = CliRunner()
    result = runner.invoke(
        decisions_group,
        ["tail", "--path", str(ledger), "-n", "10"],
    )
    assert result.exit_code == 0, result.output
    assert result.output == snapshot
