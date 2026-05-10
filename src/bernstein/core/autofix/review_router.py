"""PR review-comment polling primitive.

This module implements the smallest viable slice of GitHub PR review-comment
routing: poll a single PR via ``gh pr view --json reviewThreads,reviews``,
diff the threads against a previously-seen set of comment ids, and emit a
:class:`ReviewTask` for every newly-discovered comment that belongs to a
"changes requested" review.

The primitive is intentionally narrow:

* One PR per :class:`ReviewRouter` instance — multi-PR fan-out is deferred
  to a follow-up that owns scheduling and rate-limit budgets.
* No webhook surface — callers drive :meth:`ReviewRouter.poll_once` on a
  cadence they control.
* No re-spawn / dispatcher integration — the router only emits structured
  tasks via an injected ``task_sink`` callable.  Wiring those tasks into
  the autofix dispatcher (audit chain, cost caps, attempt counter) lands
  in a follow-up.

The module deliberately mirrors the shape of
:mod:`bernstein.core.autofix.daemon` so the follow-up can lift the loop
into the existing supervisor without reshaping any public types.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReviewTask:
    """One structured task emitted to the spawning agent's queue.

    Attributes:
        pr_number: GitHub PR number the comment belongs to.
        thread_id: Stable GitHub review-thread id.
        comment_id: Stable GitHub review-comment id; used for dedup.
        reviewer: GitHub login of the reviewer who left the comment.
        verdict: Reviewer verdict that produced the task; always
            ``"CHANGES_REQUESTED"`` in this slice.
        path: Repository path of the file the comment is anchored to,
            or ``""`` when GitHub did not return one.
        line_start: First diff line covered by the comment, or ``0``.
        line_end: Last diff line covered by the comment, or ``0``.
        body: Raw comment body as authored by the reviewer.
        diff_hunk: Diff hunk GitHub displays under the thread, when
            available.  Empty string otherwise.
        url: Permalink to the comment on github.com.
    """

    pr_number: int
    thread_id: str
    comment_id: str
    reviewer: str
    verdict: str
    path: str
    line_start: int
    line_end: int
    body: str
    diff_hunk: str
    url: str

    def to_payload(self) -> dict[str, Any]:
        """Return a JSON-serialisable mapping suitable for queue sinks."""
        return {
            "kind": "review_comment",
            "pr_number": self.pr_number,
            "thread_id": self.thread_id,
            "comment_id": self.comment_id,
            "reviewer": self.reviewer,
            "verdict": self.verdict,
            "path": self.path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "body": self.body,
            "diff_hunk": self.diff_hunk,
            "url": self.url,
        }


@dataclass(frozen=True)
class PollResult:
    """Outcome of a single :meth:`ReviewRouter.poll_once` invocation.

    Attributes:
        tasks: Newly-emitted tasks (already passed to ``task_sink``).
        skipped_seen: Number of comments skipped because their id was
            already in the seen-set.
        skipped_non_changes: Number of comments skipped because the
            owning review was not a ``CHANGES_REQUESTED`` verdict.
    """

    tasks: tuple[ReviewTask, ...] = field(default_factory=tuple)
    skipped_seen: int = 0
    skipped_non_changes: int = 0


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------


GhRunner = Callable[[list[str]], str]
"""Callable signature for invoking ``gh``.  Returns stdout as text."""


class GhInvocationError(RuntimeError):
    """Raised when ``gh pr view`` fails or emits non-JSON output."""


def _default_gh_runner(argv: list[str]) -> str:
    """Run ``gh`` with the supplied argv and return stdout text.

    Args:
        argv: Full argv (must start with the ``gh`` binary name).

    Returns:
        The captured stdout, decoded as UTF-8.

    Raises:
        GhInvocationError: When ``gh`` exits non-zero or cannot be
            located on ``$PATH``.
    """
    try:
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise GhInvocationError(f"gh binary not on PATH: {exc}") from exc
    if completed.returncode != 0:
        raise GhInvocationError(f"gh exited {completed.returncode}: {completed.stderr.strip() or 'no stderr'}")
    return completed.stdout


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


_CHANGES_REQUESTED = "CHANGES_REQUESTED"


def _coerce_int(value: object, default: int = 0) -> int:
    """Best-effort int conversion that survives missing/null fields."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return default


def _str_field(record: dict[str, Any], key: str) -> str:
    """Return ``record[key]`` as a string, falling back to ``""``."""
    value = record.get(key)
    return value if isinstance(value, str) else ""


def _changes_requested_logins(reviews: object) -> set[str]:
    """Return the set of reviewer logins whose latest verdict is changes-requested.

    GitHub returns one review record per submission; a reviewer who first
    requested changes and later approved should not produce review tasks.
    The function therefore walks the list in submission order and keeps
    the *latest* verdict per login.
    """
    if not isinstance(reviews, list):
        return set()
    latest: dict[str, str] = {}
    for entry in reviews:
        if not isinstance(entry, dict):
            continue
        login = ""
        author = entry.get("author")
        if isinstance(author, dict):
            login = _str_field(author, "login")
        if not login:
            continue
        state = _str_field(entry, "state").upper()
        if state:
            latest[login] = state
    return {login for login, state in latest.items() if state == _CHANGES_REQUESTED}


