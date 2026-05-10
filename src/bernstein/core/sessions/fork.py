"""Fork a recorded session into a sibling git worktree (smallest viable slice).

The ``fork_session`` function clones the *task-state* of a parent
:class:`~bernstein.core.orchestration.run_session.RunSession` into a
freshly-allocated session id, materialises a sibling git worktree
branched from the parent's current commit, and writes the snapshot into
the fork worktree's session directory.

This is the snapshot-based slice of GH-1222.  It does **not** attempt to
pause-and-fork a live agent process, replicate streaming conversation
state, or auto-merge fork results — those are deferred follow-ups.

Usage::

    fork = fork_session(
        parent_session_id="20260510-120000-abcdef",
        fork_label="alternate-path",
        repo_root=Path("/path/to/repo"),
    )
    print(fork.fork_worktree)
    print(fork.fork_branch)

Why ``repo_root`` is explicit: the function refuses to guess the
repository when the caller is itself running inside a worktree, since
nesting fork worktrees inside an agent worktree silently breaks
isolation guarantees the rest of the codebase relies on (T481, T580).
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from bernstein.core.orchestration.run_session import RunSession, sessions_dir_for
from bernstein.core.persistence.atomic_write import write_atomic_json

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

# Worktree base directory (mirrors ``WorktreeManager._WORKTREE_BASE``).
_WORKTREE_BASE_REL = Path(".sdd/worktrees")

# Slugify pattern for the optional fork label — keep it tight so the
# resulting branch and directory names stay shell- and git-safe.
_LABEL_SLUG_RE = re.compile(r"[^a-zA-Z0-9._-]+")
_LABEL_MAX_LEN = 32

# Branch convention for forks. Mirrors the lineage hint described in #1222
# so operators can read parent → child relationships from ``git branch``.
_FORK_BRANCH_PREFIX = "fork"


class SessionForkError(Exception):
    """Raised when a fork operation cannot complete safely."""


@dataclass(frozen=True)
class SessionFork:
    """Result of a successful :func:`fork_session` call.

    Attributes:
        parent_session_id: Source session identifier.
        fork_session_id: Newly-allocated session identifier for the fork.
        parent_branch: Git branch of the parent session (best-effort —
            may be empty when the parent was checked out in detached
            HEAD or when ``git symbolic-ref`` fails).
        fork_branch: Git branch created for the fork.
        parent_worktree: Filesystem path of the parent worktree
            (``repo_root`` when no per-session worktree exists).
        fork_worktree: Filesystem path of the new sibling worktree.
        snapshot_path: Path to the cloned session JSON inside
            ``fork_worktree``.
        fork_commit: Commit SHA the fork branched from.
    """

    parent_session_id: str
    fork_session_id: str
    parent_branch: str
    fork_branch: str
    parent_worktree: Path
    fork_worktree: Path
    snapshot_path: Path
    fork_commit: str

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-friendly representation (paths coerced to ``str``)."""
        raw = asdict(self)
        raw["parent_worktree"] = str(self.parent_worktree)
        raw["fork_worktree"] = str(self.fork_worktree)
        raw["snapshot_path"] = str(self.snapshot_path)
        return raw


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify_label(label: str) -> str:
    """Reduce *label* to a filesystem- and git-safe slug.

    Args:
        label: Free-text fork label provided by the caller.

    Returns:
        Slug containing only ``[a-zA-Z0-9._-]`` characters, trimmed of
        leading/trailing separators and capped at ``_LABEL_MAX_LEN``
        characters.
    """
    cleaned = _LABEL_SLUG_RE.sub("-", label).strip("-._")
    return cleaned[:_LABEL_MAX_LEN]


def _generate_fork_session_id(label_slug: str) -> str:
    """Generate a fork-flavoured session id with timestamp + random suffix.

    Args:
        label_slug: Pre-slugified label (may be empty).

    Returns:
        Identifier shaped like ``fork-<ts>-<hex>`` or
        ``fork-<label>-<ts>-<hex>``.
    """
    ts = time.strftime("%Y%m%d-%H%M%S")
    suffix = secrets.token_hex(3)
    if label_slug:
        return f"fork-{label_slug}-{ts}-{suffix}"
    return f"fork-{ts}-{suffix}"


def _resolve_session_worktree(
    repo_root: Path,
    parent_session_id: str,
    worktrees_map: Mapping[str, Path] | None,
) -> Path:
    """Return the worktree directory associated with ``parent_session_id``.

    The runtime convention (see :class:`WorktreeManager`) places per-session
    worktrees at ``<repo_root>/.sdd/worktrees/<session_id>``.  When that
    path exists we treat the parent as worktree-bound; otherwise we fall
    back to the main repo checkout, which is the common case for
    sessions recorded by ``bernstein run`` directly in the repo root.

    Args:
        repo_root: Absolute repository root.
        parent_session_id: Source session identifier.
        worktrees_map: Optional pre-resolved mapping of session id →
            worktree path (used by tests; production resolves via the
            filesystem convention).

    Returns:
        Existing directory for the parent worktree.
    """
    if worktrees_map and parent_session_id in worktrees_map:
        return worktrees_map[parent_session_id]
    candidate = repo_root / _WORKTREE_BASE_REL / parent_session_id
    if candidate.is_dir():
        return candidate
    return repo_root


