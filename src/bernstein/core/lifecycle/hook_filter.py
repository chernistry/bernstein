"""Permission-rule prefilter for lifecycle hooks.

A hook registration may carry an optional ``if:`` filter expressed in the
same permission-rule grammar the security subpackage already uses:

* ``Bash(git *)`` -- matches a ``Bash`` tool whose ``command`` matches the
  glob ``git *``.
* ``Read(/path/*)`` -- matches a ``Read`` tool whose ``path`` matches the
  glob ``/path/*``.
* ``Tool(name)`` -- matches a tool whose name matches the glob ``name``
  with no argument constraint.
* ``Bash`` (no parentheses) -- matches any ``Bash`` invocation.

The lifecycle runner evaluates the filter against a hook event payload
*before* spawning the hook subprocess. A non-matching event short-circuits
the spawn and skips the cold-start cost.

The filter reuses :class:`~bernstein.core.security.permission_rules.PermissionRule`
and its glob matcher so the grammar stays identical to the permission engine.
Parse errors are raised eagerly at config-load time via
:func:`parse_hook_filter`, never at dispatch time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, cast

from bernstein.core.security.permission_rules import (
    PermissionRule,
    PermissionRuleEngine,
    RuleAction,
)

__all__ = [
    "HookFilter",
    "HookFilterError",
    "parse_hook_filter",
]


class HookFilterError(ValueError):
    """Raised when an ``if:`` filter string is syntactically invalid.

    Surfaced at config-load time so a malformed filter prevents the hook
    from registering with a clear error, rather than failing at dispatch.
    """


# ``Bash(git *)`` -> tool="Bash", arg="git *"
# ``Read(/p/*)``  -> tool="Read", arg="/p/*"
# ``Tool(name)``  -> tool=<the literal grammar form `Tool`>, arg="name"
# ``Bash``        -> tool="Bash", arg=None
_FILTER_RE = re.compile(
    r"""
    ^\s*
    (?P<tool>[^\W\d]\w*)               # tool selector token
    (?:
        \(                              # opening paren
        (?P<arg>[^)]*)                  # argument glob (may be empty)
        \)                              # closing paren
    )?
    \s*$
    """,
    re.VERBOSE | re.ASCII,
)

# The ``Tool(...)`` form is a tool-name selector with no argument
# constraint: ``Tool(grep)`` matches the tool whose name globs ``grep``.
_TOOL_KEYWORD = "Tool"

# Tools whose single positional argument is a command string rather than a
# filesystem path. Everything else treats the argument as a path glob.
_COMMAND_TOOLS = frozenset({"bash", "shell", "sh", "exec", "run"})


@dataclass(frozen=True, slots=True)
class HookFilter:
    """A parsed hook ``if:`` filter.

    Wraps a single :class:`PermissionRule` (with ``action=allow``) and
    evaluates it against a hook event payload's ``tool`` / ``args`` fields.
    """

    source: str
    rule: PermissionRule

    def matches(self, payload: dict[str, Any]) -> bool:
        """Return True if this filter matches the given hook event payload.

        The payload is the ``data`` mapping carried on a
        :class:`~bernstein.core.lifecycle.hooks.LifecycleContext`. For
        tool-scoped events it carries ``tool`` (the tool name) and ``args``
        (the tool input mapping). Events without a ``tool`` key never match
        a tool-scoped filter.

        Matching is delegated to a single-rule
        :class:`~bernstein.core.security.permission_rules.PermissionRuleEngine`
        so the grammar stays identical to the permission engine.

        Args:
            payload: The event ``data`` mapping.

        Returns:
            True when the filter's tool and argument globs both match.
        """
        tool_name = payload.get("tool")
        if not isinstance(tool_name, str):
            return False
        args = payload.get("args")
        tool_input: dict[str, Any] = cast("dict[str, Any]", args) if isinstance(args, dict) else {}
        return PermissionRuleEngine(rules=[self.rule]).evaluate(tool_name, tool_input).matched


def parse_hook_filter(source: str | None) -> HookFilter | None:
    """Parse an ``if:`` filter string into a :class:`HookFilter`.

    Args:
        source: The raw filter string, or ``None`` when no filter was
            declared (the hook then always matches).

    Returns:
        A :class:`HookFilter`, or ``None`` when *source* is ``None``.

    Raises:
        HookFilterError: When *source* is non-empty but does not parse as a
            valid permission-rule selector.
    """
    if source is None:
        return None

    stripped = source.strip()
    if not stripped:
        raise HookFilterError("hook filter must not be empty; omit 'if:' to always match")

    match = _FILTER_RE.match(stripped)
    if match is None:
        raise HookFilterError(
            f"invalid hook filter {source!r}; expected forms like "
            "'Bash(git *)', 'Read(/path/*)', 'Tool(name)', or 'Bash'",
        )

    selector = match.group("tool")
    arg = match.group("arg")

    if selector == _TOOL_KEYWORD:
        # ``Tool(name)`` -> tool-name glob, no argument constraint.
        if arg is None:
            raise HookFilterError(
                f"invalid hook filter {source!r}; 'Tool' requires a name, e.g. 'Tool(grep)'",
            )
        tool_glob = arg.strip()
        if not tool_glob:
            raise HookFilterError(
                f"invalid hook filter {source!r}; 'Tool(...)' name must not be empty",
            )
        rule = PermissionRule(id=f"hook-filter:{source}", action=RuleAction.ALLOW, tool=tool_glob)
        return HookFilter(source=source, rule=rule)

    tool_glob = selector
    if arg is None:
        # ``Bash`` -> match any invocation of that tool.
        rule = PermissionRule(id=f"hook-filter:{source}", action=RuleAction.ALLOW, tool=tool_glob)
        return HookFilter(source=source, rule=rule)

    arg_glob = arg.strip()
    if not arg_glob:
        raise HookFilterError(
            f"invalid hook filter {source!r}; argument inside '(...)' must not be empty",
        )

    if selector.lower() in _COMMAND_TOOLS:
        rule = PermissionRule(
            id=f"hook-filter:{source}",
            action=RuleAction.ALLOW,
            tool=tool_glob,
            command=arg_glob,
        )
    else:
        rule = PermissionRule(
            id=f"hook-filter:{source}",
            action=RuleAction.ALLOW,
            tool=tool_glob,
            path=arg_glob,
        )
    return HookFilter(source=source, rule=rule)
