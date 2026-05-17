"""Distinguish flaky tests from genuine fails using commit-history signal.

A test name is judged ``flaky`` when it has failed on N >= 3 commits
where the failing commits are non-adjacent (i.e. there is at least one
passing or unrelated commit between any pair of failure events) AND
the diffs around the failures are unrelated to the test under
discussion.

The heuristic is the pass-fail-pass / non-adjacent flake signal from
the CI flaky-test literature: a genuine bug typically causes a run of
consecutive failures on adjacent commits; a flake fires sporadically.

Inputs
------
``observe()`` accepts a small history of ``(commit_sha, failed)`` tuples
for a single test name. ``classify`` returns one of:

* ``"flake"``  - retry recommended, do not patch.
* ``"real"``   - patch is appropriate.
* ``"unknown"``- not enough data; default to ``"real"`` to favour
                  patching (operator can still review).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Iterable

FlakeJudgement = Literal["flake", "real", "unknown"]


@dataclass(frozen=True, slots=True)
class TestHistoryPoint:
    """One observation of a single test on a single commit.

    ``commit_index`` is a monotonically increasing position (0 = oldest
    in the window). ``failed`` is ``True`` if the test failed on that
    commit.

    The ``__test__`` attribute is set to keep pytest's collector from
    treating the class name (which starts with ``Test``) as a test
    case to instantiate.
    """

    __test__ = False

    commit_index: int
    failed: bool


def classify(history: Iterable[TestHistoryPoint]) -> FlakeJudgement:
    """Classify the failure pattern over a sliding window.

    Rules
    -----
    * Fewer than 5 observations -> ``unknown``.
    * 3+ failures AND at least one ``(fail, pass, fail)`` non-adjacent
      pattern -> ``flake``.
    * 3+ failures on strictly adjacent commits -> ``real``.
    * Anything else -> ``real`` (default to safer side: patch + review).
    """
    pts = sorted(history, key=lambda p: p.commit_index)
    if len(pts) < 5:
        return "unknown"

    failures = [p.commit_index for p in pts if p.failed]
    if len(failures) < 3:
        return "real"

    # Detect a non-adjacent failure triple.
    # We slide a window of three failure positions and check that at
    # least one pair has a passing commit between them.
    pass_indices = {p.commit_index for p in pts if not p.failed}
    for i in range(len(failures) - 2):
        a, _b, c = failures[i], failures[i + 1], failures[i + 2]
        # If between a and c there is any passing commit, the triple is
        # non-adjacent.
        if any(idx in pass_indices for idx in range(a + 1, c)):
            return "flake"

    return "real"


__all__ = [
    "FlakeJudgement",
    "TestHistoryPoint",
    "classify",
]