def _git_head_in(path: Path) -> str:
    """Return the HEAD commit SHA visible from ``path`` or empty on failure."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.debug("git rev-parse failed in %s: %s", path, exc)
        return ""
    if result.returncode != 0:
        logger.debug("git rev-parse exited %d in %s: %s", result.returncode, path, result.stderr.strip())
        return ""
    return result.stdout.strip()


def _git_current_branch(path: Path) -> str:
    """Return the current branch of the worktree at ``path`` (empty on detached HEAD)."""
    try:
        result = subprocess.run(
            ["git", "symbolic-ref", "--short", "HEAD"],
            cwd=path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.debug("git symbolic-ref failed in %s: %s", path, exc)
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _build_fork_branch_name(parent_branch: str, fork_session_id: str) -> str:
    """Construct a git branch name for the fork.

    The branch name encodes both lineage (parent suffix when available)
    and the fork session id so it is greppable from ``git branch``.

    Args:
        parent_branch: Parent branch name (may be empty).
        fork_session_id: Newly-generated fork session id.

    Returns:
        Git-safe branch name.
    """
    base = parent_branch.replace("/", "-") if parent_branch else "session"
    return f"{_FORK_BRANCH_PREFIX}/{base}/{fork_session_id}"


def _clone_session_snapshot(
    parent_session: RunSession,
    fork_session_id: str,
    target_sessions_dir: Path,
    *,
    fork_label: str,
    parent_session_id: str,
    fork_branch: str,
    fork_commit: str,
) -> Path:
    """Write the parent session snapshot into the fork worktree.

    The snapshot keeps the *exact* task list (including ``status`` so
    in-progress tasks remain in-progress in the fork) and stamps fork
    lineage metadata under ``fork``.  We deliberately do **not** mutate
    ``run_seed`` or ``goal`` — operators want the fork to start from
    the same planning context.

    Args:
        parent_session: Loaded parent session.
        fork_session_id: New session id for the fork.
        target_sessions_dir: Sessions directory inside the fork
            worktree.
        fork_label: Original (unsanitised) label for audit purposes.
        parent_session_id: Source session id (preserved for lineage).
        fork_branch: Git branch the fork was created on.
        fork_commit: Commit the fork branched from.

    Returns:
        Filesystem path of the written snapshot JSON.
    """
    target_sessions_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = target_sessions_dir / f"{fork_session_id}.json"

    payload: dict[str, object] = {
        "session_id": fork_session_id,
        "goal": parent_session.goal,
        "run_seed": parent_session.run_seed,
        "tasks": parent_session.tasks,
        "routing_decisions": parent_session.routing_decisions,
        "git_sha": fork_commit or parent_session.git_sha,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "bernstein_version": parent_session.bernstein_version,
        "fork": {
            "parent_session_id": parent_session_id,
            "label": fork_label,
            "branch": fork_branch,
            "branched_from_commit": fork_commit,
        },
    }
    write_atomic_json(snapshot_path, payload, indent=2)
    return snapshot_path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fork_session(
    parent_session_id: str,
    fork_label: str = "",
    *,
    repo_root: Path | None = None,
    worktrees_map: Mapping[str, Path] | None = None,
) -> SessionFork:
    """Fork a recorded session into a sibling git worktree.

    Steps:

    1. Load the parent session JSON from ``<repo_root>/.sdd/runtime/sessions``.
    2. Resolve the parent's worktree (per-session if it exists, otherwise
       ``repo_root``) and capture its current commit + branch.
    3. Allocate a fresh fork session id and a sibling worktree path under
       ``<repo_root>/.sdd/worktrees/<fork_session_id>``.
    4. Run ``git worktree add -b <fork_branch>`` so the fork starts from
       the parent's current commit.
    5. Clone the parent session JSON into the fork worktree's sessions
       directory, preserving every task and its current ``status`` so
       in-progress tasks remain in-progress in the fork.

    The function fails fast (no partial state) when the parent session
    cannot be loaded, the fork worktree path already exists, or the git
    worktree command fails.  No cleanup of partial state is required
    because the fork worktree is the only side-effect and it is created
    as the very last step.

    Args:
        parent_session_id: Identifier of the source session.
        fork_label: Optional human-readable label that becomes part of
            the fork session id and branch name.
        repo_root: Repository root.  Defaults to ``Path.cwd()``.
        worktrees_map: Optional explicit mapping of session id →
            worktree path.  Useful for tests; production discovers
            worktrees via the filesystem convention.

    Returns:
        Populated :class:`SessionFork` describing the new fork.

    Raises:
        SessionForkError: When the parent session cannot be loaded or
            the worktree creation fails.
    """
    if not parent_session_id:
        raise SessionForkError("parent_session_id must not be empty")

    repo_root = (repo_root or Path.cwd()).resolve()
    if not repo_root.is_dir():
        raise SessionForkError(f"repo_root does not exist: {repo_root}")

    label_slug = _slugify_label(fork_label)
    fork_session_id = _generate_fork_session_id(label_slug)

    sessions_dir = sessions_dir_for(repo_root)
    try:
        parent_session = RunSession.load(sessions_dir, parent_session_id)
    except FileNotFoundError as exc:
        raise SessionForkError(f"parent session not found: {parent_session_id}") from exc
    except ValueError as exc:
        raise SessionForkError(f"parent session unreadable: {exc}") from exc

    parent_worktree = _resolve_session_worktree(repo_root, parent_session_id, worktrees_map)
    fork_commit = _git_head_in(parent_worktree)
    if not fork_commit:
        raise SessionForkError(
            f"could not resolve git HEAD for parent worktree {parent_worktree}; "
            "is this a git repository?"
        )

    parent_branch = _git_current_branch(parent_worktree)
    fork_branch = _build_fork_branch_name(parent_branch, fork_session_id)

    fork_worktree = repo_root / _WORKTREE_BASE_REL / fork_session_id
    if fork_worktree.exists():
        raise SessionForkError(f"fork worktree path already exists: {fork_worktree}")
    fork_worktree.parent.mkdir(parents=True, exist_ok=True)

    # Branch directly from the parent's current commit so the fork is
    # bit-identical at creation time.  Using the commit SHA (rather than
    # parent_branch) avoids race conditions with a still-running parent
    # that could advance the branch between read and worktree-add.
    result = worktree_add_from_commit(
        repo_root=repo_root,
        path=fork_worktree,
        branch=fork_branch,
        commit=fork_commit,
    )
    if not result.ok:
        stderr = (result.stderr or "").strip()
        raise SessionForkError(
            f"git worktree add failed for fork '{fork_session_id}': {stderr or result.stdout!r}"
        )

    snapshot_path = _clone_session_snapshot(
        parent_session=parent_session,
        fork_session_id=fork_session_id,
        target_sessions_dir=sessions_dir_for(fork_worktree),
        fork_label=fork_label,
        parent_session_id=parent_session_id,
        fork_branch=fork_branch,
        fork_commit=fork_commit,
    )

    fork = SessionFork(
        parent_session_id=parent_session_id,
        fork_session_id=fork_session_id,
        parent_branch=parent_branch,
        fork_branch=fork_branch,
        parent_worktree=parent_worktree,
        fork_worktree=fork_worktree,
        snapshot_path=snapshot_path,
        fork_commit=fork_commit,
    )
    logger.info(
        "Forked session %s -> %s (branch=%s commit=%s)",
        parent_session_id,
        fork_session_id,
        fork_branch,
        fork_commit[:12],
    )
    return fork


# ---------------------------------------------------------------------------
# Thin git wrappers
# ---------------------------------------------------------------------------


def worktree_add_from_commit(
    repo_root: Path,
    path: Path,
    branch: str,
    commit: str,
):  # type: ignore[no-untyped-def]  # GitResult typing kept lazy to avoid circular imports
    """Create a worktree at ``path`` on a new branch starting from ``commit``.

    The shared :func:`bernstein.core.git.git_pr.worktree_add` helper
    starts the branch from the current HEAD; for fork semantics we need
    to pin the start point to the parent's commit explicitly so a
    racing parent cannot influence the fork's base.

    Args:
        repo_root: Repository root.
        path: Filesystem path for the new worktree.
        branch: New branch name.
        commit: Start commit (full SHA preferred).

    Returns:
        ``GitResult`` from :func:`bernstein.core.git.git_basic.run_git`.
    """
    from bernstein.core.git.git_basic import run_git

    return run_git(
        ["worktree", "add", str(path), "-b", branch, commit],
        repo_root,
        timeout=30,
    )


def result_ok(result: object) -> bool:
    """Return whether a ``GitResult``-like object has ``returncode == 0``.

    Tests substitute a lightweight stub for ``run_git``; tolerating an
    object with just ``returncode`` keeps the contract loose without
    importing the concrete dataclass at module load time.
    """
    return bool(getattr(result, "ok", getattr(result, "returncode", 1) == 0))


# Public re-exports — keep ``worktree_add`` referenced so the import is
# not pruned by unused-import linters when only the helper is consumed
# in production.
_ = worktree_add

__all__ = [
    "SessionFork",
    "SessionForkError",
    "fork_session",
    "worktree_add_from_commit",
]
