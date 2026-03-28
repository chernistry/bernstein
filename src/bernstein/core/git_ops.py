"""Centralized git write operations for Bernstein.

Single source of truth for all git mutations — commit, push, merge, revert,
worktree lifecycle, and staging.  Every other module delegates here instead of
calling ``subprocess.run(["git", ...])`` directly.
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Paths that must NEVER be staged, even via explicit add.
_NEVER_STAGE: frozenset[str] = frozenset(
    {
        ".sdd/runtime/",
        ".sdd/metrics/",
        ".env",
        "*.pid",
        "*.log",
    }
)


@dataclass(frozen=True)
class GitResult:
    """Outcome of a single git command.

    Attributes:
        returncode: Exit code (0 = success).
        stdout: Captured standard output.
        stderr: Captured standard error.
    """

    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass(frozen=True)
class MergeResult:
    """Outcome of a merge attempt with conflict detection.

    Attributes:
        success: True if the merge completed without conflicts.
        conflicting_files: File paths with merge conflicts (empty on success).
        merge_diff: The diff of merged changes (empty on conflict).
        error: Error message if the merge failed for non-conflict reasons.
    """

    success: bool
    conflicting_files: list[str]
    merge_diff: str = ""
    error: str = ""


# ------------------------------------------------------------------
# Low-level runner
# ------------------------------------------------------------------


def run_git(
    args: list[str],
    cwd: Path,
    *,
    timeout: int = 30,
    input_data: str | None = None,
    check: bool = False,
) -> GitResult:
    """Execute a git command and return structured output.

    Args:
        args: Git sub-command and arguments (e.g. ``["status", "--porcelain"]``).
        cwd: Working directory for the command.
        timeout: Seconds before the command is killed.
        input_data: Optional stdin content.
        check: If True, raise on non-zero exit code.

    Returns:
        GitResult with returncode, stdout, stderr.

    Raises:
        subprocess.CalledProcessError: When *check* is True and the command fails.
        subprocess.TimeoutExpired: When the command exceeds *timeout*.
    """
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        input=input_data,
    )
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            ["git", *args],
            output=result.stdout,
            stderr=result.stderr,
        )
    return GitResult(
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )


# ------------------------------------------------------------------
# Queries (read-only git operations used by write workflows)
# ------------------------------------------------------------------


def is_git_repo(cwd: Path) -> bool:
    """Return True if *cwd* is inside a git work tree."""
    r = run_git(["rev-parse", "--is-inside-work-tree"], cwd)
    return r.ok


def status_porcelain(cwd: Path) -> str:
    """Return raw ``git status --porcelain`` output."""
    return run_git(["status", "--porcelain"], cwd).stdout.strip()


def diff_cached_names(cwd: Path) -> list[str]:
    """Return list of staged file paths."""
    r = run_git(["diff", "--cached", "--name-only"], cwd)
    return [f.strip() for f in r.stdout.strip().splitlines() if f.strip()]


def diff_cached_stat(cwd: Path) -> str:
    """Return ``git diff --cached --stat`` output for scope detection."""
    return run_git(["diff", "--cached", "--stat"], cwd).stdout.strip()


def diff_cached(cwd: Path) -> str:
    """Return full ``git diff --cached`` output."""
    return run_git(["diff", "--cached"], cwd).stdout


def diff_head(cwd: Path, files: list[str] | None = None, refs: str = "HEAD~1") -> str:
    """Return ``git diff <refs> -- [files]``."""
    cmd = ["diff", refs, "--"]
    if files:
        cmd.extend(files)
    r = run_git(cmd, cwd, timeout=30)
    return r.stdout.strip()


def rev_parse_head(cwd: Path) -> str:
    """Return the current HEAD commit hash."""
    return run_git(["rev-parse", "HEAD"], cwd).stdout.strip()


# ------------------------------------------------------------------
# Staging
# ------------------------------------------------------------------


def stage_files(cwd: Path, paths: list[str]) -> None:
    """Stage specific file paths.

    Args:
        cwd: Repository root.
        paths: Relative file paths to stage.
    """
    safe = [p for p in paths if not any(pat.rstrip("*") in p for pat in _NEVER_STAGE)]
    if safe:
        run_git(["add", "--", *safe], cwd)


def stage_task_files(cwd: Path, owned_files: list[str]) -> list[str]:
    """Stage only files owned by a task and co-located new files.

    Never stages runtime artifacts, .env, *.pid, *.log.

    Args:
        cwd: Repository root.
        owned_files: File paths the task is allowed to touch.

    Returns:
        List of actually staged paths.
    """
    if not owned_files:
        return []

    # Also find new untracked files in the same directories
    dirs = {str(Path(f).parent) for f in owned_files}
    status_out = status_porcelain(cwd)
    extra: list[str] = []
    for line in status_out.splitlines():
        if line.startswith("??"):
            fpath = line[3:].strip()
            parent = str(Path(fpath).parent)
            if parent in dirs:
                extra.append(fpath)

    all_files = list(dict.fromkeys(owned_files + extra))  # dedupe, preserve order
    safe = [p for p in all_files if not any(pat.rstrip("*") in p for pat in _NEVER_STAGE)]
    if safe:
        run_git(["add", "--", *safe], cwd)
    return safe


def unstage_paths(cwd: Path, paths: list[str]) -> None:
    """Unstage specific paths via ``git reset HEAD``."""
    run_git(["reset", "HEAD", "--", *paths], cwd)


def stage_all_except(cwd: Path, exclude: list[str] | None = None) -> None:
    """Stage everything, then unstage excluded paths.

    Args:
        cwd: Repository root.
        exclude: Paths/globs to unstage after the bulk add.
    """
    run_git(["add", "-A"], cwd)
    to_unstage = list(exclude or [])
    # Always unstage never-stage paths that are directories
    for pat in _NEVER_STAGE:
        if pat.endswith("/"):
            to_unstage.append(pat)
    if to_unstage:
        run_git(["reset", "HEAD", "--", *to_unstage], cwd)


# ------------------------------------------------------------------
# Committing
# ------------------------------------------------------------------


def commit(cwd: Path, message: str) -> GitResult:
    """Create a commit with the given message."""
    return run_git(["commit", "-m", message], cwd)


def conventional_commit(
    cwd: Path,
    *,
    task_title: str | None = None,
    task_id: str | None = None,
    evolve: bool = False,
) -> GitResult:
    """Generate a conventional commit message from the staged diff and commit.

    Parses ``git diff --cached`` to determine change type and scope.
    No LLM call — purely deterministic.

    Args:
        cwd: Repository root.
        task_title: Optional task title to use as description.
        task_id: Optional task ID for ``Refs:`` footer.
        evolve: If True, prefix with ``evolve`` scope.

    Returns:
        GitResult from the commit command.
    """
    staged_files = diff_cached_names(cwd)
    if not staged_files:
        return GitResult(returncode=1, stdout="", stderr="nothing staged")

    diff_stat = diff_cached_stat(cwd)
    full_diff = diff_cached(cwd)

    # Detect change type from diff content
    change_type = _classify_change(staged_files, full_diff)
    scope = _detect_scope(staged_files)

    if evolve:
        scope = "evolution"

    # Build subject line
    if task_title:
        subject = f"{change_type}({scope}): {_truncate(task_title, 60)}"
    else:
        subject = f"{change_type}({scope}): {_summarize_from_files(staged_files)}"

    # Build body from diff stat
    body_lines = _diff_stat_to_bullets(diff_stat, max_bullets=5)

    # Build footer
    footer_parts: list[str] = []
    if task_id:
        footer_parts.append(f"Refs: #{task_id}")
    footer_parts.append("Co-Authored-By: bernstein[bot] <noreply@bernstein.dev>")

    # Assemble
    parts = [subject]
    if body_lines:
        parts.append("")
        parts.extend(body_lines)
    parts.append("")
    parts.extend(footer_parts)

    message = "\n".join(parts)
    return commit(cwd, message)


# ------------------------------------------------------------------
# Push / Fetch / Rebase
# ------------------------------------------------------------------


def fetch(cwd: Path, remote: str = "origin") -> GitResult:
    """Fetch from remote."""
    return run_git(["fetch", remote], cwd, timeout=60)


def safe_push(cwd: Path, branch: str, remote: str = "origin") -> GitResult:
    """Fetch, rebase if behind, then push.

    Args:
        cwd: Repository root.
        branch: Branch name to push (``"master"`` is auto-corrected to ``"main"``).
        remote: Remote name (default "origin").

    Returns:
        GitResult from the push command.
    """
    # Guardrail: never push to "master" — auto-correct to "main".
    if branch == "master":
        logger.info("safe_push: correcting branch 'master' -> 'main'")
        branch = "main"

    # 1. Fetch
    fetch_result = fetch(cwd, remote)
    if not fetch_result.ok:
        logger.warning("git fetch failed: %s", fetch_result.stderr)

    # 2. Check divergence
    behind_r = run_git(["rev-list", "--count", f"HEAD..{remote}/{branch}"], cwd)
    behind = int(behind_r.stdout.strip()) if behind_r.ok and behind_r.stdout.strip().isdigit() else 0

    if behind > 0:
        logger.info("Branch %s is %d commits behind %s/%s, rebasing", branch, behind, remote, branch)
        # 3. Rebase
        rebase_r = run_git(["rebase", f"{remote}/{branch}"], cwd, timeout=120)
        if not rebase_r.ok:
            logger.warning("Rebase failed, aborting and falling back to merge")
            run_git(["rebase", "--abort"], cwd)
            merge_r = run_git(["merge", f"{remote}/{branch}", "--no-edit"], cwd, timeout=120)
            if not merge_r.ok:
                logger.error("Merge fallback also failed: %s", merge_r.stderr)
                return merge_r

    # 4. Push
    return run_git(["push", remote, branch], cwd, timeout=60)


# ------------------------------------------------------------------
# Branching
# ------------------------------------------------------------------


def merge_branch(
    cwd: Path,
    branch: str,
    *,
    message: str | None = None,
    no_ff: bool = True,
) -> GitResult:
    """Merge a branch into the current HEAD.

    Args:
        cwd: Repository root.
        branch: Branch to merge.
        message: Merge commit message.
        no_ff: If True, use ``--no-ff``.

    Returns:
        GitResult from the merge command.
    """
    cmd = ["merge"]
    if no_ff:
        cmd.append("--no-ff")
    cmd.append(branch)
    if message:
        cmd.extend(["-m", message])
    return run_git(cmd, cwd, timeout=60)


def merge_with_conflict_detection(
    cwd: Path,
    branch: str,
    *,
    message: str | None = None,
) -> MergeResult:
    """Merge a branch with explicit conflict detection and safe abort on failure.

    Performs ``git merge --no-commit --no-ff`` to stage the merge without
    committing.  If conflicts are detected, aborts the merge cleanly and
    returns the list of conflicting files so a resolver agent can act on them.

    Args:
        cwd: Repository root.
        branch: Branch to merge into the current HEAD.
        message: Commit message when the merge is clean.

    Returns:
        MergeResult indicating success or listing conflicting files.
    """
    # 1. Attempt the merge without committing
    merge_r = run_git(
        ["merge", "--no-commit", "--no-ff", branch],
        cwd,
        timeout=120,
    )

    if merge_r.ok:
        # Clean merge — commit it
        msg = message or f"Merge {branch}"
        commit_r = run_git(["commit", "-m", msg], cwd, timeout=30)
        if commit_r.ok:
            diff = run_git(["diff", "HEAD~1", "--stat"], cwd, timeout=30).stdout
            return MergeResult(success=True, conflicting_files=[], merge_diff=diff)
        # Nothing to commit (branches already identical)
        run_git(["merge", "--abort"], cwd, timeout=10)
        return MergeResult(success=True, conflicting_files=[])

    # 2. Check if the failure is due to merge conflicts
    conflicts = _parse_conflict_files(cwd)
    if conflicts:
        # Abort the conflicted merge to restore clean state
        run_git(["merge", "--abort"], cwd, timeout=10)
        return MergeResult(success=False, conflicting_files=conflicts)

    # 3. Non-conflict failure (missing branch, unrelated histories, etc.)
    run_git(["merge", "--abort"], cwd, timeout=10)
    return MergeResult(
        success=False,
        conflicting_files=[],
        error=merge_r.stderr.strip() or "merge failed for unknown reason",
    )


def _parse_conflict_files(cwd: Path) -> list[str]:
    """Extract list of files with merge conflicts from git status.

    Looks for unmerged entries (UU, AA, DD, AU, UA, DU, UD) in porcelain
    output.

    Args:
        cwd: Repository root.

    Returns:
        List of conflicting file paths.
    """
    status = run_git(["status", "--porcelain"], cwd, timeout=10)
    conflicts: list[str] = []
    for line in status.stdout.splitlines():
        if len(line) < 4:
            continue
        xy = line[:2]
        # Unmerged status codes per git-status(1)
        if xy in ("UU", "AA", "DD", "AU", "UA", "DU", "UD"):
            conflicts.append(line[3:].strip())
    return conflicts


def branch_delete(cwd: Path, branch: str) -> GitResult:
    """Force-delete a local branch."""
    return run_git(["branch", "-D", branch], cwd, timeout=10)


def create_task_branch(cwd: Path, branch_name: str) -> GitResult:
    """Create and checkout a new branch from the current HEAD.

    Args:
        cwd: Repository root.
        branch_name: Name of the new branch (e.g. ``bernstein/task-abc123``).

    Returns:
        GitResult from ``git checkout -b <branch_name>``.
    """
    return run_git(["checkout", "-b", branch_name], cwd, timeout=10)


def create_branch(cwd: Path, branch_name: str, base: str = "main") -> GitResult:
    """Create a new branch from a given base without switching to it.

    Useful for creating task/, evolve/, or agent/ branches from main
    without disrupting the current checkout.

    Args:
        cwd: Repository root.
        branch_name: Name of the new branch.
        base: Base ref to branch from (default ``"main"``).

    Returns:
        GitResult from ``git branch <branch_name> <base>``.
    """
    return run_git(["branch", branch_name, base], cwd, timeout=10)


def delete_old_branches(
    cwd: Path,
    *,
    older_than_hours: int = 24,
    prefix: str = "bernstein/",
    remote: str | None = None,
) -> list[str]:
    """Delete local branches matching *prefix* whose last commit is older than the threshold.

    Args:
        cwd: Repository root.
        older_than_hours: Delete branches with HEAD commit older than this.
        prefix: Only consider branches starting with this string.
        remote: If set, also delete the branch on this remote.

    Returns:
        List of deleted branch names.
    """
    # List local branches matching the prefix
    r = run_git(
        ["branch", "--list", f"{prefix}*", "--format=%(refname:short) %(committerdate:unix)"],
        cwd,
        timeout=10,
    )
    if not r.ok or not r.stdout.strip():
        return []

    cutoff = time.time() - (older_than_hours * 3600)
    deleted: list[str] = []

    for line in r.stdout.strip().splitlines():
        parts = line.rsplit(" ", 1)
        if len(parts) != 2:
            continue
        branch, epoch_str = parts
        try:
            epoch = float(epoch_str)
        except ValueError:
            continue

        if epoch >= cutoff:
            continue

        # Delete locally
        del_r = run_git(["branch", "-D", branch.strip()], cwd, timeout=10)
        if del_r.ok:
            deleted.append(branch.strip())
            logger.info("Deleted old branch: %s (age > %dh)", branch.strip(), older_than_hours)
            # Optionally delete on remote
            if remote:
                run_git(["push", remote, "--delete", branch.strip()], cwd, timeout=30)

    return deleted


def push_branch(cwd: Path, branch: str, remote: str = "origin") -> GitResult:
    """Push a branch to remote, setting the upstream tracking ref.

    Args:
        cwd: Repository root.
        branch: Branch name to push.
        remote: Remote name (default ``"origin"``).

    Returns:
        GitResult from ``git push --set-upstream <remote> <branch>``.
    """
    return run_git(["push", "--set-upstream", remote, branch], cwd, timeout=60)


def push_head_as(cwd: Path, branch: str, remote: str = "origin") -> GitResult:
    """Push the current HEAD to a named remote branch via refspec.

    Use when the local branch name differs from the desired remote branch name.
    For example, push an ``agent/{session_id}`` worktree as
    ``bernstein/task-{id}`` on the remote without checking out a new branch.

    Args:
        cwd: Repository root (usually a worktree).
        branch: Desired remote branch name (e.g. ``"bernstein/task-abc123"``).
        remote: Remote name (default ``"origin"``).

    Returns:
        GitResult from ``git push --set-upstream <remote> HEAD:refs/heads/<branch>``.
    """
    return run_git(
        ["push", "--set-upstream", remote, f"HEAD:refs/heads/{branch}"],
        cwd,
        timeout=60,
    )


@dataclass(frozen=True)
class PullRequestResult:
    """Outcome of a GitHub PR creation attempt.

    Attributes:
        success: True if the PR was created.
        pr_url: URL of the created PR (empty on failure).
        error: Error message on failure.
    """

    success: bool
    pr_url: str = ""
    error: str = ""


def create_github_pr(
    cwd: Path,
    *,
    title: str,
    body: str,
    head: str,
    base: str = "main",
    labels: list[str] | None = None,
) -> PullRequestResult:
    """Create a GitHub pull request via the ``gh`` CLI.

    Args:
        cwd: Repository root (used as working directory for ``gh``).
        title: PR title.
        body: PR body / description.
        head: Source branch name.
        base: Target branch (default ``"main"``).
        labels: Optional list of label names to attach.

    Returns:
        PullRequestResult with ``pr_url`` set on success.
    """
    cmd = [
        "gh",
        "pr",
        "create",
        "--title",
        title,
        "--body",
        body,
        "--head",
        head,
        "--base",
        base,
    ]
    if labels:
        cmd.extend(["--label", ",".join(labels)])
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            pr_url = result.stdout.strip()
            return PullRequestResult(success=True, pr_url=pr_url)
        return PullRequestResult(success=False, error=result.stderr.strip())
    except (subprocess.TimeoutExpired, OSError) as exc:
        return PullRequestResult(success=False, error=str(exc))


def enable_pr_auto_merge(cwd: Path, pr_url_or_number: str) -> GitResult:
    """Enable auto-merge (squash) on a PR via ``gh pr merge --auto``.

    Args:
        cwd: Repository root.
        pr_url_or_number: PR URL or number string.

    Returns:
        GitResult with the exit code from ``gh pr merge --auto --squash``.
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "merge", "--auto", "--squash", pr_url_or_number],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return GitResult(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return GitResult(returncode=1, stdout="", stderr=str(exc))


def revert_commit(cwd: Path, commit_hash: str, *, no_commit: bool = True) -> GitResult:
    """Revert a commit.

    Args:
        cwd: Repository root.
        commit_hash: Hash to revert.
        no_commit: If True, stage the revert without committing.
    """
    cmd = ["revert"]
    if no_commit:
        cmd.append("--no-commit")
    cmd.append(commit_hash)
    return run_git(cmd, cwd, timeout=30)


def checkout_discard(cwd: Path) -> GitResult:
    """Discard all unstaged changes (``git checkout -- .``)."""
    return run_git(["checkout", "--", "."], cwd)


# ------------------------------------------------------------------
# Worktree
# ------------------------------------------------------------------


def worktree_add(cwd: Path, path: Path, branch: str) -> GitResult:
    """Create a git worktree at *path* on a new branch.

    Args:
        cwd: Repository root.
        path: Filesystem path for the worktree.
        branch: New branch name.
    """
    return run_git(
        ["worktree", "add", str(path), "-b", branch],
        cwd,
        timeout=30,
    )


def worktree_remove(cwd: Path, path: Path) -> GitResult:
    """Remove a worktree (force)."""
    return run_git(
        ["worktree", "remove", "--force", str(path)],
        cwd,
        timeout=30,
    )


def worktree_list(cwd: Path) -> str:
    """Return raw ``git worktree list --porcelain`` output."""
    return run_git(["worktree", "list", "--porcelain"], cwd, timeout=10).stdout


def apply_diff(cwd: Path, diff: str) -> GitResult:
    """Apply a unified diff via ``git apply``.

    Args:
        cwd: Working directory (usually a worktree).
        diff: Unified diff content.
    """
    return run_git(
        ["apply", "--allow-empty", "-"],
        cwd,
        input_data=diff,
        timeout=30,
    )


# ------------------------------------------------------------------
# Bisect
# ------------------------------------------------------------------


def bisect_regression(
    cwd: Path,
    test_cmd: str,
    good_ref: str = "HEAD~10",
) -> str | None:
    """Find which commit introduced a test regression via ``git bisect``.

    Args:
        cwd: Repository root.
        test_cmd: Shell command to run as the bisect test.
        good_ref: Known-good reference (default HEAD~10).

    Returns:
        The first bad commit hash, or None if bisect failed.
    """
    try:
        run_git(["bisect", "start", "HEAD", good_ref], cwd, timeout=10)

        result = subprocess.run(
            ["git", "bisect", "run", *test_cmd.split()],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=600,
        )

        # Parse the first bad commit from bisect output
        bad_commit: str | None = None
        for line in result.stdout.splitlines():
            m = re.search(r"([0-9a-f]{7,40}) is the first bad commit", line)
            if m:
                bad_commit = m.group(1)
                break

        return bad_commit

    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("bisect_regression failed: %s", exc)
        return None
    finally:
        run_git(["bisect", "reset"], cwd, timeout=10)


# ------------------------------------------------------------------
# Tags
# ------------------------------------------------------------------


def tag(cwd: Path, name: str, message: str | None = None) -> GitResult:
    """Create a git tag.

    Args:
        cwd: Repository root.
        name: Tag name.
        message: Optional annotated tag message.
    """
    cmd = ["tag"]
    if message:
        cmd.extend(["-a", name, "-m", message])
    else:
        cmd.append(name)
    return run_git(cmd, cwd)


# Alias for discoverability
create_tag = tag


def list_tags(cwd: Path, pattern: str | None = None) -> list[str]:
    """Return tag names, optionally filtered by glob pattern.

    Args:
        cwd: Repository root.
        pattern: Optional glob (e.g. ``"v*"``).

    Returns:
        List of tag names, newest first.
    """
    cmd = ["tag", "-l", "--sort=-version:refname"]
    if pattern:
        cmd.append(pattern)
    r = run_git(cmd, cwd, timeout=10)
    if not r.ok or not r.stdout.strip():
        return []
    return [t.strip() for t in r.stdout.strip().splitlines() if t.strip()]


def version_from_commits(cwd: Path, tag_prefix: str = "v") -> str:
    """Compute the next semantic version from conventional commits since the last tag.

    Scans commit messages between the latest ``v*`` tag and HEAD.  Bumps
    major on ``BREAKING CHANGE`` or ``!:`` in a subject, minor on ``feat``,
    patch on anything else (``fix``, ``refactor``, ``chore``, etc.).

    If no previous tag exists, starts from ``0.1.0``.

    Args:
        cwd: Repository root.
        tag_prefix: Prefix for version tags (default ``"v"``).

    Returns:
        Next version string *without* the prefix (e.g. ``"1.3.0"``).
    """
    tags = list_tags(cwd, pattern=f"{tag_prefix}*")

    # Parse current version from latest tag
    major, minor, patch_v = 0, 0, 0
    latest_tag: str | None = None
    for t in tags:
        stripped = t.lstrip(tag_prefix)
        parts = stripped.split(".")
        if len(parts) >= 3 and all(p.isdigit() for p in parts[:3]):
            major, minor, patch_v = int(parts[0]), int(parts[1]), int(parts[2])
            latest_tag = t
            break

    # Get commits since latest tag (or all commits)
    if latest_tag:
        log_r = run_git(
            ["log", f"{latest_tag}..HEAD", "--pretty=format:%s"],
            cwd,
            timeout=10,
        )
    else:
        log_r = run_git(
            ["log", "--pretty=format:%s"],
            cwd,
            timeout=10,
        )

    if not log_r.ok or not log_r.stdout.strip():
        # No new commits — return current version as-is
        return f"{major}.{minor}.{patch_v}"

    subjects = log_r.stdout.strip().splitlines()

    bump_major = False
    bump_minor = False

    for subj in subjects:
        lower = subj.lower()
        if "breaking change" in lower or "!:" in subj:
            bump_major = True
            break
        if lower.startswith("feat"):
            bump_minor = True

    if bump_major:
        major += 1
        minor = 0
        patch_v = 0
    elif bump_minor:
        minor += 1
        patch_v = 0
    else:
        patch_v += 1

    return f"{major}.{minor}.{patch_v}"


# ------------------------------------------------------------------
# Conventional commit helpers (private)
# ------------------------------------------------------------------


_NEW_FILE_RE = re.compile(r"^new file mode", re.MULTILINE)
_RENAME_RE = re.compile(r"^rename from", re.MULTILINE)
_DELETE_RE = re.compile(r"^deleted file mode", re.MULTILINE)


def _classify_change(staged_files: list[str], diff: str) -> str:
    """Classify the change type from file list and diff content."""
    all_tests = all("test" in f for f in staged_files)
    if all_tests:
        return "test"

    all_docs = all(f.endswith((".md", ".rst", ".txt")) or "docs/" in f for f in staged_files)
    if all_docs:
        return "docs"

    all_config = all(
        f.endswith((".toml", ".yaml", ".yml", ".json", ".cfg", ".ini")) or f in {"Makefile", "Dockerfile", ".gitignore"}
        for f in staged_files
    )
    if all_config:
        return "chore"

    # Check for new files (feat) vs modifications (refactor/fix)
    new_count = len(_NEW_FILE_RE.findall(diff))
    delete_count = len(_DELETE_RE.findall(diff))
    rename_count = len(_RENAME_RE.findall(diff))

    if new_count > 0 and new_count >= len(staged_files) // 2:
        return "feat"
    if delete_count > len(staged_files) // 2:
        return "refactor"
    if rename_count > 0:
        return "refactor"

    return "feat" if new_count > 0 else "refactor"


def _detect_scope(staged_files: list[str]) -> str:
    """Detect the primary scope from staged file paths."""
    if not staged_files:
        return "unknown"

    # Count directory occurrences to find the dominant scope
    dirs: Counter[str] = Counter()
    for f in staged_files:
        parts = Path(f).parts
        if len(parts) >= 3 and parts[0] == "src" and parts[1] == "bernstein":
            dirs[parts[2]] += 1
        elif len(parts) > 0 and parts[0] == "tests":
            dirs["tests"] += 1
        elif len(parts) > 0:
            dirs[parts[0]] += 1

    if not dirs:
        return "unknown"

    top_result = dirs.most_common(1)
    if top_result:
        return top_result[0][0]
    return "unknown"


def _summarize_from_files(staged_files: list[str]) -> str:
    """Generate a short summary from changed file names."""
    if not staged_files:
        return "housekeeping"

    names = [Path(f).stem for f in staged_files[:3]]
    summary = ", ".join(names)
    if len(staged_files) > 3:
        summary += f" (+{len(staged_files) - 3} more)"
    return _truncate(summary, 60)


def _truncate(text: str, max_len: int) -> str:
    """Truncate text to max_len, adding ellipsis if needed."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _diff_stat_to_bullets(stat_output: str, max_bullets: int = 5) -> list[str]:
    """Convert ``git diff --stat`` output to markdown bullet points."""
    lines = stat_output.strip().splitlines()
    if not lines:
        return []

    bullets: list[str] = []
    for line in lines[:max_bullets]:
        line = line.strip()
        if line and "|" in line:
            # "path/to/file.py | 42 ++++---"
            parts = line.split("|", 1)
            fname = parts[0].strip()
            changes = parts[1].strip() if len(parts) > 1 else ""
            bullets.append(f"- {fname}: {changes}")
        elif line and "changed" in line:
            # Summary line: "3 files changed, 100 insertions(+), 20 deletions(-)"
            bullets.append(f"- {line}")

    return bullets
