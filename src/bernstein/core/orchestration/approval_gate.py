"""Pre-spawn human-in-the-loop approval gate (#1110).

Provides :func:`wait_for_approval`, the runtime half of the explicit
approval gate that ships with :class:`bernstein.core.models.ApprovalSpec`.
On entry the gate writes a ``<task_id>.pending`` JSON sentinel under
``.sdd/runtime/approvals/`` and emits an ``approval_pending`` event into
the HMAC-chained audit log. Operator decisions arrive as plain
``<task_id>.approved`` / ``<task_id>.rejected`` files written by the
``bernstein approve`` / ``bernstein reject`` CLI commands (the same files
the post-completion review gate already uses, so a single sentinel
directory backs both gates without filename collisions).

The gate is intentionally synchronous: the orchestrator tick path is
file-driven and runs outside an asyncio loop, so polling with
``time.monotonic`` keeps the implementation simple and free of event-loop
ownership questions. Concurrency between racing ``bernstein approve``
calls is resolved by atomic ``os.replace`` writes; the first writer wins
and any subsequent writers see the resolved state and become no-ops with
a clear "already resolved" log line. Atomic file replacement is also
what makes the pending sentinel safe to read mid-update.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from bernstein.core.security.audit import AuditLog
    from bernstein.core.tasks.models import ApprovalSpec

logger = logging.getLogger(__name__)

#: Outcome of a :func:`wait_for_approval` call.
ApprovalOutcome = Literal["approved", "rejected", "timeout"]

#: Source of the resolution recorded in the audit chain.
DecisionSource = Literal["cli", "tui", "timeout-default"]

#: Default poll interval. Short enough to feel snappy in foreground, long
#: enough to keep filesystem load negligible in background runs.
_DEFAULT_POLL_INTERVAL_S: float = 0.5

#: Relative directory where every approval sentinel lives. Keeping all
#: gates under one folder lets ``bernstein pending`` enumerate every
#: outstanding decision in a single ``glob``.
_RUNTIME_REL = Path(".sdd") / "runtime" / "approvals"


def _approvals_dir(workdir: Path) -> Path:
    """Return the canonical approvals directory rooted at *workdir*."""
    return workdir / _RUNTIME_REL


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    """Write *payload* to *path* atomically via ``os.replace``.

    A temporary file is created in the same directory so the rename is on
    one filesystem (cross-filesystem rename is not atomic on POSIX). On
    failure the temp file is unlinked and the original exception
    propagates.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _emit_audit(
    audit_log: AuditLog | None,
    *,
    event_type: str,
    task_id: str,
    details: dict[str, object],
) -> None:
    """Append an ``approval_*`` event to the HMAC-chained audit log.

    The lifecycle module owns the global :class:`AuditLog` singleton; we
    look it up dynamically when *audit_log* is ``None`` so callers do not
    have to thread the reference through. Failures are logged at debug
    level: an audit-log write should never block a gate decision.
    """
    log = audit_log
    if log is None:
        try:
            from bernstein.core.tasks.lifecycle import get_audit_log

            log = get_audit_log()
        except Exception:
            logger.debug("approval gate: audit log lookup failed", exc_info=True)
            return
    if log is None:
        return
    try:
        log.log(
            event_type=event_type,
            actor="approval_gate",
            resource_type="task",
            resource_id=task_id,
            details=details,
        )
    except Exception:
        # Audit log write is best-effort: a broken audit chain should
        # surface elsewhere (``bernstein verify``), not silently abort the
        # task lifecycle.
        logger.warning(
            "approval gate: failed to emit %s audit event for task %s",
            event_type,
            task_id,
            exc_info=True,
        )


def _pending_path(workdir: Path, task_id: str) -> Path:
    """Return the ``<task_id>.pending`` sentinel path."""
    return _approvals_dir(workdir) / f"{task_id}.pending"


def _approved_path(workdir: Path, task_id: str) -> Path:
    """Return the ``<task_id>.approved`` decision path."""
    return _approvals_dir(workdir) / f"{task_id}.approved"


def _rejected_path(workdir: Path, task_id: str) -> Path:
    """Return the ``<task_id>.rejected`` decision path."""
    return _approvals_dir(workdir) / f"{task_id}.rejected"


