"""Unit tests for the orchestrator's manager-review correction helpers.

Covers the module-level free functions that apply ManagerAgent queue-review
corrections against the task server:

* :func:`_fetch_task_states`
* :func:`_apply_manager_corrections` (dispatch + validation guards)
* :func:`_apply_reassign` / :func:`_apply_change_priority` /
  :func:`_apply_cancel` / :func:`_apply_add_task`
* :func:`_resolve_manager_llm`

The HTTP layer is exercised through a recording stub so each test asserts the
exact request shape (method, url, json body) that reaches the server, or the
fact that *no* request was made for a guarded/invalid correction.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from bernstein.core.orchestration.manager_models import QueueCorrection
from bernstein.core.orchestration.orchestrator import (
    _apply_add_task,
    _apply_cancel,
    _apply_change_priority,
    _apply_manager_corrections,
    _apply_reassign,
    _fetch_task_states,
    _resolve_manager_llm,
)


class _RecordingClient:
    """Minimal httpx.Client stand-in that records calls instead of issuing them.

    Each public verb appends a ``(verb, url, json)`` tuple to ``calls`` and
    returns a stub response. ``raise_for`` lets a test force an
    :class:`httpx.HTTPError` on a chosen verb to drive the error branches.
    """

    def __init__(self, *, get_payload: Any | None = None, raise_on: set[str] | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self._get_payload = get_payload if get_payload is not None else []
        self._raise_on = raise_on or set()

    def _maybe_raise(self, verb: str) -> None:
        if verb in self._raise_on:
            raise httpx.HTTPError(f"boom on {verb}")

    def get(self, url: str) -> Any:
        self.calls.append(("get", url, None))
        self._maybe_raise("get")
        payload = self._get_payload
        return _StubResponse(payload)

    def post(self, url: str, json: dict[str, Any] | None = None) -> Any:
        self.calls.append(("post", url, json))
        self._maybe_raise("post")
        return _StubResponse({})

    def patch(self, url: str, json: dict[str, Any] | None = None) -> Any:
        self.calls.append(("patch", url, json))
        self._maybe_raise("patch")
        return _StubResponse({})


class _StubResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


def _correction(
    action: str,
    *,
    task_id: str | None = None,
    new_role: str | None = None,
    new_priority: int | None = None,
    reason: str = "because",
    new_task: dict[str, Any] | None = None,
) -> QueueCorrection:
    return QueueCorrection(
        action=action,  # type: ignore[arg-type]
        task_id=task_id,
        new_role=new_role,
        new_priority=new_priority,
        reason=reason,
        new_task=new_task,
    )


SERVER = "http://127.0.0.1:8052"


# ---------------------------------------------------------------------------
# _fetch_task_states
# ---------------------------------------------------------------------------


def test_fetch_task_states_maps_id_to_status() -> None:
    client = _RecordingClient(
        get_payload=[
            {"id": "T-1", "status": "open"},
            {"id": "T-2", "status": "claimed"},
        ]
    )
    states = _fetch_task_states(client, SERVER)  # type: ignore[arg-type]
    assert states == {"T-1": "open", "T-2": "claimed"}
    assert client.calls == [("get", f"{SERVER}/tasks", None)]


def test_fetch_task_states_defaults_missing_status_to_unknown() -> None:
    client = _RecordingClient(get_payload=[{"id": "T-9"}])
    states = _fetch_task_states(client, SERVER)  # type: ignore[arg-type]
    assert states == {"T-9": "unknown"}


def test_fetch_task_states_returns_empty_on_http_error() -> None:
    client = _RecordingClient(raise_on={"get"})
    states = _fetch_task_states(client, SERVER)  # type: ignore[arg-type]
    assert states == {}


# ---------------------------------------------------------------------------
# _apply_reassign
# ---------------------------------------------------------------------------


def test_apply_reassign_patches_role_for_valid_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "bernstein.core.context.available_roles",
        lambda _path: ["backend", "qa"],
    )
    client = _RecordingClient()
    correction = _correction("reassign", task_id="T-1", new_role="qa")
    returned = _apply_reassign(client, SERVER, Path("/tmp/wd"), correction, None)  # type: ignore[arg-type]

    assert client.calls == [("patch", f"{SERVER}/tasks/T-1", {"role": "qa"})]
    # valid_roles is populated lazily and returned for reuse.
    assert returned == {"backend", "qa"}


def test_apply_reassign_skips_invalid_role_no_patch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "bernstein.core.context.available_roles",
        lambda _path: ["backend"],
    )
    client = _RecordingClient()
    correction = _correction("reassign", task_id="T-1", new_role="wizard")
    returned = _apply_reassign(client, SERVER, Path("/tmp/wd"), correction, None)  # type: ignore[arg-type]

    assert client.calls == []  # invalid role => no server mutation
    assert returned == {"backend"}


def test_apply_reassign_no_task_id_is_noop() -> None:
    client = _RecordingClient()
    correction = _correction("reassign", task_id=None, new_role="qa")
    returned = _apply_reassign(client, SERVER, Path("/tmp/wd"), correction, {"qa"})  # type: ignore[arg-type]
    assert client.calls == []
    assert returned == {"qa"}


def test_apply_reassign_reuses_supplied_valid_roles_without_relookup() -> None:
    # available_roles must NOT be consulted when valid_roles is already set;
    # if it were, this would explode because the path does not exist.
    client = _RecordingClient()
    correction = _correction("reassign", task_id="T-7", new_role="qa")
    returned = _apply_reassign(client, SERVER, Path("/does/not/exist"), correction, {"qa"})  # type: ignore[arg-type]
    assert client.calls == [("patch", f"{SERVER}/tasks/T-7", {"role": "qa"})]
    assert returned == {"qa"}


# ---------------------------------------------------------------------------
# _apply_change_priority
# ---------------------------------------------------------------------------


def test_apply_change_priority_patches_priority() -> None:
    client = _RecordingClient()
    _apply_change_priority(client, SERVER, _correction("change_priority", task_id="T-1", new_priority=1))  # type: ignore[arg-type]
    assert client.calls == [("patch", f"{SERVER}/tasks/T-1", {"priority": 1})]


def test_apply_change_priority_missing_priority_is_noop() -> None:
    client = _RecordingClient()
    _apply_change_priority(client, SERVER, _correction("change_priority", task_id="T-1", new_priority=None))  # type: ignore[arg-type]
    assert client.calls == []


def test_apply_change_priority_missing_task_id_is_noop() -> None:
    client = _RecordingClient()
    _apply_change_priority(client, SERVER, _correction("change_priority", task_id=None, new_priority=3))  # type: ignore[arg-type]
    assert client.calls == []


# ---------------------------------------------------------------------------
# _apply_cancel
# ---------------------------------------------------------------------------


def test_apply_cancel_posts_cancel_for_cancellable_state() -> None:
    client = _RecordingClient()
    _apply_cancel(
        client,  # type: ignore[arg-type]
        SERVER,
        _correction("cancel", task_id="T-1", reason="duplicate"),
        {"T-1": "open"},
        {"open", "claimed", "in_progress"},
    )
    assert client.calls == [("post", f"{SERVER}/tasks/T-1/cancel", {"reason": "duplicate"})]


def test_apply_cancel_skips_non_cancellable_state() -> None:
    client = _RecordingClient()
    _apply_cancel(
        client,  # type: ignore[arg-type]
        SERVER,
        _correction("cancel", task_id="T-1"),
        {"T-1": "done"},
        {"open", "claimed", "in_progress"},
    )
    assert client.calls == []  # done is not cancellable


def test_apply_cancel_unknown_state_still_cancels() -> None:
    # When the task is absent from task_states (status is None), the guard
    # does not fire and the cancel proceeds.
    client = _RecordingClient()
    _apply_cancel(
        client,  # type: ignore[arg-type]
        SERVER,
        _correction("cancel", task_id="T-unknown", reason=""),
        {},
        {"open"},
    )
    assert client.calls == [("post", f"{SERVER}/tasks/T-unknown/cancel", {"reason": "manager review"})]


def test_apply_cancel_missing_task_id_is_noop() -> None:
    client = _RecordingClient()
    _apply_cancel(client, SERVER, _correction("cancel", task_id=None), {}, {"open"})  # type: ignore[arg-type]
    assert client.calls == []


# ---------------------------------------------------------------------------
# _apply_add_task
# ---------------------------------------------------------------------------


def test_apply_add_task_posts_new_task() -> None:
    client = _RecordingClient()
    new_task = {"title": "Write docs", "role": "docs"}
    _apply_add_task(client, SERVER, _correction("add_task", new_task=new_task))  # type: ignore[arg-type]
    assert client.calls == [("post", f"{SERVER}/tasks", new_task)]


def test_apply_add_task_missing_payload_is_noop() -> None:
    client = _RecordingClient()
    _apply_add_task(client, SERVER, _correction("add_task", new_task=None))  # type: ignore[arg-type]
    assert client.calls == []


# ---------------------------------------------------------------------------
# _apply_manager_corrections (dispatch + validation)
# ---------------------------------------------------------------------------


def test_apply_manager_corrections_skips_correction_for_nonexistent_task() -> None:
    client = _RecordingClient()
    # task_states is non-empty and does NOT contain T-99 => skip.
    _apply_manager_corrections(
        client,  # type: ignore[arg-type]
        SERVER,
        Path("/tmp/wd"),
        [_correction("change_priority", task_id="T-99", new_priority=1)],
        {"T-1": "open"},
    )
    assert client.calls == []


def test_apply_manager_corrections_dispatches_each_action(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "bernstein.core.context.available_roles",
        lambda _path: ["backend", "qa"],
    )
    client = _RecordingClient()
    corrections = [
        _correction("reassign", task_id="T-1", new_role="qa"),
        _correction("change_priority", task_id="T-2", new_priority=1),
        _correction("cancel", task_id="T-3", reason="dup"),
        _correction("add_task", new_task={"title": "x"}),
    ]
    task_states = {"T-1": "open", "T-2": "open", "T-3": "open"}
    _apply_manager_corrections(client, SERVER, Path("/tmp/wd"), corrections, task_states)  # type: ignore[arg-type]

    verbs_urls = [(verb, url) for verb, url, _ in client.calls]
    assert verbs_urls == [
        ("patch", f"{SERVER}/tasks/T-1"),
        ("patch", f"{SERVER}/tasks/T-2"),
        ("post", f"{SERVER}/tasks/T-3/cancel"),
        ("post", f"{SERVER}/tasks"),
    ]


def test_apply_manager_corrections_add_task_bypasses_existence_check() -> None:
    # add_task has no task_id; the non-existent-task guard must not block it
    # even when task_states is populated.
    client = _RecordingClient()
    _apply_manager_corrections(
        client,  # type: ignore[arg-type]
        SERVER,
        Path("/tmp/wd"),
        [_correction("add_task", new_task={"title": "fresh"})],
        {"T-1": "open"},
    )
    assert client.calls == [("post", f"{SERVER}/tasks", {"title": "fresh"})]


def test_apply_manager_corrections_isolates_http_error_and_continues(monkeypatch: pytest.MonkeyPatch) -> None:
    # A failing PATCH (HTTPError) on the first correction must be swallowed so
    # the second correction still runs.
    monkeypatch.setattr(
        "bernstein.core.context.available_roles",
        lambda _path: ["backend", "qa"],
    )
    client = _RecordingClient(raise_on={"patch"})
    corrections = [
        _correction("change_priority", task_id="T-1", new_priority=1),  # raises on patch
        _correction("add_task", new_task={"title": "still-runs"}),  # must run anyway
    ]
    _apply_manager_corrections(
        client,  # type: ignore[arg-type]
        SERVER,
        Path("/tmp/wd"),
        corrections,
        {"T-1": "open"},
    )
    # Both calls attempted; the add_task POST proves the loop did not abort.
    assert ("post", f"{SERVER}/tasks", {"title": "still-runs"}) in client.calls


def test_apply_manager_corrections_empty_task_states_does_not_skip() -> None:
    # When task_states is empty (server fetch failed), the existence guard is
    # skipped (falsy task_states) and corrections still flow through.
    client = _RecordingClient()
    _apply_manager_corrections(
        client,  # type: ignore[arg-type]
        SERVER,
        Path("/tmp/wd"),
        [_correction("change_priority", task_id="T-1", new_priority=2)],
        {},
    )
    assert client.calls == [("patch", f"{SERVER}/tasks/T-1", {"priority": 2})]


# ---------------------------------------------------------------------------
# _resolve_manager_llm
# ---------------------------------------------------------------------------


def test_resolve_manager_llm_defaults_when_no_seed(tmp_path: Path) -> None:
    provider, model = _resolve_manager_llm(tmp_path)
    assert provider == "openrouter_free"
    assert model == "nvidia/nemotron-3-super-120b-a12b"


def test_resolve_manager_llm_reads_seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Stub parse_seed so the test does not depend on the full bernstein.yaml
    # schema; only the two attributes the helper reads matter.
    from types import SimpleNamespace

    seed_path = tmp_path / "bernstein.yaml"
    seed_path.write_text("goal: test\n", encoding="utf-8")

    monkeypatch.setattr(
        "bernstein.core.seed.parse_seed",
        lambda _p: SimpleNamespace(
            internal_llm_provider="anthropic",
            internal_llm_model="claude-3-5-haiku",
        ),
    )
    provider, model = _resolve_manager_llm(tmp_path)
    assert provider == "anthropic"
    assert model == "claude-3-5-haiku"


def test_resolve_manager_llm_falls_back_on_parse_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seed_path = tmp_path / "bernstein.yaml"
    seed_path.write_text("not: valid: yaml:\n", encoding="utf-8")

    def _boom(_p: Path) -> Any:
        raise ValueError("bad seed")

    monkeypatch.setattr("bernstein.core.seed.parse_seed", _boom)
    provider, model = _resolve_manager_llm(tmp_path)
    # Exception is suppressed; defaults are returned.
    assert provider == "openrouter_free"
    assert model == "nvidia/nemotron-3-super-120b-a12b"
