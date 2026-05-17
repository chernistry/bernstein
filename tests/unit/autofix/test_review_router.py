"""Unit tests for the PR review-comment routing primitive."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from bernstein.core.autofix.review_router import (
    GhInvocationError,
    ReviewRouter,
    ReviewTask,
    emit_jsonl,
    make_list_sink,
    parse_review_threads,
    poll_loop,
    resolve_pr_number,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _gh_payload(
    *,
    threads: list[dict[str, Any]] | None = None,
    reviews: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a ``gh pr view --json reviewThreads,reviews`` payload."""
    return {
        "reviewThreads": threads if threads is not None else [],
        "reviews": reviews if reviews is not None else [],
    }


def _thread(
    *,
    thread_id: str,
    path: str = "src/foo.py",
    line: int = 12,
    start_line: int | None = None,
    is_resolved: bool = False,
    comments: list[dict[str, Any]] | None = None,
    diff_hunk: str = "",
) -> dict[str, Any]:
    """Build a single review-thread record."""
    return {
        "id": thread_id,
        "isResolved": is_resolved,
        "path": path,
        "line": line,
        "startLine": start_line,
        "diffHunk": diff_hunk,
        "comments": {"nodes": comments or []},
    }


def _comment(
    *,
    comment_id: str,
    login: str = "alice",
    body: str = "fix me",
    diff_hunk: str = "",
    url: str = "https://github.com/o/r/pull/1#discussion_r1",
) -> dict[str, Any]:
    return {
        "id": comment_id,
        "author": {"login": login},
        "body": body,
        "diffHunk": diff_hunk,
        "url": url,
    }


def _review(login: str, state: str) -> dict[str, Any]:
    return {"author": {"login": login}, "state": state}


@dataclass
class _GhSpy:
    """Stub gh runner that returns canned payloads and records argv."""

    payloads: list[dict[str, Any] | str | Exception]
    captured: list[list[str]] = field(default_factory=list)

    def __call__(self, argv: list[str]) -> str:
        self.captured.append(list(argv))
        if not self.payloads:
            raise AssertionError("gh runner called more times than payloads provided")
        item = self.payloads.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, str):
            return item
        return json.dumps(item)


# ---------------------------------------------------------------------------
# parse_review_threads
# ---------------------------------------------------------------------------


def test_parse_review_threads_emits_one_task_per_changes_comment() -> None:
    payload = _gh_payload(
        threads=[
            _thread(
                thread_id="T1",
                path="src/foo.py",
                line=10,
                start_line=8,
                comments=[
                    _comment(comment_id="C1", login="alice", body="please rename"),
                    _comment(comment_id="C2", login="alice", body="and add a test"),
                ],
            ),
        ],
        reviews=[_review("alice", "CHANGES_REQUESTED")],
    )

    tasks = parse_review_threads(payload, pr_number=42)

    assert [t.comment_id for t in tasks] == ["C1", "C2"]
    first = tasks[0]
    assert first.pr_number == 42
    assert first.thread_id == "T1"
    assert first.reviewer == "alice"
    assert first.verdict == "CHANGES_REQUESTED"
    assert first.path == "src/foo.py"
    assert first.line_start == 8
    assert first.line_end == 10
    assert first.body == "please rename"


def test_parse_review_threads_skips_resolved_threads() -> None:
    payload = _gh_payload(
        threads=[
            _thread(
                thread_id="T1",
                is_resolved=True,
                comments=[_comment(comment_id="C1", login="alice")],
            ),
        ],
        reviews=[_review("alice", "CHANGES_REQUESTED")],
    )

    assert parse_review_threads(payload, pr_number=1) == []


def test_parse_review_threads_skips_approving_reviewers() -> None:
    payload = _gh_payload(
        threads=[
            _thread(
                thread_id="T1",
                comments=[_comment(comment_id="C1", login="alice")],
            ),
        ],
        reviews=[_review("alice", "APPROVED")],
    )

    assert parse_review_threads(payload, pr_number=1) == []


def test_parse_review_threads_uses_latest_review_per_login() -> None:
    """A reviewer who later approves should not produce tasks."""
    payload = _gh_payload(
        threads=[
            _thread(
                thread_id="T1",
                comments=[_comment(comment_id="C1", login="alice")],
            ),
        ],
        reviews=[
            _review("alice", "CHANGES_REQUESTED"),
            _review("alice", "APPROVED"),
        ],
    )

    assert parse_review_threads(payload, pr_number=1) == []