def write_pending_sentinel(
    workdir: Path,
    task_id: str,
    spec: ApprovalSpec,
    *,
    now: float | None = None,
) -> Path:
    """Write the ``.pending`` sentinel for *task_id* and return its path.

    The sentinel content is:

    .. code-block:: json

        {
          "prompt": "...",
          "timeout_at_iso": "...",
          "created_iso": "...",
          "default_action": "reject"
        }

    This is the canonical hand-off between the orchestrator and the CLI:
    ``bernstein pending`` reads the sentinel to render the prompt while
    ``bernstein approve``/``reject`` write the corresponding decision
    file. The write is atomic so any concurrent reader observes either
    the previous content or the full new payload, never a partial one.

    Args:
        workdir: Project root (parent of ``.sdd/``).
        task_id: Identifier whose gate is being entered.
        spec: Approval specification governing this gate.
        now: Optional injected timestamp for deterministic tests.

    Returns:
        Absolute path to the sentinel that was written.
    """
    moment = time.time() if now is None else now
    created = datetime.fromtimestamp(moment, tz=UTC)
    timeout_at = created + timedelta(seconds=spec.timeout_seconds)
    payload: dict[str, object] = {
        "task_id": task_id,
        "prompt": spec.prompt,
        "timeout_at_iso": timeout_at.isoformat(),
        "created_iso": created.isoformat(),
        "default_action": spec.default_action,
        "timeout_seconds": spec.timeout_seconds,
    }
    path = _pending_path(workdir, task_id)
    _atomic_write_json(path, payload)
    return path


def _resolve_default_action(action: Literal["reject", "approve", "fail"]) -> ApprovalOutcome:
    """Translate a spec's ``default_action`` into the wait-for-approval outcome.

    ``"approve"`` lets the body run; ``"reject"`` and ``"fail"`` both halt
    it. The two non-approve values are kept distinct in the audit chain
    via the ``decision_source`` and ``outcome`` fields, but for the gate's
    runtime contract they collapse to ``"rejected"``.
    """
    if action == "approve":
        return "approved"
    return "rejected"


def _classify_decision_files(approved: Path, rejected: Path) -> tuple[ApprovalOutcome, DecisionSource] | None:
    """Return the resolved outcome if a decision file exists, else ``None``.

    Both files are checked because a misbehaving operator could land
    either; the first one to materialise wins. Approval files take
    precedence on the (defensive) chance that both arrive in the same
    poll tick — better to honour an explicit approve than to swallow
    operator intent.
    """
    if approved.exists():
        return "approved", "cli"
    if rejected.exists():
        return "rejected", "cli"
    return None


def _cleanup_pending(workdir: Path, task_id: str) -> None:
    """Remove the ``.pending`` sentinel; ignore missing-file errors.

    Called once a decision is recorded so :func:`list_pending_approvals`
    does not surface stale entries on the next tick.
    """
    pending = _pending_path(workdir, task_id)
    with contextlib.suppress(FileNotFoundError, OSError):
        pending.unlink()


