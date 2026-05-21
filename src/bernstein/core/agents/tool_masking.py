"""Tool-masking pass for KV-cache-locality-preserving spawn prompts.

Manus's lesson #2: when a tool should be unavailable for a step, do **not**
remove it from the tool list - that shifts every byte after the removed entry
and busts the Anthropic prompt-cache prefix (90% discount on cache hits).
Instead, keep the entry in place and flip a ``unavailable: True`` flag plus an
``unavailable_reason`` string so the model sees the entry but is steered away
from invoking it.

Adapter compatibility:

- **Claude / Codex** consume the flag end-to-end (the adapter forwards the flag
  through the tool definitions).
- **Other adapters** fall back to physical removal because their tool schemas
  don't carry an ``unavailable`` field. Callers can detect this by inspecting
  the returned :class:`MaskResult` - when ``fallback_removed`` is non-empty the
  adapter must drop those tools instead.

This module is intentionally pure-data: it never touches the prompt string and
never imports adapter SDKs. The spawn pipeline drives both prompt assembly and
adapter dispatch around the result.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

#: Minimum required keys for a tool entry. Other adapter-specific fields
#: (``input_schema``, ``cache_control``, ``annotations``, ...) pass through
#: untouched.
_REQUIRED_KEYS: frozenset[str] = frozenset({"name"})


@dataclass(frozen=True, slots=True)
class MaskResult:
    """Outcome of a tool-masking pass.

    Attributes:
        tools: The masked tool list, byte-stable in entry order so cache
            prefixes survive.
        masked_names: Names that were marked unavailable (kept in the list).
        fallback_removed: Names the caller must physically drop because the
            target adapter cannot honour the ``unavailable`` flag.
    """

    tools: list[dict[str, Any]]
    masked_names: tuple[str, ...]
    fallback_removed: tuple[str, ...]


def mask_tools(
    tools: Sequence[Mapping[str, Any]],
    denied: Iterable[str],
    *,
    reason: str | Mapping[str, str] = "denied by agent identity card",
    adapter_supports_unavailable_flag: bool = True,
) -> MaskResult:
    """Mask denied tools without disturbing prefix byte positions.

    For each entry in *tools* whose ``name`` appears in *denied*:

    - When *adapter_supports_unavailable_flag* is ``True`` (Claude, Codex),
      the entry is rewritten with ``unavailable: True`` and an
      ``unavailable_reason`` set from *reason* (string applies to all denied
      tools; mapping looks up per-name reasons). Original key order is
      preserved so byte positions before the flag are untouched.
    - When ``False``, the entry is dropped and recorded in
      ``fallback_removed``; callers (typically non-Claude/non-Codex adapters)
      then physically remove those tools from the dispatch list.

    Tools not in *denied* pass through unchanged.

    Args:
        tools: Iterable of tool entries; each must be a mapping with a
            ``name`` key.
        denied: Iterable of tool names to mask. Order ignored; deduped.
        reason: Reason string (applied to all denied tools) or per-name
            mapping. Mapping misses fall back to a default.
        adapter_supports_unavailable_flag: Set ``False`` for adapters whose
            tool schema can't carry the flag.

    Returns:
        :class:`MaskResult` carrying the masked list and bookkeeping.

    Raises:
        ValueError: If a tool entry is missing the required ``name`` key.
    """
    denied_set = {name for name in denied if name}
    if isinstance(reason, str):
        reason_for = lambda _n: reason  # noqa: E731 - tiny inline closure
        default_reason = reason
    else:
        reason_map = dict(reason)
        default_reason = "denied by agent identity card"
        reason_for = lambda n: reason_map.get(n, default_reason)  # noqa: E731

    masked_names: list[str] = []
    fallback_removed: list[str] = []
    out: list[dict[str, Any]] = []

    for entry in tools:
        if not _REQUIRED_KEYS.issubset(entry.keys()):
            raise ValueError(
                f"tool entry missing required keys {_REQUIRED_KEYS - entry.keys()}: {entry!r}",
            )
        name = entry["name"]
        if name not in denied_set:
            out.append(dict(entry))
            continue

        if adapter_supports_unavailable_flag:
            # Rebuild dict preserving original key order, append flags last so
            # bytes before the flag remain in place.
            masked: dict[str, Any] = dict(entry)
            masked["unavailable"] = True
            masked["unavailable_reason"] = reason_for(name)
            out.append(masked)
            masked_names.append(name)
        else:
            fallback_removed.append(name)

    return MaskResult(
        tools=out,
        masked_names=tuple(masked_names),
        fallback_removed=tuple(fallback_removed),
    )


__all__ = [
    "MaskResult",
    "mask_tools",
]
