"""Manus-style harness policy flags for agent spawn pipelines.

This module names and toggles the five harness patterns published by Manus
(see ``agentic_systems_v2.md`` §1 line 79). Each flag is **off by default** to
preserve current behaviour; orchestrators opt agents in either via the
``HarnessPolicy`` dataclass passed to spawn helpers or via per-role overrides
loaded from ``.sdd/runtime/harness/<role>.yaml`` (loader to land in a follow-up
PR - this module ships only the data model and the two patterns wired into
``mask_tools`` / ``format_failed_action_block`` today).

The five Manus patterns:

1. ``kv_cache_locality`` - keep system-prompt prefix byte-for-byte stable across
   spawns of the same role; vary only the trailing user block.
2. ``tool_masking`` - instead of removing tools to disable them, return them
   with ``unavailable: True`` so the cached prefix is preserved.
3. ``filesystem_memory`` - agents read/write to ``.sdd/memory/<agent>/`` instead
   of stuffing observations into the context.
4. ``todo_recitation`` - every long-running task keeps a flat ``todo.md`` checked
   off in-place; the agent re-reads it each turn rather than re-deriving the
   plan.
5. ``keep_failed_actions`` - failed tool calls and their tracebacks stay in the
   visible scrollback (with a recency weight) instead of being scrubbed; this
   is the implicit-learning pattern Manus published.

This first slice ships **patterns 2 and 5 only**; the remaining three patterns
have wiring stubs but no behaviour yet - see the parent ticket for the
follow-up PRs.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import cast


@dataclass(frozen=True, slots=True)
class HarnessPolicy:
    """Toggle flags for Manus harness patterns at agent spawn time.

    All flags default to ``False`` so existing orchestrators keep their current
    behaviour. Operators opt in either by passing a ``HarnessPolicy`` instance
    to spawn helpers or by loading per-role YAML overrides via
    :func:`load_policy_for_role` (the loader is a follow-up).

    Attributes:
        kv_cache_locality: Keep system-prompt prefix byte-stable across spawns
            of the same role.
        tool_masking: Mask disabled tools with ``unavailable: True`` instead of
            removing them - preserves prefix bytes for cache hits.
        filesystem_memory: Route long observations through
            ``.sdd/memory/<agent>/`` filesystem-as-memory.
        todo_recitation: Inject a starter ``todo.md`` for tasks at or above
            ``min_recitation_complexity``.
        keep_failed_actions: Tag failed tool-call blocks with
            ``[FAILED -- kept for reference]`` instead of scrubbing them in
            compaction.
    """

    kv_cache_locality: bool = False
    tool_masking: bool = False
    filesystem_memory: bool = False
    todo_recitation: bool = False
    keep_failed_actions: bool = False

    def with_overrides(self, **changes: bool) -> HarnessPolicy:
        """Return a copy with the given flag(s) overridden.

        Args:
            **changes: Subset of flag names to override.

        Returns:
            A new :class:`HarnessPolicy` with overrides applied.

        Raises:
            TypeError: If a non-existent flag name is supplied.
        """
        updated = cast(HarnessPolicy, replace(self, **changes))
        return updated


#: Conservative all-off baseline - current Bernstein behaviour.
DEFAULT_POLICY: HarnessPolicy = HarnessPolicy()

#: All patterns enabled. Use only after the follow-up PRs ship the
#: filesystem-memory + recitation wiring; today this enables only tool
#: masking and keep-failed-actions, the rest are no-ops.
ALL_ON_POLICY: HarnessPolicy = HarnessPolicy(
    kv_cache_locality=True,
    tool_masking=True,
    filesystem_memory=True,
    todo_recitation=True,
    keep_failed_actions=True,
)


__all__ = [
    "ALL_ON_POLICY",
    "DEFAULT_POLICY",
    "HarnessPolicy",
]
