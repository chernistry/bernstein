"""Unit tests for the claude-mock adapter example plugin."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from bernstein.core.models import ModelConfig
from custom_adapter import ClaudeMockAdapter

from bernstein.adapters.base import SpawnResult


def _wait_for_proc(result: SpawnResult, *, timeout_s: float = 3.0) -> int:
    """Block until the mock proc exits and return its code."""
    proc = result.proc
    assert proc is not None, "claude-mock must wire proc into SpawnResult"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if hasattr(proc, "poll") and proc.poll() is not None:
            return int(proc.returncode)  # type: ignore[attr-defined]
        time.sleep(0.05)
    pytest.fail(f"mock proc still alive after {timeout_s}s")
    return -1  # unreachable, satisfies type checker


class TestClaudeMockSpawn:
    """``spawn()`` writes canned NDJSON and returns a fast-exit handle."""

    def test_spawn_writes_canned_stream_json(self, tmp_path: Path) -> None:
        adapter = ClaudeMockAdapter()
        result = adapter.spawn(
            prompt="please refactor",
            workdir=tmp_path,
            model_config=ModelConfig(model="claude-mock", effort="medium"),
            session_id="mock-1",
        )
        try:
            exit_code = _wait_for_proc(result)
        finally:
            adapter.cancel_timeout(result)
        assert exit_code == 0
        body = result.log_path.read_text(encoding="utf-8")
        events = [json.loads(line) for line in body.strip().split("\n")]
        assert len(events) == 3
        assert events[0]["type"] == "system"
        assert events[1]["type"] == "assistant"
        assert events[2]["type"] == "result"
        # Result text matches the default canned response.
        assert "claude-mock" in events[2]["result"]

    def test_canned_response_picks_longest_prefix(self, tmp_path: Path) -> None:
        adapter = ClaudeMockAdapter(
            canned_responses={
                "": "default",
                "fix": "short-key match",
                "fix the bug": "longer-key match",
            }
        )
        result = adapter.spawn(
            prompt="fix the bug in src/foo.py",
            workdir=tmp_path,
            model_config=ModelConfig(model="claude-mock", effort="medium"),
            session_id="mock-2",
        )
        try:
            _wait_for_proc(result)
        finally:
            adapter.cancel_timeout(result)
        body = result.log_path.read_text(encoding="utf-8")
        events = [json.loads(line) for line in body.strip().split("\n")]
        assert events[2]["result"] == "longer-key match"

    def test_spawn_is_deterministic_across_runs(self, tmp_path: Path) -> None:
        """Same prompt → same canned text on every call.

        The session-id field changes per invocation (it's the
        bernstein-side identifier), but the assistant text and the
        result text are byte-stable. This is what makes the adapter
        usable for golden-file tests in downstream consumers.
        """
        adapter = ClaudeMockAdapter()
        bodies: list[str] = []
        for i in range(3):
            result = adapter.spawn(
                prompt="anything",
                workdir=tmp_path,
                model_config=ModelConfig(model="claude-mock", effort="medium"),
                session_id=f"det-{i}",
            )
            try:
                _wait_for_proc(result)
            finally:
                adapter.cancel_timeout(result)
            events = [json.loads(line) for line in result.log_path.read_text().strip().split("\n")]
            # Strip the session id (which legitimately varies) and
            # serialise the rest.
            for event in events:
                event.pop("session_id", None)
            bodies.append(json.dumps(events, sort_keys=True))
        assert len(set(bodies)) == 1, "claude-mock output must be byte-stable across runs"


class TestClaudeMockRegistry:
    """The plugin self-registers via the ``bernstein.adapters`` entry point.

    Verifying this without actually installing the package is awkward,
    so the test instead relies on the entry-point group: when the
    plugin IS installed (editable or wheel), the registry resolves
    ``claude_mock`` to our adapter class. We skip the assertion when
    the plugin is not installed so the test passes during development
    inside the bernstein repo.
    """

    def test_registry_resolves_claude_mock_when_installed(self) -> None:
        from importlib.metadata import entry_points

        eps = list(entry_points(group="bernstein.adapters"))
        names = {ep.name for ep in eps}
        if "claude_mock" not in names:
            pytest.skip("plugin not installed; install with `pip install -e .` to verify")

        from bernstein.adapters.registry import get_adapter

        adapter = get_adapter("claude_mock")
        assert isinstance(adapter, ClaudeMockAdapter)
        assert adapter.name() == "Claude Mock"