def list_pending_approvals(workdir: Path) -> list[dict[str, object]]:
    """Return every active ``<task_id>.pending`` sentinel under *workdir*.

    Used by ``bernstein pending`` to surface approval-pending tasks
    distinct from the post-completion review queue. Each entry is the raw
    JSON dict written by :func:`write_pending_sentinel`, augmented with a
    ``task_id`` key derived from the filename for callers that prefer it
    over digging into the body.
    """
    approvals_dir = _approvals_dir(workdir)
    if not approvals_dir.exists():
        return []
    entries: list[dict[str, object]] = []
    for sentinel in sorted(approvals_dir.glob("*.pending")):
        try:
            data = json.loads(sentinel.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("approval gate: skipping unreadable sentinel %s: %s", sentinel.name, exc)
            continue
        if not isinstance(data, dict):
            continue
        data.setdefault("task_id", sentinel.stem)
        entries.append(data)
    return entries


def wait_for_approval(
    task_id: str,
    spec: ApprovalSpec,
    *,
    workdir: Path | None = None,
    audit_log: AuditLog | None = None,
    poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,
    now: float | None = None,
    monotonic: object | None = None,
    sleep: object | None = None,
) -> ApprovalOutcome:
    """Block until the operator decides or the TTL fires.

    The gate is idempotent under concurrent ``bernstein approve`` /
    ``bernstein reject`` calls: the underlying decision files are written
    via atomic rename, so the first writer's contents persist and any
    subsequent writer's content is silently dropped at the filesystem
    level. The CLI commands also detect already-resolved tasks and emit
    a "already resolved" message instead of re-resolving.

    Args:
        task_id: Identifier of the task being gated; appears in audit
            events and on-disk sentinels.
        spec: Approval specification driving prompt, timeout, and
            default action.
        workdir: Project root. Defaults to the current working directory.
        audit_log: Optional explicit audit log; defaults to the
            lifecycle-registered singleton.
        poll_interval_s: Seconds between filesystem checks.
        now: Optional injected wall-clock timestamp (used by
            :func:`write_pending_sentinel` for deterministic ISO strings).
        monotonic: Optional injected monotonic clock — useful in tests
            that drive the timeout deterministically. Must accept zero
            arguments and return a float; defaults to
            :func:`time.monotonic`.
        sleep: Optional injected sleep callable. Must accept a float;
            defaults to :func:`time.sleep`.

    Returns:
        ``"approved"``, ``"rejected"``, or ``"timeout"``.

    Notes:
        On ``"timeout"`` the gate also resolves the task by writing the
        appropriate decision file derived from ``spec.default_action``,
        so subsequent CLI calls see a terminal state and the on-disk
        history mirrors the in-memory outcome.
    """
    root = workdir if workdir is not None else Path.cwd()
    monotonic_clock = monotonic if callable(monotonic) else time.monotonic
    sleep_fn = sleep if callable(sleep) else time.sleep

    approvals_dir = _approvals_dir(root)
    approvals_dir.mkdir(parents=True, exist_ok=True)
    approved = _approved_path(root, task_id)
    rejected = _rejected_path(root, task_id)

    # Honour decisions that arrived before we even started waiting (CLI
    # may run while the orchestrator was busy in a previous tick).
    early = _classify_decision_files(approved, rejected)
    if early is not None:
        outcome, source = early
        write_pending_sentinel(root, task_id, spec, now=now)
        _emit_audit(
            audit_log,
            event_type="approval_pending",
            task_id=task_id,
            details={
                "prompt": spec.prompt,
                "timeout_seconds": spec.timeout_seconds,
                "default_action": spec.default_action,
            },
        )
        _emit_audit(
            audit_log,
            event_type="approval_resolved",
            task_id=task_id,
            details={"outcome": outcome, "decision_source": source},
        )
        _cleanup_pending(root, task_id)
        return outcome

    # No decision yet — write sentinel + emit pending event, then poll.
    write_pending_sentinel(root, task_id, spec, now=now)
    _emit_audit(
        audit_log,
        event_type="approval_pending",
        task_id=task_id,
        details={
            "prompt": spec.prompt,
            "timeout_seconds": spec.timeout_seconds,
            "default_action": spec.default_action,
        },
    )

    deadline = monotonic_clock() + float(spec.timeout_seconds)  # type: ignore[operator]
    while True:
        decision = _classify_decision_files(approved, rejected)
        if decision is not None:
            outcome, source = decision
            _emit_audit(
                audit_log,
                event_type="approval_resolved",
                task_id=task_id,
                details={"outcome": outcome, "decision_source": source},
            )
            _cleanup_pending(root, task_id)
            return outcome

        remaining = deadline - monotonic_clock()  # type: ignore[operator]
        if remaining <= 0:
            outcome = _resolve_default_action(spec.default_action)
            # Persist a decision file so future readers see a terminal state.
            terminal_path = approved if outcome == "approved" else rejected
            try:
                terminal_path.write_text(f"timeout-default:{spec.default_action}\n", encoding="utf-8")
            except OSError as exc:
                logger.warning(
                    "approval gate: could not persist timeout decision for task %s: %s",
                    task_id,
                    exc,
                )
            _emit_audit(
                audit_log,
                event_type="approval_resolved",
                task_id=task_id,
                details={
                    "outcome": "timeout",
                    "decision_source": "timeout-default",
                    "default_action": spec.default_action,
                    "applied_outcome": outcome,
                },
            )
            _cleanup_pending(root, task_id)
            return "timeout"

        sleep_fn(min(poll_interval_s, remaining))  # type: ignore[operator]


__all__ = [
    "ApprovalOutcome",
    "DecisionSource",
    "list_pending_approvals",
    "wait_for_approval",
    "write_pending_sentinel",
]