def test_parse_review_threads_falls_back_to_thread_diff_hunk() -> None:
    payload = _gh_payload(
        threads=[
            _thread(
                thread_id="T1",
                diff_hunk="@@ -1 +1 @@\n-old\n+new",
                comments=[_comment(comment_id="C1", login="alice", diff_hunk="")],
            ),
        ],
        reviews=[_review("alice", "CHANGES_REQUESTED")],
    )

    tasks = parse_review_threads(payload, pr_number=1)
    assert tasks[0].diff_hunk == "@@ -1 +1 @@\n-old\n+new"


def test_parse_review_threads_handles_missing_fields() -> None:
    """Missing ``reviews`` / malformed comments must not crash the parser."""
    payload: dict[str, Any] = {"reviewThreads": [{"id": "T1"}]}
    assert parse_review_threads(payload, pr_number=1) == []


# ---------------------------------------------------------------------------
# ReviewRouter.poll_once — dedup, sink, gh argv
# ---------------------------------------------------------------------------


def test_poll_once_emits_to_sink_and_dedupes_across_polls() -> None:
    payload_first = _gh_payload(
        threads=[
            _thread(
                thread_id="T1",
                comments=[_comment(comment_id="C1", login="alice")],
            ),
        ],
        reviews=[_review("alice", "CHANGES_REQUESTED")],
    )
    payload_second = _gh_payload(
        threads=[
            _thread(
                thread_id="T1",
                comments=[
                    _comment(comment_id="C1", login="alice"),
                    _comment(comment_id="C2", login="alice", body="and another"),
                ],
            ),
        ],
        reviews=[_review("alice", "CHANGES_REQUESTED")],
    )
    spy = _GhSpy(payloads=[payload_first, payload_second])
    sink_target: list[ReviewTask] = []

    router = ReviewRouter(
        pr_number=99,
        task_sink=make_list_sink(sink_target),
        gh_runner=spy,
        repo="owner/repo",
    )

    first = router.poll_once()
    second = router.poll_once()

    assert [t.comment_id for t in first.tasks] == ["C1"]
    assert [t.comment_id for t in second.tasks] == ["C2"]
    assert second.skipped_seen == 1
    assert [t.comment_id for t in sink_target] == ["C1", "C2"]
    # Both calls must include --repo and the PR number.
    for argv in spy.captured:
        assert argv[:4] == ["gh", "pr", "view", "99"]
        assert "--repo" in argv
        assert "owner/repo" in argv
        assert "--json" in argv


def test_poll_once_omits_repo_flag_when_unset() -> None:
    spy = _GhSpy(payloads=[_gh_payload()])
    router = ReviewRouter(
        pr_number=12,
        task_sink=make_list_sink([]),
        gh_runner=spy,
    )

    router.poll_once()

    assert "--repo" not in spy.captured[0]


def test_poll_once_pre_seeded_dedup_set_skips_known_comment() -> None:
    payload = _gh_payload(
        threads=[
            _thread(
                thread_id="T1",
                comments=[_comment(comment_id="C1", login="alice")],
            ),
        ],
        reviews=[_review("alice", "CHANGES_REQUESTED")],
    )
    spy = _GhSpy(payloads=[payload])
    sink_target: list[ReviewTask] = []
    router = ReviewRouter(
        pr_number=1,
        task_sink=make_list_sink(sink_target),
        gh_runner=spy,
        seen_comment_ids={"C1"},
    )

    outcome = router.poll_once()

    assert outcome.tasks == ()
    assert outcome.skipped_seen == 1
    assert sink_target == []


def test_poll_once_raises_on_non_json_output() -> None:
    spy = _GhSpy(payloads=["not json"])
    router = ReviewRouter(
        pr_number=1,
        task_sink=make_list_sink([]),
        gh_runner=spy,
    )

    with pytest.raises(GhInvocationError):
        router.poll_once()


# ---------------------------------------------------------------------------
# poll_loop
# ---------------------------------------------------------------------------


