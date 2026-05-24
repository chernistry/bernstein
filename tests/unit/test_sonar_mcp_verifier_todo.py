"""Regression tests for Sonar python:S1135 in MCP verifier notes."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MCP_VERIFIER = PROJECT_ROOT / "src/bernstein/core/protocols/mcp/mcp_verifier.py"


def test_mcp_verifier_deferred_sigstore_note_has_no_todo_marker() -> None:
    body = MCP_VERIFIER.read_text(encoding="utf-8")

    assert "TODO(sigstore)" not in body
    assert "TODO below" not in body
    assert "Deferred(sigstore)" not in body