def parse_review_threads(
    payload: dict[str, Any],
    *,
    pr_number: int,
) -> list[ReviewTask]:
    """Translate a ``gh pr view --json reviewThreads,reviews`` payload.

    The function returns one :class:`ReviewTask` per comment that:

    * lives on a non-resolved review thread;
    * was authored by a reviewer whose most-recent review verdict is
      ``CHANGES_REQUESTED``.

    Resolved threads and threads from approving / commenting reviews are
    skipped silently — they would only produce noise for the spawning
    agent.

    Args:
        payload: Decoded JSON returned by ``gh pr view``.
        pr_number: PR number to stamp onto every task.

    Returns:
        Tasks in the order GitHub returned them.  Callers should
        deduplicate by :attr:`ReviewTask.comment_id`.
    """
    threads = payload.get("reviewThreads")
    if not isinstance(threads, list):
        return []
    changes_logins = _changes_requested_logins(payload.get("reviews"))

    tasks: list[ReviewTask] = []
    for thread in threads:
        if not isinstance(thread, dict):
            continue
        if thread.get("isResolved") is True:
            continue
        thread_id = _str_field(thread, "id")
        path = _str_field(thread, "path")
        line_start = _coerce_int(thread.get("startLine") or thread.get("line"))
        line_end = _coerce_int(thread.get("line") or thread.get("startLine"))

        comments = thread.get("comments")
        if isinstance(comments, dict):
            comments = comments.get("nodes")
        if not isinstance(comments, list):
            continue

        for comment in comments:
            if not isinstance(comment, dict):
                continue
            author = comment.get("author")
            login = _str_field(author, "login") if isinstance(author, dict) else ""
            if not login or login not in changes_logins:
                continue
            tasks.append(
                ReviewTask(
                    pr_number=pr_number,
                    thread_id=thread_id,
                    comment_id=_str_field(comment, "id"),
                    reviewer=login,
                    verdict=_CHANGES_REQUESTED,
                    path=path,
                    line_start=line_start,
                    line_end=line_end,
                    body=_str_field(comment, "body"),
                    diff_hunk=_str_field(comment, "diffHunk") or _str_field(thread, "diffHunk"),
                    url=_str_field(comment, "url"),
                )
            )
    return tasks


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


TaskSink = Callable[[ReviewTask], None]
"""Callable that consumes a structured review task."""


@dataclass
class ReviewRouter:
    """Polling primitive for a single PR.

    Attributes:
        pr_number: PR to watch.
        task_sink: Callable invoked once per *new* task.  Errors raised
            by the sink propagate out of :meth:`poll_once` so callers
            can decide how to handle dead consumers.
        gh_runner: Override for the ``gh`` subprocess invocation.
            Tests inject a stub that returns canned JSON.
        repo: Optional ``owner/name`` slug forwarded to
            ``gh pr view --repo``.  When empty, ``gh`` resolves the
            repo from the current working tree.
        seen_comment_ids: Set of comment ids already emitted; updated in
            place by :meth:`poll_once` so consecutive polls do not
            re-emit.  Pre-populating the set lets a daemon resume after
            a restart.
    """

    pr_number: int
    task_sink: TaskSink
    gh_runner: GhRunner = field(default=_default_gh_runner)
    repo: str = ""
    seen_comment_ids: set[str] = field(default_factory=set)

    def poll_once(self) -> PollResult:
        """Run one poll cycle and emit tasks for new comments.

        Returns:
            A populated :class:`PollResult`.
        """
        payload = self._fetch_payload()
        candidates = parse_review_threads(payload, pr_number=self.pr_number)
        emitted: list[ReviewTask] = []
        skipped_seen = 0
        for task in candidates:
            if not task.comment_id:
                # Comments without ids cannot be deduped; skip rather
                # than risk unbounded re-emission.
                continue
            if task.comment_id in self.seen_comment_ids:
                skipped_seen += 1
                continue
            self.seen_comment_ids.add(task.comment_id)
            self.task_sink(task)
            emitted.append(task)

        # All non-CHANGES_REQUESTED reviews are filtered inside
        # ``parse_review_threads`` so any "skipped_non_changes" count
        # would require re-walking the payload; surface zero for now
        # and let a follow-up enrich the diagnostics.
        return PollResult(tasks=tuple(emitted), skipped_seen=skipped_seen)

    def _fetch_payload(self) -> dict[str, Any]:
        """Invoke ``gh pr view`` and return the decoded JSON payload."""
        argv: list[str] = ["gh", "pr", "view", str(self.pr_number)]
        if self.repo:
            argv.extend(["--repo", self.repo])
        argv.extend(["--json", "reviewThreads,reviews"])
        raw = self.gh_runner(argv)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GhInvocationError(f"gh returned non-JSON output: {exc}") from exc
        if not isinstance(payload, dict):
            raise GhInvocationError("gh returned a non-object JSON payload")
        return payload