def test_poll_loop_runs_iterations_then_returns() -> None:
    spy = _GhSpy(payloads=[_gh_payload(), _gh_payload(), _gh_payload()])
    router = ReviewRouter(
        pr_number=1,
        task_sink=make_list_sink([]),
        gh_runner=spy,
    )
    sleeps: list[float] = []

    polls = poll_loop(
        router,
        poll_seconds=5.0,
        iterations=3,
        sleep_fn=sleeps.append,
    )

    assert polls == 3
    # Sleep is called between polls *and* after the final one in this
    # implementation; we just assert it never blocks for real.
    assert all(s == 5.0 for s in sleeps)


def test_poll_loop_swallows_gh_errors_and_keeps_polling() -> None:
    spy = _GhSpy(payloads=[GhInvocationError("boom"), _gh_payload()])
    router = ReviewRouter(
        pr_number=1,
        task_sink=make_list_sink([]),
        gh_runner=spy,
    )
    polls = poll_loop(
        router,
        poll_seconds=0.01,
        iterations=2,
        sleep_fn=lambda _s: None,
    )
    assert polls == 2


def test_poll_loop_rejects_nonpositive_interval() -> None:
    router = ReviewRouter(pr_number=1, task_sink=make_list_sink([]), gh_runner=_GhSpy([]))
    with pytest.raises(ValueError):
        poll_loop(router, poll_seconds=0.0, iterations=1, sleep_fn=lambda _s: None)


# ---------------------------------------------------------------------------
# emit_jsonl
# ---------------------------------------------------------------------------


def test_emit_jsonl_appends_one_line_per_task(tmp_path: Path) -> None:
    target = tmp_path / "out" / "tasks.jsonl"
    sink = emit_jsonl(target)

    sink(
        ReviewTask(
            pr_number=10,
            thread_id="T1",
            comment_id="C1",
            reviewer="alice",
            verdict="CHANGES_REQUESTED",
            path="x.py",
            line_start=1,
            line_end=2,
            body="hello",
            diff_hunk="",
            url="https://example.invalid/c1",
        )
    )
    sink(
        ReviewTask(
            pr_number=10,
            thread_id="T1",
            comment_id="C2",
            reviewer="alice",
            verdict="CHANGES_REQUESTED",
            path="x.py",
            line_start=3,
            line_end=3,
            body="world",
            diff_hunk="",
            url="https://example.invalid/c2",
        )
    )

    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    payload = json.loads(lines[0])
    assert payload["kind"] == "review_comment"
    assert payload["comment_id"] == "C1"
    assert payload["body"] == "hello"


# ---------------------------------------------------------------------------
# resolve_pr_number
# ---------------------------------------------------------------------------


def test_resolve_pr_number_prefers_explicit_value() -> None:
    def _git_runner(_argv: list[str], _workdir: Path | None) -> str:
        raise AssertionError("git should not be invoked when --pr is given")

    assert (
        resolve_pr_number(
            explicit=7,
            environ={"BERNSTEIN_REVIEW_PR_NUMBER": "9"},
            git_runner=_git_runner,
        )
        == 7
    )


def test_resolve_pr_number_falls_back_to_environ() -> None:
    def _git_runner(_argv: list[str], _workdir: Path | None) -> str:
        raise AssertionError("git should not be invoked when env var is set")

    assert (
        resolve_pr_number(
            explicit=None,
            environ={"BERNSTEIN_REVIEW_PR_NUMBER": "21"},
            git_runner=_git_runner,
        )
        == 21
    )


def test_resolve_pr_number_falls_back_to_git_config() -> None:
    captured: list[list[str]] = []

    def _git_runner(argv: list[str], _workdir: Path | None) -> str:
        captured.append(list(argv))
        return "33\n"

    assert (
        resolve_pr_number(
            explicit=None,
            environ={},
            git_runner=_git_runner,
        )
        == 33
    )
    assert captured[0] == ["git", "config", "--get", "bernstein.spawn-pr"]


def test_resolve_pr_number_returns_none_when_git_config_missing() -> None:
    def _git_runner(_argv: list[str], _workdir: Path | None) -> str:
        raise subprocess.CalledProcessError(1, _argv)

    assert (
        resolve_pr_number(
            explicit=None,
            environ={},
            git_runner=_git_runner,
        )
        is None
    )


def test_resolve_pr_number_ignores_non_integer_env() -> None:
    def _git_runner(_argv: list[str], _workdir: Path | None) -> str:
        return "55\n"

    assert (
        resolve_pr_number(
            explicit=None,
            environ={"BERNSTEIN_REVIEW_PR_NUMBER": "not-a-number"},
            git_runner=_git_runner,
        )
        == 55
    )
