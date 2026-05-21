"""Short-lived resume tokens for cross-surface session handoff (op-005).

A :class:`HandoffToken` is the single piece of state the source surface
emits when it freezes; the destination presents it back to claim the
session. Tokens live in ``.sdd/runtime/handoff_tokens.json`` so any
surface (CLI, dashboard, chat bridge) can resolve them without IPC.

Lifecycle:

1. Source calls :func:`emit_token`. We mint a 32-character urlsafe id,
   stamp ``issued_at``/``expires_at`` (TTL: 5 min), and persist the
   record.
2. Destination calls :func:`claim_token`. We look the id up, check it
   is not expired or already-claimed, and return the
   :class:`HandoffToken` so the caller can re-attach to the session.
3. The token is **single-use** - claiming flips a flag and any future
   claim raises :class:`HandoffClaimError`.
4. Expired tokens are swept on every load; nothing else needs to run a
   janitor.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final, Literal, cast

from bernstein.core.persistence.atomic_write import write_atomic_json

__all__ = [
    "DEFAULT_TOKEN_TTL_S",
    "HandoffClaimError",
    "HandoffToken",
    "HandoffTokenStore",
    "HandoffUnknownTokenError",
    "Surface",
    "claim_token",
    "emit_token",
]

DEFAULT_TOKEN_TTL_S: Final[float] = 300.0  # 5 minutes per the ticket.
_TOKENS_FILE: Final[Path] = Path(".sdd") / "runtime" / "handoff_tokens.json"
_TOKEN_BYTES: Final[int] = 24  # ~32 urlsafe chars

Surface = Literal["terminal", "chat", "dashboard"]
_VALID_SURFACES: Final[tuple[str, ...]] = ("terminal", "chat", "dashboard")


class HandoffUnknownTokenError(KeyError):
    """Raised when the destination presents a token we never issued."""


class HandoffClaimError(RuntimeError):
    """Raised when a token cannot be claimed (expired or already used)."""


@dataclass(slots=True)
class HandoffToken:
    """One pending or consumed cross-surface handoff record.

    Attributes:
        token: Opaque urlsafe id minted by :func:`emit_token`.
        session_id: Bernstein session the destination should re-attach
            to.
        task_id: Active task id, if any. Empty string when the session
            is between tasks.
        source_surface: Surface that emitted the token
            (``"terminal"`` / ``"chat"`` / ``"dashboard"``).
        issued_at: Epoch seconds when the source froze.
        expires_at: Epoch seconds after which the token is invalid.
        claimed: ``True`` once a destination has consumed the token.
        claimed_at: Epoch seconds when the claim happened, or ``None``.
        claimed_by: Surface that claimed the token. Empty until claimed.
        note: Free-form note the source can attach (e.g. the chat
            thread id) so the destination can render context.
    """

    token: str
    session_id: str
    task_id: str = ""
    source_surface: Surface = "terminal"
    issued_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    claimed: bool = False
    claimed_at: float | None = None
    claimed_by: str = ""
    note: str = ""

    def is_expired(self, *, now: float | None = None) -> bool:
        """Return True when the token is past its TTL.

        Args:
            now: Optional clock override (used in tests).
        """
        ts = now if now is not None else time.time()
        return ts >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HandoffToken:
        """Deserialise from a JSON-parsed dict.

        Args:
            data: Parsed JSON object.

        Returns:
            Populated :class:`HandoffToken`.

        Raises:
            KeyError: If ``token`` or ``session_id`` is absent.
            ValueError: If numeric fields cannot be coerced.
        """
        surface_raw = str(data.get("source_surface", "terminal"))
        if surface_raw not in _VALID_SURFACES:
            surface_raw = "terminal"
        claimed_at_raw = data.get("claimed_at")
        return cls(
            token=str(data["token"]),
            session_id=str(data["session_id"]),
            task_id=str(data.get("task_id", "")),
            source_surface=cast("Surface", surface_raw),
            issued_at=float(data.get("issued_at", 0.0)),
            expires_at=float(data.get("expires_at", 0.0)),
            claimed=bool(data.get("claimed", False)),
            claimed_at=float(claimed_at_raw) if claimed_at_raw is not None else None,
            claimed_by=str(data.get("claimed_by", "")),
            note=str(data.get("note", "")),
        )


class HandoffTokenStore:
    """File-backed registry of pending and consumed handoff tokens.

    Persists to ``<workdir>/.sdd/runtime/handoff_tokens.json``. Each
    public mutation goes through :func:`write_atomic_json` so concurrent
    crashes never leave a torn JSON document.
    """

    def __init__(
        self,
        workdir: Path,
        *,
        ttl_s: float = DEFAULT_TOKEN_TTL_S,
        clock: object | None = None,
    ) -> None:
        """Create a store rooted at *workdir*.

        Args:
            workdir: Project root (the ``.sdd/`` parent).
            ttl_s: Token lifetime in seconds. Defaults to 5 minutes per
                the ticket.
            clock: Optional callable returning the current epoch
                seconds; tests inject deterministic clocks here.
        """
        if ttl_s <= 0:
            raise ValueError("ttl_s must be positive")
        self._workdir = workdir
        self._ttl_s = ttl_s
        self._path = workdir / _TOKENS_FILE
        self._lock = threading.Lock()
        self._clock: Any = clock if callable(clock) else time.time

    @property
    def path(self) -> Path:
        """Absolute path of the on-disk JSON registry."""
        return self._path

    @property
    def ttl_s(self) -> float:
        """Configured token TTL in seconds."""
        return self._ttl_s

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def issue(
        self,
        *,
        session_id: str,
        task_id: str = "",
        source_surface: Surface = "terminal",
        note: str = "",
    ) -> HandoffToken:
        """Mint a new token bound to ``session_id``.

        Args:
            session_id: Bernstein session the destination should
                attach to. Must be non-empty.
            task_id: Active task id, if any.
            source_surface: Surface emitting the token.
            note: Free-form note (e.g. chat thread id).

        Returns:
            The freshly issued :class:`HandoffToken`.
        """
        if not session_id:
            raise ValueError("session_id is required")
        now = float(self._clock())
        # Prefix with "h_" so the token never starts with "-" (urlsafe base64
        # alphabet includes "-"). A leading dash makes click misparse the
        # token as an option in `bernstein handoff claim TOKEN` and breaks
        # ~1.5% of issued tokens at random - flaky in CI, surprising in prod.
        token = HandoffToken(
            token=f"h_{secrets.token_urlsafe(_TOKEN_BYTES)}",
            session_id=session_id,
            task_id=task_id,
            source_surface=source_surface,
            issued_at=now,
            expires_at=now + self._ttl_s,
            note=note,
        )
        with self._lock:
            tokens = self._load_locked()
            tokens[token.token] = token
            self._save_locked(self._purge_expired(tokens, now=now))
        return token

    def claim(self, token: str, *, claimed_by: Surface) -> HandoffToken:
        """Consume the token, returning its payload.

        Args:
            token: Opaque token string the destination presents.
            claimed_by: Destination surface (recorded for audit).

        Returns:
            The :class:`HandoffToken` after marking it consumed.

        Raises:
            HandoffUnknownTokenError: When ``token`` was never issued.
            HandoffClaimError: When the token has expired or has
                already been claimed.
        """
        if not token:
            raise HandoffUnknownTokenError("empty token")
        now = float(self._clock())
        with self._lock:
            tokens = self._load_locked()
            tokens = self._purge_expired(tokens, now=now)
            existing = tokens.get(token)
            if existing is None:
                self._save_locked(tokens)
                raise HandoffUnknownTokenError(token)
            if existing.claimed:
                raise HandoffClaimError(f"token already claimed by {existing.claimed_by!r}")
            if existing.is_expired(now=now):
                # Expiry sweep above should already have removed it,
                # but double-check for clock skew safety.
                tokens.pop(token, None)
                self._save_locked(tokens)
                raise HandoffClaimError("token expired")
            existing.claimed = True
            existing.claimed_at = now
            existing.claimed_by = claimed_by
            tokens[token] = existing
            self._save_locked(tokens)
            return existing

    def get(self, token: str) -> HandoffToken | None:
        """Return the stored record for ``token`` or ``None``.

        Performs an expiry sweep but does **not** mutate the
        ``claimed`` flag; this is a read-only inspection helper used by
        tests and the dashboard route.
        """
        if not token:
            return None
        now = float(self._clock())
        with self._lock:
            tokens = self._load_locked()
            tokens = self._purge_expired(tokens, now=now)
            self._save_locked(tokens)
            return tokens.get(token)

    def all(self) -> list[HandoffToken]:
        """Return every live (non-expired) token in issue order."""
        now = float(self._clock())
        with self._lock:
            tokens = self._load_locked()
            tokens = self._purge_expired(tokens, now=now)
            self._save_locked(tokens)
            return sorted(tokens.values(), key=lambda t: t.issued_at)

    # ------------------------------------------------------------------
    # I/O helpers (assume the lock is already held)
    # ------------------------------------------------------------------

    def _load_locked(self) -> dict[str, HandoffToken]:
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        out: dict[str, HandoffToken] = {}
        for key, value in cast("dict[str, Any]", raw).items():
            if not isinstance(value, dict):
                continue
            try:
                token = HandoffToken.from_dict(cast("dict[str, Any]", value))
            except (KeyError, ValueError):
                continue
            out[key] = token
        return out

    def _save_locked(self, tokens: dict[str, HandoffToken]) -> None:
        payload = {key: tok.to_dict() for key, tok in tokens.items()}
        write_atomic_json(self._path, payload)

    def _purge_expired(
        self,
        tokens: dict[str, HandoffToken],
        *,
        now: float,
    ) -> dict[str, HandoffToken]:
        return {
            tid: tok
            for tid, tok in tokens.items()
            # Keep claimed tokens around for ttl_s after issue so
            # operators can still see "who claimed what" briefly.
            if not tok.is_expired(now=now)
        }


# ---------------------------------------------------------------------------
# High-level helpers used by the CLI / dashboard / chat surfaces.
# ---------------------------------------------------------------------------


def emit_token(
    workdir: Path,
    *,
    session_id: str,
    task_id: str = "",
    source_surface: Surface = "terminal",
    note: str = "",
    ttl_s: float = DEFAULT_TOKEN_TTL_S,
) -> HandoffToken:
    """Mint a new handoff token for *session_id*.

    Convenience wrapper around :class:`HandoffTokenStore`. Callers that
    need to share a store instance (e.g. a long-running server) should
    instantiate it directly.

    Args:
        workdir: Project root.
        session_id: Bernstein session id to hand off.
        task_id: Active task id, if any.
        source_surface: Surface emitting the token.
        note: Free-form note carried alongside the token.
        ttl_s: Token lifetime in seconds.

    Returns:
        The freshly issued :class:`HandoffToken`.
    """
    return HandoffTokenStore(workdir, ttl_s=ttl_s).issue(
        session_id=session_id,
        task_id=task_id,
        source_surface=source_surface,
        note=note,
    )


def claim_token(
    workdir: Path,
    token: str,
    *,
    claimed_by: Surface,
    ttl_s: float = DEFAULT_TOKEN_TTL_S,
) -> HandoffToken:
    """Consume a token, returning its payload.

    Args:
        workdir: Project root.
        token: Opaque token presented by the destination.
        claimed_by: Surface claiming the token.
        ttl_s: Token lifetime - used purely for expiry sweeping when the
            store is constructed; the previously-recorded
            ``expires_at`` is what gates the claim.

    Returns:
        The claimed :class:`HandoffToken`.

    Raises:
        HandoffUnknownTokenError: When the token was never issued.
        HandoffClaimError: When the token is expired or already claimed.
    """
    return HandoffTokenStore(workdir, ttl_s=ttl_s).claim(token, claimed_by=claimed_by)