# ---------------------------------------------------------------------------
# Loop driver
# ---------------------------------------------------------------------------


def poll_loop(
    router: ReviewRouter,
    *,
    poll_seconds: float,
    iterations: int | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> int:
    """Drive ``router.poll_once`` on a fixed cadence.

    Args:
        router: Configured :class:`ReviewRouter` instance.
        poll_seconds: Seconds to sleep between polls.  Must be > 0.
        iterations: When set, run that many polls then return.  ``None``
            means "loop forever" — the production behaviour.
        sleep_fn: Callable matching :func:`time.sleep`; tests inject a
            no-op.

    Returns:
        Total number of polls executed.

    Raises:
        ValueError: If ``poll_seconds`` is not strictly positive.
    """
    if poll_seconds <= 0:
        raise ValueError("poll_seconds must be > 0")

    polls = 0
    while True:
        try:
            router.poll_once()
        except GhInvocationError:
            logger.exception("review_router: gh invocation failed for PR #%d", router.pr_number)
        except Exception:
            logger.exception(
                "review_router: poll_once raised for PR #%d",
                router.pr_number,
            )
        polls += 1
        if iterations is not None and polls >= iterations:
            return polls
        sleep_fn(poll_seconds)


# ---------------------------------------------------------------------------
# PR resolution
# ---------------------------------------------------------------------------


_GIT_CONFIG_KEY = "bernstein.spawn-pr"
_ENV_VAR = "BERNSTEIN_REVIEW_PR_NUMBER"


def resolve_pr_number(
    *,
    explicit: int | None = None,
    workdir: Path | None = None,
    git_runner: Callable[[list[str], Path | None], str] | None = None,
    environ: dict[str, str] | None = None,
) -> int | None:
    """Resolve which PR the router should watch.

    Resolution order:

    1. ``explicit`` argument (CLI ``--pr``).
    2. Environment variable ``BERNSTEIN_REVIEW_PR_NUMBER``.
    3. ``git config bernstein.spawn-pr`` inside ``workdir``.

    Args:
        explicit: PR number passed on the CLI.
        workdir: Project root used for the ``git config`` lookup.
            Defaults to the current working directory.
        git_runner: Override for ``git`` subprocess calls; tests inject
            a stub.  Returns stdout as text or raises
            ``subprocess.CalledProcessError``.
        environ: Override for ``os.environ`` lookups.

    Returns:
        The resolved PR number, or ``None`` when none of the sources
        produced a non-empty value.
    """
    if explicit is not None:
        return explicit

    env = environ if environ is not None else dict(os.environ)
    raw_env = env.get(_ENV_VAR, "").strip()
    if raw_env:
        try:
            return int(raw_env)
        except ValueError:
            logger.warning(
                "review_router: %s='%s' is not an integer; ignoring",
                _ENV_VAR,
                raw_env,
            )

    runner = git_runner if git_runner is not None else _default_git_runner
    try:
        raw = runner(["git", "config", "--get", _GIT_CONFIG_KEY], workdir).strip()
    except subprocess.CalledProcessError:
        return None
    except FileNotFoundError:
        return None
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "review_router: git config %s='%s' is not an integer; ignoring",
            _GIT_CONFIG_KEY,
            raw,
        )
        return None


def _default_git_runner(argv: list[str], workdir: Path | None) -> str:
    """Run ``git`` and return stdout; raise ``CalledProcessError`` on failure."""
    completed = subprocess.run(
        argv,
        check=True,
        capture_output=True,
        text=True,
        cwd=str(workdir) if workdir else None,
    )
    return completed.stdout


# ---------------------------------------------------------------------------
# Convenience: in-memory queue sink
# ---------------------------------------------------------------------------


def make_list_sink(target: list[ReviewTask]) -> TaskSink:
    """Return a :class:`TaskSink` that appends every task to ``target``.

    The helper is the smallest possible queue adapter — useful for the
    CLI ``--once`` / ``--poll`` modes where the spawning agent picks up
    tasks from an in-process list rather than a dedicated broker.
    """

    def _append(task: ReviewTask) -> None:
        target.append(task)

    return _append


def emit_jsonl(target_path: Path) -> TaskSink:
    """Return a :class:`TaskSink` that appends each task as JSONL.

    Args:
        target_path: File the sink writes to.  Parent directories are
            created on demand.

    Returns:
        Callable suitable for use as :attr:`ReviewRouter.task_sink`.
    """
    target_path.parent.mkdir(parents=True, exist_ok=True)

    def _append(task: ReviewTask) -> None:
        with target_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(task.to_payload(), sort_keys=True) + "\n")

    return _append


__all__ = [
    "GhInvocationError",
    "GhRunner",
    "PollResult",
    "ReviewRouter",
    "ReviewTask",
    "TaskSink",
    "emit_jsonl",
    "make_list_sink",
    "parse_review_threads",
    "poll_loop",
    "resolve_pr_number",
]
