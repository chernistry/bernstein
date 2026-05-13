"""Tests for the post-write lineage hook on ``CLIAdapter``.

The base adapter exposes a single classmethod-like helper that adapters
opt into after they've persisted bytes for an artefact. The helper:

  * Is a no-op when the ``BERNSTEIN_LINEAGE_ENABLED`` env var is falsey.
  * Records the write through a ``LineageRecorder`` when enabled.
  * Swallows errors in soft mode (default v1) as a WARNING log line.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from bernstein.adapters.base import (
    LINEAGE_ENABLED_ENV,
    post_write_lineage_hook,
)
from bernstein.core.lineage.identity import AgentCard, generate_keypair
from bernstein.core.lineage.store import LineageStore


@pytest.fixture
def card_and_key() -> tuple[AgentCard, str]:
    priv, pub = generate_keypair()
    return AgentCard(agent_id="agent:worker", kid="k1", public_key_pem=pub), priv


def test_hook_records_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    card_and_key: tuple[AgentCard, str],
) -> None:
    monkeypatch.setenv(LINEAGE_ENABLED_ENV, "1")
    card, priv = card_and_key

    lineage_root = tmp_path / "lineage"
    post_write_lineage_hook(
        artefact_path="src/foo.py",
        new_content=b"hello",
        agent_id=card.agent_id,
        agent_card=card,
        private_key_pem=priv,
        tool_call_id="tc-1",
        span_id="span-1",
        lineage_root=lineage_root,
        operator_hmac_key=b"k" * 32,
    )
    log = lineage_root / "log.jsonl"
    assert log.exists()
    assert log.read_bytes().count(b"\n") == 1


def test_hook_noop_when_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    card_and_key: tuple[AgentCard, str],
) -> None:
    monkeypatch.setenv(LINEAGE_ENABLED_ENV, "false")
    card, priv = card_and_key
    lineage_root = tmp_path / "lineage"
    post_write_lineage_hook(
        artefact_path="src/foo.py",
        new_content=b"hello",
        agent_id=card.agent_id,
        agent_card=card,
        private_key_pem=priv,
        tool_call_id="tc-1",
        span_id="span-1",
        lineage_root=lineage_root,
        operator_hmac_key=b"k" * 32,
    )
    # The root is never touched in disabled mode.
    assert not lineage_root.exists()


def test_hook_default_is_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    card_and_key: tuple[AgentCard, str],
) -> None:
    # Unset → default behaviour is on (lineage v1 soft mode).
    monkeypatch.delenv(LINEAGE_ENABLED_ENV, raising=False)
    card, priv = card_and_key
    lineage_root = tmp_path / "lineage"
    post_write_lineage_hook(
        artefact_path="src/foo.py",
        new_content=b"hello",
        agent_id=card.agent_id,
        agent_card=card,
        private_key_pem=priv,
        tool_call_id="tc-1",
        span_id="span-1",
        lineage_root=lineage_root,
        operator_hmac_key=b"k" * 32,
    )
    assert (lineage_root / "log.jsonl").exists()


def test_hook_soft_mode_logs_warning_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    card_and_key: tuple[AgentCard, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An exception inside the recorder must not propagate in soft mode."""
    monkeypatch.setenv(LINEAGE_ENABLED_ENV, "1")
    card, priv = card_and_key
    # Inject a broken store that raises on append.
    from bernstein.adapters import base as base_module

    class _BoomStore(LineageStore):
        def append(self, *_a: object, **_kw: object) -> str:
            raise RuntimeError("disk on fire")

    monkeypatch.setattr(base_module, "LineageStore", _BoomStore)

    with caplog.at_level(logging.WARNING):
        post_write_lineage_hook(
            artefact_path="src/foo.py",
            new_content=b"hello",
            agent_id=card.agent_id,
            agent_card=card,
            private_key_pem=priv,
            tool_call_id="tc-1",
            span_id="span-1",
            lineage_root=tmp_path / "lineage",
            operator_hmac_key=b"k" * 32,
        )

    assert any("lineage" in r.message.lower() for r in caplog.records), caplog.records
