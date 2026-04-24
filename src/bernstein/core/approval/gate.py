"""Security-layer hook for the interactive approval queue.

This module owns the "miss the allow-list" branch: it checks the always
allow engine, and if no rule matches, reads ``bernstein.yaml`` to decide
whether to enqueue an interactive approval. A blocking call
(:func:`gate_tool_call`) and an async variant (:func:`await_tool_call`)
are provided so both synchronous and coroutine-based callers can plug in
without extra glue.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from bernstein.core.approval.models import (
    ApprovalDecision,
    ApprovalTimeoutError,
    PendingApproval,
    ResolvedApproval,
)
from bernstein.core.approval.queue import (
    DEFAULT_TTL_SECONDS,
    ApprovalQueue,
    get_default_queue,
    promote_to_always_allow,
)
from bernstein.core.security.always_allow import (
    AlwaysAllowEngine,
    load_always_allow_rules,
)
from bernstein.core.security.guardrails import check_always_allow_tool

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ApprovalConfig:
    """``approvals:`` section of ``bernstein.yaml``.

    Attributes:
        interactive: When ``True`` the gate queues missed calls for
            operator review. When ``False`` the gate is a no-op and the
            caller falls back to whatever legacy ask-mode behaviour was
            configured.
        timeout_seconds: How long the gate blocks waiting for a
            decision. Defaults to ten minutes.
    """

    interactive: bool = False
    timeout_seconds: int = DEFAULT_TTL_SECONDS


def load_approval_config(workdir: Path | None = None) -> ApprovalConfig:
    """Read the ``approvals:`` block from ``bernstein.yaml``.

    Missing fields fall back to safe defaults (``interactive=False`` so
    existing headless runs are unaffected).
    """
    root = workdir if workdir is not None else Path.cwd()
    path = root / "bernstein.yaml"
    if not path.exists():
        return ApprovalConfig()
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("Could not read bernstein.yaml for approval config: %s", exc)
        return ApprovalConfig()
    if not isinstance(raw, dict):
        return ApprovalConfig()
    section = raw.get("approvals") or {}
    if not isinstance(section, dict):
        return ApprovalConfig()
    interactive = bool(section.get("interactive", False))
    timeout = int(section.get("timeout_seconds", DEFAULT_TTL_SECONDS))
    return ApprovalConfig(interactive=interactive, timeout_seconds=max(1, timeout))


def _always_allow_hit(
    tool_name: str,
    tool_args: dict[str, Any],
    engine: AlwaysAllowEngine | None,
) -> bool:
    """Return ``True`` when *tool_name* + *tool_args* hits the allow list."""
    if engine is None or not engine.rules:
        return False
    return check_always_allow_tool(tool_name, tool_args, engine).matched


async def await_tool_call(
    *,
    session_id: str,
    agent_role: str,
    tool_name: str,
    tool_args: dict[str, Any],
    workdir: Path | None = None,
    queue: ApprovalQueue | None = None,
    engine: AlwaysAllowEngine | None = None,
    config: ApprovalConfig | None = None,
) -> ResolvedApproval | None:
    """Async variant of :func:`gate_tool_call`.

    Returns ``None`` when the gate is disabled or when the allow-list
    already permits the invocation. When the caller should block the
    tool call, the returned :class:`ResolvedApproval` carries the
    operator's decision.

    Raises:
        ApprovalTimeoutError: When the TTL expires without an operator
            decision. The caller MUST treat this as a rejection.
    """
    cfg = config if config is not None else load_approval_config(workdir)
    if not cfg.interactive:
        return None

    root = workdir if workdir is not None else Path.cwd()
    allow_engine = engine if engine is not None else load_always_allow_rules(root, strict=False)
    if _always_allow_hit(tool_name, tool_args, allow_engine):
        return None

    q = queue if queue is not None else get_default_queue(root / ".sdd" / "runtime" / "approvals")
    approval = PendingApproval(
        session_id=session_id,
        agent_role=agent_role,
        tool_name=tool_name,
        tool_args=dict(tool_args),
        ttl_seconds=cfg.timeout_seconds,
    )
    q.push(approval)
    logger.info(
        "Approval gate: tool=%s role=%s session=%s queued as %s",
        tool_name,
        agent_role,
        session_id,
        approval.id,
    )
    resolution = await q.wait_for(approval.id, timeout_seconds=cfg.timeout_seconds)
    if resolution.decision is ApprovalDecision.ALWAYS:
        try:
            promote_to_always_allow(approval, workdir=root)
        except OSError as exc:
            logger.warning("Could not promote approval %s to always-allow: %s", approval.id, exc)
    return resolution


def gate_tool_call(
    *,
    session_id: str,
    agent_role: str,
    tool_name: str,
    tool_args: dict[str, Any],
    workdir: Path | None = None,
    queue: ApprovalQueue | None = None,
    engine: AlwaysAllowEngine | None = None,
    config: ApprovalConfig | None = None,
) -> ResolvedApproval | None:
    """Blocking entry point used from sync guardrail code.

    Returns ``None`` when the gate is disabled or when the allow-list
    already permits the invocation; otherwise a :class:`ResolvedApproval`
    with the operator's decision.

    Raises:
        ApprovalTimeoutError: When the TTL expires without a decision.
    """
    try:
        return asyncio.run(
            await_tool_call(
                session_id=session_id,
                agent_role=agent_role,
                tool_name=tool_name,
                tool_args=tool_args,
                workdir=workdir,
                queue=queue,
                engine=engine,
                config=config,
            )
        )
    except RuntimeError as exc:
        # Fallback: when called from inside an already-running loop we
        # cannot spin up a new one, so busy-wait on the queue state in
        # an explicit executor to preserve sync semantics.
        if "cannot be called from a running event loop" not in str(exc):
            raise
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                await_tool_call(
                    session_id=session_id,
                    agent_role=agent_role,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    workdir=workdir,
                    queue=queue,
                    engine=engine,
                    config=config,
                )
            )
        finally:
            loop.close()


__all__ = [
    "ApprovalConfig",
    "ApprovalTimeoutError",
    "await_tool_call",
    "gate_tool_call",
    "load_approval_config",
]
