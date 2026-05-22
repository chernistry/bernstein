"""Contracts for the generated workflow topology report (#1827 F-058)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPORT = Path("docs/operations/ci-topology.md")


def test_workflow_topology_report_is_current() -> None:
    """The checked-in report must match the workflow YAML graph."""
    proc = subprocess.run(
        [sys.executable, "scripts/gen_workflow_topology.py", "--check"],
        capture_output=True,
        check=False,
        text=True,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_workflow_topology_report_names_high_risk_edges() -> None:
    """The report must expose the edges reviewers need to inspect."""
    text = REPORT.read_text(encoding="utf-8")

    for heading in (
        "## Workflow Summary",
        "## Check Emitters",
        "## Permissions And Secrets",
        "## Cross-Workflow Calls",
        "## Artifact Hand-Offs",
    ):
        assert heading in text
