"""Tests for the install-rev fingerprint trace JSONL emit slot.

``TraceStore.write`` embeds a top-level ``_rev`` field into every
persisted trace dict when emission is on.

Covers: round-trip emit+decode, kill-switch suppress, operator-seed-
unset suppress, the field is absent under default off-state, and the
existing ``read_by_task`` / ``read_by_trace_id`` paths still parse the
augmented payload.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bernstein.core.identity import install_rev as ir
from bernstein.core.identity.install_rev import (
    DISABLED_SENTINEL,
    ENV_DISABLE,
    ENV_NONCE_PATH,
    ENV_SEED,
    NONCE_BYTES,
    _compute_token,
)
from bernstein.core.observability.traces import (
    AgentTrace,
    TraceStep,
    TraceStore,
)

TEST_SEED_HEX = "01" * 32
TEST_NONCE = bytes.fromhex("0123456789abcdef0123")
assert len(TEST_NONCE) == NONCE_BYTES


@pytest.fixture(autouse=True)
def _reset_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Pin nonce path + clear cache + disable emission by default."""
    nonce_path = tmp_path / "install_nonce"
    monkeypatch.setenv(ENV_NONCE_PATH, str(nonce_path))
    monkeypatch.delenv(ENV_DISABLE, raising=False)
    monkeypatch.delenv(ENV_SEED, raising=False)
    monkeypatch.setattr(ir, "IDENTITY_EMISSION_ENABLED", False)
    ir._reset_cache_for_tests()


def _build_trace() -> AgentTrace:
    """Construct a minimal trace object suitable for write+read tests."""
    return AgentTrace(
        trace_id="abcdef1234567890",
        session_id="test-session",
        task_ids=["task-1"],
        agent_role="backend",
        model="sonnet",
        effort="high",
        spawn_ts=1.0,
        end_ts=2.0,
        steps=[TraceStep(type="spawn", timestamp=1.0, detail="spawn")],
        outcome="success",
    )


def _enable_emission(monkeypatch: pytest.MonkeyPatch, nonce_path: Path) -> str:
    """Turn on emission with a deterministic seed + nonce."""
    monkeypatch.setattr(ir, "IDENTITY_EMISSION_ENABLED", True)
    monkeypatch.setenv(ENV_SEED, TEST_SEED_HEX)
    nonce_path.parent.mkdir(parents=True, exist_ok=True)
    nonce_path.write_bytes(TEST_NONCE)
    ir._reset_cache_for_tests()
    return _compute_token(bytes.fromhex(TEST_SEED_HEX), TEST_NONCE, ir._version_byte())


# ---------------------------------------------------------------------------
# Suppression paths
# ---------------------------------------------------------------------------


class TestTraceEmitSuppression:
    """The default off-state and kill switches must yield no ``_rev``."""

    def test_no_rev_field_when_emission_disabled(self, tmp_path: Path) -> None:
        store = TraceStore(tmp_path / "traces")
        trace = _build_trace()
        store.write(trace)

        per_trace = json.loads((tmp_path / "traces" / "trace-abcdef1234567890.json").read_text())
        per_task_lines = (tmp_path / "traces" / "task-1.jsonl").read_text().splitlines()
        assert "_rev" not in per_trace
        assert all("_rev" not in json.loads(line) for line in per_task_lines if line)

    def test_no_rev_field_when_kill_switch_set(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # Emission gate ON, kill switch ON - must still suppress.
        monkeypatch.setattr(ir, "IDENTITY_EMISSION_ENABLED", True)
        monkeypatch.setenv(ENV_DISABLE, "1")
        monkeypatch.setenv(ENV_SEED, TEST_SEED_HEX)
        ir._reset_cache_for_tests()

        store = TraceStore(tmp_path / "traces")
        store.write(_build_trace())

        per_trace = json.loads((tmp_path / "traces" / "trace-abcdef1234567890.json").read_text())
        assert "_rev" not in per_trace

    def test_no_rev_field_when_seed_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(ir, "IDENTITY_EMISSION_ENABLED", True)
        ir._reset_cache_for_tests()

        store = TraceStore(tmp_path / "traces")
        store.write(_build_trace())

        per_trace = json.loads((tmp_path / "traces" / "trace-abcdef1234567890.json").read_text())
        assert "_rev" not in per_trace


# ---------------------------------------------------------------------------
# Live emit
# ---------------------------------------------------------------------------


class TestTraceEmitLive:
    """When the operator opts in, every persisted trace carries ``_rev``."""

    def test_per_trace_file_carries_rev(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        nonce_path = tmp_path / "install_nonce"
        monkeypatch.setenv(ENV_NONCE_PATH, str(nonce_path))
        expected = _enable_emission(monkeypatch, nonce_path)

        store = TraceStore(tmp_path / "traces")
        store.write(_build_trace())

        per_trace = json.loads((tmp_path / "traces" / "trace-abcdef1234567890.json").read_text())
        assert per_trace["_rev"] == expected
        assert per_trace["_rev"] != DISABLED_SENTINEL

    def test_per_task_jsonl_carries_rev(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        nonce_path = tmp_path / "install_nonce"
        monkeypatch.setenv(ENV_NONCE_PATH, str(nonce_path))
        expected = _enable_emission(monkeypatch, nonce_path)

        store = TraceStore(tmp_path / "traces")
        store.write(_build_trace())

        line = (tmp_path / "traces" / "task-1.jsonl").read_text().splitlines()[0]
        assert json.loads(line)["_rev"] == expected

    def test_existing_readers_ignore_rev_gracefully(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # ``AgentTrace.from_dict`` discards unknown keys so the augmented
        # payload still loads as a valid trace - no schema break.
        nonce_path = tmp_path / "install_nonce"
        monkeypatch.setenv(ENV_NONCE_PATH, str(nonce_path))
        _enable_emission(monkeypatch, nonce_path)

        store = TraceStore(tmp_path / "traces")
        original = _build_trace()
        store.write(original)

        loaded = store.read_by_trace_id("abcdef1234567890")
        assert loaded is not None
        assert loaded.trace_id == original.trace_id
        assert loaded.session_id == original.session_id
        assert loaded.outcome == "success"

    def test_round_trip_decode(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # Operator picks up the trace from a public github paste, runs
        # verify_with_nonce against the user's nonce, and the token
        # matches at HMAC strength.
        nonce_path = tmp_path / "install_nonce"
        monkeypatch.setenv(ENV_NONCE_PATH, str(nonce_path))
        expected = _enable_emission(monkeypatch, nonce_path)

        store = TraceStore(tmp_path / "traces")
        store.write(_build_trace())
        line = (tmp_path / "traces" / "task-1.jsonl").read_text().splitlines()[0]
        emitted = json.loads(line)["_rev"]

        assert emitted == expected
        assert ir.verify_with_nonce(emitted, TEST_NONCE, version_major=ir._version_byte()) is True
