"""Coverage for the narrow error/skip branches in watchdog + stalled_manager.

The main suites cover the happy paths; this module pins down:

watchdog:
* :func:`classify_prompt` returning ``"none"`` for non-empty, non-matching
  output.
* :func:`_emit_audit_event` swallowing an OSError on write.
* :func:`tick` skipping a paused session whose tail classifies as ``"none"``.

stalled_manager:
* :func:`_read_hook_events` skipping blank lines, malformed JSON, non-dict
  rows, and returning ``[]`` on a read error.
* :func:`_redact_env` ``<unset>`` for an empty sensitive value + value
  truncation.
* :func:`detect_stalled_manager` early returns for malformed orchestrator
  state.
* :func:`_write_failure_record` returning ``None`` on a write error.
* :func:`handle_stalled_manager` swallowing a bulletin exception.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from bernstein.core.orchestration.stalled_manager import (
    STALL_THRESHOLD_S,
    StalledManagerDiagnostic,
    _extract_last_bash_commands,
    _read_hook_events,
    _redact_env,
    _write_failure_record,
    detect_stalled_manager,
    handle_stalled_manager,
)
from bernstein.core.orchestration.watchdog import (
    FEATURE_FLAG_ENV,
    SessionSnapshot,
    _emit_audit_event,
    classify_prompt,
    tick,
)

# ---------------------------------------------------------------------------
# watchdog: classify_prompt "none" for non-matching content
# ---------------------------------------------------------------------------


def test_classify_prompt_returns_none_for_plain_log_line() -> None:
    # Non-empty tail that matches neither a model question nor a safety prompt.
    assert classify_prompt("Compiling module foo.bar ...") == "none"


def test_classify_prompt_returns_none_for_trailing_status() -> None:
    assert classify_prompt("step 3/7\nrunning tests") == "none"


# ---------------------------------------------------------------------------
# watchdog: _emit_audit_event swallows OSError
# ---------------------------------------------------------------------------


def test_emit_audit_event_swallows_write_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    audit_path = tmp_path / "watchdog.jsonl"

    # Force the open() to raise an OSError so the except branch runs.
    def _boom(*_a: Any, **_k: Any) -> Any:
        raise OSError("read-only filesystem")

    monkeypatch.setattr(Path, "open", _boom)
    # Should not raise.
    _emit_audit_event(audit_path, "watchdog.test", {"k": "v"})


# ---------------------------------------------------------------------------
# watchdog: tick skips paused session with non-matching prompt
# ---------------------------------------------------------------------------


def test_tick_skips_paused_session_with_no_recognised_prompt(tmp_path: Path) -> None:
    captured: list[tuple[str, str]] = []

    def _respond(session_id: str, keystroke: str) -> bool:
        captured.append((session_id, keystroke))
        return True

    snapshot = SessionSnapshot(
        session_id="s1",
        recent_output="just some streaming output, no prompt",
        is_paused=True,
        approved_prompt_classes=frozenset({"safety"}),
    )
    audit = tmp_path / "watchdog.jsonl"
    result = tick([snapshot], _respond, audit, env={FEATURE_FLAG_ENV: "1"})

    # Paused but classified "none": neither recovered nor escalated.
    assert result.recoveries == ()
    assert result.skipped_model_questions == ()
    assert captured == []
    assert not audit.exists()  # no audit row written for a "none" prompt


# ---------------------------------------------------------------------------
# stalled_manager: _read_hook_events skip + error branches
# ---------------------------------------------------------------------------


def test_read_hook_events_returns_empty_when_file_absent(tmp_path: Path) -> None:
    assert _read_hook_events(tmp_path, "missing-session") == []


def test_read_hook_events_skips_blank_and_malformed_lines(tmp_path: Path) -> None:
    hooks = tmp_path / ".sdd" / "runtime" / "hooks" / "sess.jsonl"
    hooks.parent.mkdir(parents=True)
    hooks.write_text(
        "\n"  # blank line
        '{"event": "A"}\n'  # valid dict
        "not-json\n"  # malformed -> skipped
        "[1,2,3]\n"  # valid JSON but not a dict -> skipped
        "   \n"  # whitespace-only -> skipped
        '{"event": "B"}\n',  # valid dict
        encoding="utf-8",
    )
    events = _read_hook_events(tmp_path, "sess")
    assert [e["event"] for e in events] == ["A", "B"]


def test_read_hook_events_returns_empty_on_read_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hooks = tmp_path / ".sdd" / "runtime" / "hooks" / "sess.jsonl"
    hooks.parent.mkdir(parents=True)
    hooks.write_text('{"event": "A"}\n', encoding="utf-8")

    def _boom(*_a: Any, **_k: Any) -> str:
        raise OSError("io error")

    monkeypatch.setattr(Path, "read_text", _boom)
    assert _read_hook_events(tmp_path, "sess") == []


def test_read_hook_events_tail_limits_returned_rows(tmp_path: Path) -> None:
    hooks = tmp_path / ".sdd" / "runtime" / "hooks" / "sess.jsonl"
    hooks.parent.mkdir(parents=True)
    lines = "".join(json.dumps({"event": f"E{i}"}) + "\n" for i in range(10))
    hooks.write_text(lines, encoding="utf-8")
    events = _read_hook_events(tmp_path, "sess", tail=3)
    assert [e["event"] for e in events] == ["E7", "E8", "E9"]


def test_extract_last_bash_commands_ignores_non_bash_and_empty() -> None:
    events: list[dict[str, Any]] = [
        {"tool_name": "Bash", "tool_input": "ls"},
        {"tool_name": "Grep", "tool_input": "pattern"},  # non-Bash
        {"tool_name": "Bash", "tool_input": ""},  # empty command skipped
        {"tool_name": "Bash", "tool_input": "pwd"},
    ]
    assert _extract_last_bash_commands(events) == ["ls", "pwd"]


# ---------------------------------------------------------------------------
# stalled_manager: _redact_env
# ---------------------------------------------------------------------------


def test_redact_env_unset_for_empty_sensitive_value() -> None:
    out = _redact_env({"BERNSTEIN_AUTH_TOKEN": ""})
    assert out["BERNSTEIN_AUTH_TOKEN"] == "<unset>"


def test_redact_env_truncates_long_non_sensitive_value() -> None:
    long_value = "x" * 500
    out = _redact_env({"BERNSTEIN_SERVER_URL": long_value})
    assert out["BERNSTEIN_SERVER_URL"] == "x" * 120  # truncated to 120 chars


def test_redact_env_drops_untracked_prefixes() -> None:
    out = _redact_env({"RANDOM_VAR": "v", "BERNSTEIN_X": "keep"})
    assert "RANDOM_VAR" not in out
    assert out["BERNSTEIN_X"] == "keep"


# ---------------------------------------------------------------------------
# stalled_manager: detect_stalled_manager malformed-state early returns
# ---------------------------------------------------------------------------


def test_detect_returns_none_when_workdir_not_path() -> None:
    orch = SimpleNamespace(_workdir="not-a-path", _agents={})
    assert detect_stalled_manager(orch) is None


def test_detect_returns_none_when_agents_not_dict(tmp_path: Path) -> None:
    orch = SimpleNamespace(_workdir=tmp_path, _agents=["not", "a", "dict"])
    assert detect_stalled_manager(orch) is None


def test_detect_returns_none_when_latest_tasks_not_dict(tmp_path: Path) -> None:
    now = 1_000.0
    session = SimpleNamespace(
        id="manager-1",
        role="manager",
        status="working",
        spawn_ts=now - 200.0,
        task_ids=["mgr-task"],
    )
    orch = SimpleNamespace(
        _workdir=tmp_path,
        _agents={"manager-1": session},
        _config=SimpleNamespace(stalled_manager_threshold_s=STALL_THRESHOLD_S),
        _latest_tasks_by_id="not-a-dict",
    )
    with patch("bernstein.core.orchestration.stalled_manager.time.time", return_value=now):
        assert detect_stalled_manager(orch) is None


# ---------------------------------------------------------------------------
# stalled_manager: _write_failure_record write error
# ---------------------------------------------------------------------------


def test_write_failure_record_returns_none_on_write_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    diag = StalledManagerDiagnostic(
        session_id="sess",
        manager_task_id="task-1",
        runtime_s=120.0,
        hook_event_count=2,
    )

    def _boom(*_a: Any, **_k: Any) -> int:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", _boom)
    assert _write_failure_record(tmp_path, diag) is None


def test_write_failure_record_returns_none_on_mkdir_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    diag = StalledManagerDiagnostic(
        session_id="sess",
        manager_task_id="task-1",
        runtime_s=120.0,
        hook_event_count=0,
    )

    def _boom(*_a: Any, **_k: Any) -> None:
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "mkdir", _boom)
    assert _write_failure_record(tmp_path, diag) is None


# ---------------------------------------------------------------------------
# stalled_manager: handle_stalled_manager swallows bulletin exception
# ---------------------------------------------------------------------------


def test_handle_swallows_bulletin_exception(tmp_path: Path, capsys: Any) -> None:
    now = 1_000.0
    session = SimpleNamespace(
        id="manager-xyz",
        role="manager",
        status="working",
        spawn_ts=now - 200.0,
        task_ids=["mgr-task"],
    )

    def _bad_bulletin(_kind: str, _body: str) -> None:
        raise RuntimeError("bulletin board offline")

    orch = SimpleNamespace(
        _workdir=tmp_path,
        _agents={"manager-xyz": session},
        _latest_tasks_by_id={"mgr-task": object()},
        _config=SimpleNamespace(stalled_manager_threshold_s=STALL_THRESHOLD_S),
        _manager_env_snapshot={},
        _running=True,
        _post_bulletin=_bad_bulletin,
    )
    with patch("bernstein.core.orchestration.stalled_manager.time.time", return_value=now):
        diag = handle_stalled_manager(orch)

    # Despite the bulletin raising, the run is still aborted cleanly and the
    # diagnostic is returned.
    assert diag is not None
    assert orch._running is False
    assert orch._stalled_manager_emitted is True
