"""Session handoff between terminal and chat/dashboard surfaces (op-005).

A run started in the terminal should be continuable from the web
dashboard, a chat bridge, or another terminal session - and vice versa.
The :func:`emit_token` call freezes the source surface and writes a
short-lived (5 min) resume token; the destination calls
:func:`claim_token` to re-attach to the same session_id without
interrupting any in-flight tool calls.

State lives in ``.sdd/runtime/handoff_tokens.json`` (atomic JSON map)
plus a per-session ring buffer at ``.sdd/runtime/handoff_tail/``.

Public API:

* :class:`HandoffToken` - typed payload carrying session/task identity
  and the ring-buffer pointer the destination uses to replay the stream
  tail.
* :class:`HandoffTokenStore` - file-backed token table (atomic read /
  write, expiry sweep, single-claim semantics).
* :class:`StreamTailBuffer` - bounded log ring buffer the source writes
  into and the destination drains on claim.
* :func:`emit_token` / :func:`claim_token` - high-level helpers used by
  the CLI, the dashboard route, and the chat slash command.
"""

from __future__ import annotations

from bernstein.core.handoff.ring_buffer import StreamTailBuffer, TailEntry
from bernstein.core.handoff.tokens import (
    DEFAULT_TOKEN_TTL_S,
    HandoffClaimError,
    HandoffToken,
    HandoffTokenStore,
    HandoffUnknownTokenError,
    claim_token,
    emit_token,
)

__all__ = [
    "DEFAULT_TOKEN_TTL_S",
    "HandoffClaimError",
    "HandoffToken",
    "HandoffTokenStore",
    "HandoffUnknownTokenError",
    "StreamTailBuffer",
    "TailEntry",
    "claim_token",
    "emit_token",
]
